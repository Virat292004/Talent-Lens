# Service boundaries and data ownership

| Service | Owns | Store | Publishes / consumes |
|---|---|---|---|
| API Gateway | Authenticated edge behavior and request context | Redis rate-limit counters | Routes to all internal APIs |
| Job Service | Job description and recruiter-edited requirements | PostgreSQL `jobs` | Publishes `JobCreated`, `JobUpdated`, `JobDeleted` via outbox |
| Resume Service | Upload metadata, hash, status and deletion | PostgreSQL `resumes`; private local volume | Publishes `ResumeUploaded`, `ResumeDeleted` via outbox |
| Parsing Service | Extracted profile document | MongoDB `profiles` | Consumes resume events; publishes parsing lifecycle events |
| Candidate Service | Candidate UUID and resume association | PostgreSQL `candidates` | Consumes `ResumeParsed` and `ResumeDeleted` |
| Matching Service | Job read projection and authoritative match result | PostgreSQL `jobs_projection`, `match_results` | Consumes jobs/profiles; publishes `CandidateEvaluated` via outbox |
| Ranking Service | Query optimized candidate ranking projection | MongoDB `rankings` | Consumes evaluation and deletion events; publishes `RankingUpdated` |

UUIDs are stable cross-service references. A service should read and write only its owned records. The single local PostgreSQL instance and shared development credential keep this prototype easy to run; logical table ownership is separated in code but not enforced with per-service database roles. A production deployment should provision separate database roles/schemas and secrets for each service.

PostgreSQL is authoritative for jobs, uploads, candidate identifiers, and evaluation results. MongoDB profile and ranking views are eventually consistent projections. RabbitMQ is used for asynchronous work and projection updates, not routine read requests.

## Relational schema (core)

- `jobs(id, title, description, requirements JSONB, created_at, updated_at)` — Job Service.
- `resumes(id, sha256 UNIQUE, original_name, storage_key, media_type, size_bytes, status, error, uploaded_at)` — Resume Service.
- `candidates(id, resume_id UNIQUE, name, email, phone, created_at)` — Candidate Service.
- `jobs_projection(id, title, description, requirements JSONB)` — Matching Service's event-fed read projection.
- `match_results(job_id, candidate_id, resume_id, score, components JSONB, explanation JSONB, updated_at)` — Matching Service.
- `event_outbox(event_id, owner, event_type, payload JSONB, published_at, attempts, created_at)` — transactional event publication.

MongoDB collections:

- `profiles`: parsed fields, skills, experience, education, projects, certifications, and warnings keyed by `resume_id`.
- `rankings`: candidate/job reference, component results, explanations, and a profile projection keyed by `(job_id, candidate_id)`.

RabbitMQ data is retained in a Docker volume so durable queues survive container restarts.

## CQRS

Matching writes authoritative scores to PostgreSQL. It emits `CandidateEvaluated`; Ranking Service consumes it and replaces the corresponding MongoDB projection. Ranking and candidate analysis read from this projection. The brief lag between score write and ranking view update is expected eventual consistency.
