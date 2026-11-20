# API reference

Browser-facing routes are served by the API Gateway at `http://localhost:8080`. Obtain a bearer token first:

```http
POST /api/auth/login
Content-Type: application/json

{"username":"recruiter","password":"talentlens-demo"}
```

Use the returned `access_token` as `Authorization: Bearer <token>` on all `/api/*` routes below. Gateway health and Prometheus metrics do not require authentication.

## Jobs

- `POST /api/jobs` — JSON `{ "title": "Backend Engineer", "description": "...", "requirements": { ... } }`. `requirements` is optional; the service extracts a first draft when omitted.
- `POST /api/jobs/upload` — multipart `file` (TXT/PDF/DOCX) and optional `title`.
- `GET /api/jobs` and `GET /api/jobs/{job_id}` — list/retrieve saved job descriptions and structured requirements.
- `PUT /api/jobs/{job_id}` — replace title, description and optionally recruiter-edited requirements.
- `DELETE /api/jobs/{job_id}` — remove the job and eventually its score projections.

## Resumes and candidates

- `POST /api/resumes/upload` — multipart field `files`, 10–100 PDF/DOCX files. Per-file limit is `MAX_UPLOAD_MB`; batch limit is 100 MB. Response includes each resume UUID, queued status and duplicate flag.
- `GET /api/resumes` and `GET /api/resumes/{resume_id}` — processing status and errors.
- `GET /api/resumes/{resume_id}/file` — download the private source document for recruiter review.
- `DELETE /api/resumes/{resume_id}` — delete the source and asynchronously remove associated profile/ranking data.
- `GET /api/parsing/{resume_id}` — extracted profile document.
- `GET /api/candidates` and `GET /api/candidates/{candidate_id}` — candidate identity records.

## Matching and rankings

- `GET /api/matching/jobs/{job_id}` — authoritative PostgreSQL results, ordered by score then candidate UUID.
- `GET /api/matching/settings` — configured component weights.
- `PUT /api/matching/settings` — `{ "weights": { "skills": 0.4, "semantic": 0.25, "experience": 0.2, "education": 0.1, "projects": 0.05 } }`; all five weights must be decimals in 0–1 and total exactly 1. Existing profiles are reevaluated in a background task.
- `GET /api/rankings?job_id={id}&search={text}&min_score={0-100}&sort={asc|desc}` — MongoDB read projection with stable UUID tie ordering.
- `GET /api/rankings/candidates/{candidate_id}?job_id={id}` — candidate profile, evidence, score breakdown and warnings.
- `GET /api/rankings/compare?job_id={id}&candidate_ids={uuid}&candidate_ids={uuid}` — compare two to four profiles.

## Health and observability

- `GET /health` — Gateway liveness.
- `GET /metrics` — Prometheus metrics at the gateway and each internal service.
- Internal service liveness endpoints are available at each service's `/health` path from within the Compose backend network.

Interactive gateway OpenAPI is at `http://localhost:8080/docs`; service OpenAPI docs are intentionally internal and should not be exposed publicly.
