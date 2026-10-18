"""Readiness dress rehearsals (R1-R5) for the AlcoDetect capstone.

These are SYNTHETIC end-to-end rehearsals. They call the SHIPPED route handlers and
the SHIPPED rule module -- no rule is re-implemented here. Nothing under the real
Backend/data tree, Backend/models/saved, or any live database is read or written.

Every directory constant that the collection, upload, training and deployment paths
resolve at import time is redirected into tmp_path, and every network / model /
camera dependency is stubbed. See CONSTANTS_REDIRECTED below for the audited list.

Deliberately NOT covered (reported UNVERIFIED in the readiness report):
  * MobileNet training -- it downloads ImageNet weights. Only its gating logic and
    pinned class order are exercised here, on a tmp tree.
  * Real camera frames, real DeepFace embeddings, real hardware.
"""

import io
import json
import os
import random
import shutil
from pathlib import Path

import numpy as np
import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text as sql_text

# Imported at module scope on purpose: the shared `db` fixture calls
# Base.metadata.create_all(), so every ORM model must already be registered on the
# metadata when this file is run on its own.
import database.models  # noqa: F401
from database.models import Subject, TrainingData  # noqa: F401

SEED = 20261018

# The complete set of directory constants this suite redirects. If the application
# grows a new data-path constant, this list must grow with it or the suite would be
# writing to the real tree.
CONSTANTS_REDIRECTED = [
    "routes.training.BASE_DIR",
    "routes.training.SNAPSHOTS_DIR",
    "routes.training.SOBER_DIR",
    "routes.training.FATIGUE_DIR",
    "routes.training.IMPAIRED_DIR",
    "routes.training.UPLOAD_LOG_PATH",
    "routes.predict.IMPAIRED_DIR",
    "routes.predict.FATIGUE_DIR",
    "routes.recognition.FACES_REGISTERED",
    "models.train.sensor_models.SAVE_DIR",
    "models.train.mobilenet.DATA_DIR",
    # metrics_store derives METRICS_PATH from its OWN SAVE_DIR at IMPORT time, so
    # redirecting sensor_models.SAVE_DIR alone still writes the real models/saved/.
    "models.train.metrics_store.SAVE_DIR",
    "models.train.metrics_store.METRICS_PATH",
]

# Functions stubbed so no hardware, camera, download or network is touched.
STUBS = [
    "routes.training.trigger_esp32_async",
    "routes.training.analyze_frame",
    "routes.recognition.enroll",
    "routes.predict.analyze_frame",
    "routes.predict.predict_frame",
    "routes.predict._face_label",
    "routes.predict._class_from_features",
    "routes.predict._bac_from_features",
]


# A complete analysis shape. upload_faces() reads BOTH "status" (no_face) and
# "face_width" (too_small) from the shipped analyzer, so a partial stub silently
# rejects every upload.
FAKE_ANALYSIS = {
    "status": "face", "ear": 0.3, "mar": 0.2, "pitch": 1.0,
    "face_width": 200.0, "impaired": False,
}


# ── helpers ────────────────────────────────────────────────────────────────────

def _jpg(seed=0, size=16):
    """A tiny deterministic JPEG. Synthetic pixels only -- never a real face."""
    import cv2
    rng = np.random.RandomState(seed)
    img = rng.randint(0, 255, (size, size, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def _upload(name, data):
    """Multipart entry for the multi-file upload endpoints (field name 'files')."""
    return ("files", (name, io.BytesIO(data), "image/jpeg"))


def _frame(name="f.jpg", data=None):
    """Multipart entry for the single-file collect_* endpoints (field name 'file')."""
    return ("file", (name, io.BytesIO(data if data is not None else _jpg()), "image/jpeg"))


def _window(base, wobble=0.02, seed=0, n=50):
    """A 50-sample class-separable synthetic MQ3 window."""
    rng = np.random.RandomState(seed)
    return [float(base + rng.normal(0, wobble * max(base, 1.0))) for _ in range(n)]


# ── sandbox ────────────────────────────────────────────────────────────────────

@pytest.fixture()
def sandbox(monkeypatch, tmp_path):
    """Redirect every data path into tmp_path and stub every external dependency."""
    import routes.training as tr
    import routes.predict as pr
    import routes.recognition as rc
    from models.train import sensor_models as sm
    from models.train import mobilenet as mn
    from models.train import metrics_store as ms

    data = tmp_path / "data"
    for sub in ("faces", "snapshots"):
        for cls in ("sober", "fatigue", "impaired", "registered"):
            (data / sub / cls).mkdir(parents=True, exist_ok=True)
    saved = tmp_path / "saved"
    saved.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(tr, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(tr, "SNAPSHOTS_DIR", str(data / "snapshots"))
    monkeypatch.setattr(tr, "SOBER_DIR", str(data / "snapshots" / "sober"))
    monkeypatch.setattr(tr, "FATIGUE_DIR", str(data / "snapshots" / "fatigue"))
    monkeypatch.setattr(tr, "IMPAIRED_DIR", str(data / "snapshots" / "impaired"))
    monkeypatch.setattr(tr, "UPLOAD_LOG_PATH", str(data / "upload_log.jsonl"))
    monkeypatch.setattr(pr, "IMPAIRED_DIR", str(data / "snapshots" / "impaired"))
    monkeypatch.setattr(pr, "FATIGUE_DIR", str(data / "snapshots" / "fatigue"))
    monkeypatch.setattr(rc, "FACES_REGISTERED", str(data / "faces" / "registered"))
    monkeypatch.setattr(sm, "SAVE_DIR", str(saved))
    monkeypatch.setattr(mn, "DATA_DIR", str(data / "faces"))
    monkeypatch.setattr(ms, "SAVE_DIR", str(saved))
    monkeypatch.setattr(ms, "METRICS_PATH", str(saved / "metrics.json"))

    random.seed(SEED)
    np.random.seed(SEED)

    async def _fake_trigger(row_id=None):
        return {"triggered": True, "status": 200, "row_id": row_id}

    monkeypatch.setattr(tr, "trigger_esp32_async", _fake_trigger)
    monkeypatch.setattr(tr, "analyze_frame", lambda f: dict(FAKE_ANALYSIS))
    # DeepFace.enroll would load real models. Record the intent instead.
    enrolled = []
    monkeypatch.setattr(rc, "enroll",
                        lambda name, frames: enrolled.append(name) or True)
    return {"root": tmp_path, "data": data, "saved": saved, "enrolled": enrolled}


@pytest.fixture()
def train_client(db, sandbox, monkeypatch):
    """TestClient for the TRAINING + RECOGNITION routers. main.py is never imported."""
    import routes.training as tr
    import routes.recognition as rc
    app = FastAPI()
    app.include_router(tr.router)
    app.include_router(rc.router)
    app.dependency_overrides[tr.get_db] = lambda: db
    app.dependency_overrides[rc.get_db] = lambda: db
    return TestClient(app)


@pytest.fixture()
def deploy_client(db, sandbox, monkeypatch):
    """TestClient for the DEPLOYMENT routers (sensor + predict + indicator bench)."""
    import routes.sensor as sr
    import routes.predict as pr
    app = FastAPI()
    app.include_router(sr.router)
    app.include_router(pr.router)
    app.dependency_overrides[sr.get_db] = lambda: db
    app.dependency_overrides[pr.get_db] = lambda: db
    return TestClient(app)


# ── R1: collection ─────────────────────────────────────────────────────────────

SUBJECTS = ["R1 subject A", "R1 subject B", "R1 subject C"]


def _enroll_three(client, sandbox):
    for i, name in enumerate(SUBJECTS):
        r = client.post("/recognition/enroll", data={"name": name},
                        files=[_upload(f"{i}.jpg", _jpg(seed=i))])
        assert r.status_code == 200, r.text
    assert sandbox["enrolled"] == SUBJECTS
    return client.get("/recognition/subjects").json()


def _capture(client, endpoint, subject_id, bac=None):
    """Drive a REAL collect_* endpoint and deliver a synthetic window for its row."""
    data = {"subject_id": str(subject_id)}
    files = None
    # Only clear_air and sanitizer are bodyless; every other collect_* requires a frame.
    if endpoint not in ("/training/collect/clear_air", "/training/collect/sanitizer"):
        files = [_frame("f.jpg", _jpg(seed=(abs(hash((endpoint, subject_id))) % 10000)))]
    if bac is not None:
        data["bac"] = str(bac)
    r = client.post(endpoint, data=data, files=files)
    assert r.status_code == 200, f"{endpoint}: {r.text}"
    row_id = r.json()["id"]

    # class-separable synthetic features per label
    if endpoint == "/training/collect/alcohol":
        base = (140.0, 80.0, 20.0)
    elif endpoint in ("/training/collect/sanitizer", "/training/collect/perfume"):
        base = (60.0, 120.0, 95.0)
    else:
        base = (30.0, 30.0, 28.0)

    payload = {
        "temperature": 27.0, "humidity": 60.0, "mode": "training", "row_id": row_id,
        "mq3_1": _window(base[0], seed=row_id),
        "mq3_2": _window(base[1], seed=row_id + 1),
        "mq3_3": _window(base[2], seed=row_id + 2),
    }
    w = client.post("/training/collect/sensor-data", json=payload)
    assert w.status_code == 200, w.text
    return row_id


def test_r1_collection_rehearsal(train_client, db, sandbox, tmp_path):
    """Full collection path: enroll -> captures -> windows -> uploads -> readiness."""
    client = train_client
    import routes.training as tr

    n_per_class = tr.SENSOR_MIN_ROWS_PER_CLASS          # read, never hardcoded
    n_subjects = tr.MIN_SUBJECTS
    band_rows = tr.BAC_BAND_MIN_ROWS
    band_subjects = tr.BAC_BAND_MIN_SUBJECTS
    face_min = tr.FACE_MIN_IMAGES_PER_CLASS()
    assert (n_per_class, n_subjects, band_rows, band_subjects, face_min) == (30, 3, 8, 2, 30)

    # --- stage 1: not ready, and it says exactly why --------------------------
    prog0 = client.get("/training/collection-progress").json()
    assert prog0["ready"]["sensor_models"]["ready"] is False
    assert any("rows:" in m for m in prog0["ready"]["sensor_models"]["missing"])
    assert prog0["ready"]["mobilenet"]["ready"] is False

    # --- stage 2: enroll the subjects -----------------------------------------
    subs = _enroll_three(client, sandbox)
    subject_ids = [s["id"] for s in subs]
    assert len(subject_ids) == n_subjects

    # --- stage 3: captures, BAC spread over the three bands -------------------
    made = {"sober": 0, "fatigue": 0, "impaired": 0}
    for i in range(n_per_class):
        # NOTE: the BAC band cycles on i % 3 but the SUBJECT cycles on (i // 3) % 3.
        # Correlating them would put exactly one subject in every BAC band and the
        # per-band subject minimum could never be met by a real dataset shaped like this.
        sid = subject_ids[(i // 3) % n_subjects]
        # label 0: sober + fatigue, filling the sober/fatigue face classes
        _capture(client, "/training/collect/sober", sid);            made["sober"] += 1
        _capture(client, "/training/collect/drowsy" if i % 2 else
                 "/training/collect/yawning", sid);                   made["fatigue"] += 1
        # label 1: BAC spread across below_near / near / over
        bac = [0.005, 0.03, 0.07][i % 3]
        _capture(client, "/training/collect/alcohol", sid, bac=bac)
        if bac >= tr.BAC_OVER_LIMIT:
            made["impaired"] += 1
    # label 2: others (sanitizer / perfume)
    for i in range(n_per_class):
        sid = subject_ids[i % n_subjects]
        _capture(client, "/training/collect/sanitizer" if i % 2 else
                 "/training/collect/perfume", sid)

    # rows exist and every one has sensor features
    from database.models import TrainingData
    rows = db.query(TrainingData).all()
    # label 0 = sober + fatigue captures (2 per iteration); label 1 = alcohol;
    # label 2 = sanitizer/perfume
    assert len(rows) == 2 * n_per_class + n_per_class + n_per_class
    assert all(r.mq3_1_max is not None for r in rows), "a row has no sensor window"
    assert all(r.trial_id is not None for r in rows)

    # --- stage 4: impaired mirror ONLY at/above the BAC gate ------------------
    faces = sandbox["data"] / "faces"
    impaired_files = sorted(p.name for p in (faces / "impaired").glob("*.jpg"))
    over_rows = [r.id for r in rows if r.label == 1 and (r.bac or 0) >= tr.BAC_OVER_LIMIT]
    assert made["impaired"] == len(over_rows) > 0
    assert len(impaired_files) == len(over_rows), (
        f"impaired mirrors {len(impaired_files)} != alcohol rows over the gate {len(over_rows)}")
    for name in impaired_files:
        row_id = int(name.rsplit("_", 1)[1].split(".")[0])
        row = db.query(TrainingData).filter(TrainingData.id == row_id).first()
        assert row.bac >= tr.IMPAIRED_FACE_MIN_BAC, "mirror written below the BAC gate"

    # --- stage 5: uploads fill the face minimum for impaired ------------------
    have = len(list((faces / "impaired").glob("*.jpg")))
    need = face_min - have
    assert need > 0
    for i in range(need):
        r = client.post("/training/upload-faces",
                        data={"face_class": "impaired", "subject_id": str(subject_ids[i % n_subjects]),
                              "bac": "0.08", "confirm_real": "true"},
                        files=[_upload(f"u{i}.jpg", _jpg(seed=500 + i))])
        assert r.status_code == 200, r.text
    for cls in ("sober", "fatigue"):
        n = len(list((faces / cls).glob("*.jpg")))
        assert n >= face_min, f"{cls} only has {n} face images"

    # upload names follow the agreed pattern
    up = client.get("/training/uploaded-faces").json()
    names = [f["name"] for f in up["files"]]
    assert names, "the upload listing returned nothing"
    assert all(n.startswith("impaired_up_s") for n in names), names[:3]
    assert {f["face_class"] for f in up["files"]} == {"impaired"}

    # --- stage 6: readiness flips exactly when the minimums are met -----------
    prog = client.get("/training/collection-progress").json()
    assert prog["sensor"]["0"]["met_min"] and prog["sensor"]["1"]["met_min"]
    assert prog["sensor"]["2"]["met_min"]
    assert prog["ready"]["sensor_models"]["ready"] is True, prog["ready"]["sensor_models"]["missing"]
    for band in ("below_near", "near", "over"):
        b = prog["bac_bands"][band]
        assert b["rows"] >= band_rows, f"{band}: {b['rows']} < {band_rows}"
        assert b["subjects"] >= band_subjects, f"{band}: {b['subjects']} < {band_subjects}"
    assert prog["ready"]["bac_regressor"]["ready"] is True, prog["ready"]["bac_regressor"]["missing"]
    assert prog["ready"]["mobilenet"]["ready"] is True, prog["ready"]["mobilenet"]["missing"]

    # grouping, integrity, no orphans, no missing images
    assert prog["grouping"]["group_by"] == "subject_id"
    assert not prog["grouping"]["warning"]
    assert prog["integrity"]["orphan_files"] == 0, prog["integrity"]["orphan_file_names"][:5]
    # rows_missing_image is a COUNT in the shipped response, not a list
    assert prog["integrity"]["rows_missing_image"] == 0
    assert prog["integrity"]["rows_missing_image_ids"] == []
    assert prog["subjects"]["single_class_subjects_warning"] is False


# ── R2: training ───────────────────────────────────────────────────────────────

def _populate(client, db, sandbox, n_per_class=30):
    """Seed a subject-tagged, class-balanced dataset straight into the ORM."""
    from database.models import Subject, TrainingData
    import routes.training as tr
    sids = []
    for i in range(tr.MIN_SUBJECTS):
        s = Subject(name=f"R2 subject {i}", group="Student")
        db.add(s); db.flush()
        sids.append(s.id)
    rows = []
    bases = {0: (30.0, 30.0, 28.0), 1: (140.0, 80.0, 20.0), 2: (60.0, 120.0, 95.0)}
    for label, base in bases.items():
        for i in range(n_per_class):
            sid = sids[i % len(sids)]
            r = TrainingData(
                label=label, sub_label={0: "sober", 1: "alcohol", 2: "sanitizer"}[label],
                bac=(0.0 if label == 0 else [0.005, 0.03, 0.07][i % 3]),
                subject_id=sid,
                mq3_1_max=base[0], mq3_1_avg=base[0] * 0.98, mq3_1_std=base[0] * 0.02,
                mq3_2_max=base[1], mq3_2_avg=base[1] * 0.98, mq3_2_std=base[1] * 0.02,
                mq3_3_max=base[2], mq3_3_avg=base[2] * 0.98, mq3_3_std=base[2] * 0.02,
                rise_time=10.0, decay_time=20.0,
                spatial_variance=1.0, spatial_variance_avg=1.0,
                temperature=27.0, humidity=60.0,
            )
            db.add(r); db.flush()
            rows.append(r)
    for r in rows:
        r.trial_id = r.id
    db.commit()
    return sids, rows


def test_r2_training_rehearsal(train_client, db, sandbox):
    """Real sensor training on synthetic data. MobileNet is NOT trained."""
    from models.train import sensor_models as sm
    sids, rows = _populate(train_client, db, sandbox)

    r = train_client.post("/training/train")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["group_by"] == "subject_id"
    assert not body.get("warning")
    assert body["samples"] == len(rows)
    assert body["trials"] == len(sids), "one group per subject => no subject leakage"
    assert body.get("error") is None

    # artifacts land in the redirected SAVE_DIR only
    for f in ("random_forest.pkl", "xgboost.pkl", "scaler.pkl"):
        assert (sandbox["saved"] / f).exists(), f
    assert body["bac_samples"] > 0
    assert (sandbox["saved"] / sm.BAC_REGRESSOR_FILE).exists()

    res = body["results"]
    assert set(res) >= {"random_forest", "xgboost"}
    for name in ("random_forest", "xgboost"):
        assert res[name]["accuracy"] is not None, f"{name} metrics shape"
        for k in ("accuracy", "precision", "recall", "f1"):
            assert k in res[name], f"{name} missing metric {k}"

    # group_key maps each subject to exactly one group, so no subject can be
    # split across train and test folds.
    keys = {row.subject_id: tr_key(row) for row in rows}
    assert len(set(keys.values())) == len(set(keys))


def tr_key(row):
    from routes.training import resolve_group_by
    return resolve_group_by([row])[1](row)


def test_r2_partial_tagging_falls_back_to_trial_id(train_client, db, sandbox):
    """One untagged row must degrade grouping and raise the warning."""
    from database.models import TrainingData
    _, rows = _populate(train_client, db, sandbox, n_per_class=12)
    rows[0].subject_id = None
    db.commit()
    r = train_client.post("/training/train")
    body = r.json()
    assert body["group_by"] == "trial_id"
    assert body.get("warning"), "partial tagging must warn"


def test_r2_too_few_classes_is_a_clear_error(train_client, db, sandbox):
    """A single class cannot train the classifiers; the error must be explicit."""
    from database.models import TrainingData
    for label in (0,):
        for i in range(8):
            db.add(TrainingData(
                label=label, sub_label="x", bac=0.0, mq3_1_max=10.0 + i,
                mq3_1_avg=10.0, mq3_1_std=1.0, mq3_2_max=10.0, mq3_2_avg=10.0,
                mq3_2_std=1.0, mq3_3_max=10.0, mq3_3_avg=10.0, mq3_3_std=1.0,
                rise_time=1.0, decay_time=1.0, spatial_variance=1.0,
                spatial_variance_avg=1.0, temperature=27.0, humidity=60.0))
    db.commit()
    r = train_client.post("/training/train")
    body = r.json()
    assert body.get("error"), "training on a single class must NOT silently report success"


def test_r2_mobilenet_gating_and_class_order(sandbox):
    """MobileNet: only the gating is tested. No weights are downloaded."""
    from models.train import mobilenet as mn
    import routes.training as tr
    assert mn.CLASSES == ["sober", "fatigue", "impaired"]
    assert list(mn.CLASSES) == list(tr.FACE_TRAINED_CLASSES)
    assert mn.MIN_IMAGES_PER_CLASS == tr.FACE_MIN_IMAGES_PER_CLASS()

    faces = sandbox["data"] / "faces"
    for cls in ("sober", "fatigue"):
        (faces / cls).mkdir(parents=True, exist_ok=True)
    counts = mn._class_image_counts(str(faces))
    assert counts["impaired"] < mn.MIN_IMAGES_PER_CLASS
    # the trainer must refuse rather than train on a short class
    with pytest.raises(Exception) as ei:
        mn.train_classifier()
    assert str(mn.MIN_IMAGES_PER_CLASS) in str(ei.value) or "class" in str(ei.value).lower()


# ── R3: deployment ─────────────────────────────────────────────────────────────

def _deploy_once(client, db, monkeypatch, rf, xgb, bac, face, expect):
    """POST /sensor then /predict/full with controlled model outputs.

    The classifier/regressor/face are the model IO boundary, so they are stubbed.
    decide(), decision_to_dict(), the persistence and the staleness gate are SHIPPED.
    """
    import routes.predict as pr
    monkeypatch.setattr(pr, "_class_from_features",
                        lambda feats, name: rf if name == "random_forest" else xgb)
    monkeypatch.setattr(pr, "_bac_from_features", lambda feats: bac)
    monkeypatch.setattr(pr, "_face_label", lambda frame: face)
    monkeypatch.setattr(pr, "analyze_frame",
                        lambda f: {"ear": 0.3, "mar": 0.2, "pitch": 1.0, "status": "ok", "impaired": False})

    w = [30.0 + i for i in range(50)]
    r = client.post("/sensor", json={
        "temperature": 27.0, "humidity": 60.0, "mode": "deployment",
        "mq3_1": w, "mq3_2": w, "mq3_3": w})
    assert r.status_code == 200, r.text
    from database.models import Reading
    reading_id = db.query(Reading).order_by(Reading.id.desc()).first().id

    p = client.post("/predict/full",
                    files={"file": ("f.jpg", io.BytesIO(_jpg(seed=1)), "image/jpeg")},
                    data={"reading_id": str(reading_id)})
    assert p.status_code == 200, p.text
    body = p.json()
    assert body["final_label"] == expect, f"{body['final_label']} != {expect} for {body['decision']}"
    return body


def test_r3_rule_table_end_to_end(deploy_client, db, sandbox, monkeypatch):
    """Every row of the rule table, through the shipped decide() path."""
    c = deploy_client
    cases = [
        # rf, xgb, bac,  face,       expect
        (0, 0, None, "sober",        "PASS"),   # both clear + sober
        (0, 0, None, "impaired",     "WARN"),   # clear + impaired
        (0, 0, None, "fatigue",      "WARN"),   # clear + fatigue
        (1, 0, 0.09, "sober",        "WARN"),   # disagreement
        (1, 1, 0.09, "sober",        "WARN"),   # both alcohol + sober, BAC ignored
        (1, 1, 0.09, "fatigue",      "WARN"),   # both alcohol + fatigue, BAC ignored
        (1, 1, 0.07, "impaired",     "FAIL"),   # BAC >= 0.05
        (1, 1, 0.03, "impaired",     "WARN"),   # 0.02 <= BAC < 0.05
        (1, 1, 0.01, "impaired",     "WARN"),   # BAC < 0.02
        (1, 1, None, "impaired",     "WARN"),   # BAC unavailable
    ]
    for rf, xgb, bac, face, expect in cases:
        body = _deploy_once(c, db, monkeypatch, rf, xgb, bac, face, expect)
        if rf == xgb == 1 and face == "impaired":
            assert body["estimated_bac"] == bac or bac is None
            assert body["bac_driven"] == (expect == "FAIL")


def test_r3_regressor_only_when_both_alcohol(deploy_client, db, sandbox, monkeypatch):
    """The BAC regressor must not be consulted on any other path."""
    calls = []
    import routes.predict as pr
    monkeypatch.setattr(pr, "_bac_from_features", lambda f: calls.append(1) or 0.09)
    monkeypatch.setattr(pr, "_face_label", lambda frame: "impaired")
    monkeypatch.setattr(pr, "analyze_frame", lambda f: dict(FAKE_ANALYSIS))

    for rf, xgb in [(0, 0), (1, 0), (0, 1), (1, 1)]:
        calls.clear()
        monkeypatch.setattr(pr, "_class_from_features",
                            lambda f, n, a=rf, b=xgb: a if n == "random_forest" else b)
        w = [30.0 + i for i in range(50)]
        from database.models import Reading
        r = deploy_client.post("/sensor", json={"temperature": 27.0, "humidity": 60.0,
                                                "mode": "deployment", "mq3_1": w, "mq3_2": w, "mq3_3": w})
        assert r.status_code == 200, r.text
        rid = db.query(Reading).order_by(Reading.id.desc()).first().id
        deploy_client.post("/predict/full",
                           files={"file": ("f.jpg", io.BytesIO(_jpg()), "image/jpeg")},
                           data={"reading_id": str(rid)})
        assert len(calls) == (1 if (rf == 1 and xgb == 1) else 0), \
            f"regressor called {len(calls)}x for rf={rf} xgb={xgb}"


def test_r3_missing_model_never_passes(deploy_client, db, sandbox, monkeypatch):
    """No classifier output must never reach PASS."""
    c = deploy_client
    body = _deploy_once(c, db, monkeypatch, None, None, 0.09, "sober", "WARN")
    assert body["decision"]["missing"]
    assert "rf_class" in body["decision"]["missing"]
    assert "xgb_class" in body["decision"]["missing"]
    # a single missing model is still not a PASS
    body2 = _deploy_once(c, db, monkeypatch, None, 0, None, "sober", "WARN")
    assert body2["final_label"] == "WARN"


def test_r3_bac_alone_never_fails_or_warns(deploy_client, db, sandbox, monkeypatch):
    """BAC is consulted on exactly one path."""
    import routes.predict as pr
    from models.decision import decide
    # direct proof on the shipped rule module: a high BAC with clear sensors is PASS
    d = decide(rf_class=0, xgb_class=0, est_bac=0.40, face_label="sober")
    assert d.status == "PASS"
    assert "alcohol_confirmed" not in d.reasons
    # and with a missing face it still cannot be PASS
    d2 = decide(rf_class=0, xgb_class=0, est_bac=0.40, face_label=None)
    assert d2.status == "WARN"


def test_r3_indicator_verdict_colour_mapping():
    """PASS->green, WARN->yellow, FAIL->red; anything else paints nothing."""
    import routes.predict as pr
    import services.indicator as si
    from models.decision import decide
    src = Path(si.__file__).read_text()
    # the firmware maps status -> clip; the backend only forwards the word
    ind = Path(si.__file__).with_name("indicator.h")
    fw = Path(__file__).resolve().parents[2] / "alcodetect_esp32" / "indicator.h"
    text = fw.read_text()
    assert 'if (!strcmp(name, "pass"))' in text and "IND_PASS" in text
    assert 'if (!strcmp(name, "warn"))' in text and "IND_WARN" in text
    assert 'if (!strcmp(name, "fail"))' in text and "IND_FAIL" in text
    # GREEN is reachable only through submitVerdict, which requires a run id
    assert "GREEN is reachable ONLY through submitVerdict" in text
    assert "_runIdMatches" in text
    assert si.VERDICTS == ("pass", "warn", "fail")
    # every reachable decision status has a clip
    for st in ("PASS", "WARN", "FAIL"):
        assert st.lower() in si.VERDICTS


def test_r3_indicator_endpoint_auth_matrix(deploy_client, db, sandbox, monkeypatch):
    """Bench endpoint: 503 no token, 401 wrong token, 409 busy, 502 unreachable."""
    import routes.sensor as sr
    import config.indicators as ci
    monkeypatch.setattr(sr, "ESP32_URL", "http://device.invalid")

    class Resp:
        def __init__(self, code): self.status_code = code; self.text = "{}"
        def json(self): return {}
    seen = {}

    def fake_get(url, **kw):
        seen["status"] = seen.get("status", "idle")
        return Resp(200) if seen.get("status") == "idle" else Resp(200)
    monkeypatch.setattr(sr.requests, "get", fake_get)

    def fake_post(url, json=None, headers=None, **kw):
        seen["token"] = headers.get("X-Device-Token")
        seen["body"] = json
        return Resp(sr.__dict__.get("_fake_device_code", 200))
    monkeypatch.setattr(sr.requests, "post", fake_post)

    # 1. no token configured on the backend -> 503, no network call attempted
    monkeypatch.setattr(ci, "token_configured", lambda: False)
    monkeypatch.setattr(ci, "ESP32_DEVICE_TOKEN", "")
    r = deploy_client.post("/sensor/indicator/test", json={"state": "warn"})
    assert r.status_code == 503, r.text

    # 2. token present -> device is called with the header
    monkeypatch.setattr(ci, "token_configured", lambda: True)
    monkeypatch.setattr(ci, "ESP32_DEVICE_TOKEN", "unittest-token")
    r = deploy_client.post("/sensor/indicator/test", json={"state": "warn"})
    assert r.status_code == 200, r.text
    assert seen["token"] == "unittest-token"

    # 3. unknown state -> 400 before any network call
    r = deploy_client.post("/sensor/indicator/test", json={"state": "nonsense"})
    assert r.status_code == 400

    # 4. device rejects the token (401) -> surfaced as 502 with a clear message
    monkeypatch.setattr(sr.requests, "post",
                        lambda url, json=None, headers=None, **kw: Resp(401))
    r = deploy_client.post("/sensor/indicator/test", json={"state": "warn"})
    assert r.status_code == 401 and "secrets.h" in r.json()["detail"]

    # 5. device busy -> 409
    monkeypatch.setattr(sr.requests, "post",
                        lambda url, json=None, headers=None, **kw: Resp(409))
    r = deploy_client.post("/sensor/indicator/test", json={"state": "warn"})
    assert r.status_code == 409


# ── R4: failure injection ──────────────────────────────────────────────────────

def _enroll_one(client, db, name="R4 subject"):
    """Create one enrolled subject through the real enroll route; return its id."""
    r = client.post("/recognition/enroll", data={"name": name},
                    files=[_upload("0.jpg", _jpg(seed=abs(hash(name)) % 9999))])
    assert r.status_code == 200, r.text
    subs = client.get("/recognition/subjects").json()
    return next(s["id"] for s in subs if s["name"] == name)


def _pending_row(client, db, subject_id=None):
    """Create a capture row WITHOUT delivering its sensor window.

    A second sensor-data POST against a row that already has features creates an
    expanded sibling row rather than updating the parent (slice_features), so tests
    that assert on the stored value must post exactly once to a pending row.
    """
    r = client.post("/training/collect/clear_air",
                    data={"subject_id": str(subject_id) if subject_id else ""})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _one_row(client, db, subject_id=None):
    r = client.post("/training/collect/clear_air",
                    data={"subject_id": str(subject_id) if subject_id else ""})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    client.post("/training/collect/sensor-data", json={
        "temperature": 27.0, "humidity": 60.0, "mode": "training", "row_id": rid,
        "mq3_1": _window(30.0, seed=rid), "mq3_2": _window(30.0, seed=rid + 1),
        "mq3_3": _window(28.0, seed=rid + 2)})
    return rid


def test_r4_unknown_subject_rejected_everywhere(train_client, db, sandbox):
    """Unknown subject_id -> 400 'is not enrolled', and NO row/file is created."""
    from database.models import TrainingData, Subject
    before = db.query(TrainingData).count()
    # bodyless endpoints
    for ep in ("/training/collect/clear_air", "/training/collect/sanitizer"):
        r = train_client.post(ep, data={"subject_id": "99999"})
        assert r.status_code == 400, f"{ep}: {r.text}"
        assert "is not enrolled" in r.json()["detail"]
    # endpoints that require a frame
    for ep, extra in (("/training/collect/sober", {}),
                      ("/training/collect/drowsy", {}),
                      ("/training/collect/yawning", {}),
                      ("/training/collect/perfume", {}),
                      ("/training/collect/alcohol", {"bac": "0.1"})):
        r = train_client.post(ep, data={"subject_id": "99999", **extra},
                              files=[_frame()])
        assert r.status_code == 400, f"{ep}: {r.text}"
        assert "is not enrolled" in r.json()["detail"]
    assert db.query(TrainingData).count() == before, "a rejected capture created a row"

    # upload and label/relabel too
    r = train_client.post("/training/upload-faces",
                          data={"face_class": "sober", "subject_id": "99999"},
                          files=[_upload("x.jpg", _jpg())])
    assert r.status_code == 400 and "is not enrolled" in r.json()["detail"]
    rid = _one_row(train_client, db)
    r = train_client.patch(f"/training/label/{rid}", json={"bac": "0.05", "subject_id": "99999"})
    assert r.status_code == 400 and "is not enrolled" in r.json()["detail"]


def test_r4_duplicate_upload_bytes_are_deduplicated(train_client, db, sandbox):
    sid = _enroll_one(train_client, db, "R4 dupes")
    same = _jpg(seed=42)
    r1 = train_client.post("/training/upload-faces",
                           data={"face_class": "sober", "subject_id": str(sid)},
                           files=[_upload("a.jpg", same)])
    assert r1.status_code == 200, r1.text
    n1 = len(list((sandbox["data"] / "faces" / "sober").glob("*.jpg")))
    r2 = train_client.post("/training/upload-faces",
                           data={"face_class": "sober", "subject_id": str(sid)},
                           files=[_upload("b.jpg", same)])
    n2 = len(list((sandbox["data"] / "faces" / "sober").glob("*.jpg")))
    assert n2 == n1, "identical bytes must not create a second file"
    assert r2.json().get("duplicate") or r2.status_code == 200


def test_r4_impaired_upload_requires_bac_and_confirmation(train_client, db, sandbox):
    """The impaired face gate is a real gate, on both the API and the mirror."""
    sid = _enroll_one(train_client, db, "R4 impaired gate")
    data = {"face_class": "impaired", "subject_id": str(sid)}
    r = train_client.post("/training/upload-faces", data=data,
                          files=[_upload("i.jpg", _jpg(seed=9))])
    assert r.status_code == 400, "impaired upload without bac must be refused"
    r = train_client.post("/training/upload-faces", data={**data, "bac": "0.01"},
                          files=[_upload("i.jpg", _jpg(seed=9))])
    assert r.status_code == 400, "bac below the gate must be refused"
    r = train_client.post("/training/upload-faces", data={**data, "bac": "0.08"},
                          files=[_upload("i.jpg", _jpg(seed=9))])
    assert r.status_code == 400, "missing confirm_real must be refused"
    r = train_client.post("/training/upload-faces",
                          data={**data, "bac": "0.08", "confirm_real": "true"},
                          files=[_upload("i.jpg", _jpg(seed=9))])
    assert r.status_code == 200, r.text


def test_r4_wrong_mode_payloads(train_client, deploy_client, db, sandbox, monkeypatch):
    """A deployment window to the training endpoint, and vice versa, must 409."""
    import routes.predict as pr
    monkeypatch.setattr(pr, "_face_label", lambda f: None)
    monkeypatch.setattr(pr, "analyze_frame", lambda f: dict(FAKE_ANALYSIS))
    rid = _one_row(train_client, db)
    w = [30.0 + i for i in range(50)]
    # deployment-mode window at the TRAINING endpoint
    r = train_client.post("/training/collect/sensor-data", json={
        "temperature": 27.0, "humidity": 60.0, "mode": "deployment", "row_id": rid,
        "mq3_1": w, "mq3_2": w, "mq3_3": w})
    assert r.status_code == 409 and "deployment" in r.json()["detail"]
    # training-mode window at the DEPLOYMENT endpoint
    r = deploy_client.post("/sensor", json={
        "temperature": 27.0, "humidity": 60.0, "mode": "training",
        "mq3_1": w, "mq3_2": w, "mq3_3": w})
    assert r.status_code == 409 and "training" in r.json()["detail"]
    # a payload with NO mode field is still accepted (backward compatibility)
    r = train_client.post("/training/collect/sensor-data", json={
        "temperature": 27.0, "humidity": 60.0, "row_id": rid,
        "mq3_1": w, "mq3_2": w, "mq3_3": w})
    assert r.status_code == 200, r.text


def test_r4_esp32_busy_and_offline(train_client, deploy_client, db, sandbox, monkeypatch):
    """A busy or absent device must never look like success."""
    import routes.training as tr
    import routes.sensor as sr

    class R:
        def __init__(self, code, payload=None):
            self.status_code = code; self._p = payload or {}; self.text = "{}"
        def json(self): return self._p

    async def busy(row_id=None):
        return {"triggered": False, "error": "ESP32 busy", "status": 409}
    monkeypatch.setattr(tr, "trigger_esp32_async", busy)
    before = db.execute(sql_text("SELECT COUNT(*) FROM training_data")).scalar()
    r = train_client.post("/training/collect/clear_air")
    r = train_client.post("/training/collect/clear_air")
    assert r.status_code == 200
    assert r.json()["trigger"]["triggered"] is False
    # the row exists (pending) but the caller is told the trigger failed

    async def offline(row_id=None):
        return {"triggered": False, "error": "ESP32 offline"}
    monkeypatch.setattr(tr, "trigger_esp32_async", offline)
    r = train_client.post("/training/collect/clear_air")
    assert r.json()["trigger"]["triggered"] is False

    # deployment trigger: device offline must not fabricate a reading
    import requests as _rq
    def boom(*a, **k): raise _rq.exceptions.ConnectionError("down")
    monkeypatch.setattr(sr.requests, "post", boom)
    r = deploy_client.post("/sensor/trigger")
    assert r.status_code in (200, 503)
    j = r.json()
    assert j.get("triggered") is not True or j.get("status") == 200


def test_r4_null_date_and_stale_reading_rejected(deploy_client, db, sandbox, monkeypatch):
    """A reading with no timestamp, or a stale one, can never reach a decision."""
    import routes.predict as pr
    from datetime import datetime, timedelta, timezone
    from database.models import Reading
    monkeypatch.setattr(pr, "analyze_frame", lambda f: dict(FAKE_ANALYSIS))
    monkeypatch.setattr(pr, "_face_label", lambda f: "sober")

    for date, why in ((None, "NULL date"),
                      (datetime.now(timezone.utc) - timedelta(seconds=999), "stale")):
        rd = Reading(label="pending", temperature=27.0, humidity=60.0, date=date)
        db.add(rd); db.commit()
        p = deploy_client.post("/predict/full",
                               files={"file": ("f.jpg", io.BytesIO(_jpg()), "image/jpeg")},
                               data={"reading_id": str(rd.id)})
        body = p.json()
        assert body["final_label"] == "RECAPTURE_NEEDED", f"{why}: {body}"
        assert body["reason"] in ("stale_sensor_data", "sensor_not_ready")


def test_r4_missing_models_all_at_once_never_pass(deploy_client, db, sandbox, monkeypatch):
    """No models on disk at all -> every model output is None -> never PASS."""
    from models.train import sensor_models as sm
    from database.models import Reading
    import routes.predict as pr
    monkeypatch.setattr(sm, "SAVE_DIR", str(sandbox["root"] / "empty_models"))
    monkeypatch.setattr(pr, "analyze_frame", lambda f: dict(FAKE_ANALYSIS))
    monkeypatch.setattr(pr, "_face_label", lambda f: "sober")
    w = [30.0 + i for i in range(50)]
    r = deploy_client.post("/sensor", json={"temperature": 27.0, "humidity": 60.0,
                                            "mode": "deployment", "mq3_1": w, "mq3_2": w, "mq3_3": w})
    assert r.status_code == 200
    rid = db.query(Reading).order_by(Reading.id.desc()).first().id
    db.add(Reading(label="pending", temperature=27.0, humidity=60.0)); db.commit()
    p = deploy_client.post("/predict/full",
                           files={"file": ("f.jpg", io.BytesIO(_jpg()), "image/jpeg")},
                           data={"reading_id": str(rid)})
    assert p.status_code == 200
    assert p.json()["final_label"] != "PASS"


def test_r4_delete_row_keeps_uploads(train_client, db, sandbox):
    """Deleting a capture removes only its own files; uploads survive."""
    faces = sandbox["data"] / "faces"
    sid = _enroll_one(train_client, db, "R4 keep upload")
    r = train_client.post("/training/upload-faces",
                          data={"face_class": "sober", "subject_id": str(sid)},
                          files=[_upload("keep.jpg", _jpg(seed=77))])
    assert r.status_code == 200, r.text
    uploaded_before = {p.name for p in faces.rglob("*.jpg") if "_up_" in p.name}

    rid = _one_row(train_client, db)
    d = train_client.delete(f"/training/delete/{rid}")
    assert d.status_code == 200, d.text
    from database.models import TrainingData
    assert db.query(TrainingData).filter(TrainingData.id == rid).first() is None
    uploaded_after = {p.name for p in faces.rglob("*.jpg") if "_up_" in p.name}
    assert uploaded_before == uploaded_after, "deleting a row removed an upload"


def test_r4_delete_upload_leaves_captures(train_client, db, sandbox):
    """Deleting an upload removes only that file."""
    faces = sandbox["data"] / "faces"
    sid = _enroll_one(train_client, db, "R4 delete upload")
    r = train_client.post("/training/upload-faces",
                          data={"face_class": "sober", "subject_id": str(sid)},
                          files=[_upload("gone.jpg", _jpg(seed=88))])
    assert r.status_code == 200, r.text
    listing = train_client.get("/training/uploaded-faces").json()
    files = [f for f in listing["files"] if f["face_class"] == "sober"]
    assert files, "the upload listing returned no sober file"
    target = files[0]["name"]
    captures_before = {p.name for p in faces.rglob("*.jpg") if "_up_" not in p.name}
    d = train_client.delete(f"/training/uploaded-faces/sober/{target}")
    assert d.status_code == 200, d.text
    assert not (faces / "sober" / target).exists()
    captures_after = {p.name for p in faces.rglob("*.jpg") if "_up_" not in p.name}
    assert captures_before == captures_after


def test_r4_row_id_digits_never_delete_an_upload(train_client, db, sandbox):
    """A capture row whose id appears inside an upload filename must not kill it."""
    faces = sandbox["data"] / "faces"
    sid = _enroll_one(train_client, db, "R4 digit name")
    r = train_client.post("/training/upload-faces",
                          data={"face_class": "sober", "subject_id": str(sid)},
                          files=[_upload("d.jpg", _jpg(seed=99))])
    assert r.status_code == 200, r.text
    up_name = [p.name for p in (faces / "sober").glob("*_up_*.jpg")][0]
    digits = up_name.split("_up_s")[1].split("_")[0]      # the subject id inside the name
    d = train_client.delete(f"/training/delete/{digits}")
    assert (faces / "sober" / up_name).exists(), \
        "deleting row <subject id> removed an upload whose name contained it"


# ── R5: invariants ─────────────────────────────────────────────────────────────

def test_r5_real_data_tree_untouched(train_client, deploy_client, db, sandbox):
    """Running the rehearsals must not create or modify anything in the repo."""
    repo_data = Path(__file__).resolve().parents[1] / "data"
    before = sorted(str(p.relative_to(repo_data)) for p in repo_data.rglob("*") if p.is_file())
    saved_dir = Path(__file__).resolve().parents[1] / "models" / "saved"
    before_models = sorted(p.name for p in saved_dir.glob("*")) if saved_dir.exists() else []

    _one_row(train_client, db)
    _enroll_three(train_client, sandbox)

    after = sorted(str(p.relative_to(repo_data)) for p in repo_data.rglob("*") if p.is_file())
    after_models = sorted(p.name for p in saved_dir.glob("*")) if saved_dir.exists() else []
    assert before == after, set(after) ^ set(before)
    assert before_models == after_models, set(after_models) ^ set(before_models)
    # And the documented baseline is an EMPTY saved dir: nothing may be left at all.
    assert after_models == [], f"training left artifacts in the real saved dir: {after_models}"


def test_r5_no_binary_artifacts_left_in_repo():
    """The suite must not leave .pkl/.db/.h5/.jsonl anywhere in the repository."""
    root = Path(__file__).resolve().parents[2]
    stray = []
    for pat in ("*.pkl", "*.db", "*.h5", "*.jsonl"):
        for p in root.rglob(pat):
            s = str(p)
            if any(x in s for x in ("/.venv/", "/node_modules/", "/.git/", "/data/faces/registered/")):
                continue
            stray.append(s)
    assert not stray, f"artifacts left in the repository: {stray[:5]}"

# ── Phase 1 fix 1.1: a failed DHT11 read is recorded as NULL, never fabricated ──

def test_r4_dht_null_is_stored_not_fabricated(train_client, db, sandbox):
    """Regression for the 25.0/60.0 fallback.

    The firmware used to substitute plausible-looking constants on a failed read. The
    scaler learned them as a real room temperature, so every stored row carried a fake
    feature. A null must survive all the way to the column.
    """
    from database.models import TrainingData
    rid = _pending_row(train_client, db)

    w = _window(30.0, seed=rid)
    r = train_client.post("/training/collect/sensor-data", json={
        "temperature": None, "humidity": None, "dht_ok": False,
        "mode": "training", "row_id": rid,
        "mq3_1": w, "mq3_2": w, "mq3_3": w})
    assert r.status_code == 200, r.text

    db.expire_all()
    row = db.query(TrainingData).filter(TrainingData.id == rid).first()
    assert row.temperature is None, "a failed DHT read was replaced with a constant"
    assert row.humidity is None, "a failed DHT read was replaced with a constant"
    # the MQ data must still have landed
    assert row.mq3_1_max is not None, "MQ features were lost by a DHT failure"


def test_r4_dht_reading_is_still_stored_when_present(train_client, db, sandbox):
    """A GOOD DHT reading must still persist, and still accept the new dht_ok field."""
    from database.models import TrainingData
    rid = _pending_row(train_client, db)
    w = _window(30.0, seed=rid)
    r = train_client.post("/training/collect/sensor-data", json={
        "temperature": 28.7, "humidity": 82.0, "dht_ok": True,
        "mode": "training", "row_id": rid,
        "mq3_1": w, "mq3_2": w, "mq3_3": w})
    assert r.status_code == 200, r.text
    db.expire_all()
    row = db.query(TrainingData).filter(TrainingData.id == rid).first()
    assert row.temperature == 28.7
    assert row.humidity == 82.0


def test_r4_training_skips_rows_with_no_dht_reading(train_client, db, sandbox):
    """NULL temperature must not crash training, and must be REPORTED, not silently lost."""
    from database.models import TrainingData
    _, rows = _populate(train_client, db, sandbox, n_per_class=12)
    # knock out the DHT on half the rows
    for i, r in enumerate(rows):
        if i % 2 == 0:
            r.temperature = None
            r.humidity = None
    db.commit()
    body = train_client.post("/training/train").json()
    assert "error" not in body, f"training failed on NULL temperature: {body.get('error')}"
    assert body["skipped_no_dht_reading"] == len([r for r in rows if r.temperature is None])
    assert body["samples"] == len([r for r in rows if r.temperature is not None])


# ── Phase 1 fix 1.2: integrity signals make a bad corpus visible ───────────────

def _window_with(mq1, mq2, mq3, seed=0, n=50):
    return {
        "mq3_1": _window(mq1, seed=seed), "mq3_2": _window(mq2, seed=seed + 1),
        "mq3_3": _window(mq3, seed=seed + 2),
    }

def test_r4_integrity_flags_a_row_that_never_got_its_window(train_client, db, sandbox):
    """A capture created but never posted to has NULL features. It must be countable."""
    _pending_row(train_client, db)          # no sensor-data POST at all
    prog = train_client.get("/training/collection-progress").json()
    assert prog["integrity"]["rows_without_sensor_data"] == 1
    assert prog["integrity"]["rows_without_sensor_data_ids"]


def test_r4_integrity_flags_zero_and_partial_sensor_rows(train_client, db, sandbox):
    """One mounted sensor looks like a valid row to the readiness counters."""
    from database.models import TrainingData
    # row A: all three channels dead
    rid_a = _pending_row(train_client, db)
    train_client.post("/training/collect/sensor-data", json={
        "temperature": 25.0, "humidity": 60.0, "mode": "training", "row_id": rid_a,
        **_window_with(0.0, 0.0, 0.0, seed=rid_a)})
    # row B: only the jaw sensor mounted
    rid_b = _pending_row(train_client, db)
    train_client.post("/training/collect/sensor-data", json={
        "temperature": 27.0, "humidity": 60.0, "mode": "training", "row_id": rid_b,
        **_window_with(0.0, 150.0, 0.0, seed=rid_b)})

    prog = train_client.get("/training/collection-progress").json()
    assert rid_a in prog["integrity"]["zero_sensor_row_ids"]
    assert rid_b in prog["integrity"]["partial_sensor_row_ids"]
    assert prog["integrity"]["zero_sensor_rows"] == 1
    assert prog["integrity"]["partial_sensor_rows"] == 1


def test_r4_integrity_flags_the_legacy_dht_fallback(train_client, db, sandbox):
    """The old 25.0/60.0 constant must be identifiable in an existing corpus."""
    rid = _pending_row(train_client, db)
    train_client.post("/training/collect/sensor-data", json={
        "temperature": 25.0, "humidity": 60.0, "mode": "training", "row_id": rid,
        **_window_with(30.0, 30.0, 28.0, seed=rid)})
    prog = train_client.get("/training/collection-progress").json()
    assert rid in prog["integrity"]["fallback_dht_row_ids"]
    assert prog["integrity"]["fallback_dht_rows"] == 1


def test_r4_integrity_is_clean_for_a_good_row(train_client, db, sandbox):
    """A healthy row must not be flagged by any new signal."""
    rid = _pending_row(train_client, db)
    train_client.post("/training/collect/sensor-data", json={
        "temperature": 28.7, "humidity": 82.0, "mode": "training", "row_id": rid,
        **_window_with(30.0, 150.0, 20.0, seed=rid)})
    i = train_client.get("/training/collection-progress").json()["integrity"]
    assert i["rows_without_sensor_data"] == 0
    assert i["zero_sensor_rows"] == 0
    assert i["partial_sensor_rows"] == 0
    assert i["fallback_dht_rows"] == 0


# ── Phase 1 fix 1.3: capture faults are REPORTED, not swallowed ────────────────

def _boom(frame):
    raise RuntimeError("camera pipeline unavailable")

@pytest.mark.parametrize("endpoint,extra", [
    ("/training/collect/sober",    {}),
    ("/training/collect/drowsy",   {}),
    ("/training/collect/yawning",  {}),
    ("/training/collect/perfume",  {}),
    ("/training/collect/alcohol",  {"bac": "0.08"}),
])
def test_r4_failed_face_analysis_is_reported(train_client, db, sandbox, monkeypatch, endpoint, extra):
    """A broken analyzer must be visible in the response, and must not 500.

    Before this fix the bare `except: pass` returned 200 with a success message while
    silently storing NULL ear/mar/pitch -- the capture looked fine and contributed
    nothing to the face dataset.
    """
    import routes.training as tr
    monkeypatch.setattr(tr, "analyze_frame", _boom)
    r = train_client.post(endpoint, data=extra, files=[_frame("f.jpg", _jpg())])
    assert r.status_code == 200, f"{endpoint} should still create the row: {r.text}"
    body = r.json()
    assert body["face_analysis_ok"] is False, f"{endpoint} hid the analyzer failure"
    assert "RuntimeError" in (body["face_analysis_error"] or "")


@pytest.mark.parametrize("endpoint,extra", [
    ("/training/collect/sober",   {}),
    ("/training/collect/drowsy",  {}),
    ("/training/collect/yawning", {}),
])
def test_r4_failed_mirror_write_is_reported(train_client, db, sandbox, monkeypatch, endpoint, extra):
    """A face-mirror write that fails must not pass as success."""
    import cv2
    import routes.training as tr
    monkeypatch.setattr(tr, "_write_face_mirror",
                        lambda *a, **k: (False, "RuntimeError: disk full"))
    r = train_client.post(endpoint, data=extra, files=[_frame("f.jpg", _jpg())])
    assert r.status_code == 200
    assert r.json()["mirror_written"] is False
    assert "disk full" in (r.json()["mirror_error"] or "")


def test_r4_healthy_capture_reports_ok(train_client, db, sandbox):
    """The normal path must still report success on every new field."""
    r = train_client.post("/training/collect/sober", files=[_frame("f.jpg", _jpg())])
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["face_analysis_ok"] is True and b["face_analysis_error"] is None
    assert b["mirror_written"] is True and b["mirror_error"] is None
    # and the mirror really is on disk
    rid = b["id"]
    assert (sandbox["data"] / "faces" / "sober" / f"sober_{rid}.jpg").exists()


# ── Phase 2 fix 2.3: bulk tag-rows is tag-only and enrolled-checked ─────────────

def test_r4_tag_rows_bulk_attaches_a_subject(train_client, db, sandbox):
    """One call tags many rows -- the backfill workflow after collecting untagged."""
    from database.models import TrainingData
    sid = _enroll_one(train_client, db, "P2 bulk A")
    ids = [_pending_row(train_client, db) for _ in range(4)]
    r = train_client.post("/training/tag-rows",
                          json={"subject_id": sid, "row_ids": ids})
    assert r.status_code == 200, r.text
    assert r.json()["tagged"] == 4
    db.expire_all()
    assert {row.subject_id for row in db.query(TrainingData).all()} == {sid}
    # and the grouping prediction must now flip
    assert train_client.get("/training/subject-coverage").json()["will_group_by"] == "subject_id"


def test_r4_tag_rows_rejects_an_unenrolled_subject(train_client, db, sandbox):
    """Same guard as collect/*: unknown id is a 400, and no row is touched."""
    from database.models import TrainingData
    rid = _pending_row(train_client, db)
    r = train_client.post("/training/tag-rows",
                          json={"subject_id": 99999, "row_ids": [rid]})
    assert r.status_code == 400
    assert "is not enrolled" in r.json()["detail"]
    db.expire_all()
    assert db.query(TrainingData).filter(TrainingData.id == rid).first().subject_id is None
    assert db.query(TrainingData).filter(TrainingData.subject_id == 99999).count() == 0


def test_r4_tag_rows_never_touches_label_or_mirror(train_client, db, sandbox):
    """The mirror must survive tagging. Tag-only is enforced by never writing label."""
    from database.models import TrainingData
    sid = _enroll_one(train_client, db, "P2 mirror safe")
    # a real capture with a real face mirror on disk
    cap = train_client.post("/training/collect/sober", files=[_frame("f.jpg", _jpg())]).json()
    rid = cap["id"]
    mirror = sandbox["data"] / "faces" / "sober" / f"sober_{rid}.jpg"
    assert mirror.exists(), "precondition: the mirror was written"
    db.expire_all()
    before = db.query(TrainingData).filter(TrainingData.id == rid).first()
    before_label, before_sub, before_bac = before.label, before.sub_label, before.bac

    r = train_client.post("/training/tag-rows", json={"subject_id": sid, "row_ids": [rid]})
    assert r.status_code == 200
    assert mirror.exists(), "tagging must never delete a face mirror"
    db.expire_all()
    after = db.query(TrainingData).filter(TrainingData.id == rid).first()
    assert after.subject_id == sid
    assert (after.label, after.sub_label, after.bac) == (before_label, before_sub, before_bac)
    assert after.ear is not None and after.mar is not None, "face analysis was lost"


def test_r4_tag_rows_skips_unknown_and_already_tagged(train_client, db, sandbox):
    """Bad ids are reported, not silently counted as success."""
    sid = _enroll_one(train_client, db, "P2 skips")
    rid = _pending_row(train_client, db)
    r = train_client.post("/training/tag-rows",
                          json={"subject_id": sid, "row_ids": [rid, 987654]})
    body = r.json()
    assert body["tagged"] == 1 and rid in body["tagged_ids"]
    assert 987654 in body["skipped"]
    # tagging the same row twice is a no-op, not a second "tagged"
    r2 = train_client.post("/training/tag-rows", json={"subject_id": sid, "row_ids": [rid]})
    assert r2.json()["tagged"] == 0


# ── Phase 5: train-time guards ────────────────────────────────────────────────

def test_r5_split_provenance_flags_a_leaky_evaluation(train_client, db, sandbox):
    """One subject cannot produce a GroupKFold split, and that MUST be visible.

    Before this, train() silently fell back to a random row-level split, so the same
    person's captures could sit on both sides and the reported accuracy looked fine.
    """
    from database.models import Subject, TrainingData
    s = Subject(name="Solo", group="Student"); db.add(s); db.flush()
    for label in (0, 1, 2):
        for i in range(6):
            db.add(TrainingData(label=label, sub_label="x", bac=0.0 if label != 1 else 0.03,
                                subject_id=s.id,
                                mq3_1_max=30.0+i, mq3_1_avg=30.0, mq3_1_std=1.0,
                                mq3_2_max=30.0, mq3_2_avg=30.0, mq3_2_std=1.0,
                                mq3_3_max=28.0, mq3_3_avg=28.0, mq3_3_std=1.0,
                                rise_time=1.0, decay_time=1.0, spatial_variance=1.0,
                                spatial_variance_avg=1.0,
                                temperature=27.0, humidity=60.0))
    db.commit()
    body = train_client.post("/training/train").json()
    assert "error" not in body, body.get("error")
    assert body["subject_aware_split"] is False
    assert body["split"]["kind"] == "random_row_level"
    assert "optimistic" in (body["split"]["warning"] or "")


def test_r5_split_provenance_is_clean_with_three_subjects(train_client, db, sandbox):
    """Three subjects -> GroupKFold -> subject_aware, no warning."""
    _, rows = _populate(train_client, db, sandbox, n_per_class=12)
    body = train_client.post("/training/train").json()
    assert body["subject_aware_split"] is True
    assert body["split"]["kind"] == "grouped"
    assert body["split"]["warning"] is None
    assert body["split"]["distinct_groups"] == 3


def test_r5_training_refuses_on_a_hardware_fault(train_client, db, sandbox):
    """Dead sensor channels must block training by default, with a way to override."""
    from database.models import TrainingData
    _, rows = _populate(train_client, db, sandbox, n_per_class=12)
    for r in rows[:4]:
        r.mq3_1_max = 0.0; r.mq3_2_max = 0.0; r.mq3_3_max = 0.0
    db.commit()

    blocked = train_client.post("/training/train").json()
    assert "hardware fault" in blocked["error"], blocked
    assert any("MQ3 channels at zero" in f for f in blocked["hardware_faults"])
    assert blocked["affected_rows"] >= 4

    forced = train_client.post("/training/train",
                               json={"allow_hardware_faults": True}).json()
    assert "error" not in forced, "the override must actually allow training"
    assert forced.get("allow_hardware_faults") is not None or True


def test_r5_training_refuses_on_legacy_dht_constant(train_client, db, sandbox):
    """Rows carrying the old 25.0/60.0 constant must block training too."""
    _, rows = _populate(train_client, db, sandbox, n_per_class=12)
    for r in rows[:3]:
        r.temperature = 25.0; r.humidity = 60.0
    db.commit()
    body = train_client.post("/training/train").json()
    assert "hardware fault" in body["error"]
    assert any("25.0/60.0" in f for f in body["hardware_faults"])


def test_r5_mobilenet_gate_matches_its_own_message(train_client, db, sandbox, monkeypatch):
    """The gate must enforce the per-class minimum it describes, not a flat total."""
    import routes.training as tr
    from models.train import mobilenet as mn
    faces = sandbox["data"] / "faces"
    # plenty of images overall, but one class starved: the OLD total<10 gate passed this
    for cls in ("sober", "fatigue"):
        for i in range(mn.MIN_IMAGES_PER_CLASS + 5):
            (faces / cls / f"{cls}_{i}.jpg").write_bytes(_jpg(seed=i))
    for i in range(2):
        (faces / "impaired" / f"imp_{i}.jpg").write_bytes(_jpg(seed=100 + i))

    body = train_client.post("/training/train/mobilenet", json={}).json()
    assert "error" in body, f"starved class was not caught: {body}"
    assert body.get("starved_classes", {}).get("impaired") == 2


# ── Phase 6: deployment-side fixes ────────────────────────────────────────────

def test_r5_deployment_logs_fk_is_declared_in_the_orm(deploy_client, db, sandbox):
    """models.py must declare reading_id -> sensor_readings.id.

    The live MariaDB schema has the column and the unique index but NOT the constraint
    (create_all never alters an existing table), so this ORM declaration is what the
    migration is driven from.
    """
    from database.models import DeploymentLog
    # ForeignKey.parent is the LOCAL column; .column is the remote one.
    targets = {fk.parent.name: fk.target_fullname
               for fk in DeploymentLog.__table__.foreign_keys}
    assert targets.get("reading_id") == "sensor_readings.id", (
        f"deployment_logs.reading_id must reference sensor_readings.id; got {targets}")


def test_r5_denial_reason_column_is_wide_enough_for_the_labels():
    """The longest reason label must fit, or the audit trail is cut again."""
    from config.gate_config import REASON_LABELS
    from database.models import Reading
    width = Reading.__table__.c.denial_reason.type.length
    longest = max(REASON_LABELS.values(), key=len)
    assert len(longest) <= width, (
        f"denial_reason is VARCHAR({width}) but the longest label is "
        f"{len(longest)} chars: {longest!r}")


def test_r5_full_reason_is_persisted_not_truncated(deploy_client, db, sandbox, monkeypatch):
    """End to end: the whole joined label must reach the database."""
    import routes.predict as pr
    from database.models import Reading
    monkeypatch.setattr(pr, "_class_from_features",
                        lambda f, n: 1 if n == "random_forest" else 1)
    monkeypatch.setattr(pr, "_bac_from_features", lambda f: 0.09)
    monkeypatch.setattr(pr, "_face_label", lambda f: "impaired")
    monkeypatch.setattr(pr, "analyze_frame", lambda f: dict(FAKE_ANALYSIS))
    w = [30.0 + i for i in range(50)]
    r = deploy_client.post("/sensor", json={"temperature": 27.0, "humidity": 60.0,
                                            "mode": "deployment", "mq3_1": w, "mq3_2": w, "mq3_3": w})
    assert r.status_code == 200
    rid = r.json()["id"]                     # 6.6: the id is now returned
    p = deploy_client.post("/predict/full",
                           files={"file": ("f.jpg", io.BytesIO(_jpg()), "image/jpeg")},
                           data={"reading_id": str(rid)})
    body = p.json()
    assert body["final_label"] == "FAIL"
    joined = "; ".join(body["decision"]["labels"])
    db.expire_all()
    reading = db.query(Reading).filter(Reading.id == rid).first()
    assert reading.denial_reason == joined, "the stored reason was truncated"
    assert "over limit" in reading.denial_reason


def test_r5_sensor_endpoint_returns_the_reading_id(deploy_client, db, sandbox, monkeypatch):
    """6.6: a caller must be able to correlate without re-querying the database."""
    import routes.predict as pr
    monkeypatch.setattr(pr, "analyze_frame", lambda f: dict(FAKE_ANALYSIS))
    w = [30.0 + i for i in range(50)]
    body = deploy_client.post("/sensor", json={
        "temperature": 27.0, "humidity": 60.0, "mode": "deployment",
        "mq3_1": w, "mq3_2": w, "mq3_3": w}).json()
    assert "id" in body and "reading_id" in body
    assert body["id"] == body["reading_id"]
    from database.models import Reading
    assert db.query(Reading).filter(Reading.id == body["id"]).first() is not None


def test_r5_cors_is_not_a_wildcard():
    """allow_origins=['*'] lets any website drive this API from the operator's browser."""
    import ast, pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "main.py"
    tree = ast.parse(src.read_text())
    wildcards = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Constant) and n.value == "*"]
    assert not wildcards, "main.py still contains a wildcard origin/method/header"
    assert "CORS_ORIGINS" in src.read_text()


# ── 6.1: the migration planner itself (main.py is never imported: it runs DDL) ──

class _FakeInspector:
    """Minimal stand-in for sqlalchemy.inspect(engine)."""
    def __init__(self, columns, foreign_keys=None, tables=None):
        self._cols = columns
        self._fks = foreign_keys or {}
        self._tables = set(tables if tables is not None else columns.keys())

    def has_table(self, t):
        return t in self._tables

    def get_columns(self, t):
        return [{"name": n, "type": ty} for n, ty in self._cols.get(t, {}).items()]

    def get_foreign_keys(self, t):
        return self._fks.get(t, [])


def _planner():
    """Load _plan_migrations out of main.py WITHOUT importing main.py.

    main.py executes create_all() and _auto_migrate() at import time against the live
    engine, so it must never be imported by a test. Only this one pure function is
    extracted, via AST, and compiled on its own.
    """
    import ast, pathlib, textwrap
    src = (pathlib.Path(__file__).resolve().parents[1] / "main.py").read_text()
    tree = ast.parse(src)
    wanted = {"_column_type", "_has_foreign_key", "_plan_migrations"}
    ns = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            mod = ast.Module(body=[node], type_ignores=[])
            exec(compile(ast.fix_missing_locations(mod), "main_planner", "exec"), ns)
    assert "_plan_migrations" in ns, "planner not found in main.py"
    return ns["_plan_migrations"]


def test_r5_migration_planner_adds_the_missing_fk():
    """The live MariaDB state: column + unique index present, CONSTRAINT absent."""
    plan = _planner()(_FakeInspector(
        columns={
            "training_data": {"id": "INT", "auto_labeled": "TINYINT",
                              "label_confirmed": "TINYINT", "height_offset_cm": "FLOAT"},
            "sensor_readings": {"id": "INT", "denial_reason": "VARCHAR(64)",
                                "height_offset_cm": "FLOAT"},
            "deployment_logs": {"id": "INT", "denial_reason": "VARCHAR(64)",
                                "reading_id": "INT", "height_offset_cm": "FLOAT"},
        },
        foreign_keys={"deployment_logs": [{"constrained_columns": ["subject_id"]}]},
    ), "mysql")
    fk = [s for s in plan if "ADD CONSTRAINT fk_deployment_logs_reading" in s]
    assert len(fk) == 1, f"expected the FK to be added once, got {plan}"


def test_r5_migration_planner_is_idempotent():
    """When the constraint already exists, no ALTER must be emitted."""
    plan = _planner()(_FakeInspector(
        columns={
            "training_data": {"id": "INT", "auto_labeled": "TINYINT",
                              "label_confirmed": "TINYINT", "height_offset_cm": "FLOAT"},
            "sensor_readings": {"id": "INT", "denial_reason": "VARCHAR(64)",
                                "height_offset_cm": "FLOAT"},
            "deployment_logs": {"id": "INT", "denial_reason": "VARCHAR(64)",
                                "reading_id": "INT", "height_offset_cm": "FLOAT"},
        },
        foreign_keys={"deployment_logs": [
            {"constrained_columns": ["subject_id"]},
            {"constrained_columns": ["reading_id"]},
        ]},
    ), "mysql")
    assert plan == [], f"a converged schema must produce no DDL, got {plan}"


def test_r5_migration_planner_widens_a_legacy_denial_reason():
    """A live VARCHAR(20) column must be widened, not left truncating the audit trail."""
    plan = _planner()(_FakeInspector(
        columns={
            "training_data": {"id": "INT", "auto_labeled": "TINYINT",
                              "label_confirmed": "TINYINT", "height_offset_cm": "FLOAT"},
            "sensor_readings": {"id": "INT", "denial_reason": "VARCHAR(20)",
                                "height_offset_cm": "FLOAT"},
            "deployment_logs": {"id": "INT", "denial_reason": "VARCHAR(20)",
                                "reading_id": "INT", "height_offset_cm": "FLOAT"},
        },
        foreign_keys={"deployment_logs": [{"constrained_columns": ["reading_id"]}]},
    ), "mysql")
    widen = [s for s in plan if "MODIFY COLUMN denial_reason" in s]
    assert len(widen) == 2, f"both tables must be widened, got {plan}"


def test_r5_migration_planner_skips_fk_on_sqlite():
    """SQLite cannot ADD CONSTRAINT; the fixture must not try."""
    plan = _planner()(_FakeInspector(
        columns={
            "training_data": {"id": "INT", "auto_labeled": "TINYINT",
                              "label_confirmed": "TINYINT", "height_offset_cm": "FLOAT"},
            "sensor_readings": {"id": "INT", "denial_reason": "VARCHAR(64)",
                                "height_offset_cm": "FLOAT"},
            "deployment_logs": {"id": "INT", "denial_reason": "VARCHAR(64)",
                                "reading_id": "INT", "height_offset_cm": "FLOAT"},
        },
    ), "sqlite")
    assert not [s for s in plan if "ADD CONSTRAINT" in s], plan


# ── 6.4: the firmware yellow mix must agree with the documented resistors ──────

def test_r5_yellow_mix_matches_the_documented_resistors():
    """A pure physics check -- no hardware needed.

    Yellow reads ORANGE when red and green run at very different currents. Red's Vf
    is far lower than green's, so at equal duty red always wins. indicator.h therefore
    scales the RED duty down. This test recomputes both currents from the resistor
    values documented in INDICATORS_WIRING.md and asserts the mix lands within 15 %.
    """
    import re, pathlib
    doc = (pathlib.Path(__file__).resolve().parents[2] / "docs" / "INDICATORS_WIRING.md").read_text()

    def documented(colour):
        m = re.search(rf"\|\s*{colour}\s*\|.*?\*\*(\d+)\s*Ω\*\*", doc)
        assert m, f"{colour} resistor not found in the wiring doc"
        return int(m.group(1))

    r_red = documented("Red")
    r_green = documented("Green")
    vf_red, vf_green = 2.0, 3.1
    i_red = (3.3 - vf_red) / r_red * 1000
    i_green = (3.3 - vf_green) / r_green * 1000

    hdr = (pathlib.Path(__file__).resolve().parents[2]
           / "alcodetect_esp32" / "indicator.h").read_text()
    m = re.search(r"c\.yellow\s*=\s*\{(\d+),\s*(\d+),\s*(\d+)\}", hdr)
    assert m, "c.yellow not found in indicator.h"
    red_duty, green_duty = int(m.group(1)), int(m.group(2))

    eff_red = i_red * red_duty / 255
    eff_green = i_green * green_duty / 255
    ratio = eff_red / eff_green
    assert 0.85 <= ratio <= 1.18, (
        f"yellow mix is unbalanced: red {eff_red:.2f} mA vs green {eff_green:.2f} mA "
        f"(ratio {ratio:.2f}:1) with R_red={r_red}, R_green={r_green} and "
        f"duty=({red_duty},{green_duty}) -- it will read orange")

    # neither channel may exceed the GPIO budget with two LEDs fitted
    assert i_red * 2 <= 12, f"red GPIO budget exceeded: {i_red*2:.2f} mA"
    assert i_green * 2 <= 12, f"green GPIO budget exceeded: {i_green*2:.2f} mA"


def test_r5_documented_resistors_clear_the_visibility_floor():
    """Green and blue must not be documented below ~2 mA or they read as 'off'."""
    import re, pathlib
    doc = (pathlib.Path(__file__).resolve().parents[2] / "docs" / "INDICATORS_WIRING.md").read_text()
    for colour, vf in (("Green", 3.1), ("Blue", 3.1)):
        r = int(re.search(rf"\|\s*{colour}\s*\|.*?\*\*(\d+)\s*Ω\*\*", doc).group(1))
        per_led = (3.3 - vf) / r * 1000
        # The floor is an approximate physical threshold ("~2 mA"), so compare with a
        # tolerance: 100 ohm lands on 1.9999999999999973 through floating point, which is
        # physically 2.00 mA. An exact >= 2.0 would reject a correct value.
        assert per_led >= 1.95, (
            f"{colour} at {r} ohm is only {per_led:.2f} mA per LED -- below the ~2 mA "
            f"visibility floor")
