# Configuration

Copy `.env.example` to `.env` before running Compose. Compose reads `.env` for interpolation and passes it to the backend containers. Use local demo credentials only for local development.

| Variable | Purpose | Local default |
|---|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | PostgreSQL container initialization | `talentlens` / `talentlens-local` |
| `POSTGRES_DSN` | Service database connection | points to Compose `postgres` |
| `MONGO_URL`, `MONGO_DB` | MongoDB profile/projection store | Compose MongoDB |
| `RABBITMQ_URL` | Broker connection for event producers/consumers | Compose RabbitMQ |
| `REDIS_URL` | Gateway rate-limit counters | Compose Redis |
| `JWT_SECRET` | HS256 signing key | replace before any shared deployment |
| `DEMO_USERNAME`, `DEMO_PASSWORD` | Local recruiter login | `recruiter` / `talentlens-demo` |
| `ALLOWED_ORIGINS` | Gateway CORS origins, comma-separated | `http://localhost:5173` |
| `MAX_UPLOAD_MB` | Per-resume upload cap | `15` |
| `MAX_REQUEST_MB` | Gateway request body cap, including batches | `110` |
| `DATA_RETENTION_DAYS` | Delete older source resumes and projections; `0` disables | `0` |
| `RATE_LIMIT_PER_MINUTE` | Authenticated gateway requests per recruiter/IP | `180` |
| `SENTENCE_MODEL` | Sentence Transformers model ID | `sentence-transformers/all-MiniLM-L6-v2` |
| `SPACY_MODEL` | Optional installed spaCy model for extra named-entity extraction | `en_core_web_sm` |
| `SKILL_TAXONOMY` | Optional comma-separated skill vocabulary override | built-in shared list |
| `VITE_API_BASE` | Gateway URL baked into the frontend image at build time | `http://localhost:8080` |
| `ENABLE_TRACING` | Export gateway and HTTPX spans to Zipkin | `false` |

To run monitoring, start Compose with `--profile monitoring`. To collect Zipkin traces as well, set `ENABLE_TRACING=true` in `.env` and recreate the gateway container.
