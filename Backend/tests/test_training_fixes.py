"""
Regression tests for the training-side fixes confirmed by the architecture audit.

A2  - sensor_models.train() fit every model in MODELS; scaler saved only after success
H1  - class_indices pinned to ["sober","fatigue","impaired"]; registered/ excluded
H2  - training gated on per-class image counts
5   - MobileNet metrics report the final epoch, not max(val_accs)

All fixtures are synthetic and live in tmp_path. No database, no network, no real
model training.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.train import mobilenet, sensor_models  # noqa: E402


# ----------------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------------

def _tiny_models():
    """Small/fast stand-ins for MODELS so the test does not spend real compute."""
    from sklearn.ensemble import RandomForestClassifier
    from xgboost import XGBClassifier
    return {
        "random_forest": RandomForestClassifier(n_estimators=10, random_state=42),
        "xgboost": XGBClassifier(n_estimators=10, random_state=42, eval_metric="mlogloss"),
    }


def _synthetic_sensor_set(n=60, seed=0):
    """18-feature rows, 2 classes, learnable — mirrors the real extract_features width."""
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 18))
    y = np.array([0 if i < n // 2 else 1 for i in range(n)])
    # give the model something real to fit on
    X[:n // 2, 0] += 2.0
    X[n // 2:, 0] -= 2.0
    return X, y


def _write_tree(root, counts, extra_dirs=()):
    """Create <root>/<class>/*.jpg plus any extra dirs (e.g. registered/)."""
    import cv2
    for cls, n in counts.items():
        d = os.path.join(root, cls)
        os.makedirs(d, exist_ok=True)
        for i in range(n):
            arr = np.full((32, 32, 3), (i * 7) % 255, dtype=np.uint8)
            cv2.imwrite(os.path.join(d, f"{cls}_{i}.jpg"), arr)
    for extra in extra_dirs:
        os.makedirs(os.path.join(root, extra), exist_ok=True)


# ----------------------------------------------------------------------------------
# A2 — sensor_models.train()
# ----------------------------------------------------------------------------------

def test_a2_sensor_train_writes_all_three_artifacts(tmp_path, monkeypatch):
    """A2 regression: must produce random_forest.pkl, xgboost.pkl AND scaler.pkl.

    Before the fix this raised UnboundLocalError on `accuracy` at the first loop
    iteration, so no artifact of any kind was produced.
    """
    monkeypatch.setattr(sensor_models, "SAVE_DIR", str(tmp_path))
    monkeypatch.setattr(sensor_models, "MODELS", _tiny_models())
    # keep metrics_store from writing into the repo
    monkeypatch.setattr(
        "models.train.metrics_store.save_metrics",
        lambda section, data: None,
        raising=True,
    )

    X, y = _synthetic_sensor_set()
    results = sensor_models.train(X, y)

    assert "error" not in results, f"train() refused: {results}"
    assert set(results) == {"random_forest", "xgboost"}, results.keys()
    for name in ("random_forest.pkl", "xgboost.pkl", "scaler.pkl"):
        assert (tmp_path / name).exists(), f"{name} was not written"
    for name in ("random_forest", "xgboost"):
        assert "accuracy" in results[name]
        assert 0.0 <= results[name]["accuracy"] <= 100.0


def test_a2_no_orphan_scaler_when_no_model_trains(tmp_path, monkeypatch):
    """A2: scaler must not be written unless at least one model actually trained."""
    monkeypatch.setattr(sensor_models, "SAVE_DIR", str(tmp_path))
    monkeypatch.setattr(sensor_models, "MODELS", {})
    monkeypatch.setattr(
        "models.train.metrics_store.save_metrics",
        lambda section, data: None,
        raising=True,
    )

    X, y = _synthetic_sensor_set()
    results = sensor_models.train(X, y)

    assert "error" in results, "empty MODELS should not report success"
    assert not (tmp_path / "scaler.pkl").exists(), "orphan scaler.pkl was written"


def test_a2_single_class_guard_still_works(tmp_path, monkeypatch):
    """The pre-existing single-class guard must be preserved."""
    monkeypatch.setattr(sensor_models, "SAVE_DIR", str(tmp_path))
    X, y = _synthetic_sensor_set(n=20)
    results = sensor_models.train(X, np.zeros_like(y))

    assert "error" in results
    assert "at least 2 distinct classes" in results["error"]
    assert not (tmp_path / "scaler.pkl").exists()


# ------------------------------------------------------------------------------
# helpers: a throwaway SQLite file (tmp_path) + real ORM rows
# ------------------------------------------------------------------------------

# The shared `db` fixture now lives in tests/conftest.py so every module uses it.
import pytest
from sqlalchemy import text

from database.models import Subject, TrainingData


def _seed_subject(session, *ids):
    """training_data.subject_id has a real FK to subjects(id) in MariaDB
    (fk_training_data_subject), so every subject_id used in a test needs a
    backing subjects row."""
    for sid in ids:
        session.add(Subject(name=f"Person {sid}", group="Student"))
        session.flush()
        session.execute(
            text("UPDATE subjects SET id = :i WHERE name = :n"),
            {"i": sid, "n": f"Person {sid}"})
    session.commit()


def _jpeg(tmp_path, name, value=120):
    import cv2
    path = str(tmp_path / name)
    ok, buf = cv2.imencode(".jpg", np.full((16, 16, 3), value, dtype=np.uint8))
    assert ok
    path_buf = open(path, "wb")
    path_buf.write(buf.tobytes())
    path_buf.close()
    return path


class _BG:
    def add_task(self, *a, **k):
        pass


class _Upload:
    def __init__(self, data):
        self._d = data

    async def read(self):
        return self._d


def _upload(tmp_path, name="up.jpg"):
    import cv2
    ok, buf = cv2.imencode(".jpg", np.full((16, 16, 3), 90, dtype=np.uint8))
    return _Upload(buf.tobytes())


def _quiet_collect(monkeypatch, tmp_path):
    """Neutralize the network + vision side effects of the collect endpoints."""
    import routes.training as tr
    import cv2

    ok, buf = cv2.imencode(".jpg", np.full((16, 16, 3), 90, dtype=np.uint8))

    async def _fake_trigger(row_id=None):
        return {"triggered": True, "status": 200, "row_id": row_id}
    monkeypatch.setattr(tr, "trigger_esp32_async", _fake_trigger)
    monkeypatch.setattr(tr, "analyze_frame", lambda f: {"ear": 0.3, "mar": 0.2, "pitch": 1.0})
    monkeypatch.setattr(tr, "BASE_DIR", str(tmp_path))
    return tr, buf.tobytes()


# ------------------------------------------------------------------------------
# item 5 — H3: collect_alcohol mirrors into data/faces/impaired/
# ------------------------------------------------------------------------------

def test_collect_alcohol_writes_to_faces_impaired(db, tmp_path, monkeypatch):
    """The impaired class must reach data/faces/impaired/, the only root the
    MobileNet trainer globs."""
    import asyncio
    tr, png = _quiet_collect(monkeypatch, tmp_path)

    _seed_subject(db, 3)
    res = asyncio.run(tr.collect_alcohol(
        background_tasks=_BG(), file=_Upload(png), bac=0.07, subject_id=3, db=db))

    faces = tmp_path / "data" / "faces" / "impaired"
    mirrored = sorted(faces.glob("*.jpg"))
    assert mirrored, f"H3 not fixed: nothing written to {faces}"
    assert mirrored[0].name.startswith(f"impaired_{res['id']}")
    assert res["subject_id"] == 3


def test_collect_perfume_does_not_write_to_faces_impaired(db, tmp_path, monkeypatch):
    """Perfume is not an impairment class — it must not pollute the impaired tree."""
    import asyncio
    tr, png = _quiet_collect(monkeypatch, tmp_path)

    _seed_subject(db, 2)
    asyncio.run(tr.collect_perfume(
        background_tasks=_BG(), file=_Upload(png), subject_id=2, db=db))

    faces = tmp_path / "data" / "faces" / "impaired"
    assert not faces.exists() or not list(faces.glob("*.jpg"))


# ------------------------------------------------------------------------------
# item 6 — H8: subject_id accepted on collect + survives window expansion
# ------------------------------------------------------------------------------

def test_subject_id_accepted_on_every_collect_endpoint(db, tmp_path, monkeypatch):
    import asyncio
    import inspect
    tr, png = _quiet_collect(monkeypatch, tmp_path)

    endpoints = [
        (tr.collect_clear_air, dict(subject_id=11)),
        (tr.collect_sanitizer, dict(subject_id=12)),
        (tr.collect_sober, dict(background_tasks=_BG(), file=_Upload(png), subject_id=13)),
        (tr.collect_drowsy, dict(background_tasks=_BG(), file=_Upload(png), subject_id=14)),
        (tr.collect_yawning, dict(background_tasks=_BG(), file=_Upload(png), subject_id=15)),
        (tr.collect_perfume, dict(background_tasks=_BG(), file=_Upload(png), subject_id=16)),
        (tr.collect_alcohol, dict(background_tasks=_BG(), file=_Upload(png), bac=0.09, subject_id=17)),
    ]
    _seed_subject(db, 11, 12, 13, 14, 15, 16, 17)
    for fn, kwargs in endpoints:
        sig = inspect.signature(fn)
        assert "subject_id" in sig.parameters, f"{fn.__name__} has no subject_id parameter"
        asyncio.run(fn(db=db, **kwargs))

    rows = db.query(TrainingData).order_by(TrainingData.id).all()
    assert len(rows) == 7
    assert sorted(r.subject_id for r in rows) == [11, 12, 13, 14, 15, 16, 17]


def test_collect_rejects_negative_subject_id(db, tmp_path, monkeypatch):
    import asyncio
    import pytest as _pytest
    from fastapi import HTTPException
    tr, png = _quiet_collect(monkeypatch, tmp_path)

    with _pytest.raises(HTTPException) as e:
        asyncio.run(tr.collect_clear_air(subject_id=-1, db=db))
    assert e.value.status_code == 400


def test_subject_id_survives_window_expansion(db, tmp_path, monkeypatch):
    """The window-expansion copy must carry subject_id (plus ear/mar) from the parent."""
    import routes.training as tr
    from models.train import sensor_models

    _seed_subject(db, 5)
    parent = TrainingData(
        label=1, sub_label=None, bac=0.07, trial_id=42, subject_id=5,
        image_path=_jpeg(tmp_path, "parent.jpg"),
        ear=0.31, mar=0.22, head_pitch=3.0,
        mq3_1_max=50.0, mq3_1_avg=40.0, mq3_1_std=2.0,
        mq3_2_max=51.0, mq3_2_avg=41.0, mq3_2_std=2.0,
        mq3_3_max=52.0, mq3_3_avg=42.0, mq3_3_std=2.0,
        rise_time=1.0, decay_time=1.0,
        spatial_variance=2.0, spatial_variance_avg=2.0,
        temperature=27.0, humidity=60.0,
    )
    db.add(parent)
    db.commit()
    db.refresh(parent)

    monkeypatch.setattr(sensor_models, "extract_features",
                        lambda *a, **k: [float(i) + 0.5 for i in range(18)])

    payload = tr.SensorPayload(temperature=27.0, humidity=60.0,
                               mq3_1=[1.0], mq3_2=[1.0], mq3_3=[1.0], row_id=parent.id)
    res = tr.receive_sensor_data(payload, db)

    assert res["rows_created"] >= 1
    rows = db.query(TrainingData).order_by(TrainingData.id).all()
    assert len(rows) >= 2, "expansion should have created an extra row"
    expanded = rows[-1]
    assert expanded.subject_id == 5, f"subject_id lost in expansion: {expanded.subject_id}"
    assert expanded.trial_id == 42
    assert expanded.bac == 0.07
    assert expanded.label == 1


# ------------------------------------------------------------------------------
# item 4 — every non-alcohol endpoint stores bac = 0.0, never NULL
# ------------------------------------------------------------------------------

def test_all_non_alcohol_collect_endpoints_store_bac_zero(db, tmp_path, monkeypatch):
    """sober / drowsy / yawning / perfume / clear_air / sanitizer must all store
    bac = 0.0 rather than NULL, so the BAC regressor always has a value."""
    import asyncio
    tr, png = _quiet_collect(monkeypatch, tmp_path)
    _seed_subject(db, 1, 2)

    asyncio.run(tr.collect_clear_air(subject_id=1, db=db))
    asyncio.run(tr.collect_sanitizer(subject_id=2, db=db))
    for fn in (tr.collect_sober, tr.collect_drowsy, tr.collect_yawning, tr.collect_perfume):
        asyncio.run(fn(background_tasks=_BG(), file=_Upload(png), subject_id=None, db=db))

    rows = db.query(TrainingData).order_by(TrainingData.id).all()
    assert len(rows) == 6
    for r in rows:
        assert r.bac is not None, f"bac is NULL for sub_label={r.sub_label}"
        assert float(r.bac) == 0.0, f"bac != 0.0 for sub_label={r.sub_label}: {r.bac}"
    subs = sorted(r.sub_label or "" for r in rows)
    assert subs == ["", "clear_air", "drowsy", "perfume", "sanitizer", "yawning"]


# ------------------------------------------------------------------------------
# item 7 — H13: subject_map resolves snapshots/ paths onto the faces/ tree
# ------------------------------------------------------------------------------

def test_h13_subject_map_resolves_faces_paths(db, tmp_path, monkeypatch):
    """The DB stores image_path under data/snapshots/<cls>/ but the trainer globs
    data/faces/<cls>/. The map must key the faces/ sibling so the globbed file
    resolves to a real subject instead of 'unknown_<basename>'."""
    from models.train import mobilenet
    from database.db import SessionLocal

    # 3 distinct subjects: _collect_subject_split needs >=3 to engage at all
    faces_dir = tmp_path / "data" / "faces" / "sober"
    faces_dir.mkdir(parents=True)
    _seed_subject(db, 5, 6, 7)
    rows = []
    for sid in (5, 6, 7):
        (faces_dir / f"s{sid}_1.jpg").write_bytes(b"\xff\xd8\xff\xd9")
        snap = str(tmp_path / "data" / "snapshots" / "sober" / f"s{sid}_1.jpg")
        rows.append(TrainingData(label=0, bac=0.0, trial_id=sid, subject_id=sid,
                                 image_path=snap, mq3_1_max=1.0))
    for r in rows:
        db.add(r)
    db.commit()
    real_file = faces_dir / "s5_1.jpg"

    monkeypatch.setattr(mobilenet, "DATA_DIR", str(tmp_path / "data" / "faces"))
    monkeypatch.setattr("database.db.SessionLocal", lambda: db)

    seen = {}
    orig = mobilenet._collect_subject_split

    # exercise the map by capturing what the globber resolves
    import glob
    matched = None
    for fp in glob.glob(os.path.join(str(mobilenet.DATA_DIR), "sober", "*")):
        if os.path.basename(fp) == "s5_1.jpg":
            matched = fp
    assert matched is not None

    # The snapshot paths live in data/snapshots/, the trainer globs data/faces/.
    # If the map does not translate, every file falls back to "unknown_<basename>"
    # and the subject split is skipped entirely.
    train_t, val_t = orig()
    assert train_t and val_t, "subject split did not engage"
    subjects = {s for _, _, s in train_t + val_t}
    assert subjects == {"subject_5", "subject_6", "subject_7"}, subjects

# ------------------------------------------------------------------------------
# item 1 — grouping key: subject_id when universal, else trial_id, never mixed
# ------------------------------------------------------------------------------

def test_resolve_group_by_prefers_subject_id_when_universal():
    from routes.training import resolve_group_by

    class R:
        def __init__(self, sid, tid):
            self.subject_id = sid
            self.trial_id = tid
            self.id = tid

    rows = [R(1, 10), R(1, 11), R(2, 20), R(2, 21)]
    name, key = resolve_group_by(rows)
    assert name == "subject_id"
    assert [key(r) for r in rows] == [1, 1, 2, 2]


def test_resolve_group_by_falls_back_to_trial_id():
    from routes.training import resolve_group_by

    class R:
        def __init__(self, sid, tid, rid):
            self.subject_id = sid
            self.trial_id = tid
            self.id = rid

    # one row without subject_id -> must NOT use subject_id, and must not mix
    rows = [R(1, 10, 10), R(None, 11, 11), R(2, 20, 20)]
    name, key = resolve_group_by(rows)
    assert name == "trial_id"
    assert [key(r) for r in rows] == [10, 11, 20]


def test_resolve_group_by_legacy_rows_fall_back_to_row_id():
    from routes.training import resolve_group_by

    class R:
        subject_id = None
        trial_id = None

        def __init__(self, rid):
            self.id = rid

    name, key = resolve_group_by([R(7), R(8)])
    assert name == "trial_id"
    assert [key(r) for r in [R(7), R(8)]] == [7, 8]


def test_trigger_training_reports_group_by_subject_id(db, tmp_path, monkeypatch):
    import routes.training as tr
    from models.train import sensor_models

    _seed_subject(db, 1, 2, 3)
    rows = []
    for i in range(12):
        r = TrainingData(label=i % 2, sub_label=None, bac=0.0 if i % 2 == 0 else 0.05,
                         trial_id=100 + i, subject_id=(i % 3) + 1,
                         mq3_1_max=10.0 + i, mq3_1_avg=9.0, mq3_1_std=0.5,
                         mq3_2_max=11.0, mq3_2_avg=10.0, mq3_2_std=0.5,
                         mq3_3_max=12.0, mq3_3_avg=11.0, mq3_3_std=0.5,
                         rise_time=1.0, decay_time=1.0,
                         spatial_variance=2.0, spatial_variance_avg=2.0,
                         temperature=27.0, humidity=60.0)
        db.add(r)
        rows.append(r)
    db.commit()

    seen = {}

    def _fake_train(X, y, groups=None):
        seen["groups"] = list(groups)
        return {"random_forest": {"accuracy": 90.0}, "xgboost": {"accuracy": 90.0}}

    monkeypatch.setattr(sensor_models, "train", _fake_train)
    monkeypatch.setattr(sensor_models, "train_bac", lambda *a, **k: {"accuracy": 1.0})

    out = tr.trigger_training(db)
    assert out["group_by"] == "subject_id"
    # 12 rows, 3 subjects -> each subject appears 4x, not once per row
    assert sorted(seen["groups"]) == [1] * 4 + [2] * 4 + [3] * 4


def test_trigger_training_reports_group_by_trial_id_when_any_row_lacks_subject(db, tmp_path, monkeypatch):
    import routes.training as tr
    from models.train import sensor_models

    _seed_subject(db, 1, 2, 3)
    for i in range(12):
        db.add(TrainingData(
            label=i % 2, sub_label=None, bac=0.0, trial_id=100 + i,
            subject_id=None if i == 3 else (i % 3) + 1,
            mq3_1_max=10.0 + i, mq3_1_avg=9.0, mq3_1_std=0.5,
            mq3_2_max=11.0, mq3_2_avg=10.0, mq3_2_std=0.5,
            mq3_3_max=12.0, mq3_3_avg=11.0, mq3_3_std=0.5,
            rise_time=1.0, decay_time=1.0,
            spatial_variance=2.0, spatial_variance_avg=2.0,
            temperature=27.0, humidity=60.0))
    db.commit()

    seen = {}
    monkeypatch.setattr(sensor_models, "train",
                        lambda X, y, groups=None: (seen.update(groups=list(groups)),
                                                   {"random_forest": {"accuracy": 90.0}})[1])
    monkeypatch.setattr(sensor_models, "train_bac", lambda *a, **k: {"accuracy": 1.0})

    out = tr.trigger_training(db)
    assert out["group_by"] == "trial_id"
    assert sorted(seen["groups"]) == sorted(100 + i for i in range(12))


# ------------------------------------------------------------------------------
# item 4 — metrics consumer tolerates error entries
# ------------------------------------------------------------------------------

def test_describe_flags_error_entry_without_accuracy():
    from models.train.metrics_store import describe, is_failed

    ok, payload = describe({"error": "ValueError: boom", "samples": 12})
    assert ok is False
    assert "boom" in payload
    assert is_failed({"error": "x"}) is True

    ok, payload = describe({"accuracy": 91.2, "precision": 90})
    assert ok is True
    assert payload["accuracy"] == 91.2
    assert is_failed({"accuracy": 91.2}) is False

    # missing / None / non-dict
    assert describe(None)[0] is False
    assert describe({"accuracy": None})[0] is False
    assert describe("nope")[0] is False


# ------------------------------------------------------------------------------
# item 5 — bodyless POST to clear_air / sanitizer through the real ASGI stack
# ------------------------------------------------------------------------------

def test_bodyless_post_clear_air_and_sanitizer(db, tmp_path, monkeypatch):
    """Training.jsx used to POST these two with no body at all. Adding an optional
    Form field must not turn a bodyless request into a 422."""
    import asyncio
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import training as tr

    async def _fake_trigger(row_id=None):
        return {"triggered": True, "status": 200, "row_id": row_id}
    monkeypatch.setattr(tr, "trigger_esp32_async", _fake_trigger)

    app = FastAPI()
    app.include_router(tr.router)
    app.dependency_overrides[tr.get_db] = lambda: db
    client = TestClient(app)

    for path in ("/training/collect/clear_air", "/training/collect/sanitizer"):
        r = client.post(path)                      # no body, no headers
        assert r.status_code == 200, f"{path} -> {r.status_code} {r.text}"
        body = r.json()
        assert body["subject_id"] is None, f"{path} returned {body}"

    rows = db.query(TrainingData).order_by(TrainingData.id).all()
    assert len(rows) == 2
    assert all(r.subject_id is None for r in rows)
    assert all(float(r.bac) == 0.0 for r in rows)


def test_bodyless_post_still_accepts_explicit_subject_id(db, tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import training as tr

    async def _fake_trigger(row_id=None):
        return {"triggered": True, "status": 200, "row_id": row_id}
    monkeypatch.setattr(tr, "trigger_esp32_async", _fake_trigger)

    app = FastAPI()
    app.include_router(tr.router)
    app.dependency_overrides[tr.get_db] = lambda: db
    client = TestClient(app)

    _seed_subject(db, 4)
    r = client.post("/training/collect/clear_air", data={"subject_id": 4})
    assert r.status_code == 200, r.text
    assert r.json()["subject_id"] == 4


# ------------------------------------------------------------------------------
# MariaDB parity — training_data.subject_id has a real FK to subjects(id)
# ------------------------------------------------------------------------------

def test_subject_id_must_reference_a_real_subjects_row(db):
    """Production is MariaDB: `CONSTRAINT fk_training_data_subject FOREIGN KEY
    (subject_id) REFERENCES subjects (id)`. Typing a subject_id into the Relabel
    modal for a person who was never enrolled is rejected with MariaDB error 1452.
    This runs against real MariaDB, so the constraint is genuinely enforced."""
    from sqlalchemy.exc import IntegrityError

    def _row(sid):
        return TrainingData(label=0, sub_label=None, bac=0.0, trial_id=1,
                            subject_id=sid, mq3_1_max=1.0)

    # no subjects rows yet -> must fail
    with pytest.raises(IntegrityError):
        db.add(_row(4))
        db.commit()
    db.rollback()

    # enroll the person first -> now it is accepted
    _seed_subject(db, 4)
    db.add(_row(4))
    db.commit()
    assert db.query(TrainingData).filter(TrainingData.subject_id == 4).count() == 1


def test_relabel_rejects_unknown_subject_id(db):
    """End-to-end through the route: a subject_id with no subjects row must produce
    an actionable HTTP 400, never a raw IntegrityError (TASK 2).

    CHANGED during TASK 2: this test previously asserted sqlalchemy IntegrityError,
    i.e. it locked in the behaviour the task forbids. The code is correct; the
    expectation was wrong, so the assertion was updated (not weakened)."""
    import routes.training as tr
    from fastapi import HTTPException

    row = TrainingData(label=0, sub_label=None, bac=0.0, trial_id=1, mq3_1_max=1.0)
    db.add(row)
    db.commit()
    db.refresh(row)

    with pytest.raises(HTTPException) as e:
        tr.relabel(row.id, tr.RelabelPayload(label=0, subject_id=999), db)
    assert e.value.status_code == 400
    assert e.value.detail == "subject_id 999 is not enrolled - enroll the person first"
    assert db.query(TrainingData).filter(TrainingData.id == row.id).one().subject_id is None


# ------------------------------------------------------------------------------
# TASK 0 — BAC band edges are centralized and boundary-correct
# ------------------------------------------------------------------------------

def test_bac_constants_define_the_intended_tiers():
    """pass < 0.02, near_limit 0.02..<0.05, over_limit >= 0.05."""
    from models.train.sensor_models import BAC_NEAR_LIMIT, BAC_OVER_LIMIT, PH_LIMIT
    assert BAC_NEAR_LIMIT == 0.02
    assert BAC_OVER_LIMIT == 0.05
    assert BAC_OVER_LIMIT == PH_LIMIT


# ------------------------------------------------------------------------------
# TASK 2 / 3 / 4 — subject existence, coverage, loud partial-tagging warning
# ------------------------------------------------------------------------------

def _client(db, monkeypatch, tmp_path):
    """A TestClient wired to the throwaway SQLite session, with ESP32 stubbed."""
    import asyncio
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import training as tr

    async def _fake_trigger(row_id=None):
        return {"triggered": True, "status": 200, "row_id": row_id}
    monkeypatch.setattr(tr, "trigger_esp32_async", _fake_trigger)
    monkeypatch.setattr(tr, "analyze_frame",
                        lambda f: {"ear": 0.3, "mar": 0.2, "pitch": 1.0})
    monkeypatch.setattr(tr, "BASE_DIR", str(tmp_path))

    app = FastAPI()
    app.include_router(tr.router)
    app.dependency_overrides[tr.get_db] = lambda: db
    return TestClient(app)


def _jpg(tmp_path, name="f.jpg"):
    import cv2
    ok, buf = cv2.imencode(".jpg", np.full((16, 16, 3), 80, dtype=np.uint8))
    assert ok
    return buf.tobytes()


@pytest.mark.parametrize("endpoint", [
    "/training/collect/clear_air", "/training/collect/sanitizer",
])
def test_collect_rejects_unenrolled_subject_id(db, tmp_path, monkeypatch, endpoint):
    client = _client(db, monkeypatch, tmp_path)
    r = client.post(endpoint, data={"subject_id": 77})
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "subject_id 77 is not enrolled - enroll the person first"
    # nothing leaked into the table
    assert db.query(TrainingData).count() == 0


def test_collect_file_endpoint_rejects_unenrolled_subject_id(db, tmp_path, monkeypatch):
    client = _client(db, monkeypatch, tmp_path)
    r = client.post("/training/collect/sober",
                    files={"file": ("f.jpg", _jpg(tmp_path), "image/jpeg")},
                    data={"subject_id": 88})
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "subject_id 88 is not enrolled - enroll the person first"
    assert db.query(TrainingData).count() == 0


def test_collect_alcohol_rejects_unenrolled_subject_id(db, tmp_path, monkeypatch):
    client = _client(db, monkeypatch, tmp_path)
    r = client.post("/training/collect/alcohol",
                    files={"file": ("f.jpg", _jpg(tmp_path), "image/jpeg")},
                    data={"bac": "0.07", "subject_id": 99})
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "subject_id 99 is not enrolled - enroll the person first"
    assert db.query(TrainingData).count() == 0


def test_collect_accepts_enrolled_subject_id(db, tmp_path, monkeypatch):
    _seed_subject(db, 12)
    client = _client(db, monkeypatch, tmp_path)
    r = client.post("/training/collect/clear_air", data={"subject_id": 12})
    assert r.status_code == 200, r.text
    assert r.json()["subject_id"] == 12
    assert db.query(TrainingData).one().subject_id == 12


def test_collect_still_allows_no_subject_id(db, tmp_path, monkeypatch):
    client = _client(db, monkeypatch, tmp_path)
    for ep in ("/training/collect/clear_air", "/training/collect/sanitizer"):
        r = client.post(ep)
        assert r.status_code == 200, r.text
        assert r.json()["subject_id"] is None
    assert db.query(TrainingData).count() == 2


def test_label_rejects_unenrolled_subject_id(db, tmp_path, monkeypatch):
    import routes.training as tr
    from fastapi import HTTPException
    row = TrainingData(label=0, sub_label=None, bac=0.0, trial_id=1, mq3_1_max=1.0)
    db.add(row); db.commit(); db.refresh(row)
    with pytest.raises(HTTPException) as e:
        tr.attach_label(row.id, tr.LabelPayload(bac=0.0, subject_id=123), db)
    assert e.value.status_code == 400
    assert e.value.detail == "subject_id 123 is not enrolled - enroll the person first"


def test_label_accepts_enrolled_subject_id_on_non_alcohol_row(db, tmp_path, monkeypatch):
    """The point of TASK 2: subject_id must be settable for label != 1 too."""
    import routes.training as tr
    _seed_subject(db, 21)
    row = TrainingData(label=0, sub_label="sober", bac=0.0, trial_id=1, mq3_1_max=1.0)
    db.add(row); db.commit(); db.refresh(row)
    out = tr.attach_label(row.id, tr.LabelPayload(bac=0.0, subject_id=21), db)
    db.refresh(row)
    assert out["label_name"]
    assert row.subject_id == 21


def test_subject_coverage_all_tagged(db, tmp_path, monkeypatch):
    _seed_subject(db, 1, 2)
    for i, sid in enumerate([1, 1, 2, 2]):
        db.add(TrainingData(label=i % 2, bac=0.0, trial_id=i, subject_id=sid, mq3_1_max=1.0))
    db.commit()
    client = _client(db, monkeypatch, tmp_path)
    r = client.get("/training/subject-coverage")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["rows_considered"] == 4
    assert d["rows_with_subject_id"] == 4
    assert d["distinct_subjects"] == 2
    assert d["untagged_row_ids"] == []
    assert d["will_group_by"] == "subject_id"
    assert d["per_label"] == {"0": 2, "1": 2}
    assert d["warning"] is None


def test_subject_coverage_one_untagged(db, tmp_path, monkeypatch):
    _seed_subject(db, 1, 2)
    for i, sid in enumerate([1, 1, 2, None]):
        db.add(TrainingData(label=i % 2, bac=0.0, trial_id=i, subject_id=sid, mq3_1_max=1.0))
    db.commit()
    client = _client(db, monkeypatch, tmp_path)
    d = client.get("/training/subject-coverage").json()
    assert d["will_group_by"] == "trial_id"
    assert d["rows_with_subject_id"] == 3
    assert len(d["untagged_row_ids"]) == 1
    row = db.query(TrainingData).filter(TrainingData.subject_id.is_(None)).one()
    assert d["untagged_row_ids"] == [row.id]
    assert d["warning"]["partial_subject_tagging"] is True
    assert d["warning"]["tagged"] == 3
    assert d["warning"]["untagged"] == 1


def test_subject_coverage_empty_table(db, tmp_path, monkeypatch):
    client = _client(db, monkeypatch, tmp_path)
    d = client.get("/training/subject-coverage").json()
    assert d["rows_considered"] == 0
    assert d["rows_with_subject_id"] == 0
    assert d["distinct_subjects"] == 0
    assert d["untagged_row_ids"] == []
    assert d["per_label"] == {}
    assert d["will_group_by"] == "trial_id"
    assert d["warning"] is None


def _train_row(i, sid):
    return TrainingData(label=i % 2, bac=0.0, trial_id=i, subject_id=sid,
                        mq3_1_max=10.0 + i, mq3_1_avg=9.0, mq3_1_std=0.5,
                        mq3_2_max=11.0, mq3_2_avg=10.0, mq3_2_std=0.5,
                        mq3_3_max=12.0, mq3_3_avg=11.0, mq3_3_std=0.5,
                        rise_time=1.0, decay_time=1.0,
                        spatial_variance=2.0, spatial_variance_avg=2.0,
                        temperature=27.0, humidity=60.0)


def _stub_sensor_train(monkeypatch):
    from models.train import sensor_models
    monkeypatch.setattr(sensor_models, "train",
                        lambda X, y, groups=None: {"random_forest": {"accuracy": 90.0}})
    monkeypatch.setattr(sensor_models, "train_bac", lambda *a, **k: {"accuracy": 1.0})


def test_trigger_training_warns_on_partial_tagging(db, tmp_path, monkeypatch):
    import routes.training as tr
    _seed_subject(db, 1, 2)
    _stub_sensor_train(monkeypatch)
    for i, sid in enumerate([1, 1, 2, None, None, None]):
        db.add(_train_row(i, sid))
    db.commit()
    out = tr.trigger_training(db)
    assert out["group_by"] == "trial_id"
    w = out["warning"]
    assert w["partial_subject_tagging"] is True
    assert w["tagged"] == 3
    assert w["untagged"] == 3
    assert len(w["untagged_row_ids"]) == 3


def test_trigger_training_no_warning_when_fully_tagged(db, tmp_path, monkeypatch):
    import routes.training as tr
    _seed_subject(db, 1, 2)
    _stub_sensor_train(monkeypatch)
    for i, sid in enumerate([1, 1, 2, 2]):
        db.add(_train_row(i, sid))
    db.commit()
    out = tr.trigger_training(db)
    assert out["group_by"] == "subject_id"
    assert "warning" not in out or out["warning"] is None


def test_trigger_training_no_warning_when_fully_untagged(db, tmp_path, monkeypatch):
    import routes.training as tr
    _stub_sensor_train(monkeypatch)
    for i in range(6):
        db.add(_train_row(i, None))
    db.commit()
    out = tr.trigger_training(db)
    assert out["group_by"] == "trial_id"
    assert "warning" not in out or out["warning"] is None


def test_partial_tagging_warning_caps_row_ids_at_50():
    from routes.training import partial_tagging_warning

    class R:
        def __init__(self, i, sid):
            self.id = i
            self.subject_id = sid

    rows = [R(i, 1) for i in range(60)] + [R(100 + i, None) for i in range(60)]
    w = partial_tagging_warning(rows, "trial_id")
    assert w["tagged"] == 60 and w["untagged"] == 60
    assert len(w["untagged_row_ids"]) == 50
    assert w["untagged_row_ids"][0] == 100


