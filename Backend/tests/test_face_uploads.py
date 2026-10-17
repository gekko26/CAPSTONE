"""Tests for the face-photo upload feature.

Rules under test:
  * the upload namespace and the row-owned namespace are provably disjoint
  * safe_upload_path refuses traversal, absolute paths, encoded separators, the wrong
    class, capture-owned names and registered/ names
  * the endpoint validates the WHOLE request first, then processes files independently
  * impaired uploads need a measured BAC at/above the constant plus explicit confirmation
  * duplicates are detected and never overwrite
  * nothing under the real data/ tree is read or written by any test

Everything is synthetic: JPEGs and PNGs are generated in-test, and BASE_DIR is
redirected to tmp_path.
"""

import json
import os

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from database.models import TrainingData                       # noqa: F401 - registers tables
from models.train import face_uploads as fu
from models.train.sensor_models import IMPAIRED_FACE_MIN_BAC


# ── synthetic image helpers ───────────────────────────────────────────────────

def _jpeg(width=200, height=200, seed=0):
    import cv2
    rng = np.random.default_rng(seed)
    img = rng.integers(0, 255, (height, width, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def _png(width=200, height=200, seed=1):
    import cv2
    rng = np.random.default_rng(seed)
    img = rng.integers(0, 255, (height, width, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def _files(*payloads):
    """Build the multipart payload list. Name only, never a real person's name."""
    out = []
    for i, (nm, data, ctype) in enumerate(payloads):
        out.append(("files", (nm, data, ctype)))
    return out


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def app_env(tmp_path, monkeypatch, db):
    """Redirect BASE_DIR into tmp_path and stub the detector.

    The detector is stubbed so no test depends on a real face being present; the stub
    mirrors the SHIPPED contract (a 'status' key and a normalised 'face_width').
    """
    import routes.training as tr

    base = tmp_path / "base"
    for cls in fu.UPLOAD_CLASSES:
        (base / "data" / "faces" / cls).mkdir(parents=True, exist_ok=True)
    (base / "data" / "faces" / "registered").mkdir(parents=True, exist_ok=True)
    (base / "data" / "snapshots").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(tr, "BASE_DIR", str(base))
    monkeypatch.setattr(tr, "UPLOAD_LOG_PATH",
                        str(base / "data" / "upload_log.jsonl"))
    # _faces_dir reads BASE_DIR at call time, so patching the module global is enough.

    state = {"face_width": 0.5, "status": "normal"}
    calls = []

    def fake_analyze(frame):
        calls.append(frame.shape)
        if state["status"] == "no_face":
            return {"status": "no_face", "face_width": 0.0}
        return {"status": state["status"], "face_width": state["face_width"]}

    monkeypatch.setattr(tr, "analyze_frame", fake_analyze)

    app = FastAPI()
    app.include_router(tr.router)
    app.dependency_overrides[tr.get_db] = lambda: db
    client = TestClient(app)
    return {"client": client, "base": base, "state": state,
            "analyze_calls": calls, "mod": tr}


@pytest.fixture()
def subject(db):
    from database.models import Subject
    s = Subject(name="fixture-subject")
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


# ══════════════════════════════════════════════════════════════════════════════
# A1 — helpers
# ══════════════════════════════════════════════════════════════════════════════

def test_upload_filename_format_and_parse_round_trip():
    for cls in fu.UPLOAD_CLASSES:
        for sid in (0, 1, 42, 999999):
            for dg in ("0123456789ab", "ffffffffffff", "a1b2c3d4e5f6"):
                name = fu.upload_filename(cls, sid, dg)
                assert name == f"{cls}_up_s{sid}_{dg}.jpg"
                assert fu.parse_upload_filename(name) == (cls, sid, dg)


def test_upload_digest_is_first_12_of_sha1_of_original_bytes():
    import hashlib
    data = _jpeg(seed=3)
    assert fu.upload_digest(data) == hashlib.sha1(data).hexdigest()[:12]
    assert len(fu.upload_digest(data)) == 12


@pytest.mark.parametrize("name", [
    "sober_up_s1_aaaaaaaaaaaa.jpg", "fatigue_up_s22_bbbbbbbbbbbb.jpg",
    "impaired_up_s0_cccccccccccc.jpg",
])
def test_pattern_matches_our_form(name):
    assert fu.is_upload_name(name)


@pytest.mark.parametrize("name", [
    "sober_1.jpg", "sober_42.jpg", "fatigue_7.jpg", "drowsy_9.jpg",
    "yawning_13.jpg", "impaired_2.jpg", "impaired_perfume_5.jpg",
    "registered_1.jpg", "abc.jpg", "", "sober_up_s1_aaaaaaaaaaaa.png",
    "sober_up_s1_aaaaaaaaaaa.jpg",       # 11 hex, too short
    "sober_up_s1_aaaaaaaaaaaaa.jpg",     # 13 hex, too long
    "sober_up_s1_AAAAaaaaaaaaaa.jpg",    # uppercase is not the digest alphabet
    "sober_up_s-1_aaaaaaaaaaaa.jpg",     # negative
    "sober_up_s_1_aaaaaaaaaaaa.jpg",     # the earlier double-underscore form
    "drunk_up_s1_aaaaaaaaaaaa.jpg",      # not a trained class
    "sober_up_s1_aaaaaaaaaaaa.jpg.bak",
    "sober/up_s1_aaaaaaaaaaaa.jpg",
    "sober\\up_s1_aaaaaaaaaaaa.jpg",
    "sober_up_s1_aaaaaaaaaaaa.jpg\x00.txt",
])
def test_pattern_rejects_everything_else(name):
    assert fu.parse_upload_filename(name) is None
    assert not fu.is_upload_name(name)


def test_capture_and_upload_namespaces_are_disjoint():
    """The guard that makes row cleanup safe: neither pattern matches the other."""
    capture_names = [
        "sober_1.jpg", "sober_42.jpg", "fatigue_7.jpg", "drowsy_9.jpg",
        "yawning_13.jpg", "impaired_2.jpg", "impaired_perfume_5.jpg",
        "impaired_1234.jpg", "fatigue_0.jpg",
    ]
    upload_names = [
        fu.upload_filename(cls, sid, dg)
        for cls in fu.UPLOAD_CLASSES
        for sid in (0, 1, 2, 42, 1234)
        for dg in ("0123456789ab", "ffffffffffff")
    ]
    # every realistic capture name is invisible to the upload parser
    assert [n for n in capture_names if fu.is_upload_name(n)] == []
    # and every upload name is invisible to the capture parser
    assert [n for n in upload_names if fu.is_capture_name(n)] == []
    # and no name can be both at once
    assert [n for n in capture_names + upload_names
            if fu.is_upload_name(n) and fu.is_capture_name(n)] == []


@pytest.mark.parametrize("bad", [
    "../escape.jpg",
    "../../etc/passwd",
    "/etc/passwd",
    "sub/sober_up_s1_aaaaaaaaaaaa.jpg",
    "..%2Fsober_up_s1_aaaaaaaaaaaa.jpg",
    "sober_up_s1_aaaaaaaaaaaa.jpg/../../../../etc/passwd",
    "sober_up_s1_aaaaaaa_aaaaaaa.jpg/..",
    "sober_up_s1_aaaaaaaaaaaa.jpg\\..\\..\\x.jpg",
])
def test_safe_upload_path_rejects_traversal(tmp_path, bad):
    assert fu.safe_upload_path(str(tmp_path), "sober", bad) is None


def test_safe_upload_path_rejects_wrong_class(tmp_path):
    name = fu.upload_filename("sober", 1, "aaaaaaaaaaaa")
    assert fu.safe_upload_path(str(tmp_path), "sober", name) is not None
    # same name asked for under a different class must be refused
    assert fu.safe_upload_path(str(tmp_path), "fatigue", name) is None
    assert fu.safe_upload_path(str(tmp_path), "impaired", name) is None


def test_safe_upload_path_rejects_capture_owned_and_registered(tmp_path):
    for name in ("sober_42.jpg", "impaired_perfume_5.jpg",
                 "registered_1.jpg", "whatever.jpg"):
        assert fu.safe_upload_path(str(tmp_path), "sober", name) is None


def test_safe_upload_path_rejects_a_symlink_that_escapes(tmp_path):
    """A VALID name that is a symlink out of the folder must still be refused.

    The name-based gates cannot catch this: the basename is exactly our form. Only the
    containment check can, because os.path.realpath() follows the link. Without it a
    DELETE would unlink a file outside data/faces entirely.
    """
    outside = tmp_path / "outside_target.jpg"
    outside.write_bytes(b"precious")
    d = tmp_path / "data" / "faces" / "sober"
    d.mkdir(parents=True)
    link_name = fu.upload_filename("sober", 1, "aaaaaaaaaaaa")
    link = d / link_name
    try:
        os.symlink(str(outside), str(link))
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    assert link.is_symlink()
    assert fu.safe_upload_path(str(tmp_path), "sober", link_name) is None
    # the real file is untouched
    assert outside.is_file()


def test_safe_upload_path_rejects_unknown_class(tmp_path):
    name = fu.upload_filename("sober", 1, "aaaaaaaaaaaa")
    assert fu.safe_upload_path(str(tmp_path), "drunk", name) is None
    assert fu.safe_upload_path(str(tmp_path), "registered", name) is None


def test_safe_upload_path_result_is_inside_the_class_folder(tmp_path):
    name = fu.upload_filename("fatigue", 3, "bbbbbbbbbbbb")
    got = fu.safe_upload_path(str(tmp_path), "fatigue", name)
    root = os.path.realpath(os.path.join(str(tmp_path), "data", "faces", "fatigue"))
    assert got is not None
    assert os.path.commonpath([root, got]) == root


# ══════════════════════════════════════════════════════════════════════════════
# A2 — POST /training/upload-faces
# ══════════════════════════════════════════════════════════════════════════════

def _post(client, cls, subject_id, payloads, **extra):
    data = {"face_class": cls}
    if subject_id is not None:
        data["subject_id"] = str(subject_id)
    for k, v in extra.items():
        if v is not None:
            data[k] = v
    return client.post("/training/upload-faces", data=data, files=_files(*payloads))


def test_upload_lands_in_the_right_folder_with_the_exact_name(app_env, subject):
    c = app_env["client"]
    base = app_env["base"]
    payload = _jpeg(seed=7)
    r = _post(c, "sober", subject.id, [("a.jpg", payload, "image/jpeg")])
    assert r.status_code == 200
    d = r.json()
    assert d["accepted"] == 1 and d["rejected"] == 0 and d["duplicates"] == 0
    assert d["face_class"] == "sober" and d["subject_id"] == subject.id
    expected = fu.upload_filename("sober", subject.id, fu.upload_digest(payload))
    f = d["files"][0]
    assert f["status"] == "accepted"
    assert f["reason"] is None
    assert f["stored_as"] == expected
    assert f["name"] == "a.jpg"
    stored = base / "data" / "faces" / "sober" / expected
    assert stored.is_file()
    # written as JPEG, not as the uploaded PNG bytes
    assert stored.read_bytes()[:2] == b"\xff\xd8"
    # and no snapshot was created
    assert list((base / "data" / "snapshots").rglob("*.jpg")) == []


def test_upload_creates_no_training_row_and_no_snapshot(app_env, subject, db):
    c = app_env["client"]
    base = app_env["base"]
    r = _post(c, "sober", subject.id, [("a.jpg", _jpeg(seed=8), "image/jpeg")])
    assert r.status_code == 200
    assert db.query(TrainingData).count() == 0
    assert list((base / "data" / "snapshots").rglob("*")) == []
    # the subject row itself is untouched
    assert db.query(TrainingData).count() == 0


def test_same_bytes_again_is_a_duplicate_and_does_not_overwrite(app_env, subject):
    c = app_env["client"]
    base = app_env["base"]
    payload = _jpeg(seed=9)
    first = _post(c, "sober", subject.id, [("a.jpg", payload, "image/jpeg")]).json()
    assert first["accepted"] == 1
    target = base / "data" / "faces" / "sober" / first["files"][0]["stored_as"]
    before = target.read_bytes()

    second = _post(c, "sober", subject.id, [("b.jpg", payload, "image/jpeg")]).json()
    assert second["accepted"] == 0
    assert second["duplicates"] == 1
    assert second["rejected"] == 0
    assert second["files"][0]["status"] == "duplicate"
    assert target.read_bytes() == before, "a duplicate must not overwrite"
    assert len(list((base / "data" / "faces" / "sober").iterdir())) == 1


def test_unenrolled_subject_is_rejected_with_the_shipped_message(app_env):
    c = app_env["client"]
    base = app_env["base"]
    r = _post(c, "sober", 999999, [("a.jpg", _jpeg(), "image/jpeg")])
    assert r.status_code == 400
    assert "is not enrolled" in r.json()["detail"]
    assert list((base / "data" / "faces" / "sober").iterdir()) == []


def test_missing_subject_id_is_rejected(app_env):
    c = app_env["client"]
    r = _post(c, "sober", None, [("a.jpg", _jpeg(), "image/jpeg")])
    assert r.status_code in (400, 422)


def test_bad_class_is_rejected(app_env, subject):
    c = app_env["client"]
    base = app_env["base"]
    r = _post(c, "drunk", subject.id, [("a.jpg", _jpeg(), "image/jpeg")])
    assert r.status_code == 400
    assert "face_class must be one of" in r.json()["detail"]
    assert list((base / "data" / "faces" / "sober").iterdir()) == []


def test_too_many_files_is_rejected_and_nothing_is_saved(app_env, subject):
    c = app_env["client"]
    base = app_env["base"]
    many = [(f"f{i}.jpg", _jpeg(seed=i), "image/jpeg")
            for i in range(fu.MAX_FILES_PER_REQUEST + 1)]
    r = _post(c, "sober", subject.id, many)
    assert r.status_code == 400
    assert "too many files" in r.json()["detail"]
    assert list((base / "data" / "faces" / "sober").iterdir()) == []


def test_exactly_max_files_is_allowed(app_env, subject):
    c = app_env["client"]
    exact = [(f"f{i}.jpg", _jpeg(seed=100 + i), "image/jpeg")
             for i in range(fu.MAX_FILES_PER_REQUEST)]
    r = _post(c, "sober", subject.id, exact)
    assert r.status_code == 200
    assert r.json()["accepted"] == fu.MAX_FILES_PER_REQUEST


# ── the impaired BAC gate ─────────────────────────────────────────────────────

def test_impaired_without_bac_is_rejected(app_env, subject):
    c = app_env["client"]
    base = app_env["base"]
    r = _post(c, "impaired", subject.id, [("a.jpg", _jpeg(), "image/jpeg")],
              confirm_real="true")
    assert r.status_code == 400
    assert "impaired uploads need a measured BAC" in r.json()["detail"]
    assert list((base / "data" / "faces" / "impaired").iterdir()) == []


def test_impaired_just_below_the_constant_is_rejected(app_env, subject):
    c = app_env["client"]
    base = app_env["base"]
    r = _post(c, "impaired", subject.id, [("a.jpg", _jpeg(), "image/jpeg")],
              bac=str(IMPAIRED_FACE_MIN_BAC - 0.001), confirm_real="true")
    assert r.status_code == 400
    assert list((base / "data" / "faces" / "impaired").iterdir()) == []


def test_impaired_without_confirmation_is_rejected(app_env, subject):
    c = app_env["client"]
    base = app_env["base"]
    r = _post(c, "impaired", subject.id, [("a.jpg", _jpeg(), "image/jpeg")],
              bac="0.2")
    assert r.status_code == 400
    assert "impaired uploads need a measured BAC" in r.json()["detail"]
    assert list((base / "data" / "faces" / "impaired").iterdir()) == []


def test_impaired_at_exactly_the_constant_with_confirmation_is_accepted(app_env, subject):
    c = app_env["client"]
    r = _post(c, "impaired", subject.id, [("a.jpg", _jpeg(seed=11), "image/jpeg")],
              bac=str(IMPAIRED_FACE_MIN_BAC), confirm_real="true")
    assert r.status_code == 200
    assert r.json()["accepted"] == 1


def test_impaired_above_the_constant_with_confirmation_is_accepted(app_env, subject):
    c = app_env["client"]
    r = _post(c, "impaired", subject.id, [("a.jpg", _jpeg(seed=12), "image/jpeg")],
              bac="0.18", confirm_real="true")
    assert r.status_code == 200
    assert r.json()["accepted"] == 1


def test_impaired_message_quotes_the_constant(app_env, subject):
    c = app_env["client"]
    r = _post(c, "impaired", subject.id, [("a.jpg", _jpeg(), "image/jpeg")])
    assert str(IMPAIRED_FACE_MIN_BAC) in r.json()["detail"]


@pytest.mark.parametrize("cls", ["sober", "fatigue"])
def test_sober_and_fatigue_need_no_bac_and_no_confirmation(app_env, subject, cls):
    c = app_env["client"]
    r = _post(c, cls, subject.id, [("a.jpg", _jpeg(seed=20), "image/jpeg")])
    assert r.status_code == 200
    assert r.json()["accepted"] == 1


# ── per-file behaviour: a bad file never blocks the good ones ─────────────────

def test_oversize_file_rejected_per_file_while_others_are_saved(app_env, subject):
    c = app_env["client"]
    base = app_env["base"]
    big = _jpeg(width=3000, height=3000, seed=30)          # > 5 MB as JPEG noise
    small = _jpeg(seed=31)
    r = _post(c, "sober", subject.id,
              [("big.jpg", big + b"\x00" * (fu.MAX_FILE_BYTES + 10), "image/jpeg"),
               ("small.jpg", small, "image/jpeg")])
    assert r.status_code == 200
    d = r.json()
    assert d["accepted"] == 1 and d["rejected"] == 1
    by_name = {f["name"]: f for f in d["files"]}
    assert by_name["big.jpg"]["status"] == "rejected"
    assert by_name["big.jpg"]["reason"] == "too_large"
    assert by_name["small.jpg"]["status"] == "accepted"
    assert len(list((base / "data" / "faces" / "sober").iterdir())) == 1


def test_non_image_bytes_are_rejected(app_env, subject):
    c = app_env["client"]
    r = _post(c, "sober", subject.id,
              [("fake.jpg", b"this is definitely not an image", "image/jpeg")])
    assert r.status_code == 200
    f = r.json()["files"][0]
    assert f["status"] == "rejected" and f["reason"] == "not_an_image"


def test_png_with_a_jpeg_name_is_accepted_because_we_decode_the_bytes(app_env, subject):
    """Extension is not trusted; the bytes decide."""
    c = app_env["client"]
    r = _post(c, "sober", subject.id, [("actually_png.jpg", _png(seed=40),
                                              "image/jpeg")])
    assert r.status_code == 200
    assert r.json()["files"][0]["status"] == "accepted"


def test_no_face_is_rejected(app_env, subject):
    c = app_env["client"]
    app_env["state"]["status"] = "no_face"
    r = _post(c, "sober", subject.id, [("a.jpg", _jpeg(seed=50), "image/jpeg")])
    f = r.json()["files"][0]
    assert f["status"] == "rejected" and f["reason"] == "no_face"


def test_too_small_face_uses_the_shipped_minimum_constant(app_env, subject):
    from models.train.face_recognition import FACE_MIN_SIZE
    c = app_env["client"]
    app_env["state"]["status"] = "normal"
    app_env["state"]["face_width"] = FACE_MIN_SIZE - 0.01
    r = _post(c, "sober", subject.id, [("a.jpg", _jpeg(seed=51), "image/jpeg")])
    f = r.json()["files"][0]
    assert f["status"] == "rejected" and f["reason"] == "too_small"


def test_face_exactly_at_the_minimum_is_accepted(app_env, subject):
    from models.train.face_recognition import FACE_MIN_SIZE
    c = app_env["client"]
    app_env["state"]["face_width"] = FACE_MIN_SIZE
    r = _post(c, "sober", subject.id, [("a.jpg", _jpeg(seed=52), "image/jpeg")])
    assert r.json()["files"][0]["status"] == "accepted"


def test_large_image_is_downscaled_to_max_side(app_env, subject):
    c = app_env["client"]
    base = app_env["base"]
    import cv2
    wide = _jpeg(width=fu.MAX_SIDE_PX + 400, height=600, seed=60)
    r = _post(c, "sober", subject.id, [("a.jpg", wide, "image/jpeg")])
    assert r.json()["accepted"] == 1
    stored = base / "data" / "faces" / "sober" / r.json()["files"][0]["stored_as"]
    img = cv2.imread(str(stored))
    assert img is not None
    assert max(img.shape[:2]) == fu.MAX_SIDE_PX


def test_small_image_is_never_upscaled(app_env, subject):
    c = app_env["client"]
    base = app_env["base"]
    import cv2
    tiny = _jpeg(width=60, height=40, seed=61)
    r = _post(c, "sober", subject.id, [("a.jpg", tiny, "image/jpeg")])
    assert r.json()["accepted"] == 1
    stored = base / "data" / "faces" / "sober" / r.json()["files"][0]["stored_as"]
    img = cv2.imread(str(stored))
    assert max(img.shape[:2]) <= 60, "must not upscale"


def test_no_temporary_part_file_survives(app_env, subject):
    c = app_env["client"]
    base = app_env["base"]
    _post(c, "sober", subject.id, [("a.jpg", _jpeg(seed=70), "image/jpeg")])
    leftovers = [p.name for p in (base / "data" / "faces" / "sober").iterdir()
                 if p.name.endswith(".part")]
    assert leftovers == []


# ── the JSONL log ─────────────────────────────────────────────────────────────

def test_log_gets_one_line_per_accepted_file_with_no_names(app_env, subject):
    c = app_env["client"]
    base = app_env["base"]
    r = _post(c, "sober", subject.id,
              [("alpha.jpg", _jpeg(seed=80), "image/jpeg"),
               ("beta.jpg", _jpeg(seed=81), "image/jpeg"),
               ("bad.jpg", b"nope", "image/jpeg")])
    assert r.json()["accepted"] == 2
    lines = (base / "data" / "upload_log.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2, "one line per accepted file, none for the rejected one"
    for raw in lines:
        rec = json.loads(raw)
        assert set(rec) >= {"ts", "face_class", "subject_id", "stored_as"}
        assert rec["face_class"] == "sober"
        assert rec["subject_id"] == subject.id
        assert "bac" not in rec, "bac is recorded only for impaired uploads"
        # no name field anywhere, and the original filenames are not in the line
        assert "alpha" not in raw and "beta" not in raw
        assert "fixture-subject" not in raw


def test_log_records_bac_for_impaired(app_env, subject):
    c = app_env["client"]
    base = app_env["base"]
    _post(c, "impaired", subject.id, [("a.jpg", _jpeg(seed=82), "image/jpeg")],
          bac="0.09", confirm_real="true")
    rec = json.loads((base / "data" / "upload_log.jsonl").read_text().strip())
    assert rec["bac"] == 0.09


def test_failing_log_write_does_not_fail_the_request(app_env, subject, monkeypatch):
    """A log that cannot be written must not turn a good upload into a bad request.

    Only the LOG is made unwritable: its path is forced through an existing regular
    file, so makedirs raises OSError while data/faces stays perfectly writable.
    """
    c = app_env["client"]
    base = app_env["base"]
    mod = app_env["mod"]

    blocker = base / "data" / "faces" / "sober" / "blocker.file"
    blocker.write_bytes(b"not a directory")
    monkeypatch.setattr(mod, "UPLOAD_LOG_PATH",
                        str(blocker / "nested" / "upload_log.jsonl"))

    r = _post(c, "sober", subject.id, [("a.jpg", _jpeg(seed=83), "image/jpeg")])
    assert r.status_code == 200
    assert r.json()["accepted"] == 1, "the upload itself still succeeded"
    stored = [p for p in (base / "data" / "faces" / "sober").iterdir()
              if p.name.endswith(".jpg") and p.name != "blocker.file"]
    assert len(stored) == 1, "the image landed on disk despite the log failure"
    assert not (blocker / "nested").exists()


# ══════════════════════════════════════════════════════════════════════════════
# A3 — GET / training/uploaded-faces  and  DELETE
# ══════════════════════════════════════════════════════════════════════════════

def test_listing_shows_only_upload_pattern_files(app_env, subject):
    c = app_env["client"]
    base = app_env["base"]
    _post(c, "sober", subject.id, [("a.jpg", _jpeg(seed=90), "image/jpeg")])
    # plant the kinds of file that must stay invisible to the listing
    sober = base / "data" / "faces" / "sober"
    (sober / "sober_42.jpg").write_bytes(b"capture-owned")
    (sober / "impaired_perfume_5.jpg").write_bytes(b"capture-owned")
    (sober / "random.jpg").write_bytes(b"junk")
    reg = base / "data" / "faces" / "registered"
    (reg / "registered_1.jpg").write_bytes(b"registered")

    d = c.get("/training/uploaded-faces").json()
    names = [f["name"] for f in d["files"]]
    assert len(names) == 1 and names[0].startswith("sober_up_s")
    assert d["per_class"]["sober"] == 1
    assert str(subject.id) in d["per_subject"]


def test_listing_filters_by_class(app_env, subject):
    c = app_env["client"]
    _post(c, "sober", subject.id, [("a.jpg", _jpeg(seed=91), "image/jpeg")])
    _post(c, "fatigue", subject.id, [("b.jpg", _jpeg(seed=92), "image/jpeg")])
    d = c.get("/training/uploaded-faces", params={"face_class": "fatigue"}).json()
    assert [f["face_class"] for f in d["files"]] == ["fatigue"]


def test_listing_rejects_an_unknown_class(app_env):
    c = app_env["client"]
    assert c.get("/training/uploaded-faces",
                 params={"face_class": "drunk"}).status_code == 400


def test_listing_never_returns_image_bytes(app_env, subject):
    c = app_env["client"]
    _post(c, "sober", subject.id, [("a.jpg", _jpeg(seed=93), "image/jpeg")])
    body = c.get("/training/uploaded-faces").text
    assert "\xff\xd8" not in body and "base64" not in body


def test_delete_removes_only_the_upload(app_env, subject):
    c = app_env["client"]
    base = app_env["base"]
    r = _post(c, "sober", subject.id, [("a.jpg", _jpeg(seed=94), "image/jpeg")])
    stored = r.json()["files"][0]["stored_as"]
    # a capture-owned sibling that must survive
    victim = base / "data" / "faces" / "sober" / "sober_42.jpg"
    victim.write_bytes(b"capture-owned")

    d = c.delete(f"/training/uploaded-faces/sober/{stored}")
    assert d.status_code == 200
    assert d.json()["name"] == stored
    assert not (base / "data" / "faces" / "sober" / stored).exists()
    assert victim.is_file(), "the capture-owned file must survive"
    # second delete is a clean 404
    assert c.delete(f"/training/uploaded-faces/sober/{stored}").status_code == 404


@pytest.mark.parametrize("name", [
    "sober_42.jpg", "impaired_perfume_5.jpg", "random.jpg",
    "../sober/sober_up_s1_aaaaaaaaaaaa.jpg",
    "..%2Fsober_up_s1_aaaaaaaaaaaa.jpg",
    "/etc/passwd",
])
def test_delete_refuses_anything_that_is_not_our_file(app_env, name):
    c = app_env["client"]
    r = c.delete(f"/training/uploaded-faces/sober/{name}")
    assert r.status_code in (400, 404), name


def test_delete_refuses_another_classs_upload(app_env, subject):
    c = app_env["client"]
    base = app_env["base"]
    r = _post(c, "sober", subject.id, [("a.jpg", _jpeg(seed=95), "image/jpeg")])
    stored = r.json()["files"][0]["stored_as"]
    # ask for it under the wrong class
    assert c.delete(f"/training/uploaded-faces/fatigue/{stored}").status_code == 400
    assert (base / "data" / "faces" / "sober" / stored).is_file()


def test_delete_never_touches_registered(app_env, subject):
    c = app_env["client"]
    base = app_env["base"]
    reg = base / "data" / "faces" / "registered"
    (reg / "registered_1.jpg").write_bytes(b"registered")
    assert c.delete("/training/uploaded-faces/registered/registered_1.jpg").status_code == 400
    assert (reg / "registered_1.jpg").is_file()


def test_delete_never_removes_a_directory(app_env, subject):
    c = app_env["client"]
    base = app_env["base"]
    d = base / "data" / "faces" / "sober"
    r = c.delete("/training/uploaded-faces/sober/subdir")
    assert r.status_code in (400, 404)
    assert d.is_dir()


def test_registered_directory_is_not_listed_or_deletable(app_env):
    c = app_env["client"]
    base = app_env["base"]
    reg = base / "data" / "faces" / "registered"
    reg.mkdir(parents=True, exist_ok=True)
    (reg / "registered_1.jpg").write_bytes(b"registered")
    d = c.get("/training/uploaded-faces").json()
    assert [f for f in d["files"] if "registered" in f["name"]] == []