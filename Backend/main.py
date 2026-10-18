import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database.db import engine
from database import models
from routes import sensor, readings,camera,predict,recognition,training,model_status
from sqlalchemy import text, inspect

models.Base.metadata.create_all(bind=engine)

# Auto-migrate missing FIX columns on existing DB (since create_all never alters tables)
def _column_type(insp, table, column):
    """Live declared type of a column, uppercased ('' when absent)."""
    try:
        for c in insp.get_columns(table):
            if c["name"] == column:
                return str(c.get("type", "")).upper()
    except Exception:
        pass
    return ""

def _has_foreign_key(insp, table, constrained_column):
    """True when `constrained_column` already participates in a FOREIGN KEY."""
    try:
        for fk in insp.get_foreign_keys(table) or []:
            if constrained_column in (fk.get("constrained_columns") or []):
                return True
    except Exception:
        pass
    return False

def _plan_migrations(insp, dialect_name):
    """PURE: decide which DDL statements the live schema still needs.

    Split out of _auto_migrate() so the decision can be unit-tested against a fake
    inspector. main.py runs DDL at import time and must never be imported by a test,
    so this is the only part that is safely testable -- and it is the part that
    actually decides whether the FK gets created.
    """
    cols = {
        "training_data": {c["name"] for c in insp.get_columns("training_data")} if insp.has_table("training_data") else set(),
        "sensor_readings": {c["name"] for c in insp.get_columns("sensor_readings")} if insp.has_table("sensor_readings") else set(),
        "deployment_logs": {c["name"] for c in insp.get_columns("deployment_logs")} if insp.has_table("deployment_logs") else set(),
    }
    stmts = []
    # ── schema widening (VARCHAR(20) -> VARCHAR(64)) ──────────────────────
    # denial_reason stores the joined human-readable reason labels. The longest
    # label is "Sensors flag alcohol, face looks sober" (38 chars), which a
    # VARCHAR(20) silently truncated mid-word, corrupting the audit trail.
    # Widen the existing column too, not just the create path.
    if "denial_reason" not in cols["sensor_readings"]:
        stmts.append("ALTER TABLE sensor_readings ADD COLUMN denial_reason VARCHAR(64) NULL")
    elif _column_type(insp, "sensor_readings", "denial_reason") == "VARCHAR(20)":
        stmts.append("ALTER TABLE sensor_readings MODIFY COLUMN denial_reason VARCHAR(64) NULL")
    if "denial_reason" not in cols["deployment_logs"]:
        stmts.append("ALTER TABLE deployment_logs ADD COLUMN denial_reason VARCHAR(64) NULL")
    elif _column_type(insp, "deployment_logs", "denial_reason") == "VARCHAR(20)":
        stmts.append("ALTER TABLE deployment_logs MODIFY COLUMN denial_reason VARCHAR(64) NULL")
    if "auto_labeled" not in cols["training_data"]: stmts.append("ALTER TABLE training_data ADD COLUMN auto_labeled TINYINT(1) NULL DEFAULT 0")
    if "label_confirmed" not in cols["training_data"]: stmts.append("ALTER TABLE training_data ADD COLUMN label_confirmed TINYINT(1) NULL DEFAULT 1")
    if "height_offset_cm" not in cols["training_data"]: stmts.append("ALTER TABLE training_data ADD COLUMN height_offset_cm FLOAT NULL")
    if "height_offset_cm" not in cols["sensor_readings"]: stmts.append("ALTER TABLE sensor_readings ADD COLUMN height_offset_cm FLOAT NULL")
    if "height_offset_cm" not in cols["deployment_logs"]: stmts.append("ALTER TABLE deployment_logs ADD COLUMN height_offset_cm FLOAT NULL")
    if "reading_id" not in cols["deployment_logs"]:
        stmts.append("ALTER TABLE deployment_logs ADD COLUMN reading_id INT NULL")
        stmts.append("CREATE UNIQUE INDEX uq_deployment_logs_reading_id ON deployment_logs (reading_id)")
    # ── referential integrity ──────────────────────────────────────────────
    # models.py declares deployment_logs.reading_id -> sensor_readings.id, but the
    # live MariaDB schema has the COLUMN and the UNIQUE INDEX while the CONSTRAINT
    # is absent, because create_all() never alters an existing table. Without the
    # FK a log can reference a reading that no longer exists. SQLite cannot
    # ADD CONSTRAINT, so this is skipped there (the test fixture is SQLite).
    if (insp.has_table("deployment_logs") and insp.has_table("sensor_readings")
            and "reading_id" in cols["deployment_logs"]
            and dialect_name != "sqlite"
            and not _has_foreign_key(insp, "deployment_logs", "reading_id")):
        stmts.append("ALTER TABLE deployment_logs ADD CONSTRAINT fk_deployment_logs_reading "
                     "FOREIGN KEY (reading_id) REFERENCES sensor_readings(id)")
    return stmts


def _auto_migrate():
    try:
        for s in _plan_migrations(inspect(engine), engine.dialect.name):
            try:
                with engine.begin() as c: c.execute(text(s))
                print(f"✅ auto-migrate: {s}")
            except Exception as e:
                # unique index may fail if dup data; log and continue
                print(f"⚠️ auto-migrate skip: {s} -> {e}")
    except Exception as e:
        print(f"⚠️ auto-migrate check failed: {e}")

_auto_migrate()

app = FastAPI(title="AlcoDetect API")

# CORS is an allowlist, not a wildcard. With allow_origins=["*"] any website the
# operator visits could drive this API from their browser: read the training dataset,
# read enrolled faces by name, delete rows, and fire the relays. Set CORS_ORIGINS to
# comma-separated origins; the default covers the local Vite dev server.
CORS_ORIGINS = [o.strip() for o in os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,"
    "http://localhost:4173,http://127.0.0.1:4173"
).split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Device-Token", "Authorization"],
)

@app.get("/")
def root():
    return {"status": "AlcoDetect API running"}

app.include_router(sensor.router)
app.include_router(readings.router)
app.include_router(camera.router)
app.include_router(recognition.router)
app.include_router(predict.router)
app.include_router(training.router)
app.include_router(model_status.router)
