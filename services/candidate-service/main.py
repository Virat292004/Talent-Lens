import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from shared.db import connect
from shared.platform import consume, instrument_app

async def on_parsed(event):
    if event.get("event_type")=="ResumeDeleted":
        with connect() as conn: conn.execute("DELETE FROM candidates WHERE resume_id=%s",(event["payload"].get("resume_id"),))
        return
    if event.get("event_type")!="ResumeParsed": return
    profile=event["payload"].get("profile",{}); rid=event["payload"].get("resume_id")
    with connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS candidates(id uuid PRIMARY KEY,resume_id uuid UNIQUE NOT NULL,name text,email text,phone text,created_at timestamptz NOT NULL DEFAULT now())")
        conn.execute("CREATE TABLE IF NOT EXISTS candidate_events(event_id text PRIMARY KEY,processed_at timestamptz NOT NULL DEFAULT now())")
        fresh=conn.execute("INSERT INTO candidate_events(event_id) VALUES (%s) ON CONFLICT DO NOTHING RETURNING event_id",(event["event_id"],)).fetchone()
        if not fresh: return
        conn.execute("INSERT INTO candidates(id,resume_id,name,email,phone) VALUES (%s,%s,%s,%s,%s) ON CONFLICT(resume_id) DO UPDATE SET name=excluded.name,email=excluded.email,phone=excluded.phone",(profile.get("candidate_id"),rid,profile.get("name"),profile.get("email"),profile.get("phone")))

@asynccontextmanager
async def lifespan(app):
    with connect() as conn: conn.execute("CREATE TABLE IF NOT EXISTS candidates(id uuid PRIMARY KEY,resume_id uuid UNIQUE NOT NULL,name text,email text,phone text,created_at timestamptz NOT NULL DEFAULT now())")
    task=asyncio.create_task(consume("candidate.resume-parsed",["ResumeParsed","ResumeDeleted"],on_parsed)); yield; task.cancel()
    try: await task
    except asyncio.CancelledError: pass

app=FastAPI(title="TalentLens Candidate Service",version="1.0.0",lifespan=lifespan)
instrument_app(app)

@app.get("/health")
def health():
    try:
        with connect() as conn: conn.execute("SELECT 1")
        return {"status":"ok","service":"candidate-service"}
    except Exception as e: raise HTTPException(503,str(e))

@app.get("/api/candidates")
def list_candidates():
    with connect() as conn: return conn.execute("SELECT id,resume_id,name,email,phone,created_at FROM candidates ORDER BY created_at DESC").fetchall()

@app.get("/api/candidates/{candidate_id}")
def get_candidate(candidate_id:str):
    with connect() as conn: row=conn.execute("SELECT id,resume_id,name,email,phone,created_at FROM candidates WHERE id=%s",(candidate_id,)).fetchone()
    if not row: raise HTTPException(404,"Candidate not found")
    return row
