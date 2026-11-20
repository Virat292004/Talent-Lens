import asyncio, re
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query
from shared.platform import consume, mongo_collection, instrument_app

async def on_event(event):
    if event.get("event_type")=="ResumeDeleted":
        mongo_collection("rankings").delete_many({"resume_id":event["payload"].get("resume_id")})
        return
    if event.get("event_type")=="JobDeleted":
        mongo_collection("rankings").delete_many({"job_id":event["payload"].get("job_id")})
        return
    if event.get("event_type")!="CandidateEvaluated": return
    item=event["payload"]
    mongo_collection("rankings").replace_one({"job_id":item["job_id"],"candidate_id":item["candidate_id"]},{**item,"updated_at":event.get("occurred_at")},upsert=True)
    from shared.platform import publish
    await publish("RankingUpdated",{"job_id":item["job_id"],"candidate_id":item["candidate_id"],"score":item["score"]["overall_score"]},event.get("correlation_id"))

@asynccontextmanager
async def lifespan(app):
    mongo_collection("rankings").create_index([("job_id",1),("score.overall_score",-1)])
    task=asyncio.create_task(consume("ranking.candidate-evaluated",["CandidateEvaluated"],on_event)); yield; task.cancel()
    try: await task
    except asyncio.CancelledError: pass

app=FastAPI(title="TalentLens Ranking Service",version="1.0.0",lifespan=lifespan)
instrument_app(app)

@app.get("/health")
def health():
    try: mongo_collection("rankings").database.command("ping"); return {"status":"ok","service":"ranking-service"}
    except Exception as e: raise HTTPException(503,str(e))

@app.get("/api/rankings")
def rankings(job_id:str=Query(...),search:str|None=Query(None,max_length=80),min_score:float=Query(0,ge=0,le=100),sort:str="desc"):
    query={"job_id":job_id,"score.overall_score":{"$gte":min_score}}
    if search: query["$or"]=[{"profile.name":{"$regex":re.escape(search),"$options":"i"}},{"candidate_name":{"$regex":re.escape(search),"$options":"i"}},{"profile.skills":{"$regex":re.escape(search),"$options":"i"}}]
    direction=-1 if sort.lower()!="asc" else 1
    rows=list(mongo_collection("rankings").find(query,{"_id":0,"profile.raw_text":0}).sort([("score.overall_score",direction),("candidate_id",1)]))
    return {"items":rows,"count":len(rows)}

@app.get("/api/rankings/candidates/{candidate_id}")
def candidate_analysis(candidate_id:str,job_id:str|None=None):
    q={"candidate_id":candidate_id}
    if job_id: q["job_id"]=job_id
    rows=list(mongo_collection("rankings").find(q,{"_id":0,"profile.raw_text":0}).sort("updated_at",-1))
    if not rows: raise HTTPException(404,"Candidate analysis not found")
    return rows[0] if job_id else {"items":rows}

@app.get("/api/rankings/compare")
def compare(job_id:str,candidate_ids:list[str]=Query(...)):
    if len(candidate_ids)<2 or len(candidate_ids)>4: raise HTTPException(422,"Compare between 2 and 4 candidates")
    rows=list(mongo_collection("rankings").find({"job_id":job_id,"candidate_id":{"$in":candidate_ids}},{"_id":0,"profile.raw_text":0}).sort([("score.overall_score",-1),("candidate_id",1)]))
    return {"items":rows}
