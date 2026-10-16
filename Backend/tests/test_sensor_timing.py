"""Tests for the ESP32 relay pre-roll: firmware host test, timing constants,
GET /sensor/timing, trigger 409 handling and predict staleness.

Every ESP32 call is mocked. Nothing here contacts hardware, the network, or any
file under data/.
"""

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

import pytest

# imported at module scope so Reading is on Base.metadata BEFORE the shared
# `db` fixture runs create_all()
from database.models import Reading as _Reading  # noqa: F401

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
FIRMWARE_DIR = REPO / "alcodetect_esp32"
HOST_TEST_SRC = FIRMWARE_DIR / "tests" / "relay_sequence_test.cpp"


# ── host firmware test ───────────────────────────────────────────────────────

def _host_compiler():
    return shutil.which("g++") or shutil.which("clang++")


@pytest.mark.skipif(_host_compiler() is None, reason="no host C++ compiler (g++/clang++) available")
def test_relay_sequence_host_tests_pass(tmp_path):
    """Compile and run the pure relay state machine on the host with a fake clock."""
    exe = tmp_path / "relay_sequence_test"
    cmd = [_host_compiler(), "-std=c++11", "-Wall", "-o", str(exe), str(HOST_TEST_SRC)]
    comp = subprocess.run(cmd, capture_output=True, text=True)
    assert comp.returncode == 0, f"compile failed:\n{comp.stderr}"
    run = subprocess.run([str(exe)], capture_output=True, text=True)
    assert run.returncode == 0, f"host tests failed:\n{run.stdout}\n{run.stderr}"
    assert "0 failed" in run.stdout, run.stdout


@pytest.mark.skipif(_host_compiler() is None, reason="no host C++ compiler available")
def test_host_build_leaves_no_artifacts_in_the_repo():
    """The compile output must go to a temp dir, never into the repository."""
    before = set(p for p in REPO.rglob("relay_sequence_test*") if ".git" not in str(p))
    with tempfile.TemporaryDirectory() as td:
        exe = pathlib.Path(td) / "relay_sequence_test"
        comp = subprocess.run(
            [_host_compiler(), "-std=c++11", "-o", str(exe), str(HOST_TEST_SRC)],
            capture_output=True, text=True)
        assert comp.returncode == 0, comp.stderr
    after = set(p for p in REPO.rglob("relay_sequence_test*") if ".git" not in str(p))
    assert before == after, "a build artifact was left in the repo"


def test_firmware_constants_are_present_and_single_sourced():
    src = (FIRMWARE_DIR / "alcodetect_esp32.ino").read_text()
    assert "#define PREROLL_MS          3000" in src
    assert "#define POSTROLL_MS         2000" in src
    assert "#define MAX_RELAY_ON_MS     (PREROLL_MS + BUFFER_DURATION_MS + POSTROLL_MS + 2000)" in src
    assert "#define FAN_RELAY_PIN     27" in src
    assert "#define BLOWER_RELAY_PIN  25" in src
    assert "const bool RELAY_ACTIVE_LOW = true;" in src
    assert "#define BUFFER_DURATION_MS  5000" in src   # unchanged sampling window
    # GPIO 26 is reserved for a future solenoid and must stay untouched
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("*") or stripped.startswith("//"):
            continue
        assert "GPIO26" not in stripped and "GPIO 26" not in stripped


def test_firmware_has_no_delay_in_the_relay_sequence():
    """The sequence must be millis()-driven; delay() is only allowed elsewhere."""
    src = (FIRMWARE_DIR / "alcodetect_esp32.ino").read_text()
    assert "relays.tick(millis());" in src
    assert "delay(PREROLL_MS)" not in src
    assert "delay(POSTROLL_MS)" not in src


# ── timing constants ─────────────────────────────────────────────────────────

def test_derived_timing_values():
    from config.sensor_timing import (
        SENSOR_BUSY_UNTIL_MS, SENSOR_PREROLL_MS, SENSOR_READY_AFTER_MS,
        SENSOR_SETTLE_MARGIN_MS, SENSOR_WINDOW_MS,
    )
    assert (SENSOR_PREROLL_MS, SENSOR_WINDOW_MS) == (3000, 5000)
    assert SENSOR_SETTLE_MARGIN_MS == 1000
    assert SENSOR_READY_AFTER_MS == 3000 + 5000 + 1000      # 9000
    assert SENSOR_BUSY_UNTIL_MS == 3000 + 5000 + 2000        # 10000


def test_frontend_timing_matches_backend():
    js = (REPO / "Frontend" / "src" / "config" / "timing.js").read_text()
    from config.sensor_timing import as_dict
    d = as_dict()
    assert f"preroll_ms: {d['preroll_ms']}" in js
    assert f"buffer_ms: {d['buffer_ms']}" in js
    assert f"postroll_ms: {d['postroll_ms']}" in js
    assert f"settle_margin_ms: {d['settle_margin_ms']}" in js
    assert f"ready_after_ms: {d['ready_after_ms']}" in js
    assert f"busy_until_ms: {d['busy_until_ms']}" in js


# ── GET /sensor/timing ───────────────────────────────────────────────────────

@pytest.fixture()
def sensor_client(monkeypatch, db):
    import routes.sensor as sensor
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(sensor.router)
    app.dependency_overrides[sensor.get_db] = lambda: db
    return sensor, TestClient(app)


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")


def test_timing_returns_constants_with_matching_firmware(sensor_client, monkeypatch):
    sensor, client = sensor_client
    monkeypatch.setattr(sensor.requests, "get", lambda *a, **k: _Resp({
        "sequence_state": "idle", "preroll_ms": 3000,
        "buffer_ms": 5000, "postroll_ms": 2000, "fan_on": False, "blower_on": False}))
    r = client.get("/sensor/timing")
    assert r.status_code == 200
    d = r.json()
    assert d["preroll_ms"] == 3000
    assert d["buffer_ms"] == 5000
    assert d["postroll_ms"] == 2000
    assert d["ready_after_ms"] == 9000
    assert d["busy_until_ms"] == 10000
    assert d["matches"] is True
    assert d["diff"] is None


def test_timing_detects_a_mismatched_firmware(sensor_client, monkeypatch):
    sensor, client = sensor_client
    monkeypatch.setattr(sensor.requests, "get", lambda *a, **k: _Resp({
        "preroll_ms": 3000, "buffer_ms": 4000, "postroll_ms": 2000}))
    d = client.get("/sensor/timing").json()
    assert d["matches"] is False
    assert "buffer_ms" in d["diff"]
    assert d["diff"]["buffer_ms"] == {"firmware": 4000, "backend": 5000}


def test_timing_never_errors_when_the_esp32_is_unreachable(sensor_client, monkeypatch):
    sensor, client = sensor_client

    def boom(*a, **k):
        raise OSError("unreachable")

    monkeypatch.setattr(sensor.requests, "get", boom)
    r = client.get("/sensor/timing")
    assert r.status_code == 200
    d = r.json()
    assert d["firmware"] is None
    assert d["matches"] is None
    assert d["preroll_ms"] == 3000      # constants still served


# ── trigger forwarding ───────────────────────────────────────────────────────

def _in_deployment_mode(monkeypatch, sensor):
    """The trigger endpoint now asks the device its mode first and only forwards a
    gate trigger when the answer is 'deployment'. Tests about the forward itself
    must therefore place the device in deployment mode."""
    monkeypatch.setattr(sensor.requests, "get", lambda *a, **k: _Resp(
        {"mode": "deployment"}, status=200))


def test_trigger_409_is_busy_not_success(sensor_client, monkeypatch):
    """The known bug: a 409 used to be reported as triggered=True."""
    sensor, client = sensor_client
    _in_deployment_mode(monkeypatch, sensor)
    monkeypatch.setattr(sensor.requests, "post", lambda *a, **k: _Resp(
        {"status": "busy", "busy": True, "busy_row_id": 7, "incoming_row_id": 9,
         "sequence_state": "sampling"}, status=409))
    r = client.post("/sensor/trigger")
    assert r.status_code == 200
    d = r.json()
    assert d["triggered"] is False
    assert d["busy"] is True
    assert d["esp32_status"] == 409
    assert d["busy_row_id"] == 7
    assert d["incoming_row_id"] == 9
    assert d["sequence_state"] == "sampling"
    assert d["busy_until_ms"] == 10000


def test_trigger_200_is_still_success(sensor_client, monkeypatch):
    sensor, client = sensor_client
    _in_deployment_mode(monkeypatch, sensor)
    monkeypatch.setattr(sensor.requests, "post", lambda *a, **k: _Resp({"ok": True}, 200))
    d = client.post("/sensor/trigger").json()
    assert d["triggered"] is True
    assert d.get("busy") is not True


def test_trigger_connection_error_keeps_existing_offline_behaviour(sensor_client, monkeypatch):
    sensor, client = sensor_client
    _in_deployment_mode(monkeypatch, sensor)

    def boom(*a, **k):
        raise sensor.requests.exceptions.ConnectionError()

    monkeypatch.setattr(sensor.requests, "post", boom)
    d = client.post("/sensor/trigger").json()
    assert d["triggered"] is False
    assert "unreachable" in d["message"].lower()


# ── predict staleness ────────────────────────────────────────────────────────

def test_stale_seconds_grew_by_the_preroll():
    from routes.predict import STALE_SECONDS
    assert STALE_SECONDS == 13.0, STALE_SECONDS


@pytest.fixture()
def predict_client(monkeypatch, db):
    import routes.predict as predict
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(predict.router)
    app.dependency_overrides[predict.get_db] = lambda: db
    return predict, TestClient(app)


def _reading(db, age_seconds, date=None):
    from datetime import datetime, timedelta, timezone
    r = _Reading(label="PASS", temperature=20.0, humidity=50.0, bac=0.0)
    r.date = date if date is not None else datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def test_reading_inside_the_new_limit_is_not_rejected_as_stale(predict_client, db):
    predict, client = predict_client
    r = _reading(db, age_seconds=12)      # > old 10 s, inside the new 13 s
    body = _post_full(client, r.id)
    assert body.get("reason") != "stale_sensor_data", body


def test_reading_outside_the_new_limit_is_stale(predict_client, db):
    predict, client = predict_client
    r = _reading(db, age_seconds=15)      # beyond 13 s
    body = _post_full(client, r.id)
    assert body.get("reason") == "stale_sensor_data", body


def test_null_date_is_still_stale(predict_client, db):
    """Existing behaviour preserved: a reading with no timestamp can never be fresh.

    The column carries a Python-side default, so NULL has to be forced with raw SQL
    — assigning None would just be overwritten on insert.
    """
    predict, client = predict_client
    from sqlalchemy import text
    r = _Reading(label="PASS", temperature=20.0, humidity=50.0)
    db.add(r)
    db.commit()
    db.refresh(r)
    db.execute(text("UPDATE sensor_readings SET date = NULL WHERE id = :i"), {"i": r.id})
    db.commit()
    db.refresh(r)
    assert r.date is None
    body = _post_full(client, r.id)
    assert body.get("reason") == "stale_sensor_data", body


def _jpeg_bytes():
    import cv2
    import numpy as np
    ok, buf = cv2.imencode(".jpg", np.full((32, 32, 3), 80, dtype=np.uint8))
    assert ok
    return buf.tobytes()


def _post_full(client, reading_id):
    """/predict/full is multipart (file + reading_id); the staleness check runs early."""
    r = client.post("/predict/full",
                    data={"reading_id": str(reading_id)},
                    files={"file": ("frame.jpg", _jpeg_bytes(), "image/jpeg")})
    return r.json()
