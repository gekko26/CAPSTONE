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
def rec(monkeypatch, db):
    """routes.recognition with face_recognition stubbed and enroll recorded."""
    calls = []

    stub = types.ModuleType("models.train.face_recognition")

    def fake_enroll(name, frames):
        calls.append((name, list(frames)))

    stub.enroll = fake_enroll
    stub.identify = lambda frame: {"identified": False, "name": "Unknown", "confidence": 0.0}
    stub.verify = lambda *a, **k: {"verified": False}
    monkeypatch.setitem(sys.modules, "models.train.face_recognition", stub)

    sys.modules.pop("routes.recognition", None)
    rr = importlib.import_module("routes.recognition")
    monkeypatch.setattr(rr, "enroll", fake_enroll)

    # the avatar lookup reads data/faces/<name> — point it somewhere harmless
    monkeypatch.setattr(
        rr, "__file__", str(pathlib.Path(__file__).parent / "routes" / "recognition.py"))

    app = FastAPI()
    app.include_router(rr.router)
    app.dependency_overrides[rr.get_db] = lambda: db
    return types.SimpleNamespace(router=rr, client=TestClient(app), calls=calls, db=db)


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
