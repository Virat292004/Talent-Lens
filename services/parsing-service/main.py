import asyncio, os, httpx
from prometheus_client import Counter
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from shared.platform import consume, extract_profile, extract_resume_bytes, mongo_collection, instrument_app
from shared.db import connect, ensure_outbox, enqueue, outbox_loop
from shared.storage import LocalObjectStore

STORAGE=LocalObjectStore(os.getenv("STORAGE_DIR","./data/resumes"))
RESUME_URL=os.getenv("RESUME_SERVICE_URL","http://localhost:5002")
PARSING_FAILURES=Counter("talentlens_parsing_failures_total","Resume document parsing failures")

async def status(resume_id, value, error=None):
    async with httpx.AsyncClient(timeout=5) as client:
        await client.patch(f"{RESUME_URL}/internal/resumes/{resume_id}/status",json={"status":value,"error":error})

def queue_event(event_type,payload,correlation_id):
    with connect() as conn:
        ensure_outbox(conn)
        enqueue(conn,event_type,payload,correlation_id)

async def handle(event):
    if event.get("event_type")=="ResumeDeleted":
        mongo_collection("profiles").delete_one({"resume_id":event["payload"].get("resume_id")})
        return
    if event.get("event_type")!="ResumeUploaded": return
    p=event["payload"]; rid=p["resume_id"]
    with connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS parsing_events(event_id text PRIMARY KEY,processed_at timestamptz NOT NULL DEFAULT now())")
        existing=conn.execute("SELECT 1 FROM parsing_events WHERE event_id=%s",(event["event_id"],)).fetchone()
    if existing: return
    try:
        queue_event("ResumeParsingStarted",{"resume_id":rid},event.get("correlation_id"))
        await status(rid,"parsing")
        source_key=os.path.basename(p["storage_key"])
        text,warnings=extract_resume_bytes(STORAGE.get(source_key),source_key)
        profile=extract_profile(text,rid,p.get("filename","resume"),warnings)
        profile.pop("raw_text",None)
        mongo_collection("profiles").replace_one({"resume_id":rid},profile,upsert=True)
        await status(rid,"parsed")
        queue_event("ResumeParsed",{"resume_id":rid,"profile":profile},event.get("correlation_id"))
        with connect() as conn: conn.execute("INSERT INTO parsing_events(event_id) VALUES (%s) ON CONFLICT DO NOTHING",(event["event_id"],))
    except Exception as exc:
        PARSING_FAILURES.inc()
        try: await status(rid,"failed",str(exc)[:300])
        except Exception: pass
        queue_event("ResumeParsingFailed",{"resume_id":rid,"error":str(exc)[:300]},event.get("correlation_id"))
        with connect() as conn: conn.execute("INSERT INTO parsing_events(event_id) VALUES (%s) ON CONFLICT DO NOTHING",(event["event_id"],))

@asynccontextmanager
async def lifespan(app):
    mongo_collection("profiles").create_index("resume_id",unique=True)
    with connect() as conn: ensure_outbox(conn)
    stop=asyncio.Event(); publisher=asyncio.create_task(outbox_loop(stop))
    task=asyncio.create_task(consume("parsing.resume-uploaded",["ResumeUploaded","ResumeDeleted"],handle)); app.state.consumer=task
    yield
    task.cancel(); stop.set()
    for running in (task,publisher):
        try: await running
        except asyncio.CancelledError: pass

app=FastAPI(title="TalentLens Parsing Service",version="1.0.0",lifespan=lifespan)
instrument_app(app)

@app.get("/health")
def health():
    try: mongo_collection("profiles").database.command("ping"); return {"status":"ok","service":"parsing-service"}
    except Exception as e: raise HTTPException(503,str(e))

@app.get("/api/parsing/{resume_id}")
def get_profile(resume_id:str):
    profile=mongo_collection("profiles").find_one({"resume_id":resume_id},{"_id":0})
    if not profile: raise HTTPException(404,"Profile not parsed yet")
    return profile
