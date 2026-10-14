"""Tests for identify() / verify() and the camera identity path.

DeepFace is NEVER imported here. `models.train.face_recognition` is stubbed into
sys.modules before anything imports it, and every path is redirected to tmp_path, so
nothing under data/ is read or written and no face model is loaded.
"""

import os
import sys
import types

import numpy as np
import pytest


@pytest.fixture()
def fr(monkeypatch, tmp_path):
    """The REAL face_recognition module, with DeepFace stubbed out.

    Stubbing happens before the import so no face model is ever built. The module's
    REGISTERED / SNAPSHOTS are redirected to tmp_path, so data/ is never touched.
    The tests below then exercise the shipped identify()/verify() directly — not a
    copy of their logic.
    """
    import cv2
    import pandas as pd

    registered = tmp_path / "registered"
    (registered / "p0").mkdir(parents=True, exist_ok=True)
    (registered / "p0" / "01.jpg").write_bytes(b"stub")
    (registered / "p1").mkdir(parents=True, exist_ok=True)
    (registered / "p1" / "01.jpg").write_bytes(b"stub")
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)

    calls = {"find": 0, "verify": 0, "find_kwargs": [], "verify_kwargs": []}

    class _Results(list):
        @property
        def empty(self):
            return not bool(self[0])

    def make_rows(rows):
        return _Results([pd.DataFrame(rows)])

    def fake_find(**kw):
        calls["find"] += 1
        calls["find_kwargs"].append(kw)
        return calls.get("find_result", make_rows([
            {"identity": str(registered / "p0" / "01.jpg"), "distance": 0.10}]))

    def fake_verify(**kw):
        calls["verify"] += 1
        calls["verify_kwargs"].append(kw)
        return calls.get("verify_result", {"verified": True, "distance": 0.10})

    deepface_stub = types.SimpleNamespace(
        find=fake_find, verify=fake_verify,
        build_model=lambda *a, **k: None,
        represent=lambda *a, **k: [{"embedding": [0.0]}],
    )
    df_mod = types.ModuleType("deepface")
    df_mod.DeepFace = deepface_stub
    k_backend = types.ModuleType("tensorflow.keras.backend")
    monkeypatch.setitem(sys.modules, "deepface", df_mod)
    monkeypatch.setitem(sys.modules, "tensorflow.keras.backend", k_backend)

    sys.path.insert(0, ".")
    sys.modules.pop("models.train.face_recognition", None)
    import models.train.face_recognition as real_fr

    monkeypatch.setattr(real_fr, "REGISTERED", str(registered))
    monkeypatch.setattr(real_fr, "SNAPSHOTS", str(snapshots))

    return types.SimpleNamespace(
        fr=real_fr, calls=calls, make_rows=make_rows,
        registered=registered, snapshots=snapshots,
        deepface=deepface_stub, frame=np.full((32, 32, 3), 80, dtype=np.uint8),
    )


FACE_OK = {"status": "normal", "face_width": 0.30}


# ── no-face gate (DeepFace must NOT be reached) ──────────────────────────────

def test_no_face_returns_unknown_without_calling_deepface(fr):
    out = fr.fr.identify(fr.frame, {"status": "no_face", "face_width": 0.0})
    assert out["identified"] is False
    assert out["name"] is None
    assert out["confidence"] == 0.0
    assert out["reason"] == "no_face"
    assert fr.calls["find"] == 0


def test_too_small_face_returns_unknown_without_calling_deepface(fr):
    out = fr.fr.identify(fr.frame, {"status": "normal", "face_width": 0.01})
    assert out["reason"] == "too_small"
    assert out["identified"] is False
    assert fr.calls["find"] == 0


def test_face_just_above_min_size_does_reach_deepface(fr):
    out = fr.fr.identify(fr.frame, {"status": "normal", "face_width": 0.06})
    assert fr.calls["find"] == 1
    assert out["identified"] is True


# ── threshold / margin behaviour ──────────────────────────────────────────────

def test_close_single_candidate_is_identified(fr):
    out = fr.fr.identify(fr.frame, FACE_OK)
    assert out["identified"] is True
    assert out["name"] == "p0"


def test_far_candidate_is_unknown(fr):
    fr.calls["find_result"] = fr.make_rows([
        {"identity": str(fr.registered / "p0" / "01.jpg"), "distance": 0.95}])
    out = fr.fr.identify(fr.frame, FACE_OK)
    assert out["identified"] is False
    assert out["reason"] == "above_threshold"


def test_two_people_close_together_are_ambiguous_not_a_guess(fr):
    fr.calls["find_result"] = fr.make_rows([
        {"identity": str(fr.registered / "p0" / "01.jpg"), "distance": 0.20},
        {"identity": str(fr.registered / "p1" / "01.jpg"), "distance": 0.23},
    ])
    out = fr.fr.identify(fr.frame, FACE_OK)
    assert out["identified"] is False
    assert out["reason"] == "ambiguous"


def test_empty_deepface_result_is_unknown(fr):
    fr.calls["find_result"] = fr.make_rows([])
    out = fr.fr.identify(fr.frame, FACE_OK)
    assert out["identified"] is False
    assert out["reason"] == "no_candidates"


def test_deepface_raising_no_face_is_unknown_not_an_error(fr):
    def boom(**kw):
        fr.calls["find"] += 1
        raise ValueError("Face could not be detected in the given image.")
    fr.deepface.find = boom
    out = fr.fr.identify(fr.frame, FACE_OK)
    assert out["identified"] is False
    assert out["name"] is None
    assert out["reason"] == "no_face"


def test_detector_arguments_use_a_real_detector(fr):
    fr.fr.identify(fr.frame, FACE_OK)
    kw = fr.calls["find_kwargs"][0]
    assert kw["enforce_detection"] is True
    assert kw["detector_backend"] != "skip"
    assert kw["model_name"] == "Facenet"
    assert kw["distance_metric"] == "cosine"


# ── verify() ─────────────────────────────────────────────────────────────────

def test_verify_below_threshold_is_verified(fr):
    out = fr.fr.verify(fr.frame, "p0")
    assert out["verified"] is True
    assert out["reason"] == "matched"
    assert 0.0 < out["confidence"] <= 1.0
    assert out["distance"] == pytest.approx(0.10)


def test_verify_above_threshold_is_refused_even_when_the_library_says_yes(fr):
    """DeepFace returning verified=True must NOT override our threshold."""
    fr.calls["verify_result"] = {"verified": True, "distance": 0.90}
    out = fr.fr.verify(fr.frame, "p0")
    assert out["verified"] is False
    assert out["reason"] == "above_threshold"
    assert out["confidence"] == 0.0


def test_verify_of_an_unregistered_person_is_false(fr):
    out = fr.fr.verify(fr.frame, "nobody")
    assert out["verified"] is False
    assert fr.calls["verify"] == 0


def test_verify_uses_a_real_detector(fr):
    fr.fr.verify(fr.frame, "p0")
    kw = fr.calls["verify_kwargs"][0]
    assert kw["enforce_detection"] is True
    assert kw["detector_backend"] != "skip"


# ── camera path: smoothing, throttling, TTL ──────────────────────────────────

@pytest.fixture()
def cam(monkeypatch, tmp_path, fr):
    """routes.camera with analyze_frame + identify stubbed, no RTSP."""
    import routes.camera as camera

    face_calls = {"n": 0}

    def fake_identify(frame, face_info=None):
        face_calls["n"] += 1
        return fr.fr.identify(frame, face_info)

    monkeypatch.setattr(camera, "analyze_frame",
                        lambda f: {"status": "normal", "face_width": 0.30,
                                   "proximity": "close", "is_close": True,
                                   "ear": 0.3, "impaired": False,
                                   "left_ear": 0.3, "right_ear": 0.3})
    monkeypatch.setattr(camera, "_identify_gated", camera._identify_gated)
    monkeypatch.setattr(camera, "get_rtsp_frame", lambda: fr.frame)
    monkeypatch.setattr(camera, "identify", fake_identify)
    # reset the module-level smoother/lock state between tests
    camera._ident_state["smoother"] = camera.IdentitySmoother(consecutive=3)
    camera._ident_state["frame"] = 0
    camera._ident_state["last_ident_at"] = 0.0
    camera._ident_state["current"] = {"identified": False, "name": None,
                                      "confidence": 0.0, "reason": "no_face"}
    return camera, face_calls


def test_smoothed_identity_is_returned_and_raw_is_exposed(cam, fr):
    camera, calls = cam
    fake = fr.deepface
    fake.find = lambda **kw: fr.make_rows([
        {"identity": str(fr.registered / "p0" / "01.jpg"), "distance": 0.10}])
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(camera.router)
    app.dependency_overrides[camera.get_db] = lambda: None
    c = TestClient(app)

    # 3 consecutive agreeing frames -> identity becomes stable on the 3rd
    r = None
    for _ in range(3):
        camera._ident_state["last_ident_at"] = 0.0   # force the rate limiter open
        r = c.post("/camera/analyze").json()
    assert r["identified"] is True
    assert r["name"] == "p0"
    assert "identity_raw" in r
    assert r["identity_raw"]["identified"] is True


def test_stale_analysis_is_reported_as_unknown(cam, fr):
    camera, calls = cam
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(camera.router)
    app.dependency_overrides[camera.get_db] = lambda: None
    c = TestClient(app)

    import time as _time

    base = {"proximity": "close", "face_width": 0.3, "ear": 0.3, "status": "normal",
            "confidence": 0.9, "identified": True, "name": "p0", "reason": "matched",
            "ts": _time.time() - 99.0}
    stale = camera._with_freshness(dict(base))
    assert stale["identified"] is False
    assert stale["name"] is None
    assert stale["confidence"] == 0.0
    assert stale["reason"] == "stale"
    # every original key survives
    for k in ("proximity", "face_width", "ear", "status", "ts"):
        assert k in stale

    fresh = camera._with_freshness(dict(base, ts=_time.time()))
    assert fresh["identified"] is True and fresh["reason"] == "matched"


def test_identification_rate_is_bounded(cam, fr):
    camera, calls = cam
    fake = fr.deepface
    fake.find = lambda **kw: fr.make_rows([
        {"identity": str(fr.registered / "p0" / "01.jpg"), "distance": 0.10}])
    import time as _time
    ear = {"status": "normal", "face_width": 0.3}

    n0 = calls["n"]
    camera._ident_state["last_ident_at"] = _time.time()   # just ran
    for _ in range(20):
        camera._identify_gated(fr.frame, ear)
    assert calls["n"] == n0, "rate limit ignored: DeepFace called inside the interval"

    n1 = calls["n"]
    camera._ident_state["last_ident_at"] = _time.time() - 10.0   # long expired
    camera._identify_gated(fr.frame, ear)
    assert calls["n"] == n1 + 1, "a call was allowed after the interval expired"
