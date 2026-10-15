"""Tests for the ESP32 device-mode API and the table guards.

Every ESP32 call is mocked. Nothing contacts hardware or the network.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _Resp:
    def __init__(self, payload=None, status=200):
        self._payload = payload if payload is not None else {}
        self.status_code = status

    def json(self):
        return self._payload


@pytest.fixture()
def sensor(monkeypatch, db):
    import routes.sensor as sensor
    app = FastAPI()
    app.include_router(sensor.router)
    app.dependency_overrides[sensor.get_db] = lambda: db
    return sensor, TestClient(app)


# ── GET /sensor/mode ─────────────────────────────────────────────────────────

def test_get_mode_reports_the_device_mode(sensor, monkeypatch):
    mod, client = sensor
    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: _Resp(
        {"mode": "deployment", "sequence_state": "idle", "fan_on": False,
         "blower_on": False}))
    d = client.get("/sensor/mode").json()
    assert d["mode"] == "deployment"
    assert d["online"] is True
    assert d["writable"] is True


def test_get_mode_is_not_writable_while_a_sequence_runs(sensor, monkeypatch):
    mod, client = sensor
    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: _Resp(
        {"mode": "training", "sequence_state": "preroll"}))
    d = client.get("/sensor/mode").json()
    assert d["mode"] == "training"
    assert d["writable"] is False
    assert d["sequence_state"] == "preroll"


def test_get_mode_never_errors_when_offline(sensor, monkeypatch):
    mod, client = sensor

    def boom(*a, **k):
        raise OSError("unreachable")

    monkeypatch.setattr(mod.requests, "get", boom)
    r = client.get("/sensor/mode")
    assert r.status_code == 200
    d = r.json()
    assert d["mode"] is None
    assert d["online"] is False


# ── POST /sensor/mode ────────────────────────────────────────────────────────

def test_set_training_mode_sends_the_right_firmware_arg(sensor, monkeypatch):
    mod, client = sensor
    seen = {}

    def fake_get(url, *a, **k):
        seen["url"] = url
        return _Resp({"mode": "training", "message": "Mode updated"})

    monkeypatch.setattr(mod.requests, "get", fake_get)
    r = client.post("/sensor/mode", json={"mode": "training"})
    assert r.status_code == 200
    assert r.json()["mode"] == "training"
    assert seen["url"].endswith("/mode?training=1")


def test_set_deployment_mode_sends_training_zero(sensor, monkeypatch):
    mod, client = sensor
    seen = {}
    monkeypatch.setattr(mod.requests, "get", lambda url, *a, **k: (
        seen.setdefault("url", url), _Resp({"mode": "deployment"}))[1])
    r = client.post("/sensor/mode", json={"mode": "deployment"})
    assert r.status_code == 200
    assert seen["url"].endswith("/mode?training=0")


def test_set_mode_rejects_an_invalid_value(sensor, monkeypatch):
    mod, client = sensor
    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: _Resp({"mode": "deployment"}))
    for bad in ("Training", "production", "", None, 1):
        r = client.post("/sensor/mode", json={"mode": bad})
        assert r.status_code == 400, bad


def test_set_mode_409_when_the_firmware_is_busy(sensor, monkeypatch):
    mod, client = sensor
    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: _Resp(
        {"error": "busy"}, status=409))
    r = client.post("/sensor/mode", json={"mode": "training"})
    assert r.status_code == 409
    assert "idle" in r.json()["detail"].lower()


def test_set_mode_503_when_the_device_is_unreachable(sensor, monkeypatch):
    mod, client = sensor

    def boom(*a, **k):
        raise OSError("no route to host")

    monkeypatch.setattr(mod.requests, "get", boom)
    r = client.post("/sensor/mode", json={"mode": "deployment"})
    assert r.status_code == 503


def test_set_mode_502_when_the_firmware_rejects(sensor, monkeypatch):
    mod, client = sensor
    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: _Resp({}, status=500))
    r = client.post("/sensor/mode", json={"mode": "training"})
    assert r.status_code == 502


# ── /sensor/status keeps its old keys and gains the mode ─────────────────────

def test_sensor_status_keeps_existing_keys_and_adds_mode(sensor, monkeypatch):
    mod, client = sensor
    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: _Resp(
        {"mode": "training", "sequence_state": "idle", "fan_on": True, "blower_on": False}))
    d = client.get("/sensor/status").json()
    assert d["online"] is True            # pre-existing key, unchanged
    assert d["esp32_status"] == 200       # pre-existing key, unchanged
    assert d["mode"] == "training"        # additive
    assert d["sequence_state"] == "idle"


# ── table guards: a window may only land in the table it was sampled for ─────

@pytest.fixture()
def training_client(monkeypatch, db):
    import routes.training as training
    app = FastAPI()
    app.include_router(training.router)
    app.dependency_overrides[training.get_db] = lambda: db
    return training, TestClient(app)


def _window(mode, row_id=None, reading_id=None):
    return {
        "temperature": 25.0, "humidity": 60.0,
        "mq3_1": [10.0] * 5, "mq3_2": [20.0] * 5, "mq3_3": [30.0] * 5,
        "row_id": row_id, "reading_id": reading_id, "mode": mode,
    }


def test_training_endpoint_refuses_a_deployment_window(training_client):
    """The poisoning case: a gate reading must never reach training_data."""
    mod, client = training_client
    r = client.post("/training/collect/sensor-data",
                    json=_window("deployment", row_id=1))
    assert r.status_code == 409
    assert "training" in r.json()["detail"].lower()


def test_training_endpoint_accepts_a_training_window(training_client):
    mod, client = training_client
    # reaches the row_id validation instead of being rejected for its mode
    r = client.post("/training/collect/sensor-data", json=_window("training", row_id=999999))
    assert r.status_code in (404, 400), r.text


def test_training_endpoint_still_accepts_legacy_payloads_without_a_mode(training_client):
    """An older firmware build that omits the field must not be broken by the guard."""
    mod, client = training_client
    r = client.post("/training/collect/sensor-data", json=_window(None, row_id=999999))
    assert r.status_code in (404, 400), r.text


def test_deployment_endpoint_refuses_a_training_window(sensor, db):
    from database.models import Reading
    mod, client = sensor
    r = client.post("/sensor", json=_window("training"))
    assert r.status_code == 409
    assert "deployment" in r.json()["detail"].lower()
    assert db.query(Reading).count() == 0


def test_deployment_endpoint_still_accepts_legacy_payloads_without_a_mode(sensor, db):
    mod, client = sensor
    r = client.post("/sensor", json=_window(None, reading_id=None))
    assert r.status_code == 200, r.text


# ── firmware contract ────────────────────────────────────────────────────────

def test_firmware_pins_the_destination_at_trigger_time():
    """The POST target must come from the pinned mode, not the live mode."""
    src = open("../alcodetect_esp32/alcodetect_esp32.ino").read()
    assert "activeTrainingMode = TRAINING_MODE;" in src, "trigger must pin the mode"
    assert "String endpoint = activeTrainingMode ? TRAINING_ENDPOINT : DEPLOYMENT_ENDPOINT;" in src
    # the live mode must NOT be what picks the endpoint
    assert "String endpoint = TRAINING_MODE ?" not in src


def test_firmware_refuses_a_mode_change_while_busy():
    src = open("../alcodetect_esp32/alcodetect_esp32.ino").read()
    handler = src[src.index("void handleMode()"):src.index("// GET /relay_test")]
    assert "relays.busy()" in handler, "/mode must refuse while a sequence runs"


def test_firmware_persists_the_mode_in_nvs():
    src = open("../alcodetect_esp32/alcodetect_esp32.ino").read()
    assert "#include <Preferences.h>" in src
    assert "saveModeToNvs();" in src
    assert "loadModeFromNvs();" in src


def test_firmware_default_mode_is_deployment():
    src = open("../alcodetect_esp32/alcodetect_esp32.ino").read()
    assert "bool TRAINING_MODE = false;" in src
    assert "bool TRAINING_MODE = true;" not in src


def test_firmware_tags_the_posted_window_with_its_mode():
    src = open("../alcodetect_esp32/alcodetect_esp32.ino").read()
    assert 'doc["mode"] = activeTrainingMode ? MODE_TRAINING : MODE_DEPLOYMENT;' in src
