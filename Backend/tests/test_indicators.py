"""Tests for the deployment indicator client, hooks and bench endpoint.

Every ESP32 call is mocked; nothing touches hardware or the live network. The token
used here is a DUMMY defined in this file — it is never a real secret and must never
appear in any assertion output, log capture or exception message under test.

The database is the throwaway SQLite `db` fixture from conftest.py, so the live
MariaDB is never touched.
"""

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import indicators as ind_cfg
from services import indicator as ind_mod


# A dummy, obviously-fake value. Never printed: the assertions below only ever check
# that the header was *sent*, never its contents.
DUMMY_TOKEN = "dummy-token-for-tests-only-not-a-secret"


class _Resp:
    def __init__(self, payload=None, status=200):
        self._payload = payload if payload is not None else {}
        self.status_code = status

    def json(self):
        return self._payload


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _off_by_default(monkeypatch):
    """Every test starts from indicators OFF and no token, and restores them after."""
    monkeypatch.setattr(ind_cfg, "INDICATORS_ENABLED", False)
    monkeypatch.setattr(ind_cfg, "ESP32_DEVICE_TOKEN", "")
    monkeypatch.setattr(ind_mod, "ESP32_DEVICE_TOKEN", "")
    ind_mod.reset_person_state()
    yield
    ind_mod.reset_person_state()


@pytest.fixture()
def enabled(monkeypatch):
    monkeypatch.setattr(ind_cfg, "INDICATORS_ENABLED", True)
    monkeypatch.setattr(ind_mod, "ESP32_DEVICE_TOKEN", DUMMY_TOKEN)
    monkeypatch.setattr(ind_cfg, "ESP32_DEVICE_TOKEN", DUMMY_TOKEN)


class _Spy:
    """Records every post and never blocks."""

    def __init__(self, status=200):
        self.calls = []
        self.status = status

    def __call__(self, url, **kw):
        self.calls.append({"url": url, "json": kw.get("json"),
                           "headers": kw.get("headers"), "timeout": kw.get("timeout")})
        return _Resp({"indicator_state": (kw.get("json") or {}).get("state")}, self.status)


def _drain():
    """Wait for the daemon threads the client dispatches."""
    import threading, time
    deadline = time.time() + 5
    while time.time() < deadline:
        alive = [t for t in threading.enumerate() if t.name == "indicator"]
        if not alive:
            return
        time.sleep(0.01)


# ── 1. INDICATORS_ENABLED=False makes NO network call at all ───────────────────

def test_disabled_makes_no_network_call(monkeypatch):
    """With indicators off not a single HTTP call is made.

    A token IS configured, so the only thing that can stop this call is the
    INDICATORS_ENABLED gate itself — otherwise the token check would mask a
    regression in the enablement gate.
    """
    monkeypatch.setattr(ind_cfg, "ESP32_DEVICE_TOKEN", DUMMY_TOKEN)
    monkeypatch.setattr(ind_mod, "ESP32_DEVICE_TOKEN", DUMMY_TOKEN)
    assert ind_cfg.token_configured() is True, "precondition: a token exists"
    spy = _Spy()
    monkeypatch.setattr(ind_mod.requests, "post", spy)
    ind_mod.verdict("PASS", 7)
    ind_mod.person_detected()
    ind_mod.person_cleared()
    ind_mod.bench("analyzing")
    _drain()
    assert spy.calls == [], "indicators must be completely silent when disabled"


def test_disabled_sends_nothing_even_with_a_token_configured(monkeypatch):
    """Same isolation: token present, indicators off, so only the gate can stop it."""
    monkeypatch.setattr(ind_cfg, "ESP32_DEVICE_TOKEN", DUMMY_TOKEN)
    monkeypatch.setattr(ind_mod, "ESP32_DEVICE_TOKEN", DUMMY_TOKEN)
    spy = _Spy()
    monkeypatch.setattr(ind_mod.requests, "post", spy)
    ind_mod.verdict("FAIL", 1)
    _drain()
    assert spy.calls == []


# ── 2. enabled: correct payload, token header, short timeout ──────────────────

def test_verdict_posts_correct_payload_with_token_header(monkeypatch, enabled):
    spy = _Spy()
    monkeypatch.setattr(ind_mod.requests, "post", spy)
    ind_mod.verdict("PASS", 4242)
    _drain()
    assert len(spy.calls) == 1
    call = spy.calls[0]
    assert call["url"].endswith("/indicator")
    assert call["json"] == {"state": "pass", "run_id": 4242}
    assert ind_mod.TOKEN_HEADER in call["headers"]
    # the header is present; we never assert on its value
    assert call["headers"][ind_mod.TOKEN_HEADER] == DUMMY_TOKEN
    # short timeout: must not sit on a request thread
    assert call["timeout"] == ind_cfg.INDICATOR_TIMEOUT_S
    assert call["timeout"] <= 1.0


@pytest.mark.parametrize("given,expected", [
    ("PASS", "pass"), ("WARN", "warn"), ("FAIL", "fail"),
    ("pass", "pass"), (" Warn ", "warn"),
])
def test_verdict_statuses_are_normalised(monkeypatch, enabled, given, expected):
    spy = _Spy()
    monkeypatch.setattr(ind_mod.requests, "post", spy)
    ind_mod.verdict(given, 3)
    _drain()
    assert spy.calls[0]["json"]["state"] == expected


@pytest.mark.parametrize("bad", ["green", "", "PASSED", "deny", None, 99])
def test_unknown_verdict_status_is_never_sent(monkeypatch, enabled, bad):
    spy = _Spy()
    monkeypatch.setattr(ind_mod.requests, "post", spy)
    ind_mod.verdict(bad, 1)
    _drain()
    assert spy.calls == []


def test_verdict_without_a_run_id_is_never_sent(monkeypatch, enabled):
    spy = _Spy()
    monkeypatch.setattr(ind_mod.requests, "post", spy)
    ind_mod.verdict("PASS", None)
    _drain()
    assert spy.calls == []


def test_enabled_without_a_token_makes_no_call(monkeypatch):
    """Fail closed: enabled but no token configured means nothing is sent at all.

    Mirrors the firmware, which answers 503 and keeps the LEDs off with no token.
    """
    monkeypatch.setattr(ind_cfg, "INDICATORS_ENABLED", True)
    monkeypatch.setattr(ind_cfg, "ESP32_DEVICE_TOKEN", "")
    monkeypatch.setattr(ind_mod, "ESP32_DEVICE_TOKEN", "")
    assert ind_cfg.token_configured() is False, "precondition: no token"
    spy = _Spy()
    monkeypatch.setattr(ind_mod.requests, "post", spy)
    ind_mod.verdict("PASS", 1)
    ind_mod.person_detected()
    ind_mod.bench("warn")
    _drain()
    assert spy.calls == [], "an unauthenticated command must never be sent"


# ── 3. the token is never logged ──────────────────────────────────────────────

def test_token_never_appears_in_logs(monkeypatch, enabled, caplog):
    spy = _Spy(status=500)
    monkeypatch.setattr(ind_mod.requests, "post", spy)
    with caplog.at_level(logging.DEBUG, logger="AlcoDetect.Indicator"):
        ind_mod.verdict("PASS", 1)
        _drain()
    assert spy.calls, "the call should still have been attempted"
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert DUMMY_TOKEN not in blob
    assert "dummy" not in blob.lower()


def test_token_never_appears_in_logs_when_unset(monkeypatch, caplog):
    monkeypatch.setattr(ind_mod, "ESP32_DEVICE_TOKEN", "")
    with caplog.at_level(logging.DEBUG, logger="AlcoDetect.Indicator"):
        ind_mod.verdict("PASS", 1)
        _drain()
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert DUMMY_TOKEN not in blob


# ── 4. an offline / slow / broken device never raises ─────────────────────────

def test_connection_error_is_swallowed(monkeypatch, enabled):
    def boom(*a, **k):
        raise ind_mod.requests.exceptions.ConnectionError("refused")
    monkeypatch.setattr(ind_mod.requests, "post", boom)
    ind_mod.verdict("PASS", 1)      # must not raise
    _drain()


def test_timeout_is_swallowed(monkeypatch, enabled):
    def slow(*a, **k):
        raise ind_mod.requests.exceptions.Timeout("too slow")
    monkeypatch.setattr(ind_mod.requests, "post", slow)
    ind_mod.verdict("FAIL", 1)      # must not raise
    _drain()


def test_unexpected_exception_is_swallowed(monkeypatch, enabled):
    def weird(*a, **k):
        raise RuntimeError("something entirely unexpected")
    monkeypatch.setattr(ind_mod.requests, "post", weird)
    ind_mod.verdict("WARN", 1)      # must not raise
    _drain()


def test_non_200_is_swallowed_and_logged(monkeypatch, enabled, caplog):
    monkeypatch.setattr(ind_mod.requests, "post", _Spy(status=503))
    with caplog.at_level(logging.WARNING, logger="AlcoDetect.Indicator"):
        ind_mod.verdict("PASS", 1)
        _drain()
    assert any("503" in r.getMessage() for r in caplog.records)


def test_thread_creation_failure_is_swallowed(monkeypatch, enabled):
    def no_threads(*a, **k):
        raise RuntimeError("cannot allocate thread")
    monkeypatch.setattr(ind_mod.threading, "Thread", no_threads)
    ind_mod.verdict("PASS", 1)      # must not raise


# ── 5. person-detected debounce and cooldown ──────────────────────────────────

def test_person_detected_needs_consecutive_frames(monkeypatch, enabled):
    spy = _Spy()
    monkeypatch.setattr(ind_mod.requests, "post", spy)
    monkeypatch.setattr(ind_cfg, "INDICATOR_PERSON_FRAMES", 3)

    assert ind_mod.person_detected(100.0) is False    # streak 1
    assert ind_mod.person_detected(100.4) is False    # streak 2
    assert ind_mod.person_detected(100.8) is True     # streak 3 -> announce
    _drain()
    assert len(spy.calls) == 1
    assert spy.calls[0]["json"] == {"state": "person_detected"}


def test_person_detected_does_not_repeat_inside_the_cooldown(monkeypatch, enabled):
    spy = _Spy()
    monkeypatch.setattr(ind_mod.requests, "post", spy)
    monkeypatch.setattr(ind_cfg, "INDICATOR_PERSON_FRAMES", 1)
    monkeypatch.setattr(ind_cfg, "INDICATOR_PERSON_COOLDOWN_S", 10)

    assert ind_mod.person_detected(100.0) is True
    assert ind_mod.person_detected(101.0) is False    # inside 10 s
    assert ind_mod.person_detected(105.0) is False
    assert ind_mod.person_detected(111.0) is True     # past the cooldown
    _drain()
    assert len(spy.calls) == 2


def test_a_flapping_signal_does_not_announce(monkeypatch, enabled):
    """Consecutive-frame debounce: on, off, on must not count as three frames."""
    spy = _Spy()
    monkeypatch.setattr(ind_mod.requests, "post", spy)
    monkeypatch.setattr(ind_cfg, "INDICATOR_PERSON_FRAMES", 3)
    monkeypatch.setattr(ind_cfg, "INDICATOR_PERSON_CLEAR_FRAMES", 1)
    monkeypatch.setattr(ind_cfg, "INDICATOR_PERSON_COOLDOWN_S", 0)

    ind_mod.person_detected(100.0)
    ind_mod.person_cleared(100.1)      # resets the streak
    ind_mod.person_detected(100.2)
    ind_mod.person_cleared(100.3)
    ind_mod.person_detected(100.4)
    _drain()
    # clearing may legitimately send "idle"; what must NOT happen is an announcement
    assert [c for c in spy.calls
            if c["json"].get("state") == "person_detected"] == []


def test_person_cleared_needs_consecutive_absent_frames(monkeypatch, enabled):
    spy = _Spy()
    monkeypatch.setattr(ind_mod.requests, "post", spy)
    monkeypatch.setattr(ind_cfg, "INDICATOR_PERSON_CLEAR_FRAMES", 2)

    assert ind_mod.person_cleared(100.0) is False
    assert ind_mod.person_cleared(100.4) is True
    _drain()
    assert spy.calls[0]["json"] == {"state": "idle"}


# ── 6. the bench endpoint ─────────────────────────────────────────────────────

@pytest.fixture()
def sensor_client(monkeypatch):
    import routes.sensor as sensor
    app = FastAPI()
    app.include_router(sensor.router)
    return sensor, TestClient(app)


@pytest.fixture()
def bench_token(monkeypatch):
    """The bench tests that expect success need a token in the environment. The
    value is the dummy above and is never asserted on."""
    monkeypatch.setattr(ind_cfg, "ESP32_DEVICE_TOKEN", DUMMY_TOKEN)


def test_bench_returns_503_when_the_token_env_var_is_unset(sensor_client):
    sensor, client = sensor_client
    r = client.post("/sensor/indicator/test", json={"state": "analyzing"})
    assert r.status_code == 503
    assert "ESP32_DEVICE_TOKEN" in r.json()["detail"]


def test_bench_sends_nothing_when_503(sensor_client, monkeypatch):
    """A 503 must not have touched the device at all."""
    sensor, client = sensor_client
    spy_post, spy_get = _Spy(), _Spy()
    monkeypatch.setattr(sensor.requests, "post", spy_post)
    monkeypatch.setattr(sensor.requests, "get", spy_get)
    client.post("/sensor/indicator/test", json={"state": "analyzing"})
    assert spy_post.calls == []
    assert spy_get.calls == []


def test_bench_works_with_the_dummy_token(sensor_client, bench_token, monkeypatch):
    sensor, client = sensor_client
    monkeypatch.setattr(sensor.requests, "get",
                        lambda *a, **k: _Resp({"sequence_state": "idle"}, 200))
    spy = _Spy()
    monkeypatch.setattr(sensor.requests, "post", spy)
    r = client.post("/sensor/indicator/test", json={"state": "warn"})
    assert r.status_code == 200
    assert spy.calls[0]["json"]["state"] == "warn"
    assert ind_mod.TOKEN_HEADER in spy.calls[0]["headers"]


def test_bench_maps_off_to_idle(sensor_client, bench_token, monkeypatch):
    sensor, client = sensor_client
    monkeypatch.setattr(sensor.requests, "get",
                        lambda *a, **k: _Resp({"sequence_state": "idle"}, 200))
    spy = _Spy()
    monkeypatch.setattr(sensor.requests, "post", spy)
    client.post("/sensor/indicator/test", json={"state": "off"})
    assert spy.calls[0]["json"]["state"] == "idle"


def test_bench_returns_409_while_a_sequence_runs(sensor_client, bench_token, monkeypatch):
    sensor, client = sensor_client
    monkeypatch.setattr(sensor.requests, "get",
                        lambda *a, **k: _Resp({"sequence_state": "sampling"}, 200))
    spy = _Spy()
    monkeypatch.setattr(sensor.requests, "post", spy)
    r = client.post("/sensor/indicator/test", json={"state": "analyzing"})
    assert r.status_code == 409
    assert "sampling" in r.json()["detail"]
    assert spy.calls == [], "must not drive the LEDs during a live run"


def test_bench_surfaces_a_device_401_as_401(sensor_client, bench_token, monkeypatch):
    sensor, client = sensor_client
    monkeypatch.setattr(sensor.requests, "get",
                        lambda *a, **k: _Resp({"sequence_state": "idle"}, 200))
    monkeypatch.setattr(sensor.requests, "post", lambda *a, **k: _Resp({}, 401))
    r = client.post("/sensor/indicator/test", json={"state": "warn"})
    assert r.status_code == 401
    assert DUMMY_TOKEN not in r.text


def test_bench_rejects_an_unknown_state(sensor_client, bench_token, monkeypatch):
    sensor, client = sensor_client
    monkeypatch.setattr(sensor.requests, "get",
                        lambda *a, **k: _Resp({"sequence_state": "idle"}, 200))
    spy = _Spy()
    monkeypatch.setattr(sensor.requests, "post", spy)
    r = client.post("/sensor/indicator/test", json={"state": "green"})
    assert r.status_code == 400
    assert spy.calls == []


def test_bench_clamps_the_volume(sensor_client, bench_token, monkeypatch):
    sensor, client = sensor_client
    monkeypatch.setattr(sensor.requests, "get",
                        lambda *a, **k: _Resp({"sequence_state": "idle"}, 200))
    spy = _Spy()
    monkeypatch.setattr(sensor.requests, "post", spy)
    client.post("/sensor/indicator/test", json={"state": "analyzing", "volume": 99})
    assert spy.calls[0]["json"]["volume"] == 30
    client.post("/sensor/indicator/test", json={"state": "analyzing", "volume": -5})
    assert spy.calls[1]["json"]["volume"] == 0


def test_bench_never_leaks_the_token_in_its_response(sensor_client, bench_token, monkeypatch):
    sensor, client = sensor_client
    monkeypatch.setattr(sensor.requests, "get",
                        lambda *a, **k: _Resp({"sequence_state": "idle"}, 200))
    monkeypatch.setattr(sensor.requests, "post",
                        lambda *a, **k: _Resp({"indicator_state": "analyzing"}, 200))
    r = client.post("/sensor/indicator/test", json={"state": "analyzing"})
    assert DUMMY_TOKEN not in r.text
    assert DUMMY_TOKEN not in str(r.json())