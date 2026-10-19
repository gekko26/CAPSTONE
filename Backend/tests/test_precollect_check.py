"""Endpoint and precollect_check tests.

Everything runs against the SQLite fixture with tmp_path and synthetic data. The
ESP32 trigger and the face analyzer are stubbed; no network, no camera, no real
data tree. The check script is driven through an injected request wrapper, so it
never contacts a live backend or a device.
"""

import importlib.util
import io
import json
import os
import pathlib

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import database.models  # noqa: F401  (registers ORM metadata for create_all)
from database.models import Subject, TrainingData

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "precollect_check", ROOT / "tools" / "precollect_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pcc = _load_script()


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def sandbox(monkeypatch, tmp_path):
    import routes.training as tr
    data = tmp_path / "data"
    for sub in ("faces", "snapshots"):
        for cls in ("sober", "fatigue", "impaired", "registered"):
            (data / sub / cls).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(tr, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(tr, "SNAPSHOTS_DIR", str(data / "snapshots"))
    monkeypatch.setattr(tr, "SOBER_DIR", str(data / "snapshots" / "sober"))
    monkeypatch.setattr(tr, "FATIGUE_DIR", str(data / "snapshots" / "fatigue"))
    monkeypatch.setattr(tr, "IMPAIRED_DIR", str(data / "snapshots" / "impaired"))
    return tmp_path


@pytest.fixture()
def client(db, sandbox, monkeypatch):
    import routes.training as tr
    import routes.recognition as rc

    async def _fake_trigger(row_id=None):
        return {"triggered": True, "status": 200, "row_id": row_id}

    monkeypatch.setattr(tr, "trigger_esp32_async", _fake_trigger)
    monkeypatch.setattr(tr, "analyze_frame",
                        lambda f: {"ear": 0.3, "mar": 0.2, "pitch": 1.0,
                                   "status": "face", "face_width": 200.0})
    monkeypatch.setattr(rc, "enroll", lambda name, frames: True)

    app = FastAPI()
    app.include_router(tr.router)
    app.include_router(rc.router)
    app.dependency_overrides[tr.get_db] = lambda: db
    app.dependency_overrides[rc.get_db] = lambda: db
    return TestClient(app)


def _jpg(seed=0):
    import cv2
    ok, buf = cv2.imencode(".jpg", np.full((16, 16, 3), seed, dtype=np.uint8))
    assert ok
    return buf.tobytes()


def _enroll(client, name):
    r = client.post("/recognition/enroll", data={"name": name},
                    files=[("files", ("0.jpg", io.BytesIO(_jpg()), "image/jpeg"))])
    assert r.status_code == 200, r.text
    return next(s["id"] for s in client.get("/recognition/subjects").json()
                if s["name"] == name)


def _capture(client, endpoint, subject_id, extra=None, mq=(30.0, 30.0, 28.0)):
    # Omit the field entirely when there is no subject: the endpoints take
    # Form(None), so sending the STRING "None" is a 422, not an untagged row.
    data = {} if subject_id is None else {"subject_id": str(subject_id)}
    files = None
    if endpoint not in ("/training/collect/clear_air", "/training/collect/sanitizer"):
        files = [("file", ("f.jpg", io.BytesIO(_jpg(seed=abs(hash((endpoint, subject_id))) % 255)),
                         "image/jpeg"))]
    if extra:
        data.update(extra)
    r = client.post(endpoint, data=data, files=files)
    assert r.status_code == 200, f"{endpoint}: {r.text}"
    rid = r.json()["id"]
    window = {k: [v + i * 0.01 for i in range(50)] for k, v in
              zip(("mq3_1", "mq3_2", "mq3_3"), mq)}
    window.update({"temperature": 27.5, "humidity": 70.0,
                   "mode": "training", "row_id": rid})
    w = client.post("/training/collect/sensor-data", json=window)
    assert w.status_code == 200, w.text
    return rid


# ── GET /training/session-plan ────────────────────────────────────────────────

def test_session_plan_on_an_empty_database(client, db):
    plan = client.get("/training/session-plan").json()
    assert plan["people"] == []
    assert plan["overall"]["untagged_rows"] == 0
    assert plan["overall"]["rows_per_sensor_class"] == {"0": 0, "1": 0, "2": 0}
    assert plan["baseline"]["status"] == "insufficient_data"


def test_session_plan_after_real_collect_endpoints(client, db):
    sid = _enroll(client, "Plan subject A")
    before = client.get("/training/session-plan").json()
    person_before = before["people"][0]
    assert person_before["counts"]["clear_air"] == 0
    assert person_before["remaining"]["clear_air"] > 0

    _capture(client, "/training/collect/clear_air", sid)
    _capture(client, "/training/collect/sober", sid)
    _capture(client, "/training/collect/drowsy", sid)
    _capture(client, "/training/collect/alcohol", sid, extra={"bac": "0.07"})

    after = client.get("/training/session-plan").json()
    person = after["people"][0]
    assert person["counts"] == {"clear_air": 1, "sober": 1, "fatigue": 1,
                                "alcohol": 1, "sanitizer": 0, "perfume": 0}
    assert person["remaining"]["clear_air"] == person_before["remaining"]["clear_air"] - 1
    assert person["alcohol_bands"]["over"] == 1
    assert after["overall"]["rows_per_sensor_class"] == {"0": 3, "1": 1, "2": 0}


def test_session_plan_reflects_tag_rows(client, db):
    """A backfilled row counts toward its person once tagged."""
    from models.train.session_plan import PER_PERSON_QUOTA
    sid = _enroll(client, "Plan subject B")
    _capture(client, "/training/collect/clear_air", None)      # untagged
    _capture(client, "/training/collect/clear_air", sid)

    plan = client.get("/training/session-plan").json()
    assert plan["overall"]["untagged_rows"] == 1
    assert plan["people"][0]["counts"]["clear_air"] == 1

    untagged_id = [r.id for r in db.query(TrainingData).filter(TrainingData.subject_id.is_(None))]
    r = client.post("/training/tag-rows",
                    json={"subject_id": sid, "row_ids": untagged_id})
    assert r.status_code == 200, r.text
    assert r.json()["tagged"] == 1

    plan = client.get("/training/session-plan").json()
    assert plan["overall"]["untagged_rows"] == 0
    assert plan["people"][0]["counts"]["clear_air"] == 2
    assert plan["people"][0]["remaining"]["clear_air"] == PER_PERSON_QUOTA["clear_air"] - 2


def test_session_plan_performs_no_writes(client, db, sandbox):
    """The endpoint is read-only: same rows, same files, same mtimes."""
    sid = _enroll(client, "Plan subject C")
    _capture(client, "/training/collect/clear_air", sid)
    before = sorted(p.relative_to(sandbox).as_posix() for p in sandbox.rglob("*") if p.is_file())
    rows_before = db.query(TrainingData).count()

    for _ in range(3):
        client.get("/training/session-plan")

    after = sorted(p.relative_to(sandbox).as_posix() for p in sandbox.rglob("*") if p.is_file())
    assert before == after, "session-plan created or removed a file"
    assert db.query(TrainingData).count() == rows_before
    assert db.query(Subject).count() == 1


def test_collection_progress_keeps_every_key_and_adds_baseline(client, db):
    """The addition is purely additive; no pre-existing key changes."""
    sid = _enroll(client, "Plan subject D")
    for _ in range(3):
        _capture(client, "/training/collect/clear_air", sid)

    before = client.get("/training/collection-progress").json()
    for key in ("targets", "sensor", "faces", "subjects", "bac_bands",
                "grouping", "ready", "integrity"):
        assert key in before, f"collection-progress lost the {key!r} key"
    for key in ("orphan_files", "rows_missing_image", "rows_without_sensor_data",
                "zero_sensor_rows", "partial_sensor_rows", "fallback_dht_rows"):
        assert key in before["integrity"], f"integrity lost {key!r}"

    assert "baseline" in before
    assert before["baseline"]["status"] in ("stable", "unstable", "insufficient_data")
    # advisory only: it must not have changed any readiness verdict
    assert before["ready"]["sensor_models"]["ready"] is False


def test_baseline_flag_appears_once_clear_air_rows_accumulate(client, db):
    sid = _enroll(client, "Plan subject E")
    for _ in range(3):
        _capture(client, "/training/collect/clear_air", sid)
    body = client.get("/training/session-plan").json()
    assert body["baseline"]["n_used"] == 3
    assert body["baseline"]["status"] in ("stable", "unstable")


def test_session_plan_exposes_only_id_and_name(client, db):
    sid = _enroll(client, "Plan subject F")
    person = client.get("/training/session-plan").json()["people"][0]
    assert set(person) >= {"subject_id", "name"}
    # no other personal field may leak through
    assert not ({"face_id", "avatar_path", "group", "created_at"} & set(person))
    assert person["subject_id"] == sid


# ── tools/precollect_check.py ─────────────────────────────────────────────────

FAKE_URL = "http://example.invalid:9/secret-host"   # must never appear in output
FAKE_TOKEN = "TOKEN-SHOULD-NOT-APPEAR"
FAKE_NAME = "NAME-SHOULD-NOT-APPEAR"


def _green():
    return {
        "/": {"status": "ok"},
        "/sensor/status": {"online": True, "mode": "training", "sequence_state": "idle",
                           "mq3_live": 3, "temperature": 27.5, "humidity": 70},
        "/training/collection-progress": {
            "subjects": {"total_with_data": 1},
            "targets": {"min_subjects": 1, "target_subjects": 1},
            "baseline": {"status": "stable", "message": "settled"},
            "integrity": {"zero_sensor_rows": 0, "fallback_dht_rows": 0,
                          "partial_sensor_rows": 0, "rows_without_sensor_data": 0}},
        "/recognition/subjects": [{"id": 1, "name": FAKE_NAME}],
        "/training/subject-coverage": {"untagged_row_ids": []},
        "/training/session-plan": {
            "people": [{"subject_id": 1, "name": FAKE_NAME,
                        "counts": {"sober": 1}, "remaining": {"sober": 3},
                        "next_suggested": "sober"}],
            "overall": {"untagged_rows": 0, "alcohol_bands": {}},
            "quotas": {"sober": 10}, "event_keys": ["sober"],
            "baseline": {"status": "stable", "message": "settled"}},
    }


def _mock_get(overrides=None, raise_on=None, bad_json_on=None):
    table = _green()
    table.update(overrides or {})
    raise_on = raise_on or []

    def get(url, params=None, timeout=None):
        for suffix in raise_on:
            if url.endswith(suffix):
                raise OSError("simulated transport failure")
        for suffix, payload in (bad_json_on or {}).items():
            if url.endswith(suffix):
                return payload          # a string, not a dict: callers must survive
        for key, value in table.items():
            if url.endswith(key):
                return value
        raise OSError("unmocked")
    return get


def _output(capsys, result):
    rendered = "\n".join(" ".join(str(part) for part in row) for row in result.rows)
    return rendered


def test_get_only_guard_blocks_writes():
    """The read-only guarantee is enforced in code, not by convention."""
    for method in ("POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "get"):
        with pytest.raises(pcc.GetOnlyGuard):
            pcc._get("http://example.invalid/x", method=method)
    # a non-HTTP scheme is refused too, so file:// and friends can never be read
    with pytest.raises(pcc.GetOnlyGuard):
        pcc._get("ftp://example.invalid/x")
    with pytest.raises(pcc.GetOnlyGuard):
        pcc._get("file:///etc/passwd")


def test_every_request_the_script_makes_is_a_get():
    """No call site may smuggle a write past the wrapper."""
    import inspect
    src = inspect.getsource(pcc)
    for bad in ('method="POST"', "method='POST'", "Request(url, data=",
                'urlopen(Request(url, method="P'):
        assert bad not in src, f"the script appears to issue {bad}"


def test_missing_backend_address_exits_two(capsys):
    """No hardcoded fallback: without the env var the script must refuse to run."""
    old = {k: os.environ.pop(k, None) for k in (pcc.API_URL_ENV, pcc.ESP32_URL_ENV)}
    try:
        assert pcc.main([]) == 2
        out = capsys.readouterr().out
        assert pcc.API_URL_ENV in out
        assert "127.0.0.1" not in out and "localhost" not in out, "no address may be invented"
    finally:
        for k, v in old.items():
            if v is not None:
                os.environ[k] = v


def test_unset_device_address_is_reported_as_info(monkeypatch, capsys):
    monkeypatch.delenv(pcc.ESP32_URL_ENV, raising=False)
    result, _ = pcc.run_checks(get=_mock_get(), api_url="http://example.invalid")
    row = [r for r in result.rows if r[1] == "esp32 address"][0]
    assert row[0] == pcc.INFO
    result.render()
    out = capsys.readouterr().out
    assert "127.0.0.1" not in out and "localhost" not in out, "no address may be invented"


def test_configured_device_matches_the_answering_host():
    result, _ = pcc.run_checks(get=_mock_get(), api_url="http://example.invalid",
                               esp32_url="http://example.invalid:8000")
    row = [r for r in result.rows if r[1] == "esp32 address"][0]
    assert row[0] == pcc.PASS


def test_mismatched_device_address_warns_without_failing():
    result, _ = pcc.run_checks(get=_mock_get(), api_url="http://example.invalid",
                               esp32_url="http://other-host.invalid")
    row = [r for r in result.rows if r[1] == "esp32 address"][0]
    assert row[0] == pcc.WARN
    assert result.failed is False, "a device mismatch must not block collection"


def test_all_green_exits_zero():
    result, _ = pcc.run_checks(get=_mock_get(), api_url="http://example.invalid")
    assert result.failed is False
    assert result.counts()[pcc.FAIL] == 0


def test_device_in_deployment_mode_fails():
    result, _ = pcc.run_checks(
        get=_mock_get({"/sensor/status": {"online": True, "mode": "deployment",
                                         "sequence_state": "idle", "mq3_live": 3,
                                         "temperature": 27.5}}),
        api_url="http://example.invalid")
    assert result.failed is True
    reasons = [r for r in result.rows if "device mode" in r[1]]
    assert reasons and "deployment" in reasons[0][2]


def test_busy_relay_sequence_fails():
    result, _ = pcc.run_checks(
        get=_mock_get({"/sensor/status": {"online": True, "mode": "training",
                                         "sequence_state": "sampling", "mq3_live": 3,
                                         "temperature": 27.5}}),
        api_url="http://example.invalid")
    assert any(r[0] == pcc.FAIL and "relay sequence" in r[1] for r in result.rows)


@pytest.mark.parametrize("live,expect_fail", [(3, False), (2, True), (0, True)])
def test_partial_sensor_channels_fail(live, expect_fail):
    result, _ = pcc.run_checks(
        get=_mock_get({"/sensor/status": {"online": True, "mode": "training",
                                         "sequence_state": "idle", "mq3_live": live,
                                         "temperature": 27.5}}),
        api_url="http://example.invalid")
    mq = [r for r in result.rows if "mq3 channels" in r[1]][0]
    assert (mq[0] == pcc.FAIL) is expect_fail


def test_dht_failure_fails():
    result, _ = pcc.run_checks(
        get=_mock_get({"/sensor/status": {"online": True, "mode": "training",
                                         "sequence_state": "idle", "mq3_live": 3,
                                         "temperature": None}}),
        api_url="http://example.invalid")
    assert any(r[0] == pcc.FAIL and "dht11" in r[1] for r in result.rows)


def test_unreported_wifi_signal_is_info_not_failure():
    result, _ = pcc.run_checks(get=_mock_get(), api_url="http://example.invalid")
    wifi = [r for r in result.rows if "wifi signal" in r[1]][0]
    assert wifi[0] == pcc.INFO


@pytest.mark.parametrize("rssi,level", [(-65, pcc.PASS), (-75, pcc.WARN), (-85, pcc.FAIL)])
def test_wifi_signal_thresholds(rssi, level):
    result, _ = pcc.run_checks(
        get=_mock_get({"/sensor/status": {"online": True, "mode": "training",
                                         "sequence_state": "idle", "mq3_live": 3,
                                         "temperature": 27.5, "wifi_rssi": rssi}}),
        api_url="http://example.invalid")
    wifi = [r for r in result.rows if "wifi signal" in r[1]][0]
    assert wifi[0] == level


def test_too_few_enrolled_subjects_fails():
    result, _ = pcc.run_checks(
        get=_mock_get({"/training/collection-progress": {
            "subjects": {"total_with_data": 0},
            "targets": {"min_subjects": 3, "target_subjects": 5},
            "integrity": {"zero_sensor_rows": 0, "fallback_dht_rows": 0,
                          "partial_sensor_rows": 0, "rows_without_sensor_data": 0,
                          "baseline": {"status": "stable", "message": "s"}}},
            "/recognition/subjects": []}),
        api_url="http://example.invalid")
    assert any(r[0] == pcc.FAIL and "enrolled subjects" in r[1] for r in result.rows)


def test_untagged_rows_fail_and_print_only_the_count():
    result, _ = pcc.run_checks(
        get=_mock_get({"/training/subject-coverage":
                       {"untagged_row_ids": [11, 12, 13]}}),
        api_url="http://example.invalid")
    row = [r for r in result.rows if "tagged rows" in r[1]][0]
    assert row[0] == pcc.FAIL
    assert "3 untagged" in row[2]
    assert "11" not in row[2] and "12" not in row[2], "row ids must not be printed"


def test_integrity_signals_have_the_specified_severity():
    result, _ = pcc.run_checks(
        get=_mock_get({"/training/collection-progress": {
            "subjects": {"total_with_data": 1},
            "targets": {"min_subjects": 1, "target_subjects": 1},
            "integrity": {"zero_sensor_rows": 2, "fallback_dht_rows": 1,
                          "partial_sensor_rows": 3, "rows_without_sensor_data": 4,
                          "baseline": {"status": "unstable",
                                       "message": "settle between captures"}}}}),
        api_url="http://example.invalid")
    level = {name: [r for r in result.rows if name in r[1]][0][0]
             for name in ("zero-sensor rows", "dht fallback rows",
                          "partial-sensor rows", "rows without sensor data")}
    assert level["zero-sensor rows"] == pcc.FAIL
    assert level["dht fallback rows"] == pcc.FAIL
    assert level["partial-sensor rows"] == pcc.WARN
    assert level["rows without sensor data"] == pcc.WARN


def test_unstable_baseline_warns_but_never_fails():
    """The live route exposes baseline at the TOP level, not inside integrity."""
    result, _ = pcc.run_checks(
        get=_mock_get({"/training/collection-progress": {
            "subjects": {"total_with_data": 1},
            "targets": {"min_subjects": 1, "target_subjects": 1},
            "baseline": {"status": "unstable", "message": "baseline is still moving"},
            "integrity": {"zero_sensor_rows": 0, "fallback_dht_rows": 0,
                          "partial_sensor_rows": 0, "rows_without_sensor_data": 0}}}),
        api_url="http://example.invalid")
    assert result.failed is False
    warns = [r for r in result.rows if r[0] == pcc.WARN and "baseline" in r[1]]
    assert warns, "an unstable baseline must warn"
    assert "still moving" in warns[0][2], "the baseline message must reach the operator"
    # and it must be reported exactly once, not once per source
    assert len([r for r in result.rows if "baseline" in r[1]]) == 1


def test_unreachable_backend_short_circuits_without_a_traceback():
    def boom(url, params=None, timeout=None):
        raise OSError("no route to host")
    result, _ = pcc.run_checks(get=boom, api_url="http://example.invalid")
    assert result.failed is True
    assert len(result.rows) == 1, "checks after an unreachable backend must be skipped"


def test_malformed_json_is_handled_without_a_traceback():
    result, _ = pcc.run_checks(get=_mock_get(bad_json_on={"/sensor/status": "not-a-dict"}),
                               api_url="http://example.invalid")
    assert result.failed is True
    assert any("device status" in r[1] for r in result.rows)


def test_output_never_leaks_url_ip_token_or_name(capsys):
    result, api_url = pcc.run_checks(get=_mock_get(), api_url=FAKE_URL)
    rendered = _output(capsys, result)
    for secret in (FAKE_URL, FAKE_TOKEN, FAKE_NAME, "example.invalid", "secret-host"):
        assert secret not in rendered, f"leaked: {secret!r}"
    # the person is identified by id only
    assert "subject #1" in rendered


def test_main_reports_failure_exit_code(monkeypatch, capsys):
    monkeypatch.setenv(pcc.API_URL_ENV, "http://example.invalid")
    def fake_run_checks(api_url=None):
        result = pcc.Result()
        result.add(pcc.FAIL, "thing", "broken")
        return result, api_url

    monkeypatch.setattr(pcc, "run_checks", fake_run_checks)
    assert pcc.main([]) == 1
    out = capsys.readouterr().out
    assert "NOT READY" in out
    assert "example.invalid" not in out.split("backend address")[-1]


def test_script_never_prints_the_configured_address(monkeypatch, capsys):
    """main() names the env var so the operator knows what to set, never its value."""
    monkeypatch.setenv(pcc.API_URL_ENV, FAKE_URL)
    monkeypatch.setattr(pcc, "run_checks", lambda api_url=None: (pcc.Result(), FAKE_URL))
    pcc.main([])
    out = capsys.readouterr().out
    assert FAKE_URL not in out, "the configured address leaked into the output"
    assert pcc.API_URL_ENV in out, "the env var NAME should be shown"