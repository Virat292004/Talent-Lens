import json, os, re, uuid, asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Header
from pydantic import BaseModel, Field
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from shared.db import connect, ensure_outbox, enqueue, outbox_loop
from shared.platform import extract_skills, validate_docx_bytes, instrument_app
import fitz
from docx import Document

def extract_doc(file: UploadFile, raw: bytes) -> str:
    suffix = Path(file.filename or "").suffix.lower()
    if len(raw)>10*1024*1024: raise HTTPException(413,"Maximum job description file size is 10 MB")
    if suffix == ".txt": return raw.decode("utf-8", errors="replace")
    if suffix == ".docx":
        try: validate_docx_bytes(raw,20*1024*1024)
        except ValueError as exc: raise HTTPException(415,str(exc))
    tmp = Path("/tmp") / f"{uuid.uuid4()}{suffix}"
    tmp.write_bytes(raw)
    try:
        if suffix == ".pdf": return "\n".join(p.get_text() for p in fitz.open(tmp))
        if suffix == ".docx": return "\n".join(p.text for p in Document(tmp).paragraphs)
    finally: tmp.unlink(missing_ok=True)
    raise HTTPException(415, "Only TXT, PDF, and DOCX job descriptions are supported")

def requirements_for(text: str, title: str | None = None) -> dict:
    skills = extract_skills(text)
    preferred = sorted(s for s in skills if re.search(rf"(?i)(preferred|nice.to.have|bonus)[^\n]{{0,90}}\b{re.escape(s)}\b", text))
    mandatory = sorted(set(skills) - set(preferred))
    exp = re.search(r"(?i)(\d{1,2})\+?\s*(?:years?|yrs?)\s+(?:of\s+)?experience", text)
    lines = [x.strip(" •\t-") for x in text.splitlines() if x.strip()]
    responsibilities=[]; in_section=False
    for line in lines:
        if re.search(r"(?i)responsibilit|what you(?:'|’)ll do|what you will do",line) and len(line)<100:
            in_section=True
            if len(line)>45: responsibilities.append(line)
            continue
        if in_section and re.fullmatch(r"[A-Z][A-Z\s/&-]{2,}",line): in_section=False
        if in_section and (re.match(r"(?i)^(design|build|develop|implement|maintain|lead|collaborate|create|own|improve|work|partner|deliver)\b",line) or len(responsibilities)<12): responsibilities.append(line)
        elif re.search(r"(?i)you will|responsible for",line): responsibilities.append(line)
    return {"title": title or (lines[0][:120] if lines else "Untitled role"), "mandatory_skills": mandatory, "preferred_skills": preferred, "minimum_experience_years": int(exp.group(1)) if exp else None, "education": [x for x in ["bachelor", "master", "phd"] if re.search(x, text, re.I)], "responsibilities": responsibilities[:12], "tools_and_technologies": skills, "summary": text[:6000]}

@asynccontextmanager
async def lifespan(app):
    with connect() as conn:
        ensure_outbox(conn)
        conn.execute("CREATE TABLE IF NOT EXISTS jobs (id uuid PRIMARY KEY, title text NOT NULL, description text NOT NULL, requirements jsonb NOT NULL, created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL)")
    stop=asyncio.Event(); task=asyncio.create_task(outbox_loop(stop)); app.state.stop=stop; app.state.task=task
    yield
    stop.set(); await task

app = FastAPI(title="TalentLens Job Service", version="1.0.0", lifespan=lifespan)
instrument_app(app)

class JobInput(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    description: str = Field(min_length=20, max_length=100000)
    requirements: dict | None = None

@app.get("/health")
def health():
    try:
        with connect() as conn: conn.execute("SELECT 1")
        return {"status":"ok", "service":"job-service"}
    except Exception as e: raise HTTPException(503, str(e))

@app.post("/api/jobs", status_code=201)
def create_job(body: JobInput, x_correlation_id: str | None = Header(default=None)):
    job_id=str(uuid.uuid4()); now=datetime.now(timezone.utc); req=body.requirements or requirements_for(body.description, body.title)
    job={"id":job_id,"title":body.title or req.get("title") or "Untitled role","description":body.description,"requirements":req,"created_at":now.isoformat(),"updated_at":now.isoformat()}
    with connect() as conn:
        ensure_outbox(conn)
        conn.execute("INSERT INTO jobs(id,title,description,requirements,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s)", (job_id,job["title"],body.description,Jsonb(req),now,now))
        enqueue(conn,"JobCreated",job,x_correlation_id)
    return job

@app.post("/api/jobs/upload", status_code=201)
async def upload_job(file: UploadFile = File(...), title: str | None = Form(None), x_correlation_id: str | None = Header(default=None)):
    if Path(file.filename or "").suffix.lower() not in {".txt", ".pdf", ".docx"}: raise HTTPException(415,"Only TXT, PDF, DOCX are supported")
    text=extract_doc(file, await file.read())
    return create_job(JobInput(title=title,description=text),x_correlation_id)

@app.get("/api/jobs")
def list_jobs():
    with connect() as conn:
        rows=conn.execute("SELECT id,title,description,requirements,created_at,updated_at FROM jobs ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with connect() as conn: row=conn.execute("SELECT id,title,description,requirements,created_at,updated_at FROM jobs WHERE id=%s",(job_id,)).fetchone()
    if not row: raise HTTPException(404,"Job not found")
    return dict(row)

@app.put("/api/jobs/{job_id}")
def update_job(job_id: str, body: JobInput, x_correlation_id: str | None = Header(default=None)):
    now=datetime.now(timezone.utc); req=body.requirements or requirements_for(body.description,body.title)
    with connect() as conn:
        row=conn.execute("UPDATE jobs SET title=%s,description=%s,requirements=%s,updated_at=%s WHERE id=%s RETURNING id,title,description,requirements,created_at,updated_at",(body.title or req.get("title") or "Untitled role",body.description,Jsonb(req),now,job_id)).fetchone()
        if row: enqueue(conn,"JobUpdated",dict(row),x_correlation_id)
    if not row: raise HTTPException(404,"Job not found")
    return dict(row)

@app.delete("/api/jobs/{job_id}", status_code=204)
def delete_job(job_id: str):
    with connect() as conn:
        ensure_outbox(conn)
        result=conn.execute("DELETE FROM jobs WHERE id=%s",(job_id,))
        if result.rowcount: enqueue(conn,"JobDeleted",{"job_id":job_id},None)
    if result.rowcount == 0: raise HTTPException(404,"Job not found")
