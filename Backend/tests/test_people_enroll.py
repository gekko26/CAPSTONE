"""Tests for multi-photo enrollment (POST /recognition/enroll).

DeepFace and TensorFlow are NEVER imported here: ``models.train.face_recognition``
is stubbed in ``sys.modules`` before ``routes.recognition`` is imported, which also
keeps that module's import-time ``os.makedirs`` from touching ``data/``.

The on-disk naming behaviour is exercised by executing the REAL ``enroll`` body,
extracted from its source with ``ast``, in a namespace whose ``REGISTERED``
directory is ``tmp_path``. ``enroll`` itself depends only on ``os``/``cv2``.
"""

import ast
import importlib
import pathlib
import sys
import types

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# imported at module scope so Subject is registered on Base.metadata BEFORE the
# shared `db` fixture runs create_all()
from database.models import Subject


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def rec(monkeypatch, db, tmp_path):
    """routes.recognition with face_recognition stubbed and enroll recorded.

    FACES_REGISTERED is pointed at tmp_path, so the avatar lookup reads and writes
    real files under tmp_path and never touches data/.
    """
    import cv2
    calls = []
    registered = tmp_path / "registered"
    registered.mkdir(parents=True, exist_ok=True)
    state = {"write": True}

    def fake_enroll(name, frames):
        calls.append((name, list(frames)))
        if not state["write"]:
            return
        # mirror what the real enroll does: one numbered .jpg per frame
        person_dir = registered / name
        person_dir.mkdir(parents=True, exist_ok=True)
        for i, f in enumerate(frames):
            cv2.imwrite(str(person_dir / f"{i + 1:02d}.jpg"), f)

    stub = types.ModuleType("models.train.face_recognition")
    stub.enroll = fake_enroll
    stub.identify = lambda frame: {"identified": False, "name": "Unknown", "confidence": 0.0}
    stub.verify = lambda *a, **k: {"verified": False}
    stub.REGISTERED = str(registered)
    monkeypatch.setitem(sys.modules, "models.train.face_recognition", stub)

    sys.modules.pop("routes.recognition", None)
    rr = importlib.import_module("routes.recognition")
    monkeypatch.setattr(rr, "enroll", fake_enroll)
    monkeypatch.setattr(rr, "FACES_REGISTERED", str(registered))

    app = FastAPI()
    app.include_router(rr.router)
    app.dependency_overrides[rr.get_db] = lambda: db
    return types.SimpleNamespace(router=rr, client=TestClient(app), calls=calls,
                                 db=db, registered=registered, state=state)


def jpeg_bytes(value=90):
    import cv2
    ok, buf = cv2.imencode(".jpg", np.full((32, 32, 3), value, dtype=np.uint8))
    assert ok
    return buf.tobytes()


def post_files(client, name, payloads, **form):
    data = {"name": name}
    data.update(form)
    return client.post("/recognition/enroll",
                       data=data,
                       files=[("files", (f"{i}.jpg", p, "image/jpeg"))
                              for i, p in enumerate(payloads)])


# ── route: several images in one enrollment ───────────────────────────────────

def test_enroll_accepts_three_images(rec):
    r = post_files(rec.client, "juan", [jpeg_bytes(10), jpeg_bytes(120), jpeg_bytes(240)],
                   id_number="2026-0001", group="Student")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["images"] == 3
    assert body["message"] == "juan enrolled successfully"
    assert body["id_number"] == "2026-0001"
    assert rec.db.query(Subject).count() == 1
    assert len(rec.calls) == 1
    assert rec.calls[0][0] == "juan"
    assert len(rec.calls[0][1]) == 3
    assert all(isinstance(f, np.ndarray) and f.shape == (32, 32, 3) for f in rec.calls[0][1])


def test_re_enrolling_same_name_updates_the_same_subject(rec):
    first = post_files(rec.client, "juan", [jpeg_bytes(10), jpeg_bytes(120)],
                       id_number="2026-0001", group="Student").json()
    second = post_files(rec.client, "juan", [jpeg_bytes(200), jpeg_bytes(240)],
                        id_number="2026-0002", group="Staff").json()

    assert first["subject_id"] == second["subject_id"]
    assert rec.db.query(Subject).count() == 1
    row = rec.db.query(Subject).filter(Subject.name == "juan").one()
    assert row.id == first["subject_id"]
    # both calls still reached enroll with their own frames
    assert [len(c[1]) for c in rec.calls] == [2, 2]


# ── route: bad input ──────────────────────────────────────────────────────────

def test_undecodable_files_are_skipped(rec):
    r = post_files(rec.client, "juan",
                   [b"not-an-image", jpeg_bytes(10), b"\x00\x01\x02garbage", jpeg_bytes(200)],
                   id_number="2026-0001")
    assert r.status_code == 200, r.text
    assert r.json()["images"] == 2
    assert len(rec.calls[0][1]) == 2
    assert rec.db.query(Subject).count() == 1


def test_zero_valid_images_returns_error_and_creates_no_subject(rec):
    r = post_files(rec.client, "juan", [b"nope", b"still-nope"], id_number="2026-0001")
    assert r.status_code == 200
    assert r.json() == {"error": "No valid images uploaded"}
    assert rec.calls == []
    assert rec.db.query(Subject).count() == 0


# ── on-disk naming: the REAL enroll body, no DeepFace ─────────────────────────

def _load_enroll(registered_dir):
    """exec the real enroll + _next_free_path from source with REGISTERED=tmp_path.

    Importing the module for real would pull in DeepFace/TensorFlow and would run
    its import-time makedirs against data/, so the two functions are extracted by
    name and executed in an isolated namespace instead.
    """
    import cv2
    src = pathlib.Path("models/train/face_recognition.py").read_text()
    tree = ast.parse(src)
    wanted = {"_next_free_path", "enroll"}
    fns = [n for n in tree.body
           if isinstance(n, ast.FunctionDef) and n.name in wanted]
    assert {f.name for f in fns} == wanted, "enroll helpers missing from source"
    ns = {"os": __import__("os"), "cv2": cv2, "REGISTERED": str(registered_dir)}
    exec(compile(ast.Module(body=fns, type_ignores=[]), "face_recognition.py", "exec"), ns)
    return ns["enroll"]


def test_enroll_writes_distinct_files_and_second_call_adds(tmp_path):
    import cv2

    enroll = _load_enroll(tmp_path)
    subject_dir = tmp_path / "juan"

    frames = [np.full((32, 32, 3), v, dtype=np.uint8) for v in (10, 120, 240)]
    enroll("juan", frames)

    first = sorted(p.name for p in subject_dir.glob("*.jpg"))
    assert len(first) == 3, first
    before = {p.name: p.read_bytes() for p in subject_dir.glob("*.jpg")}

    enroll("juan", [np.full((32, 32, 3), 200, dtype=np.uint8),
                    np.full((32, 32, 3), 30, dtype=np.uint8)])

    after = sorted(p.name for p in subject_dir.glob("*.jpg"))
    assert len(after) == 5, after
    # the first call's three files are byte-identical: nothing was overwritten
    for name, blob in before.items():
        assert (subject_dir / name).read_bytes() == blob, f"{name} was overwritten"


def test_enroll_accepts_string_paths_without_overwriting(tmp_path):
    enroll = _load_enroll(tmp_path)
    import cv2

    for i, v in enumerate((10, 120)):
        cv2.imwrite(str(tmp_path / f"src{i}.jpg"), np.full((32, 32, 3), v, dtype=np.uint8))
    subject_dir = tmp_path / "juan"

    enroll("juan", [str(tmp_path / "src0.jpg")])
    enroll("juan", [str(tmp_path / "src1.jpg")])

    saved = sorted(p.name for p in subject_dir.glob("*.jpg"))
    assert saved == ["01.jpg", "02.jpg"]
    # 10 was darker than 120, so the two files really are different images
    assert (subject_dir / "01.jpg").read_bytes() != (subject_dir / "02.jpg").read_bytes()


# ── avatar_path ───────────────────────────────────────────────────────────────

def test_new_enrollment_stores_a_real_avatar_path(rec):
    r = post_files(rec.client, "juan", [jpeg_bytes(10), jpeg_bytes(120), jpeg_bytes(240)],
                   id_number="2026-0001")
    assert r.status_code == 200, r.text
    row = rec.db.query(Subject).filter(Subject.name == "juan").one()
    assert row.avatar_path is not None, "avatar_path is still NULL"
    assert pathlib.Path(row.avatar_path).is_file(), row.avatar_path
    # the stored path is inside the folder enroll() actually writes to
    assert str(rec.registered) in row.avatar_path


def test_avatar_lookup_uses_the_registered_folder_not_data_faces(rec):
    """The old code looked in data/faces/<name>, which never exists."""
    post_files(rec.client, "juan", [jpeg_bytes(10)], id_number="2026-0001")
    row = rec.db.query(Subject).filter(Subject.name == "juan").one()
    assert pathlib.Path(row.avatar_path).parent == rec.registered / "juan"
    # and nothing was created one level up, in the folder the old code searched
    assert not (rec.registered.parent / "juan").exists()


def test_re_enrolling_fills_a_null_avatar_path(rec):
    db = rec.db
    existing = Subject(name="juan", face_id="2026-0001", group="Student", avatar_path=None)
    db.add(existing)
    db.commit()
    assert existing.avatar_path is None

    r = post_files(rec.client, "juan", [jpeg_bytes(10), jpeg_bytes(120)], id_number="2026-0002")
    assert r.status_code == 200, r.text
    db.refresh(existing)
    assert existing.avatar_path is not None, "re-enrollment left avatar_path NULL"
    assert pathlib.Path(existing.avatar_path).is_file()
    assert r.json()["subject_id"] == existing.id


def test_subject_without_photos_keeps_avatar_null(rec):
    """No frames on disk -> no avatar, and enrollment still succeeds."""
    rec.state["write"] = False
    r = post_files(rec.client, "juan", [jpeg_bytes(10)], id_number="2026-0001")
    assert r.status_code == 200, r.text
    assert r.json()["images"] == 1
    row = rec.db.query(Subject).filter(Subject.name == "juan").one()
    assert row.avatar_path is None


def test_avatar_route_serves_the_photo(rec):
    post_files(rec.client, "juan", [jpeg_bytes(77)], id_number="2026-0001")
    r = rec.client.get("/recognition/avatar/juan")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/")
    assert len(r.content) > 0

    missing = rec.client.get("/recognition/avatar/nobody")
    assert missing.status_code == 404
    assert "error" in missing.json()


def test_avatar_route_rejects_path_traversal(rec):
    """Name-keyed lookup must not escape the registered folder."""
    for evil in ("../../etc", "..%2F..%2Fetc"):
        r = rec.client.get(f"/recognition/avatar/{evil}")
        assert r.status_code in (404, 400, 422), f"{evil} -> {r.status_code}"
        assert b"root:" not in r.content
