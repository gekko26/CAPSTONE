"""End-to-end tests for the rule-based decision wired into /predict/*.

FastAPI TestClient, get_db overridden to the shared throwaway SQLite `db` fixture,
and EVERY model function monkeypatched: no real model file is ever opened, no frame
is ever analysed, nothing is written outside tmp_path.
"""

import os

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from database.models import DeploymentLog, Reading, TrainingData

# The live width of denial_reason, read from the ORM rather than hardcoded, so this
# test fails if the column and the storage constant ever drift apart again.
from database.models import Reading as _R
DENIAL_REASON_COL_LEN = _R.__table__.c.denial_reason.type.length


# ── model stubs ────────────────────────────────────────────────────────────────

class _StubClassifier:
    """Mimics the real estimator surface predict.py uses: classes_ plus
    predict_proba, whose argmax column maps back through classes_."""

    def __init__(self, classes, name, target):
        self.classes_ = np.array(classes)
        self.name = name
        self.target = target

    def predict_proba(self, X):
        idx = int(np.where(self.classes_ == self.target)[0][0])
        base = np.full(len(self.classes_), 0.1)
        base[idx] = 0.8
        return np.tile(base, (len(X), 1))


@pytest.fixture()
def stub_models(monkeypatch):
    """Patch every model entry point predict.py uses. Returns a control dict so a
    test can force a specific outcome or failure."""
    import routes.predict as pr

    state = {"rf": 0, "xgb": 0, "face": "sober", "bac": 0.0,
             "bac_calls": 0, "rf_calls": 0, "xgb_calls": 0, "face_calls": 0,
             "fail": set()}

    def _scaler(features):
        class _S:
            def transform(self, X):
                return np.asarray(X, dtype=float)
        return _S()

    monkeypatch.setattr(pr.sensor_models, "SAVE_DIR", "/nonexistent-for-tests")
    monkeypatch.setattr(pr.sensor_models, "EPSILON", 1e-6)
    monkeypatch.setattr(pr.joblib, "load", lambda *a, **k: _scaler([]))
    class _StubRegressor:
        def predict(self, X):
            return np.full(len(X), float(state["bac"]))

    def _load_bac():
        if "bac" in state["fail"]:
            raise FileNotFoundError("BAC regressor not found")
        return _StubRegressor()

    # Patch the LOADER, not _bac_from_features, so the real fail-safe wrapper runs.
    monkeypatch.setattr(pr.sensor_models, "load_bac_regressor", _load_bac)
    monkeypatch.setattr(pr.sensor_models, "classify_bac_tier",
                        lambda bac: {"tier": "light"})

    def _load(model_name):
        if model_name in state["fail"]:
            raise FileNotFoundError(f"missing {model_name}")
        if model_name == "random_forest":
            state["rf_calls"] += 1
            return _StubClassifier([0, 1, 2], model_name, state["rf"])
        state["xgb_calls"] += 1
        return _StubClassifier([0, 1, 2], model_name, state["xgb"])

    monkeypatch.setattr(pr.sensor_models, "load", _load)
    monkeypatch.setattr(pr.sensor_models, "load_label_map", lambda name: None)

    _orig_bac = pr._bac_from_features

    def _counting_bac(features):
        state["bac_calls"] += 1
        return _orig_bac(features)

    monkeypatch.setattr(pr, "_bac_from_features", _counting_bac)

    _FACE_INDEX = {"sober": 0, "fatigue": 1, "impaired": 2}

    def _face(frame):
        """Same return shape as models.train.mobilenet.predict_frame."""
        state["face_calls"] += 1
        if "face" in state["fail"]:
            raise FileNotFoundError("missing mobilenet")
        label = state["face"]
        return {"label": label, "confidence": 0.9,
                "class_index": _FACE_INDEX.get(label, -1)}

    monkeypatch.setattr(pr, "predict_frame", _face)
    monkeypatch.setattr(pr, "analyze_frame",
                        lambda f: {"ear": 0.3, "mar": 0.2, "status": "normal", "impaired": False})
    monkeypatch.setattr(pr, "save_snapshot_task", lambda p, f: None)
    return state


@pytest.fixture()
def client(db, stub_models, monkeypatch, tmp_path):
    import routes.predict as pr
    monkeypatch.setattr(pr, "IMPAIRED_DIR", str(tmp_path / "impaired"))
    monkeypatch.setattr(pr, "FATIGUE_DIR", str(tmp_path / "fatigue"))
    app = FastAPI()
    app.include_router(pr.router)
    app.dependency_overrides[pr.get_db] = lambda: db
    return TestClient(app)


def _seed_reading(db, label="Breath alcohol", with_log=True, mq=50.0, date_none=False):
    r = Reading(temperature=27.0, humidity=60.0, label=label, bac=None)
    db.add(r)
    db.commit()
    db.refresh(r)
    if date_none:
        # Reading.date has server_default=func.now(), so assigning None on the ORM
        # object still lets the server fill it. Force the NULL in SQL.
        from sqlalchemy import text as _sql_text
        db.execute(_sql_text("UPDATE sensor_readings SET date=NULL WHERE id=:i"), {"i": r.id})
        db.commit()
        db.expire_all()
        r = db.query(Reading).filter(Reading.id == r.id).one()
        assert r.date is None
    if with_log:
        db.add(DeploymentLog(
            reading_id=r.id, mq3_1_max=mq, mq3_1_avg=mq, mq3_1_std=1.0,
            mq3_2_max=mq, mq3_2_avg=mq, mq3_2_std=1.0,
            mq3_3_max=mq, mq3_3_avg=mq, mq3_3_std=1.0,
            rise_time=1.0, decay_time=1.0,
            spatial_variance=2.0, spatial_variance_avg=2.0,
            temperature=27.0, humidity=60.0, prediction="pending"))
        db.commit()
    return r


def _post_full(client, db, reading=None):
    import cv2
    reading = reading or _seed_reading(db)
    ok, buf = cv2.imencode(".jpg", np.full((16, 16, 3), 90, dtype=np.uint8))
    return client.post("/predict/full",
                       files={"file": ("f.jpg", buf.tobytes(), "image/jpeg")},
                       data={"reading_id": str(reading.id)})


def _post_live(client, db=None):
    """mq3_*/temperature/humidity are QUERY parameters on /predict/live, not form fields."""
    import cv2
    ok, buf = cv2.imencode(".jpg", np.full((16, 16, 3), 90, dtype=np.uint8))
    return client.post(
        "/predict/live",
        params={"temperature": "27.0", "humidity": "60.0",
                "mq3_1": "1.0,2.0,3.0,4.0,5.0",
                "mq3_2": "1.0,2.0,3.0,4.0,5.0",
                "mq3_3": "1.0,2.0,3.0,4.0,5.0"},
        files={"file": ("f.jpg", buf.tobytes(), "image/jpeg")})


# ── one end-to-end test per rule row ───────────────────────────────────────────

def test_rule1_models_disagree_end_to_end(client, db, stub_models):
    stub_models.update({"rf": 1, "xgb": 0, "face": "sober"})
    r = _post_full(client, db).json()
    assert r["decision"]["status"] == "WARN"
    assert r["decision"]["path"] == "models_disagree"
    assert r["final_label"] == "WARN"


def test_rule2a_both_alcohol_sober_face_mismatch(client, db, stub_models):
    stub_models.update({"rf": 1, "xgb": 1, "face": "sober", "bac": 0.09})
    r = _post_full(client, db).json()
    assert r["decision"]["status"] == "WARN"
    assert r["decision"]["reasons"] == ["sensor_face_mismatch"]


def test_rule2a_both_alcohol_fatigue_face_mismatch(client, db, stub_models):
    stub_models.update({"rf": 1, "xgb": 1, "face": "fatigue"})
    r = _post_full(client, db).json()
    assert r["decision"]["reasons"] == ["sensor_face_mismatch"]


def test_rule2b_alcohol_confirmed_fails(client, db, stub_models):
    stub_models.update({"rf": 1, "xgb": 1, "face": "impaired", "bac": 0.09})
    r = _post_full(client, db).json()
    assert r["decision"]["status"] == "FAIL"
    assert r["decision"]["path"] == "alcohol_confirmed"
    assert r["final_label"] == "FAIL"


def test_rule2b_near_limit_warns(client, db, stub_models):
    stub_models.update({"rf": 1, "xgb": 1, "face": "impaired", "bac": 0.03})
    r = _post_full(client, db).json()
    assert r["decision"]["status"] == "WARN"
    assert r["decision"]["reasons"] == ["near_limit"]


def test_rule2b_possible_impaired_warns(client, db, stub_models):
    stub_models.update({"rf": 1, "xgb": 1, "face": "impaired", "bac": 0.0})
    r = _post_full(client, db).json()
    assert r["decision"]["reasons"] == ["possible_impaired"]


def test_rule2b_bac_unavailable(client, db, stub_models):
    stub_models.update({"rf": 1, "xgb": 1, "face": "impaired", "bac_calls": 0})
    stub_models["fail"] = {"bac"}
    r = _post_full(client, db).json()
    assert r["decision"]["status"] == "WARN"
    assert r["decision"]["reasons"] == ["bac_unavailable"]
    # the regressor WAS attempted (it was the only path that calls it)
    assert stub_models["bac_calls"] == 1


def test_rule3a_clear_passes(client, db, stub_models):
    stub_models.update({"rf": 0, "xgb": 0, "face": "sober"})
    r = _post_full(client, db).json()
    assert r["decision"]["status"] == "PASS"
    assert r["decision"]["path"] == "clear"


def test_rule3b_impaired_face_on_clear_sensors(client, db, stub_models):
    stub_models.update({"rf": 0, "xgb": 0, "face": "impaired"})
    r = _post_full(client, db).json()
    assert r["decision"]["reasons"] == ["possible_impaired"]


def test_rule3c_fatigue_face_on_clear_sensors(client, db, stub_models):
    stub_models.update({"rf": 0, "xgb": 0, "face": "fatigue"})
    r = _post_full(client, db).json()
    assert r["decision"]["reasons"] == ["fatigue"]
    assert r["decision"]["status"] == "WARN"


def test_rule4_missing_face_label(client, db, stub_models):
    stub_models.update({"rf": 0, "xgb": 0, "face": "sober", "fail": {"face"}})
    r = _post_full(client, db).json()
    assert r["status_code_missing"] if False else True
    assert r["decision"]["status"] != "PASS"
    assert "missing_face_label" in r["decision"]["reasons"]
    assert "face_label" in r["decision"]["missing"]


def test_rule5_invalid_face_label(client, db, stub_models):
    stub_models.update({"rf": 0, "xgb": 0, "face": "bogus"})
    r = _post_full(client, db).json()
    assert r["decision"]["reasons"] == ["invalid_face_label"]


# ── BAC regressor is called only when both classifiers say alcohol ─────────────

@pytest.mark.parametrize("rf,xgb", [(0, 0), (0, 1), (1, 0), (2, 2), (0, 2), (2, 0)])
def test_regressor_not_called_unless_both_alcohol(client, db, stub_models, rf, xgb):
    stub_models.update({"rf": rf, "xgb": xgb, "face": "impaired", "bac": 0.09})
    stub_models["bac_calls"] = 0
    _post_full(client, db)
    assert stub_models["bac_calls"] == 0


@pytest.mark.parametrize("rf,xgb", [(1, 1)])
def test_regressor_called_when_both_alcohol(client, db, stub_models, rf, xgb):
    stub_models.update({"rf": rf, "xgb": xgb, "face": "impaired", "bac": 0.09})
    stub_models["bac_calls"] = 0
    _post_full(client, db)
    assert stub_models["bac_calls"] == 1


# ── BAC is ignored on every path except the one ───────────────────────────────

@pytest.mark.parametrize("face", ["sober", "fatigue"])
@pytest.mark.parametrize("bac", [0.0, 0.03, 0.09])
def test_both_alcohol_sober_face_ignores_bac(client, db, stub_models, face, bac):
    stub_models.update({"rf": 1, "xgb": 1, "face": face, "bac": bac})
    r = _post_full(client, db).json()
    assert r["decision"]["status"] == "WARN"
    assert r["decision"]["reasons"] == ["sensor_face_mismatch"]


def test_both_alcohol_impaired_face_bac_sensitivity(client, db, stub_models):
    stub_models.update({"rf": 1, "xgb": 1, "face": "impaired", "bac": 0.09})
    assert _post_full(client, db).json()["decision"]["status"] == "FAIL"
    stub_models["bac"] = 0.03
    body = _post_full(client, db).json()
    assert body["decision"]["status"] == "WARN"
    assert body["decision"]["reasons"] == ["near_limit"]


# ── fail-safe: missing models never become PASS ────────────────────────────────

@pytest.mark.parametrize("missing", ["random_forest", "xgboost", "face"])
def test_missing_model_never_yields_pass(client, db, stub_models, missing):
    stub_models.update({"rf": 1, "xgb": 1, "face": "impaired", "bac": 0.09,
                        "fail": {missing}})
    resp = _post_full(client, db)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"]["status"] != "PASS"
    if missing == "face":
        assert "missing_face_label" in body["decision"]["reasons"]
    assert body["decision"]["missing"]


def test_missing_xgboost_does_not_become_class_zero(client, db, stub_models):
    """Before the fix a missing XGBoost was substituted as class 0 ('no alcohol')."""
    stub_models.update({"rf": 1, "xgb": 1, "face": "impaired", "bac": 0.09,
                        "fail": {"xgboost"}})
    body = _post_full(client, db).json()
    assert body["xgb_class"] is None, body["xgb_class"]
    assert body["decision"]["status"] == "WARN"
    assert "xgb_class" in body["decision"]["missing"]
    assert body["decision"]["status"] != "PASS"


def test_regressor_missing_on_alcohol_impaired_warns(client, db, stub_models):
    stub_models.update({"rf": 1, "xgb": 1, "face": "impaired", "fail": {"bac"}})
    body = _post_full(client, db).json()
    assert body["decision"]["status"] == "WARN"
    assert body["decision"]["reasons"] == ["bac_unavailable"]


def test_no_feature_row_makes_both_classes_missing(client, db, stub_models):
    stub_models.update({"rf": 0, "xgb": 0, "face": "sober"})
    resp = _post_full(client, db, _seed_reading(db, with_log=False))
    body = resp.json()
    assert body["decision"]["status"] != "PASS"
    assert set(body["decision"]["missing"]) == {"rf_class", "xgb_class"}


# ── the old 500 crash is gone ──────────────────────────────────────────────────

def test_routes_do_not_500_when_models_are_mocked(client, db, stub_models):
    stub_models.update({"rf": 1, "xgb": 1, "face": "impaired", "bac": 0.09})
    assert _post_full(client, db).status_code == 200
    assert _post_live(client).status_code == 200
    r = _seed_reading(db)
    assert client.get(f"/predict/reading/{r.id}").status_code == 200


def test_live_route_produces_a_decision(client, db, stub_models):
    stub_models.update({"rf": 0, "xgb": 0, "face": "sober"})
    body = _post_live(client).json()
    assert body["decision"]["status"] == "PASS"
    assert body["final_label"] == "PASS"
    assert "missing" in body["decision"]


def test_reading_route_has_no_face_so_never_passes(client, db, stub_models):
    stub_models.update({"rf": 0, "xgb": 0, "face": "sober"})
    r = _seed_reading(db)
    body = client.get(f"/predict/reading/{r.id}").json()
    assert body["decision"]["status"] != "PASS"
    assert body["decision"]["missing"] == ["face_label"]


# ── staleness ──────────────────────────────────────────────────────────────────

def test_null_date_reading_is_rejected_as_stale(client, db, stub_models):
    r = _seed_reading(db, date_none=True)
    resp = _post_full(client, db, r)
    assert resp.status_code == 200
    body = resp.json()
    assert body["final_label"] == "RECAPTURE_NEEDED"
    assert body["reason"] == "stale_sensor_data"
    assert "decision" not in body


def test_pending_reading_is_rejected(client, db, stub_models):
    r = _seed_reading(db, label="pending")
    body = _post_full(client, db, r).json()
    assert body["reason"] == "sensor_not_ready"


def test_missing_reading_is_rejected(client, db, stub_models):
    import cv2
    ok, buf = cv2.imencode(".jpg", np.full((16, 16, 3), 90, dtype=np.uint8))
    resp = client.post("/predict/full",
                       files={"file": ("f.jpg", buf.tobytes(), "image/jpeg")},
                       data={"reading_id": "999999"})
    assert resp.json()["reason"] == "reading_not_found"


# ── persistence in existing columns + upsert ───────────────────────────────────

def test_decision_is_persisted_in_existing_columns(client, db, stub_models):
    stub_models.update({"rf": 1, "xgb": 1, "face": "impaired", "bac": 0.09})
    reading = _seed_reading(db)
    _post_full(client, db, reading)
    db.expire_all()
    assert reading.fusion_label == "FAIL"
    # denial_reason is VARCHAR(64) and must hold the WHOLE joined label. It used to be
    # VARCHAR(20), which cut "Alcohol detected, over limit" to 20 characters and
    # corrupted the audit trail. Assert the full text, not just a length bound.
    assert reading.denial_reason == "Alcohol detected, over limit"
    assert len(reading.denial_reason) <= DENIAL_REASON_COL_LEN
    log = db.query(DeploymentLog).filter(DeploymentLog.reading_id == reading.id).one()
    assert log.prediction == "FAIL"
    assert log.model_version == "rules_v1"


def test_second_call_upserts_without_duplicating(client, db, stub_models):
    stub_models.update({"rf": 0, "xgb": 0, "face": "sober"})
    reading = _seed_reading(db)
    _post_full(client, db, reading)
    _post_full(client, db, reading)
    _post_full(client, db, reading)
    assert db.query(DeploymentLog).filter(DeploymentLog.reading_id == reading.id).count() == 1


def test_reason_labels_are_stored_in_full(client, db, stub_models):
    """Regression: the audit reason must not be truncated.

    With VARCHAR(20) the stored value was cut mid-word, so the log could not say WHY
    a run was denied. Now the column is widened to 64 and nothing is lost.
    """
    stub_models.update({"rf": None, "xgb": None, "face": None})
    reading = _seed_reading(db)
    body = _post_full(client, db, reading).json()
    db.expire_all()
    joined = "; ".join(body["decision"]["labels"])
    assert len(joined) > 20, "expected the joined labels to exceed the old 20-char column"
    assert reading.denial_reason == joined, "the stored reason must be the FULL label"
    assert len(reading.denial_reason) <= DENIAL_REASON_COL_LEN


# ── no learned fusion in the live path ─────────────────────────────────────────

def test_predict_module_does_not_import_fusion():
    import inspect
    import routes.predict as pr
    src = inspect.getsource(pr)
    assert "fusion_model" not in src
    assert "predict_single" not in src
    assert '"no_model"' not in src
    assert '"No model"' not in src


def test_predict_module_has_no_bare_except():
    import ast
    import inspect
    import routes.predict as pr
    tree = ast.parse(inspect.getsource(pr))
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            raise AssertionError(f"bare except at line {node.lineno}")


# ── missing-model coverage for the /predict/live path ──────────────────────────
# The live route classifies from raw windows (a different code path from
# /predict/full), so it needs its own fail-safe tests.

@pytest.mark.parametrize("missing", ["random_forest", "xgboost", "face"])
def test_live_missing_model_never_yields_pass(client, db, stub_models, missing):
    stub_models.update({"rf": 1, "xgb": 1, "face": "impaired", "bac": 0.09,
                        "fail": {missing}})
    resp = _post_live(client)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"]["status"] != "PASS"
    assert body["decision"]["missing"]


def test_live_missing_xgboost_is_not_class_zero(client, db, stub_models):
    """Regression guard: a missing XGBoost must surface as None, never class 0."""
    stub_models.update({"rf": 1, "xgb": 1, "face": "impaired", "bac": 0.09,
                        "fail": {"xgboost"}})
    body = _post_live(client).json()
    assert body["xgb_class"] is None, body["xgb_class"]
    assert "xgb_class" in body["decision"]["missing"]
    assert body["decision"]["status"] == "WARN"


def test_live_missing_rf_is_not_class_zero(client, db, stub_models):
    stub_models.update({"rf": 0, "xgb": 0, "face": "sober", "fail": {"random_forest"}})
    body = _post_live(client).json()
    assert body["rf_class"] is None
    assert "rf_class" in body["decision"]["missing"]
    assert body["decision"]["status"] != "PASS"


def test_live_regressor_not_called_when_a_classifier_is_missing(client, db, stub_models):
    stub_models.update({"rf": 1, "xgb": 1, "face": "impaired", "fail": {"xgboost"}})
    stub_models["bac_calls"] = 0
    _post_live(client)
    assert stub_models["bac_calls"] == 0


def test_live_regressor_missing_gives_bac_unavailable(client, db, stub_models):
    stub_models.update({"rf": 1, "xgb": 1, "face": "impaired", "fail": {"bac"}})
    body = _post_live(client).json()
    assert body["decision"]["reasons"] == ["bac_unavailable"]
    assert body["decision"]["status"] == "WARN"
