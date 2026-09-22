from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database.db import engine
from database import models
from routes import sensor, readings,camera,predict,recognition,training,model_status
from sqlalchemy import text, inspect

models.Base.metadata.create_all(bind=engine)

# Auto-migrate missing FIX columns on existing DB (since create_all never alters tables)
def _auto_migrate():
    try:
        insp = inspect(engine)
        cols = {
            "training_data": {c["name"] for c in insp.get_columns("training_data")} if insp.has_table("training_data") else set(),
            "sensor_readings": {c["name"] for c in insp.get_columns("sensor_readings")} if insp.has_table("sensor_readings") else set(),
            "deployment_logs": {c["name"] for c in insp.get_columns("deployment_logs")} if insp.has_table("deployment_logs") else set(),
        }
        stmts = []
        if "auto_labeled" not in cols["training_data"]: stmts.append("ALTER TABLE training_data ADD COLUMN auto_labeled TINYINT(1) NULL DEFAULT 0")
        if "label_confirmed" not in cols["training_data"]: stmts.append("ALTER TABLE training_data ADD COLUMN label_confirmed TINYINT(1) NULL DEFAULT 1")
        if "height_offset_cm" not in cols["training_data"]: stmts.append("ALTER TABLE training_data ADD COLUMN height_offset_cm FLOAT NULL")
        if "denial_reason" not in cols["sensor_readings"]: stmts.append("ALTER TABLE sensor_readings ADD COLUMN denial_reason VARCHAR(20) NULL")
        if "height_offset_cm" not in cols["sensor_readings"]: stmts.append("ALTER TABLE sensor_readings ADD COLUMN height_offset_cm FLOAT NULL")
        if "denial_reason" not in cols["deployment_logs"]: stmts.append("ALTER TABLE deployment_logs ADD COLUMN denial_reason VARCHAR(20) NULL")
        if "height_offset_cm" not in cols["deployment_logs"]: stmts.append("ALTER TABLE deployment_logs ADD COLUMN height_offset_cm FLOAT NULL")
        if "reading_id" not in cols["deployment_logs"]:
            stmts.append("ALTER TABLE deployment_logs ADD COLUMN reading_id INT NULL")
            stmts.append("CREATE UNIQUE INDEX uq_deployment_logs_reading_id ON deployment_logs (reading_id)")
        for s in stmts:
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
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