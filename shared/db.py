import os, time
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

DSN = os.getenv("POSTGRES_DSN", "postgresql://talentlens:talentlens-local@localhost:5432/talentlens")

def connect():
    return psycopg.connect(DSN, connect_timeout=5, row_factory=dict_row)

def ensure_outbox(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS event_outbox (event_id text PRIMARY KEY, owner text NOT NULL DEFAULT 'unknown', event_type text NOT NULL, payload jsonb NOT NULL, correlation_id text, published_at timestamptz, attempts int NOT NULL DEFAULT 0, created_at timestamptz NOT NULL DEFAULT now())")

def enqueue(conn, event_type, payload, correlation_id=None):
    from shared.event_contracts import make_event
    event = make_event(event_type, payload, correlation_id)
    conn.execute("INSERT INTO event_outbox(event_id,owner,event_type,payload,correlation_id) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", (event["event_id"],os.getenv("SERVICE_NAME","unknown"),event_type,Jsonb(event),correlation_id))
    return event

async def outbox_loop(stop):
    from shared.platform import publish_event
    while not stop.is_set():
        try:
            with connect() as conn:
                ensure_outbox(conn)
                rows = conn.execute("SELECT event_id,event_type,payload,correlation_id FROM event_outbox WHERE owner=%s AND published_at IS NULL ORDER BY created_at LIMIT 50",(os.getenv("SERVICE_NAME","unknown"),)).fetchall()
                for row in rows:
                    event_id,event_type,payload,cid=row["event_id"],row["event_type"],row["payload"],row["correlation_id"]
                    try:
                        await publish_event(payload)
                        conn.execute("UPDATE event_outbox SET published_at=now() WHERE event_id=%s", (event_id,))
                    except Exception:
                        conn.execute("UPDATE event_outbox SET attempts=attempts+1 WHERE event_id=%s", (event_id,))
                        break
        except Exception:
            pass
        try: await __import__('asyncio').wait_for(stop.wait(), timeout=2)
        except __import__('asyncio').TimeoutError: pass

