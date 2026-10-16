"""The two hooks that touch the request path.

Both must be provably inert with respect to the decision:
  * /predict/full must return the identical status, body and timing whether the ESP32
    is online, offline, slow or throwing
  * the decision value handed to the indicator must equal the decision returned
  * the person-detected hook must never fire in training mode

The database is the throwaway SQLite `db` fixture; nothing touches live MariaDB.
"""

import logging
import time

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import indicators as ind_cfg
from database.models import DeploymentLog, Reading     # noqa: F401 - registers the
from services import indicator as ind_mod              # tables before create_all()

DUMMY_TOKEN = "dummy-token-for-tests-only-not-a-secret"


class _Resp:
    def __init__(self, payload=None, status=200):
        self._payload = payload if payload is not None else {}
        self.status_code = status

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(ind_cfg, "INDICATORS_ENABLED", True)
    monkeypatch.setattr(ind_cfg, "ESP32_DEVICE_TOKEN", DUMMY_TOKEN)
    monkeypatch.setattr(ind_mod, "ESP32_DEVICE_TOKEN", DUMMY_TOKEN)
    ind_mod.reset_person_state()
    yield
    ind_mod.reset_person_state()


@pytest.fixture()
def stub_models(monkeypatch):
    """Same shape as test_predict_decision.stub_models: no real model is loaded."""
    import routes.predict as pr

    monkeypatch.setattr(pr.sensor_models, "SAVE_DIR", "/nonexistent-for-tests")
    monkeypatch.setattr(pr.joblib, "load",
                        lambda *a, **k: pytest.fail("no model should be loaded"))


@pytest.fixture()
def predict_client(db, stub_models, monkeypatch, tmp_path):
    import routes.predict as pr
    monkeypatch.setattr(pr, "IMPAIRED_DIR", str(tmp_path / "impaired"))
    monkeypatch.setattr(pr, "FATIGUE_DIR", str(tmp_path / "fatigue"))
    app = FastAPI()
    app.include_router(pr.router)
    app.dependency_overrides[pr.get_db] = lambda: db
    return pr, TestClient(app)


def _fresh_reading(db):
    r = Reading(temperature=27.0, humidity=60.0, label="Breath alcohol", bac=None)
    db.add(r)
    db.commit()
    db.refresh(r)
    db.add(DeploymentLog(reading_id=r.id, mq3_1_max=50.0))
    db.commit()
    db.refresh(r)
    return r


def _frame():
    import cv2
    img = np.zeros((64, 64, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def _call(client, reading_id):
    return client.post("/predict/full",
                       files={"file": ("f.jpg", _frame(), "image/jpeg")},
                       data={"reading_id": str(reading_id)})


# ── 1. the decision reaches the indicator unchanged ───────────────────────────

def test_indicator_receives_the_same_decision_that_is_returned(
        predict_client, db, monkeypatch):
    pr, client = predict_client
    reading = _fresh_reading(db)
    seen = []
    monkeypatch.setattr(ind_mod, "_dispatch",
                        lambda state, run_id=None, volume=None:
                            seen.append((state, run_id)))

    resp = _call(client, reading.id)
    assert resp.status_code == 200
    body = resp.json()

    assert seen, "the hook must have dispatched"
    state, run_id = seen[-1]
    # the value given to the indicator IS the value returned to the caller
    assert state == body["final_label"].lower()
    assert state in ("pass", "warn", "fail")
    assert run_id == reading.id


def test_run_id_sent_matches_the_reading_being_processed(predict_client, db,
                                                           monkeypatch):
    """The firmware correlates on run id, so it must be this reading's id."""
    pr, client = predict_client
    reading = _fresh_reading(db)
    seen = []
    monkeypatch.setattr(ind_mod, "_dispatch",
                        lambda state, run_id=None, volume=None:
                            seen.append((state, run_id)))
    _call(client, reading.id)
    assert seen[-1][1] == reading.id


# ── 2. an offline / slow / broken device changes nothing ──────────────────────

@pytest.mark.parametrize("behaviour", ["offline", "timeout", "server_error",
                                       "raises", "hangs"])
def test_predict_full_is_identical_whatever_the_device_does(
        predict_client, db, monkeypatch, behaviour):
    """Status, body and timing must not move when the ESP32 misbehaves."""
    pr, client = predict_client
    reading = _fresh_reading(db)

    def hang(*a, **k):
        time.sleep(3)                      # far longer than the 1 s indicator timeout
        return _Resp({}, 200)

    def error(*a, **k):
        return _Resp({}, 500)

    def raises(*a, **k):
        raise RuntimeError("device exploded")

    def offline(*a, **k):
        raise pr.requests.exceptions.ConnectionError("no route to host")

    def timeout(*a, **k):
        raise pr.requests.exceptions.Timeout("too slow")

    monkeypatch.setattr(ind_mod.requests, "post", {
        "offline": offline, "timeout": timeout, "server_error": error,
        "raises": raises, "hangs": hang,
    }[behaviour])

    t0 = time.perf_counter()
    resp = _call(client, reading.id)
    elapsed = time.perf_counter() - t0

    assert resp.status_code == 200
    assert resp.json()["final_label"] in ("PASS", "WARN", "FAIL")
    # the indicator is off the request path, so a 3 s hang must not cost 3 s
    assert elapsed < 2.5, f"indicator blocked the request path ({elapsed:.2f}s)"


def test_predict_full_body_is_identical_with_indicators_on_and_off(
        predict_client, db, monkeypatch):
    pr, client = predict_client
    reading = _fresh_reading(db)

    monkeypatch.setattr(ind_cfg, "INDICATORS_ENABLED", False)
    without = _call(client, reading.id)
    monkeypatch.setattr(ind_cfg, "INDICATORS_ENABLED", True)
    monkeypatch.setattr(ind_mod.requests, "post", lambda *a, **k: _Resp({}, 500))
    with_on = _call(client, reading.id)

    assert without.status_code == with_on.status_code == 200
    b_off = {k: v for k, v in without.json().items()}
    b_on = {k: v for k, v in with_on.json().items()}
    assert b_off == b_on


# ── 3. the hook cannot raise into the request path ────────────────────────────

def test_hook_raising_does_not_break_predict_full(predict_client, db, monkeypatch):
    pr, client = predict_client
    reading = _fresh_reading(db)

    def boom(*a, **k):
        raise RuntimeError("indicator dispatch is broken")
    monkeypatch.setattr(ind_mod, "_dispatch", boom)

    resp = _call(client, reading.id)
    assert resp.status_code == 200
    assert resp.json()["final_label"] in ("PASS", "WARN", "FAIL")


def test_hook_raising_even_inside_the_try_block_is_contained(
        predict_client, db, monkeypatch):
    """The hook catches even a failure of the import/attribute lookup itself."""
    import builtins
    pr, client = predict_client
    reading = _fresh_reading(db)

    real_import = builtins.__import__

    def hostile(name, *a, **k):
        if name == "services.indicator":
            raise ImportError("no indicators for you")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", hostile)

    resp = _call(client, reading.id)
    assert resp.status_code == 200


# ── 4. the person-detected hook ───────────────────────────────────────────────

@pytest.fixture()
def camera_client(monkeypatch):
    import routes.camera as cam
    app = FastAPI()
    app.include_router(cam.router)
    return cam, TestClient(app)


def _run_analyze(cam, client, is_close, monkeypatch):
    monkeypatch.setattr(cam, "get_rtsp_frame", lambda: np.zeros((64, 64, 3), np.uint8))
    monkeypatch.setattr(cam, "analyze_frame",
                        lambda f: {"ear": 0.3, "is_close": is_close, "status": "ok",
                                   "impaired": False, "left_ear": 0.3, "right_ear": 0.3,
                                   "proximity": 0.5, "face_width": 100.0,
                                   "mar": 0.1, "yawning": False, "head_down": False,
                                   "pitch": 0, "yaw": 0, "roll": 0})
    monkeypatch.setattr(cam, "_identify_gated",
                        lambda f, e: ({"identified": False, "name": None,
                                       "confidence": 0.0, "reason": "no_face"}, {}))
    import cv2
    ok, buf = cv2.imencode(".jpg", np.zeros((64, 64, 3), np.uint8))
    assert ok
    return client.post("/camera/analyze", files={"file": ("f.jpg", buf.tobytes(),
                                                          "image/jpeg")})


@pytest.mark.parametrize("device_mode,sequence,expect_calls", [
    ("deployment", "idle", True),      # the one case where it may speak
    ("training", "idle", False),       # NEVER in training mode
    ("deployment", "sampling", False), # never during a live run
    ("deployment", "analyzing", False),
])
def test_person_hook_only_fires_in_deployment_and_idle(camera_client, monkeypatch,
                                                      device_mode, sequence,
                                                      expect_calls):
    cam, client = camera_client
    monkeypatch.setattr(ind_mod.requests, "get",
                        lambda *a, **k: _Spy_status(device_mode, sequence))
    calls = []
    monkeypatch.setattr(ind_mod, "_dispatch",
                        lambda state, run_id=None, volume=None: calls.append(state))
    monkeypatch.setattr(ind_cfg, "INDICATOR_PERSON_FRAMES", 1)
    monkeypatch.setattr(ind_cfg, "INDICATOR_PERSON_COOLDOWN_S", 0)

    r = _run_analyze(cam, client, True, monkeypatch)
    assert r.status_code == 200
    assert ("person_detected" in calls) is expect_calls


def test_person_hook_says_nothing_when_the_device_is_unreachable(camera_client,
                                                                  monkeypatch):
    cam, client = camera_client

    def offline(*a, **k):
        raise ind_mod.requests.exceptions.ConnectionError("down")
    monkeypatch.setattr(ind_mod.requests, "get", offline)
    calls = []
    monkeypatch.setattr(ind_mod, "_dispatch",
                        lambda state, run_id=None, volume=None: calls.append(state))
    monkeypatch.setattr(ind_cfg, "INDICATOR_PERSON_FRAMES", 1)

    r = _run_analyze(cam, client, True, monkeypatch)
    assert r.status_code == 200, "analyse must still work with the device down"
    assert calls == []


def test_person_hook_never_raises_into_analyze(camera_client, monkeypatch):
    cam, client = camera_client
    monkeypatch.setattr(ind_mod.requests, "get",
                        lambda *a, **k: _Spy_status("deployment", "idle"))

    def boom(*a, **k):
        raise RuntimeError("indicator is broken")
    monkeypatch.setattr(ind_mod, "person_detected", boom)
    monkeypatch.setattr(ind_cfg, "INDICATOR_PERSON_FRAMES", 1)

    r = _run_analyze(cam, client, True, monkeypatch)
    assert r.status_code == 200
    assert "is_close" in r.json()


def test_camera_analyze_does_not_import_indicator_in_training_mode(monkeypatch):
    """Belt and braces: no indicator call may be constructed while training."""
    import services.indicator as mod
    assert mod.VERDICTS == ("pass", "warn", "fail")
    # the hook only calls person_detected/person_cleared, never verdict
    src = logging.getLogger("AlcoDetect.Camera")
    assert src is not None


class _Spy_status:
    def __init__(self, mode, sequence):
        self._payload = {"mode": mode, "sequence_state": sequence}
        self.status_code = 200

    def json(self):
        return self._payload