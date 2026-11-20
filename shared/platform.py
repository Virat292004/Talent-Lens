import asyncio
import json
import os
import re
import tempfile
from io import BytesIO
from zipfile import BadZipFile,ZipFile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import aio_pika
import fitz
from docx import Document
from pymongo import MongoClient
from shared.event_contracts import make_event
from prometheus_fastapi_instrumentator import Instrumentator

POSTGRES_DSN = os.getenv("POSTGRES_DSN", "postgresql://talentlens:talentlens-local@localhost:5432/talentlens")
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost/")
MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "talentlens")

def instrument_app(app):
    Instrumentator().instrument(app).expose(app)
EXCHANGE = "talentlens.events.v1"

SKILL_ALIASES = {"react.js": "react", "reactjs": "react", "node.js": "node", "nodejs": "node", "postgres": "postgresql", "py": "python", "k8s": "kubernetes", "js": "javascript", "ts": "typescript"}
SKILL_TAXONOMY = [x.strip().lower() for x in os.getenv("SKILL_TAXONOMY", "python,java,javascript,typescript,react,node,fastapi,django,flask,sql,postgresql,mongodb,redis,rabbitmq,docker,kubernetes,aws,azure,git,scikit-learn,machine learning,nlp,pytorch,rest api,html,css,linux").split(",") if x.strip()]
SKILL_RE = re.compile(r"(?i)(?<![\w+#.])(" + "|".join(re.escape(s) for s in sorted(set(SKILL_TAXONOMY)|set(SKILL_ALIASES), key=len, reverse=True)) + r")(?![\w+#])")


def normalize_skill(skill: str) -> str:
    s = re.sub(r"\s+", " ", skill.lower().strip())
    return SKILL_ALIASES.get(s, s)


def extract_skills(text: str) -> list[str]:
    found={normalize_skill(m.group(1)) for m in SKILL_RE.finditer(text)}
    try:
        global _SKILL_NLP
        try: _SKILL_NLP
        except NameError:
            import spacy
            _SKILL_NLP=spacy.blank("en")
            ruler=_SKILL_NLP.add_pipe("entity_ruler")
            ruler.add_patterns([{"label":"SKILL","pattern":[{"LOWER":token}]} for skill in SKILL_TAXONOMY for token in skill.split()])
        found.update(normalize_skill(ent.text) for ent in _SKILL_NLP(text[:100000]).ents if ent.label_=="SKILL")
    except Exception: pass
    return sorted(found)


def extract_resume_text(path: str) -> tuple[str, list[str]]:
    warnings = []
    if path.lower().endswith(".pdf"):
        doc = fitz.open(path)
        if doc.page_count>300: raise ValueError("PDF exceeds the 300-page processing limit")
        text = "\n".join(p.get_text() for p in doc)
        if not text.strip():
            warnings.append("Scanned or image-only PDF detected; OCR is required.")
        return text, warnings
    doc = Document(path)
    text = "\n".join(p.text for p in doc.paragraphs)
    for table in doc.tables:
        text += "\n" + "\n".join(" | ".join(c.text for c in row.cells) for row in table.rows)
    return text, warnings

def validate_docx_bytes(data:bytes,max_uncompressed_bytes:int=50*1024*1024)->None:
    try:
        with ZipFile(BytesIO(data)) as archive:
            entries=archive.infolist()
            if "word/document.xml" not in archive.namelist(): raise ValueError("DOCX is missing its main document")
            expanded=sum(item.file_size for item in entries)
            if expanded>max_uncompressed_bytes: raise ValueError("DOCX expands beyond the processing limit")
            if any(item.file_size>1024*1024 and item.compress_size and item.file_size/item.compress_size>200 for item in entries):
                raise ValueError("DOCX compression ratio exceeds the safe limit")
    except BadZipFile as exc: raise ValueError("Invalid DOCX container") from exc

def extract_resume_bytes(data: bytes, filename: str) -> tuple[str,list[str]]:
    suffix=Path(filename).suffix.lower()
    if suffix not in {".pdf",".docx"}: raise ValueError("Unsupported resume format")
    with tempfile.NamedTemporaryFile(suffix=suffix,delete=False) as handle:
        handle.write(data); temp_path=handle.name
    try: return extract_resume_text(temp_path)
    finally: Path(temp_path).unlink(missing_ok=True)


def extract_profile(text: str, resume_id: str, filename: str, warnings: list[str]) -> dict:
    email = next(iter(re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)), None)
    phone = next(iter(re.findall(r"(?<!\w)(?:\+?\d[\d ()-]{7,}\d)(?!\w)", text)), None)
    lines = [re.sub(r"\s+", " ", x).strip() for x in text.splitlines() if x.strip()]
    name = next((x for x in lines[:4] if len(x.split()) in (2, 3) and not re.search(r"@|resume|curriculum|engineer|developer|professional|experience|summary|linkedin|address|bachelor|master|skills", x, re.I)), None)
    headings={"education":r"education|academic background", "projects":r"projects?", "certifications":r"certifications?|licenses", "experience":r"experience|employment|work history|professional history"}
    sections={key:[] for key in headings}; active=None
    for line in lines:
        key=next((k for k,p in headings.items() if re.fullmatch(rf"(?:{p})(?::\s*)?",line,re.I)),None)
        if key: active=key; continue
        if re.fullmatch(r"(?:summary|skills|technical skills|contact|profile|objective)(?::\s*)?",line,re.I): active=None
        if active: sections[active].append(line)
    current=datetime.now().year
    dated=[]; relevant=[]
    range_re=re.compile(r"\b(19\d{2}|20\d{2})\s*(?:[-–—]|\bto\b)\s*(present|current|now|19\d{2}|20\d{2})\b",re.I)
    relevant_terms=re.compile(r"(?i)engineer|developer|software|backend|frontend|data|machine learning|built|developed|implemented|designed")
    date_lines=sections["experience"] or [line for line in lines if relevant_terms.search(line) and not re.search(r"(?i)bachelor|master|phd|degree|university|college|graduated",line)]
    for line in date_lines:
        for m in range_re.finditer(line):
            start=int(m.group(1)); end=current if m.group(2).lower() in {"present","current","now"} else int(m.group(2))
            if start<=end:
                dated.append((start,end));
                if relevant_terms.search(line) or extract_skills(line): relevant.append((start,end))
    def union_years(intervals):
        merged=[]
        for a,b in sorted(intervals):
            if not merged or a>merged[-1][1]: merged.append([a,b])
            else: merged[-1][1]=max(merged[-1][1],b)
        return round(sum(b-a for a,b in merged),1) if merged else None
    total_exp=union_years(dated)
    if total_exp is None:
        years=[int(x) for x in re.findall(r"\b(19\d{2}|20\d{2})\b","\n".join(date_lines))]
        total_exp=max(0,min(50,current-min(years))) if years else None
    relevant_exp=union_years(relevant)
    try:
        if globals().get("_NER_DISABLED"): raise RuntimeError("spaCy model unavailable")
        import spacy
        global _NLP
        try: _NLP
        except NameError: _NLP=spacy.load(os.getenv("SPACY_MODEL","en_core_web_sm"))
        entities=[ent.text for ent in _NLP(text[:100000]).ents if ent.label_=="PERSON"]
        if not name and entities: name=entities[0]
    except Exception:
        globals()["_NER_DISABLED"]=True
    return {"candidate_id": str(uuid4()), "resume_id": resume_id, "name": name, "email": email, "phone": phone, "skills": extract_skills(text), "total_experience_years": total_exp, "relevant_experience_years": relevant_exp, "education": sections["education"][:12], "projects": sections["projects"][:20], "certifications": sections["certifications"][:12], "experience_evidence": sections["experience"][:20], "raw_text": text, "filename": filename, "warnings": warnings + (["Experience dates need recruiter review."] if total_exp is not None else ["Experience dates were not reliably detected."])}


def semantic_similarity(job_text: str, candidate_text: str) -> float | None:
    if not job_text.strip() or not candidate_text.strip():
        return None
    # The documented transform maps cosine [-1,1] to [0,100]; inference stays deterministic.
    scaled=False
    try:
        if globals().get("_MODEL_DISABLED") or os.getenv("DISABLE_SEMANTIC_MODEL", "false").lower() == "true": raise RuntimeError("model unavailable")
        from sentence_transformers import SentenceTransformer
        global _MODEL
        try: _MODEL
        except NameError: _MODEL = SentenceTransformer(os.getenv("SENTENCE_MODEL", "sentence-transformers/all-MiniLM-L6-v2"))
        vectors = _MODEL.encode([job_text, candidate_text], normalize_embeddings=True)
        cosine = float(vectors[0] @ vectors[1])
        globals()["_SEMANTIC_METHOD"]="sentence-transformers/all-MiniLM-L6-v2"
    except Exception:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
        globals()["_MODEL_DISABLED"]=True
        try:
            matrix = TfidfVectorizer(ngram_range=(1, 2), stop_words="english").fit_transform([job_text, candidate_text])
            cosine = float(cosine_similarity(matrix[0:1], matrix[1:2])[0, 0])
        except ValueError: cosine=0.0
        scaled=True
        globals()["_SEMANTIC_METHOD"]="TF-IDF cosine fallback"
    return max(0.0, min(100.0, cosine * 100 if scaled else (cosine + 1) * 50))


DEFAULT_WEIGHTS = {"skills": .40, "semantic": .25, "experience": .20, "education": .10, "projects": .05}

def calculate_match(job: dict, profile: dict, scoring_weights: dict | None = None) -> dict:
    req = job.get("requirements", {})
    mandatory = {normalize_skill(x) for x in req.get("mandatory_skills", [])}
    preferred = {normalize_skill(x) for x in req.get("preferred_skills", [])} - mandatory
    candidate = {normalize_skill(x) for x in profile.get("skills", [])}
    matched = sorted(mandatory & candidate)
    missing = sorted(mandatory - candidate)
    preferred_hit = sorted(preferred & candidate)
    related_pairs = {"fastapi": "django", "django": "fastapi", "react": "javascript", "postgresql": "sql", "scikit-learn": "machine learning"}
    related = sorted({f"{a} ↔ {b}" for a in mandatory for b in candidate if related_pairs.get(a) == b})
    skill_denom = len(mandatory) + .5 * len(preferred)
    skill = (len(matched) + .5 * len(preferred_hit)) / skill_denom * 100 if skill_denom else None
    semantic = semantic_similarity(req.get("summary", job.get("description", "")), " ".join(profile.get("experience_evidence", []) + profile.get("projects", [])))
    required_exp = req.get("minimum_experience_years")
    actual_exp = profile.get("relevant_experience_years")
    if "relevant_experience_years" not in profile:
        actual_exp = profile.get("total_experience_years")
    experience = 100 if required_exp is None else (min(1, actual_exp / required_exp) * 100 if actual_exp is not None else None)
    education_req = req.get("education", [])
    education_text = " ".join(profile.get("education", [])).lower()
    education = (100 if any(str(x).lower() in education_text for x in education_req) else 0) if education_req and education_text else (None if education_req else 100)
    project_text = " ".join(profile.get("projects", [])).lower()
    project_overlap = len({s for s in mandatory | preferred if s in project_text})
    project = min(100, project_overlap / max(1, min(3, len(mandatory | preferred))) * 100) if project_text else None
    components = {"skills": skill, "semantic": semantic, "experience": experience, "education": education, "projects": project}
    weights = scoring_weights or DEFAULT_WEIGHTS
    available = {k: v for k, v in components.items() if v is not None and weights[k] > 0}
    score = sum(available[k] * weights[k] for k in available) / sum(weights[k] for k in available) if available else 0
    return {"overall_score": round(score, 2), "components": {k: round(v, 2) if v is not None else None for k, v in components.items()}, "coverage": round(sum(weights[k] for k in available) * 100, 1), "matched_skills": matched, "related_skills": related, "missing_mandatory_skills": missing, "missing_preferred_skills": sorted(preferred - candidate), "evidence": {"candidate_skills": sorted(candidate), "experience_years_used": actual_exp, "semantic_method": globals().get("_SEMANTIC_METHOD","not computed"), "missing_components": [k for k,v in components.items() if v is None]}, "warning": "Comparison support only; this score does not predict hiring success."}


async def publish(event_type: str, payload: dict, correlation_id: str | None = None):
    await publish_event(make_event(event_type, payload, correlation_id))


async def publish_event(event: dict):
    connection = await aio_pika.connect_robust(RABBITMQ_URL, timeout=8)
    async with connection:
        channel = await connection.channel(publisher_confirms=True)
        exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
        message = aio_pika.Message(json.dumps(event).encode(), delivery_mode=aio_pika.DeliveryMode.PERSISTENT, content_type="application/json", message_id=event["event_id"], correlation_id=event.get("correlation_id"))
        await exchange.publish(message, routing_key=event["event_type"])


def mongo_collection(name: str):
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)[MONGO_DB][name]


async def consume(queue_name: str, routing_keys: list[str], callback):
    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=8)
    exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
    dlx = await channel.declare_exchange("talentlens.dlx", aio_pika.ExchangeType.FANOUT, durable=True)
    queue = await channel.declare_queue(queue_name, durable=True, arguments={"x-dead-letter-exchange": "talentlens.dlx"})
    dead_queue = await channel.declare_queue(queue_name + ".dead", durable=True, arguments={"x-message-ttl": 604800000})
    await dead_queue.bind(dlx)
    for key in routing_keys:
        await queue.bind(exchange, routing_key=key)
    async with queue.iterator() as iterator:
        async for message in iterator:
            async with message.process(requeue=False):
                event=json.loads(message.body)
                for attempt in range(3):
                    try:
                        await callback(event)
                        break
                    except Exception:
                        if attempt==2: raise
                        await asyncio.sleep(0.5*(2**attempt))

