"""Tests for the collection-hardening work: BAC-gated face mirroring, row-owned
file lifecycle, realistic readiness targets and the collection-progress endpoint.

Every test monkeypatches BASE_DIR and the snapshot directory constants into tmp_path,
so the real Backend/data tree is never read or written.
"""

import os
import pathlib
import re

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text as sql_text

from database.models import Subject, TrainingData


@pytest.fixture()
def sandbox(monkeypatch, tmp_path):
    """Redirect every data directory into tmp_path and create the class folders."""
    import routes.training as pr

    data = tmp_path / "data"
    for sub in ("faces", "snapshots"):
        for cls in ("sober", "fatigue", "impaired", "registered"):
            (data / sub / cls).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(pr, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(pr, "SNAPSHOTS_DIR", str(data / "snapshots"))
    monkeypatch.setattr(pr, "SOBER_DIR", str(data / "snapshots" / "sober"))
    monkeypatch.setattr(pr, "FATIGUE_DIR", str(data / "snapshots" / "fatigue"))
    monkeypatch.setattr(pr, "IMPAIRED_DIR", str(data / "snapshots" / "impaired"))
    return tmp_path


@pytest.fixture()
def client(db, sandbox, monkeypatch):
    import routes.training as pr

    async def _fake_trigger(row_id=None):
        return {"triggered": True, "status": 200, "row_id": row_id}
    monkeypatch.setattr(pr, "trigger_esp32_async", _fake_trigger)
    monkeypatch.setattr(pr, "analyze_frame",
                        lambda f: {"ear": 0.3, "mar": 0.2, "pitch": 1.0})

    app = FastAPI()
    app.include_router(pr.router)
    app.dependency_overrides[pr.get_db] = lambda: db
    return TestClient(app)


def _jpg():
    import cv2
    ok, buf = cv2.imencode(".jpg", np.full((16, 16, 3), 90, dtype=np.uint8))
    assert ok
    return buf.tobytes()


def _subject(db, *ids):
    for sid in ids:
        db.add(Subject(name=f"P{sid}", group="Student"))
        db.flush()
        db.execute(sql_text("UPDATE subjects SET id=:i WHERE name=:n"),
                   {"i": sid, "n": f"P{sid}"})
    db.commit()


def _row(db, **kw):
    kw.setdefault("label", 0)
    kw.setdefault("bac", 0.0)
    kw.setdefault("mq3_1_max", 50.0)
    r = TrainingData(**kw)
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def _touch(path):
    """Write a real decodable JPEG so cv2.imread succeeds (mirroring needs pixels)."""
    import cv2
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".jpg", np.full((16, 16, 3), 77, dtype=np.uint8))
    assert ok
    path.write_bytes(buf.tobytes())


# ---------------------------------------------------------------------------------
# TASK 1 — impaired face requires BAC >= IMPAIRED_FACE_MIN_BAC
# ---------------------------------------------------------------------------------

@pytest.mark.parametrize("bac,mirrored,face_dataset", [
    (0.06,   True,  "impaired"),              # above the limit
    (0.03,   False, "excluded_below_threshold"),
    (0.02,   False, "excluded_below_threshold"),
    (0.05,   True,  "impaired"),              # exactly at BAC_OVER_LIMIT
])
def test_collect_alcohol_mirrors_only_at_or_above_threshold(client, db, sandbox, bac, mirrored, face_dataset):
    from models.train.sensor_models import BAC_OVER_LIMIT

    resp = client.post("/training/collect/alcohol",
                       files={"file": ("f.jpg", _jpg(), "image/jpeg")},
                       data={"bac": str(bac)})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["face_dataset"] == face_dataset

    # the sensor row and the snapshot exist in BOTH cases
    row = db.query(TrainingData).filter(TrainingData.id == body["id"]).one()
    assert row.label == 1 and float(row.bac) == bac
    assert row.image_path and os.path.isfile(row.image_path)

    faces_impaired = sandbox / "data" / "faces" / "impaired"
    mirror = faces_impaired / f"impaired_{row.id}.jpg"
    assert mirror.exists() is mirrored
    # nothing may leak into another faces folder
    for other in ("sober", "fatigue"):
        assert not list((sandbox / "data" / "faces" / other).glob(f"*{row.id}*.jpg"))


def test_threshold_uses_the_named_constant(client, db, sandbox):
    from models.train.sensor_models import BAC_OVER_LIMIT, IMPAIRED_FACE_MIN_BAC
    assert IMPAIRED_FACE_MIN_BAC == BAC_OVER_LIMIT
    just_under = client.post("/training/collect/alcohol",
                             files={"file": ("f.jpg", _jpg(), "image/jpeg")},
                             data={"bac": str(BAC_OVER_LIMIT - 0.0001)}).json()
    assert just_under["face_dataset"] == "excluded_below_threshold"


def test_perfume_still_never_writes_to_faces_impaired(client, db, sandbox):
    body = client.post("/training/collect/perfume",
                       files={"file": ("f.jpg", _jpg(), "image/jpeg")}).json()
    row = db.query(TrainingData).filter(TrainingData.id == body["id"]).one()
    assert not (sandbox / "data" / "faces" / "impaired" / f"impaired_{row.id}.jpg").exists()


# ---------------------------------------------------------------------------------
# TASK 2 — row-owned file lifecycle
# ---------------------------------------------------------------------------------

def test_delete_row_removes_mirror_and_snapshot(client, db, sandbox):
    body = client.post("/training/collect/alcohol",
                       files={"file": ("f.jpg", _jpg(), "image/jpeg")},
                       data={"bac": "0.09"}).json()
    row_id = body["id"]
    mirror = sandbox / "data" / "faces" / "impaired" / f"impaired_{row_id}.jpg"
    snap = pathlib.Path(body["image_path"])
    assert mirror.exists() and snap.exists()

    resp = client.delete(f"/training/delete/{row_id}")
    assert resp.status_code == 200, resp.text
    removed = resp.json()["removed_files"]

    assert not mirror.exists()
    assert not snap.exists()
    assert any(str(mirror) in r for r in removed)
    assert any(str(snap) in r for r in removed)
    assert db.query(TrainingData).filter(TrainingData.id == row_id).count() == 0


def test_delete_row_succeeds_when_files_already_missing(client, db, sandbox):
    row = _row(db, label=1, bac=0.09, sub_label=None)
    resp = client.delete(f"/training/delete/{row.id}")
    assert resp.status_code == 200
    assert resp.json()["removed_files"] == []


def test_delete_row_refuses_paths_outside_permitted_folders(client, db, sandbox):
    """A crafted image_path must not let a delete escape the class folders."""
    import routes.training as pr

    evil = sandbox / "evil.txt"
    _touch(evil)
    reg = sandbox / "data" / "faces" / "registered" / "keep.jpg"
    _touch(reg)
    traversal = sandbox / "data" / "faces" / "impaired" / "../../../evil.txt"

    row = _row(db, label=1, bac=0.09, sub_label=None,
               image_path=str(traversal))

    assert pr.safe_delete(str(evil)) is False
    assert evil.exists(), "a file outside the class folders was deleted"
    assert pr.safe_delete(str(reg)) is False
    assert reg.exists(), "a registered/ face was deleted"

    resp = client.delete(f"/training/delete/{row.id}")
    assert resp.status_code == 200
    assert evil.exists() and reg.exists()
    assert pr.safe_delete(str(sandbox / "data")) is False     # never a directory


def test_attach_label_raising_bac_adds_the_mirror(client, db, sandbox):
    row = _row(db, label=1, bac=0.03, sub_label=None)
    snap = sandbox / "data" / "snapshots" / "impaired" / f"impaired_{row.id}.jpg"
    _touch(snap)
    row.image_path = str(snap)
    db.commit()
    mirror = sandbox / "data" / "faces" / "impaired" / f"impaired_{row.id}.jpg"
    assert not mirror.exists()

    resp = client.patch(f"/training/label/{row.id}", json={"bac": 0.09})
    assert resp.status_code == 200, resp.text
    assert resp.json()["face_dataset"] == "impaired"
    assert mirror.exists()


def test_clear_air_row_keeps_its_sober_mirror(client, db, sandbox):
    """clear_air is a sober-class baseline; its mirror must not be swept as stale."""
    # start as a plain sober capture, then transition INTO clear_air: the relabel
    # must actually run reconcile for this step to mean anything
    row = _row(db, label=0, sub_label=None)
    mirror = sandbox / "data" / "faces" / "sober" / f"sober_{row.id}.jpg"
    _touch(mirror)
    resp = client.patch(f"/training/relabel/{row.id}", json={"label": 0, "sub_label": "clear_air"})
    assert resp.status_code == 200
    assert mirror.exists(), "clear_air mirror was wrongly deleted"
    # ...but moving it to an alcohol label still removes the stale sober mirror
    resp = client.patch(f"/training/relabel/{row.id}", json={"label": 1, "bac": 0.08, "subject_id": None})
    assert resp.status_code == 200
    assert not mirror.exists()


def test_attach_label_lowering_bac_removes_the_mirror(client, db, sandbox):
    row = _row(db, label=1, bac=0.09, sub_label=None)
    snap = sandbox / "data" / "snapshots" / "impaired" / f"impaired_{row.id}.jpg"
    _touch(snap)
    row.image_path = str(snap)
    db.commit()
    mirror = sandbox / "data" / "faces" / "impaired" / f"impaired_{row.id}.jpg"
    _touch(mirror)

    resp = client.patch(f"/training/label/{row.id}", json={"bac": 0.03})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["face_dataset"] == "excluded_below_threshold"
    assert not mirror.exists()
    assert any(str(mirror) in f for f in body["removed_files"])


def test_relabel_away_removes_stale_mirror_and_reports(client, db, sandbox):
    """A sober row relabelled to breath-alcohol must lose its faces/sober mirror;
    no new class is guessed."""
    row = _row(db, label=0, sub_label=None)
    snap = sandbox / "data" / "snapshots" / "sober" / f"sober_{row.id}.jpg"
    _touch(snap)
    row.image_path = str(snap)
    db.commit()
    mirror = sandbox / "data" / "faces" / "sober" / f"sober_{row.id}.jpg"
    _touch(mirror)

    resp = client.patch(f"/training/relabel/{row.id}",
                        json={"label": 1, "subject_id": None})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert not mirror.exists()
    assert body["face_dataset"] == "removed_from_face_dataset"
    assert any(str(mirror) in f for f in body["removed_files"])
    # and nothing was invented in the impaired folder
    assert not (sandbox / "data" / "faces" / "impaired" / f"impaired_{row.id}.jpg").exists()


def test_attach_label_rejects_unenrolled_subject_before_touching_files(client, db, sandbox):
    row = _row(db, label=1, bac=0.03, sub_label=None)
    snap = sandbox / "data" / "snapshots" / "impaired" / f"impaired_{row.id}.jpg"
    _touch(snap)
    row.image_path = str(snap)
    db.commit()
    resp = client.patch(f"/training/label/{row.id}", json={"bac": 0.09, "subject_id": 4242})
    assert resp.status_code == 400
    assert not (sandbox / "data" / "faces" / "impaired" / f"impaired_{row.id}.jpg").exists()


# ---------------------------------------------------------------------------------
# TASK 3 — realistic targets
# ---------------------------------------------------------------------------------

def test_readiness_targets_are_the_named_values(client, db, sandbox):
    import routes.training as pr

    resp = client.get("/training/summary")
    assert resp.status_code == 200
    t = resp.json()["targets"]
    assert t["sensor_min_rows_per_class"] == 30
    assert t["sensor_target_rows_per_class"] == 60
    assert t["face_min_images_per_class"] == 30
    assert t["face_target_images_per_class"] == 100
    assert t["min_subjects"] == 3
    assert t["target_subjects"] == 5
    assert t["bac_band_min_rows"] == 8
    assert t["bac_band_min_subjects"] == 2
    # FACE_MIN comes from mobilenet, not a copy
    from models.train import mobilenet
    assert t["face_min_images_per_class"] == mobilenet.MIN_IMAGES_PER_CLASS
    assert pr.SENSOR_MIN_ROWS_PER_CLASS == 30


@pytest.mark.parametrize("shorten,expect", [
    (None, True),     # every MIN target met exactly
    ("rows", False),  # one sensor row short of the MIN
    ("faces", False),  # one face image short of the MIN
])
def test_balanced_is_computed_from_the_min_constants(client, db, sandbox, shorten, expect):
    """Behavioural: `balanced` must track MIN_*, not the old 1000/200/3000 numbers."""
    from routes.training import SENSOR_MIN_ROWS_PER_CLASS, FACE_MIN_IMAGES_PER_CLASS

    n_rows = SENSOR_MIN_ROWS_PER_CLASS
    n_faces = FACE_MIN_IMAGES_PER_CLASS()
    _subject(db, 1)

    # label 0 -> sober, label 1 -> impaired (BAC over the gate), label 2 -> fatigue
    plan = [(0, "sober", "sober"), (1, "impaired", "impaired"), (2, "fatigue", "fatigue")]
    made = []
    for label, folder, prefix in plan:
        for idx in range(n_rows):
            r = _row(db, label=label, sub_label=None, subject_id=1,
                     bac=(0.09 if label == 1 else None))
            # "faces" case: the last impaired row gets neither a snapshot nor a
            # mirror. summary reports impaired as max(snapshots, faces), so both
            # must be short for the count to actually drop.
            skip = shorten == "faces" and label == 1 and idx == n_rows - 1
            if not skip:
                _touch(sandbox / "data" / "snapshots" / folder / f"{prefix}_{r.id}.jpg")
                _touch(sandbox / "data" / "faces" / folder / f"{prefix}_{r.id}.jpg")
            made.append(r)

    if shorten == "rows":
        # remove the single label-2 row: sanitizer and others alias the same rows
        # (2 = rubbing_alcohol/perfume), so both counts drop together.
        db.delete(made[-1])
        db.commit()
    elif shorten == "faces":
        pass  # already applied during construction

    d = client.get("/training/summary").json()
    assert d["no_alcohol"] == n_rows and d["breath_alcohol"] == n_rows
    assert d["face_images"]["sober"] == n_faces
    assert d["face_images"]["impaired"] == (n_faces - 1 if shorten == "faces" else n_faces)
    assert d["targets"]["sensor_min_rows_per_class"] == SENSOR_MIN_ROWS_PER_CLASS
    assert d["balanced"] is expect


def test_old_readiness_literals_are_gone():
    """1000 / 3000 / 200-per-face / 600 must no longer drive readiness."""
    import routes.training as pr
    root = pathlib.Path(__file__).resolve().parents[1]
    training_py = (root / "routes" / "training.py").read_text()
    training_jsx = (root.parent / "Frontend" / "src" / "pages" / "Training.jsx").read_text()

    # Only READINESS phrasing is checked. Bare numbers such as setTimeout(..., 1000)
    # are unrelated timing constants and must not trip this.
    # Only READINESS phrasing. Bare "/1000" and "3000" are too generic to match on:
    # a ms->seconds conversion (ready_after_ms / 1000) is not a readiness target.
    # Every real regression this guards against is covered by the forms below:
    #   counts[i] >= 1000, target={1000}, target = 200, "200 each", "need 1000".
    readiness = re.compile(
        r"(>=\s*(1000|200|600)\b"
        r"|target\s*=\s*\{?\s*(1000|200|600)\b"
        r"|\b(1000|200|600)\s*each\b"
        r"|need\s+(1000|200|600)\b"
        r"|(1000|3000|200|600)\s*(rows|events|samples)\b"
        r"|target\s*=\s*1000)", re.I)
    for src, name in ((training_py, "training.py"), (training_jsx, "Training.jsx")):
        for lineno, line in enumerate(src.splitlines(), 1):
            stripped = line.split("#")[0].split("//")[0]
            m = readiness.search(stripped)
            assert m is None, f"{name}:{lineno} still has an old readiness target -> {stripped.strip()}"


# ---------------------------------------------------------------------------------
# TASK 4 — collection-progress
# ---------------------------------------------------------------------------------

def test_collection_progress_empty_table(client, db, sandbox):
    resp = client.get("/training/collection-progress")
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["sensor"]["0"]["rows"] == 0
    assert d["faces"]["impaired"]["images"] == 0
    assert d["subjects"]["total_with_data"] == 0
    assert d["subjects"]["single_class_subjects_warning"] is False
    assert d["grouping"]["group_by"] == "trial_id"
    assert d["ready"]["sensor_models"]["ready"] is False
    assert d["ready"]["sensor_models"]["missing"]
    assert d["integrity"]["orphan_files"] == 0
    assert d["integrity"]["rows_missing_image"] == 0


def test_collection_progress_counts_and_subject_coverage(client, db, sandbox):
    _subject(db, 1, 2, 3, 4)
    # Mirror filenames must carry the REAL row id, that is how ownership is resolved.
    sober_rows = [_row(db, label=0, sub_label=None, subject_id=1 + (i % 2))
                  for i in range(30)]
    for r in sober_rows:
        _touch(sandbox / "data" / "snapshots" / "sober" / f"sober_{r.id}.jpg")
        _touch(sandbox / "data" / "faces" / "sober" / f"sober_{r.id}.jpg")
    drowsy_rows = [_row(db, label=0, sub_label="drowsy", subject_id=3)
                   for _ in range(5)]
    for r in drowsy_rows:
        _touch(sandbox / "data" / "snapshots" / "fatigue" / f"fatigue_{r.id}.jpg")
        _touch(sandbox / "data" / "faces" / "fatigue" / f"fatigue_{r.id}.jpg")

    d = client.get("/training/collection-progress").json()
    assert d["sensor"]["0"]["rows"] == 35
    assert d["sensor"]["0"]["met_min"] is True
    assert d["sensor"]["1"]["rows"] == 0
    assert d["faces"]["sober"]["images"] == 30
    assert d["faces"]["fatigue"]["images"] == 5
    assert d["faces"]["impaired"]["images"] == 0
    # subjects 1,2 -> sober only ; 3 -> fatigue only ; 4 -> none
    assert d["subjects"]["per_class"]["sober"] == 2
    assert d["subjects"]["per_class"]["fatigue"] == 1
    assert d["subjects"]["single_class_subjects_warning"] is True
    assert set(d["subjects"]["single_class_subject_ids"]) == {1, 2, 3}
    assert d["ready"]["mobilenet"]["ready"] is False
    assert any("impaired images" in m for m in d["ready"]["mobilenet"]["missing"])


@pytest.mark.parametrize("bac,band", [
    (0.0199, "below_near"), (0.02, "near"), (0.0499, "near"), (0.05, "over"), (0.2, "over"),
])
def test_collection_progress_bac_band_boundaries(client, db, sandbox, bac, band):
    _row(db, label=1, bac=bac, subject_id=None, sub_label=None,
         image_path=str(sandbox / "data" / "snapshots" / "impaired" / "x.jpg"))
    d = client.get("/training/collection-progress").json()
    assert d["bac_bands"][band]["rows"] == 1
    others = {"below_near", "near", "over"} - {band}
    for o in others:
        assert d["bac_bands"][o]["rows"] == 0


def test_collection_progress_bac_band_min_flags(client, db, sandbox):
    _subject(db, 1)
    for _ in range(8):
        _row(db, label=1, bac=0.09, subject_id=1, sub_label=None)
    d = client.get("/training/collection-progress").json()
    assert d["bac_bands"]["over"]["rows"] == 8
    assert d["bac_bands"]["over"]["met_min"] is True
    assert d["ready"]["bac_regressor"]["ready"] is False
    # plain-language reasons, naming the band and how many rows are still needed
    # both empty bands are short on rows; the populated band is short on subjects
    assert len(d["ready"]["bac_regressor"]["missing"]) == 3
    for m in d["ready"]["bac_regressor"]["missing"]:
        assert m.startswith("BAC band ")


def test_collection_progress_detects_orphans_and_missing_images(client, db, sandbox):
    # a row with a real snapshot + mirror
    r = _row(db, label=0, sub_label=None,
             image_path=str(sandbox / "data" / "snapshots" / "sober" / "sober_ok.jpg"))
    _touch(r.image_path)
    _touch(sandbox / "data" / "faces" / "sober" / f"sober_{r.id}.jpg")

    # orphan: matches the row-owned pattern but the row id does not exist
    _touch(sandbox / "data" / "faces" / "sober" / "sober_999999.jpg")
    # row expecting an image but the file is absent
    missing = _row(db, label=0, sub_label="drowsy",
                   image_path=str(sandbox / "data" / "snapshots" / "fatigue" / "gone.jpg"))

    d = client.get("/training/collection-progress").json()
    assert d["integrity"]["orphan_files"] == 1
    assert "sober_999999.jpg" in d["integrity"]["orphan_file_names"]
    assert d["integrity"]["rows_missing_image"] >= 1
    assert missing.id in d["integrity"]["rows_missing_image_ids"]


def test_collection_progress_performs_no_writes(client, db, sandbox):
    r = _row(db, label=0, sub_label=None,
             image_path=str(sandbox / "data" / "snapshots" / "sober" / "sober_x.jpg"))
    _touch(r.image_path)
    _touch(sandbox / "data" / "faces" / "sober" / f"sober_{r.id}.jpg")
    _touch(sandbox / "data" / "faces" / "sober" / "sober_777777.jpg")

    def snapshot():
        rows = sorted(p.relative_to(sandbox).as_posix()
                      for p in sandbox.rglob("*") if p.is_file())
        return db.query(TrainingData).count(), rows

    before = snapshot()
    client.get("/training/collection-progress")
    client.get("/training/collection-progress")
    after = snapshot()
    assert before == after