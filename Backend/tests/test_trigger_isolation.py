"""
Training / deployment trigger isolation.

The gate (deployment) and the training collection page both talk to the ESP32.
Nothing must stop a deployment trigger from being accepted by the device while it is
collecting training data and then being written into a training session.

Contract enforced here:

  * /sensor/trigger consults the device mode BEFORE touching the database
  * a gate trigger while the device is in training mode is refused, writes nothing
  * a gate trigger is refused even if the device reports it late, via a 409
  * a device whose mode cannot be read writes nothing
  * a device that will not accept the trigger leaves no orphan pending Reading
  * the training ingest path is unaffected by any of this

Every ESP32 call is mocked and the database is the throwaway SQLite from the `db`
fixture in conftest.py, so the live MariaDB is never touched.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from database.models import Reading, TrainingData


class _Resp:
    def __init__(self, payload, status):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


@pytest.fixture()
def sensor(monkeypatch, db):
    """Sensor router on a throwaway SQLite, every ESP32 call mocked."""
    import routes.sensor as mod
    app = FastAPI()
    app.include_router(mod.router)
    app.dependency_overrides[mod.get_db] = lambda: db
    return mod, TestClient(app)


@pytest.fixture()
def sensor_and_training(monkeypatch, db):
    """Both routers, for the cross-path check."""
    import routes.sensor as mod
    import routes.training as training
    app = FastAPI()
    app.include_router(mod.router)
    app.include_router(training.router)
    app.dependency_overrides[mod.get_db] = lambda: db
    app.dependency_overrides[training.get_db] = lambda: db
    return mod, TestClient(app)


def _readings(db):
    return db.query(Reading).count()


def _training(db):
    return db.query(TrainingData).count()


def _mode(mod, monkeypatch, mode):
    monkeypatch.setattr(mod.requests, "get",
                        lambda *a, **k: _Resp({"mode": mode}, 200))


def _offline(mod, monkeypatch):
    def boom(*a, **k):
        raise mod.requests.exceptions.ConnectionError()
    monkeypatch.setattr(mod.requests, "get", boom)


# ── the central guarantee ────────────────────────────────────────────────────

def test_gate_trigger_refused_in_training_mode_writes_nothing(sensor, db, monkeypatch):
    mod, client = sensor
    _mode(mod, monkeypatch, "training")

    def never(*a, **k):
        raise AssertionError("device must not be asked to trigger in training mode")
    monkeypatch.setattr(mod.requests, "post", never)

    before = _readings(db)
    d = client.post("/sensor/trigger").json()

    assert d["triggered"] is False
    assert d["blocked"] is True
    assert d["busy"] is False
    assert d["reason"] == "device_in_training_mode"
    assert d["reading_id"] is None
    assert _readings(db) == before
    assert _training(db) == 0


def test_gate_trigger_refusal_never_pollutes_training_data(sensor, db, monkeypatch):
    mod, client = sensor
    _mode(mod, monkeypatch, "training")
    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: _Resp({"ok": True}, 200))
    client.post("/sensor/trigger")
    assert _training(db) == 0
    assert _readings(db) == 0


# ── the device may only report its mode late, via 409 ───────────────────────

def test_late_409_training_refusal_rolls_back_the_pending_row(sensor, db, monkeypatch):
    mod, client = sensor
    _mode(mod, monkeypatch, "deployment")
    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: _Resp(
        {"status": "blocked", "reason": "device_in_training_mode", "mode": "training",
         "message": "Device is in training mode - gate trigger refused"}, 409))

    before = _readings(db)
    d = client.post("/sensor/trigger").json()

    assert d["triggered"] is False
    assert d["reading_id"] is None
    assert _readings(db) == before


def test_busy_409_also_leaves_no_orphan_pending_row(sensor, db, monkeypatch):
    mod, client = sensor
    _mode(mod, monkeypatch, "deployment")
    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: _Resp(
        {"status": "busy", "busy": True, "busy_row_id": 7, "incoming_row_id": 9,
         "sequence_state": "sampling"}, 409))

    before = _readings(db)
    d = client.post("/sensor/trigger").json()

    assert d["triggered"] is False
    assert d["busy"] is True
    assert d["esp32_status"] == 409
    assert d["busy_row_id"] == 7
    assert d["sequence_state"] == "sampling"
    assert _readings(db) == before


# ── an unreadable mode must fail closed ──────────────────────────────────────

def test_unreachable_device_writes_nothing(sensor, db, monkeypatch):
    mod, client = sensor
    _offline(mod, monkeypatch)
    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: _Resp({"ok": True}, 200))

    before = _readings(db)
    d = client.post("/sensor/trigger").json()

    assert d["triggered"] is False
    assert d["blocked"] is True
    assert d["reason"] == "device_mode_unknown"
    assert d["reading_id"] is None
    assert _readings(db) == before


def test_connection_error_leaves_no_orphan_row(sensor, db, monkeypatch):
    mod, client = sensor
    _mode(mod, monkeypatch, "deployment")

    def boom(*a, **k):
        raise mod.requests.exceptions.ConnectionError()
    monkeypatch.setattr(mod.requests, "post", boom)

    before = _readings(db)
    d = client.post("/sensor/trigger").json()

    assert d["triggered"] is False
    assert "unreachable" in d["message"].lower()
    assert _readings(db) == before


def test_timeout_leaves_no_orphan_row(sensor, db, monkeypatch):
    mod, client = sensor
    _mode(mod, monkeypatch, "deployment")

    def boom(*a, **k):
        raise mod.requests.exceptions.Timeout()
    monkeypatch.setattr(mod.requests, "post", boom)

    before = _readings(db)
    d = client.post("/sensor/trigger").json()

    assert d["triggered"] is False
    assert _readings(db) == before


def test_non_200_refusal_leaves_no_orphan_row(sensor, db, monkeypatch):
    mod, client = sensor
    _mode(mod, monkeypatch, "deployment")
    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: _Resp({"bad": 1}, 500))

    before = _readings(db)
    d = client.post("/sensor/trigger").json()

    assert d["triggered"] is False
    assert _readings(db) == before


# ── the happy path still works, and creates exactly one row ──────────────────

def test_deployment_trigger_still_works_and_creates_one_row(sensor, db, monkeypatch):
    mod, client = sensor
    _mode(mod, monkeypatch, "deployment")
    seen = {}

    def fake_post(*a, **k):
        seen["json"] = k.get("json")
        return _Resp({"ok": True}, 200)
    monkeypatch.setattr(mod.requests, "post", fake_post)

    before = _readings(db)
    d = client.post("/sensor/trigger").json()

    assert d["triggered"] is True
    assert d["blocked"] is False
    assert d["mode"] == "deployment"
    assert d["reading_id"] is not None
    assert seen["json"] == {"reading_id": d["reading_id"]}
    assert _readings(db) == before + 1
    assert _training(db) == 0


# ── training collection is never blocked by any of this ─────────────────────

def test_training_ingest_is_unaffected_in_training_mode(sensor_and_training, db,
                                                        monkeypatch):
    """The gate refusal must not leak into the training ingest path."""
    mod, client = sensor_and_training
    _mode(mod, monkeypatch, "training")
    r = client.post("/training/collect/sensor-data", json={
        "temperature": 25.0, "humidity": 60.0,
        "mq3_1": [10.0] * 5, "mq3_2": [20.0] * 5, "mq3_3": [30.0] * 5,
        "row_id": 999999, "reading_id": None, "mode": "training",
    })
    # It reaches the training route and is judged on its own terms (there is no row
    # 999999 to attach the window to). It is NOT short-circuited by the gate logic,
    # which would have answered with reason=device_in_training_mode.
    assert r.status_code == 404
    assert "row" in r.json()["detail"].lower()
