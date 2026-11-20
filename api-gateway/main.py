import os, time, uuid, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from jose import jwt, JWTError
import httpx
import redis.asyncio as redis
from prometheus_fastapi_instrumentator import Instrumentator

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("talentlens.gateway")
SECRET = os.getenv("JWT_SECRET", "local-only-change-me")
ALGORITHM = "HS256"
ROUTES = {"jobs":"JOB_URL", "resumes":"RESUME_URL", "candidates":"CANDIDATE_URL", "parsing":"PARSING_URL", "matching":"MATCHING_URL", "rankings":"RANKING_URL"}
FAILURES = {}

@asynccontextmanager
async def lifespan(app):
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(12, connect=2))
    try: app.state.redis = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    except Exception: app.state.redis = None
    yield
    await app.state.http.aclose()
    if app.state.redis: await app.state.redis.aclose()

app = FastAPI(title="TalentLens API Gateway", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(","), allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
Instrumentator().instrument(app).expose(app)
if os.getenv("ENABLE_TRACING","false").lower()=="true":
    from opentelemetry import trace
    from opentelemetry.exporter.zipkin.json import ZipkinExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    provider=TracerProvider(resource=Resource.create({"service.name":"talentlens-api-gateway"}))
    provider.add_span_processor(BatchSpanProcessor(ZipkinExporter(endpoint=os.getenv("ZIPKIN_URL","http://localhost:9411/api/v2/spans"))))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()
if os.getenv("ENABLE_TRACING","false").lower()=="true":
    from opentelemetry import trace
    from opentelemetry.exporter.zipkin.json import ZipkinExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    provider=TracerProvider(resource=Resource.create({"service.name":"talentlens-api-gateway"}))
    provider.add_span_processor(BatchSpanProcessor(ZipkinExporter(endpoint=os.getenv("ZIPKIN_URL","http://localhost:9411/api/v2/spans"))))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()

@app.middleware("http")
async def request_context(request: Request, call_next):
    cid = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    request.state.correlation_id = cid
    max_bytes=int(os.getenv("MAX_REQUEST_MB","110"))*1024*1024
    try:
        if int(request.headers.get("content-length","0"))>max_bytes:
            return JSONResponse({"detail":f"Request exceeds {max_bytes//1024//1024} MB limit","correlation_id":cid},status_code=413)
    except ValueError: return JSONResponse({"detail":"Invalid content length","correlation_id":cid},status_code=400)
    start = time.perf_counter()
    try: response = await call_next(request)
    except Exception:
        log.exception("gateway_unhandled correlation_id=%s", cid)
        response = JSONResponse({"detail":"Gateway internal error", "correlation_id":cid}, status_code=500)
    response.headers["X-Correlation-ID"] = cid
    log.info("request method=%s path=%s status=%s duration_ms=%.1f correlation_id=%s", request.method, request.url.path, response.status_code, (time.perf_counter()-start)*1000, cid)
    return response

def current_user(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "): raise HTTPException(401, "Bearer token required")
    try: return jwt.decode(auth[7:], SECRET, algorithms=[ALGORITHM])
    except JWTError: raise HTTPException(401, "Invalid or expired token")

@app.get("/health")
async def health(): return {"status":"ok", "service":"api-gateway"}

@app.post("/api/auth/login")
async def login(request: Request):
    try:
        ip=request.client.host if request.client else "unknown"
        count=await request.app.state.redis.incr(f"login:{ip}")
        if count==1: await request.app.state.redis.expire(f"login:{ip}",300)
        if count>30: raise HTTPException(429,"Too many login attempts; retry in five minutes")
    except HTTPException: raise
    except Exception: log.warning("redis_login_limit_unavailable")
    body = await request.json()
    if body.get("username") != os.getenv("DEMO_USERNAME", "recruiter") or body.get("password") != os.getenv("DEMO_PASSWORD", "talentlens-demo"):
        raise HTTPException(401, "Invalid credentials")
    token = jwt.encode({"sub":body["username"], "role":"recruiter", "exp":int(time.time())+28800}, SECRET, algorithm=ALGORITHM)
    return {"access_token":token, "token_type":"bearer", "expires_in":28800}

@app.api_route("/api/{service}", methods=["GET","POST","PUT","PATCH","DELETE"])
@app.api_route("/api/{service}/{path:path}", methods=["GET","POST","PUT","PATCH","DELETE"])
async def proxy(service: str, request: Request, path: str = "", user=Depends(current_user)):
    if service not in ROUTES: raise HTTPException(404, "Unknown service route")
    # Local demo guard; Redis outages are visible but do not block development.
    try:
        key = f"rate:{user.get('sub')}:{request.client.host if request.client else 'unknown'}"
        count = await request.app.state.redis.incr(key)
        if count == 1: await request.app.state.redis.expire(key, 60)
        if count > int(os.getenv("RATE_LIMIT_PER_MINUTE", "180")): raise HTTPException(429, "Rate limit exceeded")
    except HTTPException: raise
    except Exception: log.warning("redis_rate_limit_unavailable")
    origin = os.getenv(ROUTES[service], f"http://{service}-service:{5000+list(ROUTES).index(service)+1}")
    # Keep collection routes slashless: appending `/{path}` when `path` is
    # empty makes FastAPI return a 307 for every collection request.
    target = f"{origin.rstrip('/')}/api/{service}"
    if path:
        target += f"/{path.strip('/')}"
    if request.url.query: target += "?" + request.url.query
    failures = FAILURES.get(service, {"count":0,"until":0})
    if failures["count"] >= 5 and failures["until"] > time.monotonic(): return JSONResponse({"detail":"Upstream circuit is open", "service":service}, status_code=503)
    headers = {k:v for k,v in request.headers.items() if k.lower() not in {"host","authorization","content-length"}}
    headers["X-Correlation-ID"] = request.state.correlation_id
    headers["X-Recruiter"] = user.get("sub", "")
    try:
        upstream = await request.app.state.http.request(request.method, target, headers=headers, content=await request.body())
        FAILURES[service] = {"count":0,"until":0}
        return Response(upstream.content, status_code=upstream.status_code, headers={k:v for k,v in upstream.headers.items() if k.lower() in {"content-type","content-disposition","x-correlation-id"}})
    except (httpx.TimeoutException, httpx.ConnectError):
        failures["count"] += 1
        if failures["count"] >= 5: failures["until"] = time.monotonic()+20
        FAILURES[service] = failures
        return JSONResponse({"detail":"Upstream service unavailable", "service":service, "correlation_id":request.state.correlation_id}, status_code=503)
