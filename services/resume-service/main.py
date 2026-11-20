import asyncio, hashlib, os, re, uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile, File, Header
from fastapi.responses import FileResponse
from psycopg.rows import dict_row
from shared.db import connect, ensure_outbox, enqueue, outbox_loop
from shared.storage import LocalObjectStore
from shared.platform import validate_docx_bytes, instrument_app

STORAGE=LocalObjectStore(os.getenv("STORAGE_DIR","./data/resumes")); MAX_BYTES=int(os.getenv("MAX_UPLOAD_MB","15"))*1024*1024
ALLOWED={".pdf":"application/pdf", ".docx":"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}

async def retention_loop(stop):
    while not stop.is_set():
        days=max(0,int(os.getenv("DATA_RETENTION_DAYS","0")))
        if days:
            try:
                with connect() as conn:
                    ensure_outbox(conn)
                    expired=conn.execute("DELETE FROM resumes WHERE uploaded_at < now() - (%s * interval '1 day') RETURNING id,storage_key",(days,)).fetchall()
                    for row in expired: enqueue(conn,"ResumeDeleted",{"resume_id":str(row["id"])},None)
                for row in expired: STORAGE.delete(row["storage_key"])
            except Exception: pass
        try: await asyncio.wait_for(stop.wait(),timeout=3600)
        except asyncio.TimeoutError: pass

@asynccontextmanager
async def lifespan(app):
    with connect() as conn:
        ensure_outbox(conn)
        conn.execute("CREATE TABLE IF NOT EXISTS resumes (id uuid PRIMARY KEY, sha256 text NOT NULL UNIQUE, original_name text NOT NULL, storage_key text NOT NULL, media_type text NOT NULL, size_bytes bigint NOT NULL, status text NOT NULL, error text, uploaded_at timestamptz NOT NULL)")
    stop=asyncio.Event(); task=asyncio.create_task(outbox_loop(stop)); retention=asyncio.create_task(retention_loop(stop)); app.state.stop=stop; app.state.task=task; app.state.retention=retention
    yield
    stop.set(); await task; await retention

app=FastAPI(title="TalentLens Resume Service",version="1.0.0",lifespan=lifespan)
instrument_app(app)

@app.get("/health")
def health():
    try:
        with connect() as conn: conn.execute("SELECT 1")
        return {"status":"ok","service":"resume-service"}
    except Exception as e: raise HTTPException(503,str(e))

def validate(name, content_type, data):
    ext=Path(name or "").suffix.lower()
    if ext not in ALLOWED: raise HTTPException(415,"Only PDF and DOCX resumes are supported")
    if content_type and content_type not in {ALLOWED[ext],"application/octet-stream"}: raise HTTPException(415,"File MIME type does not match its extension")
    if ext==".pdf" and not data.startswith(b"%PDF-"): raise HTTPException(415,"Invalid PDF signature")
    if ext==".docx" and not data.startswith(b"PK\x03\x04"): raise HTTPException(415,"Invalid DOCX container")
    if ext==".docx":
        try: validate_docx_bytes(data)
        except ValueError as exc: raise HTTPException(415,str(exc))
    if len(data)>MAX_BYTES: raise HTTPException(413,f"Maximum file size is {MAX_BYTES//1024//1024} MB")
    if not data: raise HTTPException(400,"Empty file")
    return ext

def safe_name(name):
    leaf=re.split(r"[\\/]",name or "resume")[-1]
    return re.sub(r"[^A-Za-z0-9._ -]","_",leaf).strip(" .")[:255] or "resume"

@app.post("/api/resumes/upload",status_code=202)
async def upload_resumes(files:list[UploadFile]=File(...), x_correlation_id:str|None=Header(default=None)):
    if len(files)<10: raise HTTPException(400,"Upload at least 10 resumes per batch")
    if len(files)>100: raise HTTPException(400,"A batch may contain at most 100 files")
    results=[]; batch_bytes=0; prepared=[]
    for file in files:
        data=await file.read()
        batch_bytes+=len(data)
        if batch_bytes>100*1024*1024: raise HTTPException(413,"Batch upload limit is 100 MB")
        ext=validate(file.filename,file.content_type,data)
        digest=hashlib.sha256(data).hexdigest()
        prepared.append((file,data,ext,digest))
    for file,data,ext,digest in prepared:
        with connect() as conn:
            existing=conn.execute("SELECT id,status,original_name FROM resumes WHERE sha256=%s",(digest,)).fetchone()
            if existing:
                results.append({"id":str(existing["id"]),"filename":existing["original_name"],"status":existing["status"],"duplicate":True}); continue
            rid=str(uuid.uuid4()); key=f"{digest}{ext}"
            STORAGE.put(key,data)
            try:
                original=safe_name(file.filename or "resume")
                conn.execute("INSERT INTO resumes(id,sha256,original_name,storage_key,media_type,size_bytes,status,uploaded_at) VALUES (%s,%s,%s,%s,%s,%s,'queued',%s)",(rid,digest,original,key,ALLOWED[ext],len(data),datetime.now(timezone.utc)))
                enqueue(conn,"ResumeUploaded",{"resume_id":rid,"storage_key":key,"filename":original,"media_type":ALLOWED[ext],"sha256":digest},x_correlation_id)
            except Exception:
                STORAGE.delete(key); raise
        results.append({"id":rid,"filename":original,"status":"queued","duplicate":False})
    return {"accepted":len(results),"items":results}

@app.get("/api/resumes")
def list_resumes():
    with connect() as conn: rows=conn.execute("SELECT id,original_name,size_bytes,status,error,uploaded_at FROM resumes ORDER BY uploaded_at DESC").fetchall()
    return [dict(r) for r in rows]

@app.get("/api/resumes/{resume_id}")
def get_resume(resume_id:str):
    with connect() as conn: row=conn.execute("SELECT id,original_name,size_bytes,status,error,uploaded_at FROM resumes WHERE id=%s",(resume_id,)).fetchone()
    if not row: raise HTTPException(404,"Resume not found")
    return dict(row)

@app.get("/api/resumes/{resume_id}/file")
def download_resume(resume_id:str):
    with connect() as conn: row=conn.execute("SELECT storage_key,original_name,media_type FROM resumes WHERE id=%s",(resume_id,)).fetchone()
    if not row: raise HTTPException(404,"Resume not found")
    path=STORAGE.path_for(row["storage_key"])
    if not path.is_file(): raise HTTPException(404,"Resume file is unavailable")
    return FileResponse(path,media_type=row["media_type"],filename=row["original_name"],content_disposition_type="attachment")

@app.patch("/internal/resumes/{resume_id}/status")
def set_status(resume_id:str,payload:dict):
    status=payload.get("status")
    if status not in {"queued","parsing","parsed","failed"}: raise HTTPException(422,"Invalid status")
    with connect() as conn:
        row=conn.execute("UPDATE resumes SET status=%s,error=%s WHERE id=%s RETURNING id,status,error",(status,payload.get("error"),resume_id)).fetchone()
    if not row: raise HTTPException(404,"Resume not found")
    return dict(row)

@app.delete("/api/resumes/{resume_id}",status_code=204)
def delete_resume(resume_id:str):
    with connect() as conn:
        row=conn.execute("DELETE FROM resumes WHERE id=%s RETURNING storage_key",(resume_id,)).fetchone()
        if row:
            ensure_outbox(conn)
            enqueue(conn,"ResumeDeleted",{"resume_id":resume_id},None)
    if not row: raise HTTPException(404,"Resume not found")
    STORAGE.delete(row["storage_key"])
    return None
