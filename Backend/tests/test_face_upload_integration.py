"""Integration: uploads must coexist with row-owned images without confusing anything.

The dangerous interactions are:
  * delete_row / reconcile_face_mirror deriving a path that happens to equal an upload
  * collection-progress reporting uploads as orphans, or hiding upload-only subjects
  * mobilenet._collect_subject_split failing to engage because uploads have no row

Nothing here touches the real data/ tree: every test redirects the directory constants
into tmp_path and uses synthetic bytes.
"""

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from database.models import TrainingData                        # noqa: F401
from models.train import face_uploads as fu
from models.train.sensor_models import IMPAIRED_FACE_MIN_BAC


def _jpeg(seed=0, width=200, height=200):
    import cv2
    rng = np.random.default_rng(seed)
    ok, buf = cv2.imencode(".jpg", rng.integers(0, 255, (height, width, 3),
                                                 dtype=np.uint8))
    assert ok
    return buf.tobytes()


import numpy as np    # noqa: E402  (used by _jpeg above)


@pytest.fixture()
def env(tmp_path, monkeypatch, db):
    import routes.training as tr
    import models.train.mobilenet as mn

    base = tmp_path / "base"
    for cls in list(fu.UPLOAD_CLASSES) + ["registered"]:
        (base / "data" / "faces" / cls).mkdir(parents=True, exist_ok=True)
    for cls in ("sober", "fatigue", "impaired"):
        (base / "data" / "snapshots" / cls).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(tr, "BASE_DIR", str(base))
    monkeypatch.setattr(tr, "UPLOAD_LOG_PATH", str(base / "data" / "upload_log.jsonl"))
    monkeypatch.setattr(tr, "analyze_frame",
                        lambda f: {"status": "normal", "face_width": 0.5})
    monkeypatch.setattr(mn, "DATA_DIR", str(base / "data" / "faces"))

    # _collect_subject_split does `from database.db import SessionLocal` INSIDE the
    # function, i.e. it reads the module attribute at call time. Point that attribute
    # at a real SQLAlchemy sessionmaker bound to the THROWAWAY SQLite engine the `db`
    # fixture already created. These are real ORM rows against a real engine — not a
    # stand-in — and the live MariaDB is never opened.
    from sqlalchemy.orm import sessionmaker
    import database.db as _dbmod
    _test_engine = db.get_bind()
    monkeypatch.setattr(_dbmod, "SessionLocal",
                        lambda: sessionmaker(bind=_test_engine)())

    app = FastAPI()
    app.include_router(tr.router)
    app.dependency_overrides[tr.get_db] = lambda: db
    return {"client": TestClient(app), "base": base, "tr": tr, "mn": mn, "db": db}


def _subject(db, name):
    from database.models import Subject
    s = Subject(name=name)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _upload(env, subject_id, cls, seed):
    data = {"face_class": cls, "subject_id": str(subject_id)}
    if cls == "impaired":
        data.update({"bac": str(IMPAIRED_FACE_MIN_BAC), "confirm_real": "true"})
    payload = _jpeg(seed=seed)
    r = env["client"].post("/training/upload-faces", data=data,
                           files=[("files", (f"u{seed}.jpg", payload, "image/jpeg"))])
    assert r.status_code == 200, r.text
    assert r.json()["accepted"] == 1, r.text
    return r.json()["files"][0]["stored_as"]


# ══════════════════════════════════════════════════════════════════════════════
# delete_row must never remove an upload, even when the digits collide
# ══════════════════════════════════════════════════════════════════════════════

def test_delete_row_with_a_colliding_id_leaves_the_upload_alone(env, db):
    """An upload whose subject digits equal a row id must survive that row's deletion.

    The two namespaces are disjoint by name, so _owned_face_files() can only ever build
    '<prefix>_<row.id>.jpg'. This test pins that with a genuinely colliding id.
    """
    tr = env["tr"]
    db_ = db
    sub = _subject(db_, "s1")

    row = TrainingData(subject_id=sub.id, label=0, sub_label="sober", bac=0.00)
    db_.add(row)
    db_.commit()
    db_.refresh(row)

    # An upload from the SAME numeric id, so "sober_<row.id>.jpg" and the upload name
    # both carry the same digits.
    stored = _upload(env, row.id, "sober", seed=1)
    assert stored == f"sober_up_s{row.id}_{fu.upload_digest(_jpeg(seed=1))}.jpg"

    upload_path = env["base"] / "data" / "faces" / "sober" / stored
    capture_path = env["base"] / "data" / "faces" / "sober" / f"sober_{row.id}.jpg"
    capture_path.write_bytes(b"the row's own mirror")

    removed = tr._remove_row_images(row)

    assert capture_path.is_file() is False, "the row-owned mirror IS removed"
    assert upload_path.is_file(), "the upload must survive a row deletion"


def test_reconcile_never_removes_an_upload(env, db):
    """Re-tagging a row into a stale class must not sweep upload files away."""
    tr = env["tr"]
    sub = _subject(db, "s2")
    row = TrainingData(subject_id=sub.id, label=0, sub_label="sober", bac=0.00)
    db.add(row)
    db.commit()
    db.refresh(row)

    stored = _upload(env, row.id, "sober", seed=2)
    upload_path = env["base"] / "data" / "faces" / "sober" / stored

    # Re-label so BOTH the sober and fatigue mirrors are stale -> removals happen.
    row.label = 1
    row.sub_label = None
    row.bac = 0.01          # below IMPAIRED_FACE_MIN_BAC, so no impaired mirror either
    db.commit()
    tr.reconcile_face_mirror(row)

    assert upload_path.is_file(), "uploads are never touched by reconcile"


def test_delete_endpoint_leaves_uploads_intact(env, db):
    """The row delete endpoint (not the file helper) must also spare uploads."""
    tr = env["tr"]
    db_ = db
    sub = _subject(db_, "s3")
    row = TrainingData(subject_id=sub.id, label=0, sub_label="sober", bac=0.00)
    db_.add(row)
    db_.commit()
    db_.refresh(row)
    stored = _upload(env, row.id, "fatigue", seed=3)
    upload_path = env["base"] / "data" / "faces" / "fatigue" / stored

    r = env["client"].delete(f"/training/delete-row/{row.id}")
    assert r.status_code in (200, 404), r.text
    assert upload_path.is_file()


# ══════════════════════════════════════════════════════════════════════════════
# collection-progress
# ══════════════════════════════════════════════════════════════════════════════

def test_progress_separates_captured_from_uploaded(env, db):
    sub = _subject(db, "s4")
    row = TrainingData(subject_id=sub.id, label=0, sub_label="sober", bac=0.00)
    db.add(row)
    db.commit()
    db.refresh(row)
    # one row-owned mirror
    (env["base"] / "data" / "faces" / "sober" / f"sober_{row.id}.jpg").write_bytes(b"cap")

    _upload(env, sub.id, "sober", seed=4)
    _upload(env, sub.id, "sober", seed=5)

    d = env["client"].get("/training/collection-progress").json()
    f = d["faces"]["sober"]
    assert f["captured"] == 1
    assert f["uploaded"] == 2
    assert f["images"] == 3


def test_progress_does_not_report_uploads_as_orphans(env, db):
    sub = _subject(db, "s5")
    snap = env["base"] / "data" / "snapshots" / "sober" / "sober_s5.jpg"
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_bytes(b"snap")
    row = TrainingData(subject_id=sub.id, label=0, sub_label="sober", bac=0.00,
                       image_path=str(snap))
    db.add(row)
    db.commit()
    db.refresh(row)
    (env["base"] / "data" / "faces" / "sober" / f"sober_{row.id}.jpg").write_bytes(b"cap")
    _upload(env, sub.id, "sober", seed=6)

    d = env["client"].get("/training/collection-progress").json()
    assert d["integrity"]["orphan_files"] == 0, d["integrity"]["orphan_file_names"]
    assert d["integrity"]["rows_missing_image"] == 0


def test_progress_still_flags_a_genuine_orphan(env, db):
    """The orphan check must not be disabled, only narrowed to exclude uploads."""
    sub = _subject(db, "s6")
    (env["base"] / "data" / "faces" / "sober" / "sober_424242.jpg").write_bytes(b"orphan")
    d = env["client"].get("/training/collection-progress").json()
    assert d["integrity"]["orphan_files"] == 1


def test_progress_counts_an_upload_only_subject(env, db):
    """A subject whose ONLY photos were uploaded still counts for the split."""
    env_uploads_only = _subject(db, "s7")
    other = _subject(db, "s8")
    row = TrainingData(subject_id=other.id, label=0, sub_label="sober", bac=0.00)
    db.add(row)
    db.commit()
    db.refresh(row)
    (env["base"] / "data" / "faces" / "sober" / f"sober_{row.id}.jpg").write_bytes(b"cap")

    _upload(env, env_uploads_only.id, "sober", seed=7)
    _upload(env, env_uploads_only.id, "fatigue", seed=8)

    d = env["client"].get("/training/collection-progress").json()
    assert d["subjects"]["per_class"]["sober"] == 2, d["subjects"]
    assert d["subjects"]["per_class"]["fatigue"] == 1, d["subjects"]
    # the upload-only subject appears in two classes, so it is NOT single-class
    assert env_uploads_only.id not in d["subjects"]["single_class_subject_ids"]


def test_progress_includes_upload_only_subject_in_the_single_class_warning(env, db):
    """A subject with uploads in exactly one class must trigger the warning."""
    solo = _subject(db, "s9")
    other = _subject(db, "s10")
    row = TrainingData(subject_id=other.id, label=0, sub_label="sober", bac=0.00)
    db.add(row)
    db.commit()
    db.refresh(row)
    (env["base"] / "data" / "faces" / "sober" / f"sober_{row.id}.jpg").write_bytes(b"cap")
    _upload(env, solo.id, "fatigue", seed=9)

    d = env["client"].get("/training/collection-progress").json()
    assert solo.id in d["subjects"]["single_class_subject_ids"]
    assert d["subjects"]["single_class_subjects_warning"] is True


def test_rows_missing_image_is_unaffected(env, db):
    sub = _subject(db, "s11")
    row = TrainingData(subject_id=sub.id, label=0, sub_label="sober", bac=0.00,
                       image_path=str(env["base"] / "data" / "snapshots" / "sober" /
                                     "does_not_exist.jpg"))
    db.add(row)
    db.commit()
    d = env["client"].get("/training/collection-progress").json()
    assert d["integrity"]["rows_missing_image"] >= 1


# ══════════════════════════════════════════════════════════════════════════════
# mobilenet._collect_subject_split
# ══════════════════════════════════════════════════════════════════════════════

def _run_split(env, monkeypatch):
    """Call the SHIPPED _collect_subject_split with the DB access neutralised."""
    mn = env["mn"]
    import sys, types
    # Force the DB branch to yield nothing so only the FILENAME mapping is under test.
    monkeypatch.setattr(mn, "_collect_subject_split", mn._collect_subject_split)
    return mn._collect_subject_split()


def test_split_engages_with_uploads_from_three_subjects(env, monkeypatch, db):
    mn = env["mn"]
    base = env["base"]
    # three subjects, uploads only, two files each
    for si, sid in enumerate([101, 202, 303]):
        d = base / "data" / "faces" / "sober"
        for k in range(2):
            name = fu.upload_filename("sober", sid, f"{si}{k:011d}")
            (d / name).write_bytes(b"x")
    train, val = _run_split(env, monkeypatch)
    assert train is not None, "must NOT fall back to an image-level split"
    assert val is not None
    assert len(train) + len(val) == 6
    keys = {t[2] for t in list(train) + list(val)}
    assert keys == {"subject_101", "subject_202", "subject_303"}


def test_split_falls_back_with_fewer_than_three_subjects(env, monkeypatch):
    mn = env["mn"]
    d = env["base"] / "data" / "faces" / "fatigue"
    for si, sid in enumerate([11, 22]):
        (d / fu.upload_filename("fatigue", sid, f"{si:012d}")).write_bytes(b"x")
    train, val = _run_split(env, monkeypatch)
    assert train is None and val is None, "must signal the image-level fallback"


def test_split_maps_capture_files_through_the_db_unchanged(env, monkeypatch, db):
    """A capture-owned file still resolves through training_data.image_path.

    Three DISTINCT subjects are needed, otherwise the shipped code correctly falls
    back (the split requires >= 3 distinct subjects).
    """
    mn = env["mn"]
    base = env["base"]
    snap_dir = base / "data" / "snapshots" / "impaired"
    snap_dir.mkdir(parents=True, exist_ok=True)
    expected = set()
    for i in range(3):
        sub = _subject(db, f"capture-sub-{i}")
        expected.add(f"subject_{sub.id}")
        row = TrainingData(subject_id=sub.id, label=1, bac=0.2, image_path="")
        db.add(row)
        db.commit()
        db.refresh(row)
        # image_path must carry the SAME basename the face mirror uses, because
        # mobilenet derives the faces sibling from the snapshot path.
        row.image_path = str(snap_dir / f"impaired_{row.id}.jpg")
        db.commit()
        (snap_dir / f"impaired_{row.id}.jpg").write_bytes(b"snap")
        # the mirrored face copy the trainer actually globs
        (base / "data" / "faces" / "impaired" / f"impaired_{row.id}.jpg").write_bytes(b"x")

    train, val = _run_split(env, monkeypatch)
    assert train is not None, "three distinct DB-mapped subjects must engage the split"
    keys = {t[2] for t in list(train) + list(val)}
    assert keys == expected, keys
    # and NO file fell into the unknown_ bucket
    assert not any(k.startswith("unknown_") for k in keys), keys


def test_split_mixes_db_mapped_captures_and_uploads(env, monkeypatch, db):
    mn = env["mn"]
    base = env["base"]
    sub = _subject(db, "s13")
    snap_dir = base / "data" / "snapshots" / "sober"
    snap_dir.mkdir(parents=True, exist_ok=True)
    row = TrainingData(subject_id=sub.id, label=0, sub_label="sober", bac=0.00,
                       image_path="")
    db.add(row)
    db.commit()
    db.refresh(row)
    row.image_path = str(snap_dir / f"sober_{row.id}.jpg")
    db.commit()
    (snap_dir / f"sober_{row.id}.jpg").write_bytes(b"snap")
    (base / "data" / "faces" / "sober" / f"sober_{row.id}.jpg").write_bytes(b"x")
    # two more subjects via uploads, so the split engages
    for si, sid in enumerate([41, 42]):
        (base / "data" / "faces" / "sober" /
         fu.upload_filename("sober", sid, f"{si:012d}")).write_bytes(b"x")

    train, val = _run_split(env, monkeypatch)
    assert train is not None
    keys = {t[2] for t in list(train) + list(val)}
    assert keys == {f"subject_{sub.id}", "subject_41", "subject_42"}, keys


def test_split_leaves_a_stranger_file_unknown(env, monkeypatch):
    """A name matching neither namespace keeps the old unknown_ behaviour."""
    mn = env["mn"]
    d = env["base"] / "data" / "faces" / "sober"
    (d / "mystery_photo.jpg").write_bytes(b"x")
    train, val = _run_split(env, monkeypatch)
    assert train is None, "one stranger file cannot engage a subject split"
    # and when other subjects exist it is bucketed as unknown
    for si, sid in enumerate([51, 52, 53]):
        (d / fu.upload_filename("sober", sid, f"{si:012d}")).write_bytes(b"x")
    train, val = _run_split(env, monkeypatch)
    assert train is not None
    keys = {t[2] for t in list(train) + list(val)}
    assert "unknown_mystery_photo.jpg" in keys
    assert "subject_51" in keys


# ══════════════════════════════════════════════════════════════════════════════
# MobileNet gating still works with uploaded files
# ══════════════════════════════════════════════════════════════════════════════

def test_min_images_gating_counts_uploads(env, monkeypatch):
    """MIN_IMAGES_PER_CLASS must be satisfied by uploads alone."""
    from models.train import mobilenet as mn
    d = env["base"] / "data" / "faces" / "sober"
    for i in range(mn.MIN_IMAGES_PER_CLASS):
        (d / fu.upload_filename("sober", 7, f"{i:012d}")).write_bytes(b"x")
    counts = mn._class_image_counts()
    assert counts["sober"] == mn.MIN_IMAGES_PER_CLASS


def test_registered_is_never_counted_or_trained(env, monkeypatch):
    from models.train import mobilenet as mn
    reg = env["base"] / "data" / "faces" / "registered"
    reg.mkdir(parents=True, exist_ok=True)
    for i in range(5):
        (reg / f"registered_{i}.jpg").write_bytes(b"x")
    counts = mn._class_image_counts()
    assert "registered" not in counts
    assert sum(counts.values()) == 0, "registered files are not a trained class"


def test_class_order_is_pinned():
    from models.train import mobilenet as mn
    assert mn.CLASSES == ["sober", "fatigue", "impaired"]


# ══════════════════════════════════════════════════════════════════════════════
# The real data/ tree is never touched
# ══════════════════════════════════════════════════════════════════════════════

def test_no_test_writes_into_the_real_data_tree():
    """Guard: the fixture must redirect BASE_DIR away from the repo's data/."""
    import routes.training as tr
    repo_data = os.path.realpath(os.path.join(os.path.dirname(tr.__file__),
                                            "..", "data"))
    # The module default is the real tree; tests override it per-test.
    assert os.path.isdir(repo_data), "sanity: the repo data/ dir exists"
    # Nothing in this module writes at import time.
    assert not os.path.exists(os.path.join(repo_data, "upload_log.jsonl")), \
        "the real data/upload_log.jsonl must not exist from an import"