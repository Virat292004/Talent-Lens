import asyncio, time
from contextlib import asynccontextmanager
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb
from prometheus_client import Histogram
from shared.db import connect, ensure_outbox, enqueue, outbox_loop
from shared.platform import consume, calculate_match, mongo_collection, instrument_app

def save_job(job):
    with connect() as conn:
        conn.execute("INSERT INTO jobs_projection(id,title,description,requirements) VALUES (%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET title=excluded.title,description=excluded.description,requirements=excluded.requirements",(job["id"],job.get("title","Job"),job.get("description",""),Jsonb(job.get("requirements",{}))))

DEFAULT_WEIGHTS={"skills":.40,"semantic":.25,"experience":.20,"education":.10,"projects":.05}
MATCHING_TIME=Histogram("talentlens_matching_seconds","Time to compute and persist one candidate-job match")

def current_weights():
    with connect() as conn:
        row=conn.execute("SELECT weights FROM score_settings WHERE id=1").fetchone()
    return row["weights"] if row else DEFAULT_WEIGHTS

def score_profile(job, profile, correlation_id=None):
    started=time.perf_counter()
    score=calculate_match(job,profile,current_weights())
    result={"job_id":job["id"],"candidate_id":profile["candidate_id"],"resume_id":profile["resume_id"],"candidate_name":profile.get("name"),"score":score,"profile":profile}
    with connect() as conn:
        ensure_outbox(conn)
        conn.execute("INSERT INTO match_results(job_id,candidate_id,resume_id,score,components,explanation,updated_at) VALUES (%s,%s,%s,%s,%s,%s,now()) ON CONFLICT(job_id,candidate_id) DO UPDATE SET resume_id=excluded.resume_id,score=excluded.score,components=excluded.components,explanation=excluded.explanation,updated_at=now()",(job["id"],profile["candidate_id"],profile["resume_id"],score["overall_score"],Jsonb(score["components"]),Jsonb(score)))
        enqueue(conn,"CandidateEvaluated",result,correlation_id)
    MATCHING_TIME.observe(time.perf_counter()-started)

async def on_event(event):
    typ=event.get("event_type"); payload=event.get("payload",{})
    with connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS matching_events(event_id text PRIMARY KEY,processed_at timestamptz NOT NULL DEFAULT now())")
        if conn.execute("SELECT 1 FROM matching_events WHERE event_id=%s",(event["event_id"],)).fetchone(): return
    if typ=="ResumeDeleted":
        with connect() as conn:
            conn.execute("DELETE FROM match_results WHERE resume_id=%s",(payload.get("resume_id"),))
            conn.execute("INSERT INTO matching_events(event_id) VALUES (%s) ON CONFLICT DO NOTHING",(event["event_id"],))
        return
    if typ=="JobDeleted":
        with connect() as conn:
            conn.execute("DELETE FROM match_results WHERE job_id=%s",(payload.get("job_id"),))
            conn.execute("DELETE FROM jobs_projection WHERE id=%s",(payload.get("job_id"),))
            conn.execute("INSERT INTO matching_events(event_id) VALUES (%s) ON CONFLICT DO NOTHING",(event["event_id"],))
        return
    if typ in {"JobCreated","JobUpdated"}:
        save_job(payload)
        for profile in mongo_collection("profiles").find({}, {"_id":0}): score_profile(payload,profile,event.get("correlation_id"))
    elif typ=="ResumeParsed":
        profile=payload.get("profile")
        if not profile: return
        with connect() as conn: jobs=conn.execute("SELECT id,title,description,requirements FROM jobs_projection").fetchall()
        for job in jobs: score_profile(job,profile,event.get("correlation_id"))
    with connect() as conn: conn.execute("INSERT INTO matching_events(event_id) VALUES (%s) ON CONFLICT DO NOTHING",(event["event_id"],))

@asynccontextmanager
async def lifespan(app):
    with connect() as conn:
        ensure_outbox(conn)
        conn.execute("CREATE TABLE IF NOT EXISTS jobs_projection(id uuid PRIMARY KEY,title text,description text NOT NULL,requirements jsonb NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS match_results(job_id uuid NOT NULL,candidate_id uuid NOT NULL,resume_id uuid NOT NULL,score numeric NOT NULL,components jsonb NOT NULL,explanation jsonb NOT NULL,updated_at timestamptz NOT NULL,PRIMARY KEY(job_id,candidate_id))")
        conn.execute("CREATE TABLE IF NOT EXISTS score_settings(id int PRIMARY KEY CHECK(id=1),weights jsonb NOT NULL,updated_at timestamptz NOT NULL DEFAULT now())")
        conn.execute("INSERT INTO score_settings(id,weights) VALUES (1,%s) ON CONFLICT DO NOTHING",(Jsonb(DEFAULT_WEIGHTS),))
    stop=asyncio.Event(); publisher=asyncio.create_task(outbox_loop(stop)); consumer=asyncio.create_task(consume("matching.job-and-profile",["JobCreated","JobUpdated","JobDeleted","ResumeParsed","ResumeDeleted"],on_event)); app.state.stop=stop; app.state.tasks=[publisher,consumer]
    yield
    stop.set(); consumer.cancel()
    for task in app.state.tasks:
        try: await task
        except asyncio.CancelledError: pass

app=FastAPI(title="TalentLens Matching Service",version="1.0.0",lifespan=lifespan)
instrument_app(app)

class WeightSettings(BaseModel):
    weights: dict[str,float] = Field(...)

@app.get("/health")
def health():
    try:
        with connect() as conn: conn.execute("SELECT 1")
        return {"status":"ok","service":"matching-service"}
    except Exception as e: raise HTTPException(503,str(e))

@app.get("/api/matching/jobs/{job_id}")
def get_match_results(job_id:str):
    with connect() as conn: return conn.execute("SELECT job_id,candidate_id,resume_id,score,components,explanation,updated_at FROM match_results WHERE job_id=%s ORDER BY score DESC,candidate_id",(job_id,)).fetchall()

@app.get("/api/matching/settings")
def get_settings(): return {"weights":current_weights()}

def recalculate_all():
    with connect() as conn: jobs=conn.execute("SELECT id,title,description,requirements FROM jobs_projection").fetchall()
    profiles=list(mongo_collection("profiles").find({}, {"_id":0}))
    for job in jobs:
        for profile in profiles: score_profile(job,profile)

@app.put("/api/matching/settings")
def put_settings(body:WeightSettings,background_tasks:BackgroundTasks):
    weights=body.weights
    if set(weights)!=set(DEFAULT_WEIGHTS) or any(v<0 or v>1 for v in weights.values()) or abs(sum(weights.values())-1)>0.0001:
        raise HTTPException(422,"Provide all five weights as decimals between 0 and 1 that sum to 1")
    with connect() as conn:
        conn.execute("UPDATE score_settings SET weights=%s,updated_at=now() WHERE id=1",(Jsonb(weights),))
    background_tasks.add_task(recalculate_all)
    return {"weights":weights,"note":"Existing profiles are being reevaluated with the updated weights."}
