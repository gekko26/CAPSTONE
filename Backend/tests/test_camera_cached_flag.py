"""Honesty flags added to POST /camera/analyze.

The deployment page must never count a cached analysis, so /camera/analyze now
reports `cached` explicitly and passes the normalised `face_bbox` through. These
tests run the REAL route with MediaPipe/DeepFace/RTSP stubbed at the camera
boundary: no model is loaded, no camera is opened and no database row is written.
"""

import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# routes.camera is imported inside the fixture, not at module scope: importing it
# loads DeepFace, and test_calibration_script asserts DeepFace is not loaded before
# its own tests run. This mirrors the existing camera tests in this suite.
# Everything the route reads out of analyze_frame(). face_bbox is a tuple here,
# exactly like the real cv_model returns; JSON serialises it as a 4-element list.
EAR_RESULT = {
    "ear": 0.30,
    "mar": 0.10,
    "pitch": 0.0,
    "yaw": 0.0,
    "roll": 0.0,
    "status": "normal",
    "impaired": False,
    "left_ear": 0.30,
    "right_ear": 0.30,
    "yawning": False,
    "head_down": False,
    "proximity": "close",
    "face_width": 100.0,
    "is_close": True,
    "face_bbox": (200, 100, 400, 300),
}

DEFAULT_IDENTITY = ({"identified": False, "name": None, "confidence": 0.0,
                     "reason": "no_face"}, {})
MATCHED_IDENTITY = ({"identified": True, "name": "p0", "confidence": 0.9,
                     "reason": "matched"}, {"identified": True, "name": "p0"})

# The complete response contract that existed BEFORE this fix. The new keys are
# additive only, so the set must grow by exactly `cached` and `face_bbox`.
PRE_EXISTING_KEYS = {
    "proximity", "face_width", "is_close", "ear", "mar", "yawning", "head_down",
    "pitch", "yaw", "roll", "status", "impaired", "left_ear", "right_ear",
    "identified", "name", "confidence", "reason", "identity_raw", "ts",
}


@pytest.fixture()
def cam():
    import routes.camera as camera
    return camera


@pytest.fixture()
def client(cam, monkeypatch):
    # The person-presence indicator hook must never touch the network here.
    import config.indicators as ind_cfg
    monkeypatch.setattr(ind_cfg, "INDICATORS_ENABLED", False)
    app = FastAPI()
    app.include_router(cam.router)
    app.dependency_overrides[cam.get_db] = lambda: None
    return TestClient(app)


def _jpeg():
    ok, buf = cv2.imencode(".jpg", np.zeros((32, 32, 3), np.uint8))
    assert ok
    return buf.tobytes()


def _post(client, payload=None):
    return client.post(
        "/camera/analyze",
        files={"file": ("frame.jpg", payload if payload is not None else _jpeg(),
                        "image/jpeg")},
    )


def _stub_analysis(cam, monkeypatch, identity=DEFAULT_IDENTITY):
    monkeypatch.setattr(cam, "analyze_frame", lambda frame: dict(EAR_RESULT))
    monkeypatch.setattr(cam, "_identify_gated", lambda frame, ear: identity)


def test_fresh_analysis_is_flagged_cached_false_and_passes_face_bbox(
        client, cam, monkeypatch):
    _stub_analysis(cam, monkeypatch)

    resp = _post(client)
    assert resp.status_code == 200
    body = resp.json()

    assert body["cached"] is False
    assert body["face_bbox"] == [200, 100, 400, 300]
    assert isinstance(body["ts"], (int, float))

    # Additive change only: every pre-existing key survives, byte for byte in name.
    assert set(body) == PRE_EXISTING_KEYS | {"cached", "face_bbox"}
    for key in PRE_EXISTING_KEYS:
        assert key in body


def test_stale_identity_demotion_preserves_cached_false(client, cam, monkeypatch):
    # A negative TTL makes every identified analysis look expired, exercising
    # _with_freshness without freezing the clock.
    monkeypatch.setattr(cam, "FACE_ANALYSIS_TTL_S", -1.0)
    _stub_analysis(cam, monkeypatch, MATCHED_IDENTITY)

    resp = _post(client)
    assert resp.status_code == 200
    body = resp.json()

    assert body["cached"] is False
    assert body["identified"] is False
    assert body["name"] is None
    assert body["confidence"] == 0.0
    assert body["reason"] == "stale"

    # Decision-relevant fields must be untouched by the identity demotion.
    assert body["status"] == EAR_RESULT["status"]
    assert body["impaired"] == EAR_RESULT["impaired"]
    assert body["is_close"] == EAR_RESULT["is_close"]
    assert body["face_width"] == EAR_RESULT["face_width"]
    assert body["face_bbox"] == list(EAR_RESULT["face_bbox"])
    assert set(body) == PRE_EXISTING_KEYS | {"cached", "face_bbox"}


def test_cached_fallback_returns_a_copy_and_never_mutates_the_shared_dict(
        client, cam, monkeypatch):
    seed = {
        "proximity": "close",
        "face_width": 100.0,
        "is_close": True,
        "face_bbox": [200, 100, 400, 300],
        "ear": 0.30,
        "status": "normal",
        "impaired": False,
        "identified": False,
        "name": None,
        "reason": "no_face",
        "ts": 123.0,
    }
    monkeypatch.setattr(cam, "_last_analysis", dict(seed))

    # Garbage bytes cannot be decoded, so the route falls back to the cached result.
    resp = _post(client, payload=b"this is not a jpeg")
    assert resp.status_code == 200
    body = resp.json()

    assert body["cached"] is True
    assert {k: v for k, v in body.items() if k != "cached"} == seed

    # The shared cache is untouched: no cached key leaked into it.
    assert cam._last_analysis == seed
    assert "cached" not in cam._last_analysis

    # A subsequent fresh analysis replaces the cache and reports cached: false.
    _stub_analysis(cam, monkeypatch)
    fresh = _post(client).json()
    assert fresh["cached"] is False
    assert fresh["status"] == EAR_RESULT["status"]


def test_garbage_without_a_cache_keeps_the_existing_error_body(client, cam, monkeypatch):
    monkeypatch.setattr(cam, "_last_analysis", None)

    resp = _post(client, payload=b"this is not a jpeg")
    assert resp.status_code == 200
    assert resp.json() == {"error": "No frame available — check camera connection"}
