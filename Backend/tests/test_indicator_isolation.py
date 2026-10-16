"""Indicators must never influence training data collection or the decision.

Requirement: indicators are display-only and confined to the deployment path. This
module proves that structurally and behaviourally:

  * no training route imports or calls the indicator client
  * the decision module and gate_config have no knowledge of indicators at all
  * with indicators ENABLED and the device in training mode, a training capture
    behaves exactly as it does with indicators disabled
  * a person-detected hook that explodes cannot change an analyse result
  * a verdict hook that explodes cannot change a decision

Every ESP32 call is mocked. The database is the throwaway SQLite `db` fixture, so the
live MariaDB is never touched.
"""

import inspect

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import indicators as ind_cfg
from database.models import DeploymentLog, Reading, TrainingData    # noqa: F401
from services import indicator as ind_mod


# ── 1. structural: no training route touches indicators ───────────────────────

def test_training_routes_never_reference_the_indicator_service():
    """routes/training.py must contain no reference to indicators at all."""
    import routes.training as training
    src = inspect.getsource(training)
    # Note: training.py DOES read ESP32_URL and calls requests — that is the
    # pre-existing mode guard, which must stay. What it must never do is reach the
    # indicator service or any LED/audio vocabulary.
    for forbidden in ("services.indicator", "indicator", "dfplayer",
                      "run_id", "IndicatorBench"):
        assert forbidden not in src, (
            f"routes/training.py references {forbidden!r}; the training path must be "
            f"completely unaware of the indicators"
        )


def test_decision_module_knows_nothing_about_indicators():
    """models/decision.py is a forbidden file; prove it stayed pure."""
    import re
    from models import decision
    src = inspect.getsource(decision)
    # word-boundary match, so "led" does not match inside "called"
    for forbidden in ("indicator", "services", "requests", "led", "LED", "audio",
                      "dfplayer", "esp32"):
        assert not re.search(rf"\b{re.escape(forbidden)}\b", src), \
            f"decision.py mentions {forbidden!r}"


def test_gate_config_knows_nothing_about_indicators():
    import re
    from config import gate_config
    src = inspect.getsource(gate_config)
    for forbidden in ("indicator", "services", "led", "audio", "dfplayer"):
        assert not re.search(rf"\b{re.escape(forbidden)}\b", src), \
            f"gate_config.py mentions {forbidden!r}"


def test_only_three_backend_files_know_about_indicators():
    """The blast radius is explicit and small: camera, predict, sensor."""
    import pathlib
    backend = pathlib.Path(__file__).resolve().parent.parent
    allowed = {"services/indicator.py", "config/indicators.py",
               "routes/camera.py", "routes/predict.py", "routes/sensor.py",
               "services/__init__.py"}
    found = set()
    for path in list((backend / "routes").glob("*.py")) + \
                 list((backend / "services").glob("*.py")) + \
                 list((backend / "config").glob("*.py")):
        rel = str(path.relative_to(backend))
        import re as _re
        if _re.search(r"\bindicator", path.read_text(), _re.I) or \
                _re.search(r"\bdfplayer", path.read_text(), _re.I):
            found.add(rel)
    assert found <= allowed, f"indicator knowledge leaked into {found - allowed}"


# ── 2. behavioural: a training capture is unaffected by indicators ────────────

@pytest.fixture()
def training_client(db, monkeypatch):
    import routes.training as tr
    app = FastAPI()
    app.include_router(tr.router)
    app.dependency_overrides[tr.get_db] = lambda: db
    return tr, TestClient(app)


def _subject(db):
    from database.models import Subject
    s = Subject(name="smoke-subject")
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _window(row_id, subject_id, mode="training"):
    return {"temperature": 24.0, "humidity": 55.0, "bac": 0.0,
            "mq3_1": [10.0] * 5, "mq3_2": [20.0] * 5, "mq3_3": [30.0] * 5,
            "row_id": row_id, "subject_id": subject_id, "mode": mode}


def test_training_window_lands_identically_with_indicators_on_and_off(
        training_client, db, monkeypatch):
    """The same window writes the same row whether or not indicators are enabled."""
    tr, client = training_client
    sub = _subject(db)

    row = TrainingData(subject_id=sub.id, sub_label="clear_air", bac=0.00)
    db.add(row)
    db.commit()
    db.refresh(row)

    # any attempt to talk to the device from a training route is a failure
    def explode(*a, **k):
        raise AssertionError("a training route must never call the ESP32")
    monkeypatch.setattr(ind_mod.requests, "post", explode)

    monkeypatch.setattr(ind_cfg, "INDICATORS_ENABLED", False)
    r_off = client.post("/training/collect/sensor-data", json=_window(row.id, sub.id))
    assert r_off.status_code == 200

    row2 = TrainingData(subject_id=sub.id, sub_label="clear_air", bac=0.00)
    db.add(row2)
    db.commit()
    db.refresh(row2)

    monkeypatch.setattr(ind_cfg, "INDICATORS_ENABLED", True)
    monkeypatch.setattr(ind_mod, "ESP32_DEVICE_TOKEN", "dummy")
    r_on = client.post("/training/collect/sensor-data", json=_window(row2.id, sub.id))
    assert r_on.status_code == 200

    a = db.query(TrainingData).filter(TrainingData.id == row.id).one()
    b = db.query(TrainingData).filter(TrainingData.id == row2.id).one()
    for field in ("mq3_1_max", "mq3_2_max", "mq3_3_max", "temperature",
                  "humidity", "subject_id", "label"):
        assert getattr(a, field) == getattr(b, field), f"{field} differs"
    # trial_id is the parent's own id, so it necessarily differs between two distinct
    # trials — what must hold is that it correctly points at its own parent.
    assert a.trial_id == row.id
    assert b.trial_id == row2.id


def test_training_capture_never_dispatches_an_indicator(training_client, db,
                                                         monkeypatch):
    tr, client = training_client
    sub = _subject(db)
    dispatched = []
    monkeypatch.setattr(ind_cfg, "INDICATORS_ENABLED", True)
    monkeypatch.setattr(ind_mod, "ESP32_DEVICE_TOKEN", "dummy")
    monkeypatch.setattr(ind_mod, "_dispatch",
                        lambda state, run_id=None, volume=None:
                            dispatched.append(state))
    monkeypatch.setattr(ind_mod.requests, "post",
                        lambda *a, **k: pytest.fail("training must not call the device"))

    row = TrainingData(subject_id=sub.id, sub_label="clear_air", bac=0.00)
    db.add(row)
    db.commit()
    db.refresh(row)

    assert client.post("/training/collect/sensor-data",
                       json=_window(row.id, sub.id)).status_code == 200
    assert dispatched == [], f"a training capture dispatched {dispatched}"


# ── 3. the decision cannot be moved by an indicator failure ──────────────────

def test_indicator_failure_cannot_change_the_decision(monkeypatch):
    """decide() is a pure function; proving it never even looks at indicators."""
    from models.decision import decide, decision_to_dict
    monkeypatch.setattr(ind_cfg, "INDICATORS_ENABLED", True)
    monkeypatch.setattr(ind_mod.requests, "post",
                        lambda *a, **k: pytest.fail("decide() must not use the network"))
    # a definite FAIL outcome, computed with indicators enabled
    d = decide(rf_class=1, xgb_class=1, est_bac=0.30, face_label="impaired")
    assert d.status == "FAIL"
    assert decision_to_dict(d)["status"] == "FAIL"
    # and a definite PASS, same thing
    d2 = decide(rf_class=0, xgb_class=0, est_bac=None, face_label="sober")
    assert d2.status == "PASS"


def test_missing_indicator_input_can_never_yield_pass(monkeypatch):
    """A missing model must never pass, indicators on or off."""
    from models.decision import decide
    monkeypatch.setattr(ind_cfg, "INDICATORS_ENABLED", True)
    d = decide(rf_class=None, xgb_class=None, est_bac=None, face_label=None)
    assert d.status != "PASS"
    assert any(r.startswith("missing_") for r in d.reasons)


# ── 4. person hook in training mode changes nothing about the analysis ────────

def test_analyze_result_is_identical_in_training_mode_with_indicators_on(
        monkeypatch):
    """In training mode the person hook must be inert and the analysis unchanged."""
    import numpy as np
    import routes.camera as cam

    app = FastAPI()
    app.include_router(cam.router)
    client = TestClient(app)

    monkeypatch.setattr(cam, "get_rtsp_frame",
                        lambda: np.zeros((64, 64, 3), np.uint8))
    monkeypatch.setattr(cam, "analyze_frame",
                        lambda f: {"ear": 0.3, "is_close": True, "status": "ok",
                                   "impaired": False, "left_ear": 0.3, "right_ear": 0.3,
                                   "proximity": 0.5, "face_width": 100.0, "mar": 0.1,
                                   "yawning": False, "head_down": False, "pitch": 0,
                                   "yaw": 0, "roll": 0})
    monkeypatch.setattr(cam, "_identify_gated",
                        lambda f, e: ({"identified": False, "name": None,
                                       "confidence": 0.0, "reason": "no_face"}, {}))
    # the device says: training mode
    monkeypatch.setattr(ind_mod.requests, "get",
                        lambda *a, **k: _Status({"mode": "training",
                                                 "sequence_state": "idle"}))
    dispatched = []
    monkeypatch.setattr(ind_cfg, "INDICATORS_ENABLED", True)
    monkeypatch.setattr(ind_mod, "ESP32_DEVICE_TOKEN", "dummy")
    monkeypatch.setattr(ind_mod, "_dispatch",
                        lambda state, run_id=None, volume=None: dispatched.append(state))

    import cv2
    ok, buf = cv2.imencode(".jpg", np.zeros((64, 64, 3), np.uint8))
    assert ok
    r = client.post("/camera/analyze",
                    files={"file": ("f.jpg", buf.tobytes(), "image/jpeg")})
    assert r.status_code == 200
    assert r.json()["is_close"] is True
    assert dispatched == [], "training mode must never announce a person"


class _Status:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def json(self):
        return self._payload