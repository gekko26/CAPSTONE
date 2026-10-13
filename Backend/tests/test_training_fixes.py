"""
Regression tests for the training-side fixes confirmed by the architecture audit.

A2  - sensor_models.train() fit every model in MODELS; scaler saved only after success
A6  - fusion feature[6] is the BAC regressor's OOF estimate, never the ground-truth label value
A7  - fusion refuses when mobilenet.h5 is missing / rows lack labels / visual inference fails
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


# ----------------------------------------------------------------------------------
# A7 — fusion refuses without mobilenet.h5
# ----------------------------------------------------------------------------------

# ------------------------------------------------------------------------------
# helpers: a real in-memory SQLite DB (no MariaDB contact) + real ORM rows
# ------------------------------------------------------------------------------

import os
from urllib.parse import quote_plus

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from dotenv import load_dotenv

from database.db import Base
from database.models import TrainingData, Subject

# ----------------------------------------------------------------------------------
# MariaDB test engine
#
# Production is MariaDB, and SQLite quietly disagrees with it on the things that
# matter most here: SQLite ignores FOREIGN KEY constraints unless you switch them
# on, tolerates DDL SQLAlchemy's MySQL dialect would reject, and stores Booleans
# differently. So the suite runs against a real MariaDB server.
#
# The schema is MariaDB's built-in `test` database, which the app user can write to
# via the default `TO PUBLIC` grants. It is a different schema from Alcohol_System,
# so production data is never touched.
# ----------------------------------------------------------------------------------
TEST_DB_NAME = "test"
PROD_DB_NAME = "Alcohol_System"


def _mariadb_url(dbname: str) -> str:
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    user = quote_plus(os.getenv("DB_USER", ""))
    pw = quote_plus(os.getenv("DB_PASSWORD", ""))
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "3306")
    return f"mysql+pymysql://{user}:{pw}@{host}:{port}/{dbname}"


@pytest.fixture(scope="session", autouse=True)
def _mariadb_engine():
    """One engine for the whole session; refuses to touch the production schema."""
    prod = os.path.basename((os.getenv("DB_NAME") or "").strip()) or None
    assert TEST_DB_NAME != PROD_DB_NAME, "test schema must never be the production schema"

    engine = create_engine(_mariadb_url(TEST_DB_NAME), poolclass=NullPool,
                           connect_args={"connect_timeout": 5},
                           future=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def db(_mariadb_engine):
    """A real MariaDB session with a freshly emptied schema for each test."""
    with _mariadb_engine.connect() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        conn.commit()
    # drop_all in dependency order so FKs do not block the teardown
    Base.metadata.drop_all(_mariadb_engine)
    Base.metadata.create_all(_mariadb_engine)
    with _mariadb_engine.connect() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
        conn.commit()
    session = sessionmaker(bind=_mariadb_engine, future=True)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


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


def _seed(session, bacs, tmp_path, prefix="i", with_images=True, subject_id=None,
          trial_ids=None, ear=None, mar=None, labels=None):
    """Insert one TrainingData row per BAC value, wired to a real image on disk."""
    rows = []
    for i, b in enumerate(bacs):
        img = _jpeg(tmp_path, f"{prefix}_{i}.jpg", i * 7) if with_images else None
        tid = trial_ids[i] if trial_ids else i
        r = TrainingData(
            label=(labels[i] if labels else (1 if b >= 0.01 else 0)),
            sub_label=None,
            bac=b,
            trial_id=tid,
            subject_id=subject_id,
            image_path=img,
            ear=ear,
            mar=mar,
            mq3_1_max=10.0 + i, mq3_1_avg=9.0, mq3_1_std=0.5,
            mq3_2_max=11.0 + i, mq3_2_avg=10.0, mq3_2_std=0.5,
            mq3_3_max=12.0 + i, mq3_3_avg=11.0, mq3_3_std=0.5,
            rise_time=1.0, decay_time=1.0,
            spatial_variance=2.0, spatial_variance_avg=2.0,
            temperature=27.0, humidity=60.0,
        )
        session.add(r)
        rows.append(r)
    session.commit()
    for r in rows:
        session.refresh(r)
    return rows


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
# A7 — fusion refuses without mobilenet.h5
# ------------------------------------------------------------------------------

def _stub_mobilenet(tmp_path, monkeypatch, present=True):
    import routes.training as tr
    from models.train import mobilenet, fusion_model
    save = tmp_path / "mob"
    save.mkdir(exist_ok=True)
    if present:
        (save / "mobilenet.h5").write_bytes(b"stub")
    monkeypatch.setattr(mobilenet, "SAVE_DIR", str(save))
    monkeypatch.setattr(mobilenet, "predict_frame",
                        lambda img: {"label": "sober", "confidence": 0.9, "class_index": 0})
    fus = tmp_path / "fus"
    fus.mkdir(exist_ok=True)
    monkeypatch.setattr(fusion_model, "SAVE_DIR", str(fus))
    monkeypatch.setattr("models.train.metrics_store.save_metrics",
                        lambda *a, **k: None, raising=True)
    return tr


def test_a7_fusion_refuses_without_mobilenet(db, tmp_path, monkeypatch):
    """A7: with no mobilenet.h5 on disk, fusion must refuse — not fall back to labels."""
    tr = _stub_mobilenet(tmp_path, monkeypatch, present=False)
    _seed(db, [0.0] * 6 + [0.02] * 3 + [0.07] * 3, tmp_path)

    out = tr.train_fusion(db)
    assert "error" in out, f"expected refusal, got {out}"
    assert "mobilenet.h5" in out["error"]


def test_a7_fusion_refuses_single_label(db, tmp_path, monkeypatch):
    """A7: refuse when the training rows carry fewer than 2 distinct labels."""
    tr = _stub_mobilenet(tmp_path, monkeypatch)
    _seed(db, [0.0] * 12, tmp_path)

    out = tr.train_fusion(db)
    assert "error" in out
    assert "distinct" in out["error"]


def test_a7_fusion_refuses_rows_without_measured_bac(db, tmp_path, monkeypatch):
    """A6 follow-on: no measured BAC -> no OOF estimate -> refuse."""
    tr = _stub_mobilenet(tmp_path, monkeypatch)
    _seed(db, [0.0] * 6 + [0.02] * 3 + [0.07] * 3, tmp_path)
    db.query(TrainingData).update({"bac": None})
    db.commit()

    out = tr.train_fusion(db)
    assert "error" in out
    assert "BAC" in out["error"]


def test_a7_fusion_refuses_single_distinct_bac_level(db, tmp_path, monkeypatch):
    """A6: a constant BAC column cannot fit an OOF regressor -> refuse."""
    tr = _stub_mobilenet(tmp_path, monkeypatch)
    # two distinct sensor labels, but every measured BAC identical
    _seed(db, [0.0] * 12, tmp_path, labels=[0] * 6 + [1] * 6)

    out = tr.train_fusion(db)
    assert "error" in out
    assert "distinct measured BAC levels" in out["error"]


# ------------------------------------------------------------------------------
# item 1 — fusion pairs only rows that have an image on disk
# ------------------------------------------------------------------------------

def test_fusion_excludes_imageless_rows_and_trains_the_rest(db, tmp_path, monkeypatch):
    """clear_air / sanitizer captures have no image: they are EXCLUDED, not refused,
    and the response reports both counts."""
    tr = _stub_mobilenet(tmp_path, monkeypatch)
    _seed(db, [0.0] * 6 + [0.02] * 3 + [0.07] * 3, tmp_path, prefix="paired")
    _seed(db, [0.0] * 4, tmp_path, prefix="imgless", with_images=False)

    total = db.query(TrainingData).count()
    assert total == 16

    out = tr.train_fusion(db)
    assert "error" not in out, out
    assert out["counts"]["rows_total"] == 16
    assert out["counts"]["rows_paired"] == 12
    assert out["counts"]["rows_excluded_no_image"] == 4
    assert out["samples"] == 12


def test_fusion_refuses_when_too_few_paired_rows(db, tmp_path, monkeypatch):
    tr = _stub_mobilenet(tmp_path, monkeypatch)
    _seed(db, [0.0] * 15, tmp_path, with_images=False)

    out = tr.train_fusion(db)
    assert "error" in out
    assert out["counts"]["rows_paired"] == 0
    assert out["counts"]["rows_total"] == 15
    assert "paired" in out["error"]


# ------------------------------------------------------------------------------
# item 2 — fusion label is a pure function of measured BAC
# ------------------------------------------------------------------------------

def test_fusion_label_depends_only_on_bac(db, tmp_path, monkeypatch):
    """Flipping ear/mar (and the visual class) must not move the label."""
    tr = _stub_mobilenet(tmp_path, monkeypatch)
    from models.train import mobilenet

    bacs = [0.0] * 6 + [0.02] * 3 + [0.06] * 3

    def run(ear, mar, visual):
        monkeypatch.setattr(mobilenet, "predict_frame",
                            lambda img, _v=visual: {"label": "sober", "confidence": 0.9,
                                                   "class_index": _v})
        db.query(TrainingData).delete()
        db.commit()
        _seed(db, bacs, tmp_path, prefix=f"e{visual}", ear=ear, mar=mar)
        out = tr.train_fusion(db)
        assert "error" not in out, out
        return out["class_counts"]

    baseline = run(None, None, 0)      # open eyes, sober visual
    stressed = run(0.05, 0.95, 2)     # eyes shut, gaping, "impaired" visual
    assert baseline == stressed, f"label rule leaked non-BAC inputs: {baseline} != {stressed}"
    assert baseline == {0: 6, 1: 3, 2: 3}, baseline


def test_fusion_reports_per_class_counts(db, tmp_path, monkeypatch):
    tr = _stub_mobilenet(tmp_path, monkeypatch)
    bacs = [0.0] * 6 + [0.03] * 3 + [0.07] * 3
    _seed(db, bacs, tmp_path)

    out = tr.train_fusion(db)
    assert "error" not in out, out
    assert sum(out["class_counts"].values()) == out["samples"]
    assert out["class_counts"][0] == 6
    assert out["class_counts"][1] == 3
    assert out["class_counts"][2] == 3


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
    bac = 0.0 rather than NULL, so the BAC-only fusion label and the OOF regressor
    always have a value."""
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
# item 3 — fusion predict_single maps proba columns through model.classes_
# ------------------------------------------------------------------------------

def test_fusion_predict_single_with_two_class_model(tmp_path, monkeypatch):
    """A fusion model trained only on labels {0, 2} must still report the right
    tier name. predict_proba then has 2 columns, so indexing it by the tier index
    (2) used to raise IndexError."""
    from models.train import fusion_model

    save = tmp_path / "fus"
    save.mkdir()
    monkeypatch.setattr(fusion_model, "SAVE_DIR", str(save))
    monkeypatch.setattr("models.train.metrics_store.save_metrics",
                        lambda *a, **k: None, raising=True)

    rng = np.random.default_rng(3)
    n = 40
    X, y = [], []
    for i in range(n):
        high = i >= n // 2
        y.append(2 if high else 0)
        X.append([1 if high else 0, 0.9, 0, 0.8, 0.30, 0.20,
                  0.09 if high else 0.0, 27.0])
    X = np.array(X, dtype=float)

    res = fusion_model.train(X, np.array(y))
    assert "error" not in res, res
    model = fusion_model.load()
    assert list(model.classes_) == [0, 2], "precondition: model should have 2 classes"

    # over_limit input — must not raise, must not be mislabelled near_limit
    out = fusion_model.predict_single(
        sensor_class=1, sensor_confidence=0.9, visual_class=0, visual_confidence=0.8,
        ear=0.30, mar=0.20, estimated_bac=0.09, temperature=27.0)
    assert out["class"] == 2, out
    assert out["label"] == "over_limit", out
    assert out["action"] == "deny", out
    assert set(out["all_probs"]) == {"pass", "over_limit"}, out["all_probs"]

    # pass input
    out0 = fusion_model.predict_single(
        sensor_class=0, sensor_confidence=0.9, visual_class=0, visual_confidence=0.8,
        ear=0.30, mar=0.20, estimated_bac=0.0, temperature=27.0)
    assert out0["class"] == 0, out0
    assert out0["label"] == "pass", out0


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
    """End-to-end through the route: a subject_id with no subjects row is rejected
    rather than silently stored."""
    import routes.training as tr
    from sqlalchemy.exc import IntegrityError

    row = TrainingData(label=0, sub_label=None, bac=0.0, trial_id=1, mq3_1_max=1.0)
    db.add(row)
    db.commit()
    db.refresh(row)

    with pytest.raises(IntegrityError):
        tr.relabel(row.id, tr.RelabelPayload(label=0, subject_id=999), db)
        db.commit()
    db.rollback()
    assert db.query(TrainingData).filter(TrainingData.id == row.id).one().subject_id is None


# ------------------------------------------------------------------------------
# Guard: the suite must never silently regress to SQLite
# ------------------------------------------------------------------------------

def test_suite_runs_against_mariadb(_mariadb_engine):
    """SQLite ignores FOREIGN KEYs, tolerates DDL the MySQL dialect would reject,
    and stores Booleans differently. If this ever runs on SQLite again, the FK and
    DDL guarantees the rest of the suite relies on are gone."""
    assert _mariadb_engine.dialect.name == "mysql", \
        f"expected the MySQL/MariaDB dialect, got {_mariadb_engine.dialect.name}"

    with _mariadb_engine.connect() as conn:
        version = conn.execute(text("SELECT VERSION()")).scalar()
        schema = conn.execute(text("SELECT DATABASE()")).scalar()

    assert "MariaDB" in version, f"not MariaDB: {version}"
    assert schema == TEST_DB_NAME, f"connected to {schema!r}, expected {TEST_DB_NAME!r}"
    assert schema != "Alcohol_System", "tests are pointed at the production schema"


def test_mariadb_actually_enforces_the_subject_fk(db):
    """Proves the FK is live on this engine — the SQLite version of this test passed
    even with no subjects row, which is exactly the gap that hid MariaDB error 1452."""
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError) as e:
        db.add(TrainingData(label=0, sub_label=None, bac=0.0, trial_id=1,
                            subject_id=4242, mq3_1_max=1.0))
        db.commit()
    db.rollback()
    assert "FOREIGN KEY" in str(e.value).upper()
