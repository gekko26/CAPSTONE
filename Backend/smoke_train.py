"""Live-MariaDB training smoke test with a MOCKED ESP32.

Proves, against the real database, that a training session writes to training_data
ONLY and that nothing leaks into sensor_readings / deployment_logs.

The ESP32 is mocked: no hardware is touched, no window is ever sampled. Rows created
here are tagged and deleted at the end by exact id.
"""
import os, sys, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.sensor as sensor_mod
import routes.training as training_mod
from database.db import SessionLocal
from database.models import Reading, TrainingData, Subject, DeploymentLog

# ── mock ESP32 ──────────────────────────────────────────────────────────────
class Resp:
    def __init__(self, payload, status): self._p, self.status_code = payload, status
    def json(self): return self._p

MODE = {"m": "training"}
TRIGGER_CALLS = []

sensor_mod.requests = type("R", (), {
    "get": staticmethod(lambda *a, **k: Resp({"mode": MODE["m"], "sequence_state": "idle",
                                               "fan_on": False, "blower_on": False,
                                               "buffering": False}, 200)),
    "post": staticmethod(lambda url, **k: (TRIGGER_CALLS.append(url),
                                          Resp({"status": "triggered"}, 200))[1]),
    "exceptions": type("E", (), {"ConnectionError": OSError, "Timeout": Exception}),
})

app = FastAPI(); app.include_router(sensor_mod.router); app.include_router(training_mod.router)
db = SessionLocal()
app.dependency_overrides[sensor_mod.get_db] = lambda: db
app.dependency_overrides[training_mod.get_db] = lambda: db
client = TestClient(app)

def counts():
    db.expire_all()
    return {t.__tablename__: db.query(t).count()
            for t in (TrainingData, Reading, DeploymentLog)}

fails = []
def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"   [{detail}]" if detail else ""))
    if not cond: fails.append(label)

print("=" * 78); print("TRAINING SMOKE — live MariaDB, mocked ESP32"); print("=" * 78)
print(f"\nstart counts: {counts()}\n")

# ── setup: a subject and an open training row ───────────────────────────────
from sqlalchemy import text
db.execute(text("INSERT INTO subjects (name, created_at) VALUES (:n, NOW())"),
           {"n": f"smoke-{int(time.time())}"})
sub_id = db.execute(text("SELECT LAST_INSERT_ID()")).scalar()
db.execute(text("""INSERT INTO training_data
   (subject_id, sub_label, bac, date)
   VALUES (:s, 'smoke', 0.08, NOW())"""), {"s": sub_id})
row_id = db.execute(text("SELECT LAST_INSERT_ID()")).scalar()
# the trial the window will expand into is this row's own id
db.execute(text("UPDATE training_data SET trial_id = :t WHERE id = :i"),
           {"t": row_id, "i": row_id})
db.commit()
print(f"  created subject_id={sub_id}  row_id={row_id}\n")

# ── 1. a gate trigger during training must be refused, writing nothing ───────
print("1. gate trigger while the device is in training mode")
before = counts()
n0 = len(TRIGGER_CALLS)
r = client.post("/sensor/trigger"); d = r.json()
check("HTTP 200", r.status_code == 200, str(r.status_code))
check("triggered=false", d.get("triggered") is False)
check("blocked=true", d.get("blocked") is True)
check("reason=device_in_training_mode", d.get("reason") == "device_in_training_mode")
check("no reading_id returned", d.get("reading_id") is None)
check("ESP32 never asked to trigger", len(TRIGGER_CALLS) == n0)
check("training_data untouched", counts()["training_data"] == before["training_data"])
check("sensor_readings untouched", counts()["sensor_readings"] == before["sensor_readings"])
check("deployment_logs untouched", counts()["deployment_logs"] == before["deployment_logs"])

# ── 2. the training window itself is accepted and carries subject_id ─────────
print("\n2. training window with subject_id")
# note the deliberately WRONG subject_id in the payload: row_id binding must win
window = {"temperature": 24.5, "humidity": 58.0, "bac": 0.0,
          "mq3_1": [10.0]*5, "mq3_2": [20.0]*5, "mq3_3": [30.0]*5,
          "row_id": row_id, "subject_id": 999999, "label": "smoke",
          "buffer_used": True, "mode": "training"}
r = client.post("/training/collect/sensor-data", json=window)
check("HTTP 200", r.status_code == 200, f"{r.status_code} {r.json()}")
after = counts()
# a window attaches to its parent in place the first time (only later windows expand
# into extra rows sharing trial_id), so the row count must NOT change
check("training_data count unchanged (written in place)",
      after["training_data"] == before["training_data"],
      f"{before['training_data']} -> {after['training_data']}")
check("sensor_readings unchanged", after["sensor_readings"] == before["sensor_readings"])
check("deployment_logs unchanged", after["deployment_logs"] == before["deployment_logs"])
rows = db.query(TrainingData).filter(TrainingData.trial_id == row_id).all()
check("window wrote a row", len(rows) >= 1, f"{len(rows)} rows")
got = rows[0]
check("subject_id stored", got.subject_id == sub_id, f"got {got.subject_id}")
check("mq3_1_max computed", got.mq3_1_max is not None, str(got.mq3_1_max))
check("temperature stored", got.temperature == 24.5, str(got.temperature))
check("humidity stored", got.humidity == 58.0, str(got.humidity))
# BAC belongs to the person, not the window: the endpoint inherits it from the parent
# and must ignore whatever the sensor payload claims
check("bac inherited from parent, not taken from the window",
      got.bac == 0.08, f"got {got.bac} (payload sent bac=0.0)")
check("subject_id on every row of the trial",
      all(r.subject_id == sub_id for r in rows))

# ── 3. a deployment window is refused by the training table ─────────────────
print("\n3. deployment window must be refused by training_data")
r = client.post("/training/collect/sensor-data",
                json={**window, "mode": "deployment", "row_id": row_id})
check("refused", r.status_code == 409, str(r.status_code))
check("training_data unchanged", counts()["training_data"] == after["training_data"])

# ── 4. the same window routed to the deployment endpoint stays out of training ─
print("\n4. training window refused by sensor_readings")
r = client.post("/sensor", json={**window, "reading_id": None})
check("refused", r.status_code == 409, str(r.status_code))
check("training_data unchanged", counts()["training_data"] == after["training_data"])

print("\n" + "=" * 78)
print(f"RESULT: {'ALL CHECKS PASSED' if not fails else str(len(fails)) + ' FAILED: ' + ', '.join(fails)}")

# ── cleanup by exact id ─────────────────────────────────────────────────────
db.execute(text("DELETE FROM training_data WHERE trial_id = :i OR id = :i"), {"i": row_id})
db.execute(text("DELETE FROM subjects WHERE id = :i"), {"i": sub_id})
db.commit()
print(f"cleaned up row_id={row_id} subject_id={sub_id}")
print(f"final counts: {counts()}")
sys.exit(1 if fails else 0)
