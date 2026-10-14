"""Negative tests proving the retired fusion model is gone for good.

The final PASS / WARN / FAIL verdict comes from the rule module
(``models/decision.py``). The learned fusion model has been deleted, so these
tests FAIL again the moment its module or its training route comes back.
"""

import subprocess
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ── (a) the module itself no longer exists ────────────────────────────────────

def test_fusion_model_module_is_gone():
    """`import models.train.fusion_model` must raise ImportError.

    Run in a subprocess so the verdict cannot be masked by this session's
    already-imported modules or by a stale __pycache__ entry.
    """
    code = (
        "import sys, importlib\n"
        "sys.path.insert(0, '.')\n"
        "try:\n"
        "    importlib.import_module('models.train.fusion_model')\n"
        "except ImportError:\n"
        "    print('IMPORT_ERROR')\n"
        "else:\n"
        "    print('IMPORT_SUCCEEDED')\n"
    )
    out = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True, cwd=".")
    assert "IMPORT_ERROR" in out.stdout, (
        f"models.train.fusion_model is importable again: {out.stdout!r} {out.stderr!r}")


def test_fusion_model_source_file_is_absent():
    """The file is deleted, not merely un-imported."""
    import pathlib
    import models.train as pkg
    path = pathlib.Path(pkg.__file__).parent / "fusion_model.py"
    assert not path.exists(), f"{path} is back on disk"


# ── (b) the training route no longer exists ───────────────────────────────────

def test_train_fusion_route_returns_404_or_405(db, tmp_path, monkeypatch):
    """POST /training/train/fusion must not reach a handler.

    404 = route absent, 405 = path exists under another method. Anything else
    (notably 200) means the fusion trainer was restored.
    """
    import routes.training as pr

    monkeypatch.setattr(pr, "trigger_esp32_async",
                        lambda *a, **k: __import__("asyncio").sleep(0))

    app = FastAPI()
    app.include_router(pr.router)
    app.dependency_overrides[pr.get_db] = lambda: db
    r = TestClient(app).post("/training/train/fusion")
    assert r.status_code in (404, 405), (
        f"expected 404/405 for the removed fusion route, got {r.status_code}: {r.text}")


def test_train_fusion_route_is_not_in_the_router_table(db, tmp_path, monkeypatch):
    """No route in the training router answers /train/fusion."""
    import routes.training as pr
    paths = {r.path for r in pr.router.routes if hasattr(r, "path")}
    assert "/training/train/fusion" not in paths, paths


# ── supporting surfaces stay clean ────────────────────────────────────────────

def test_model_status_no_longer_lists_fusion(db, tmp_path, monkeypatch):
    """/models/status must not advertise a fusion artifact."""
    import routes.model_status as ms
    from fastapi import FastAPI as FA
    from fastapi.testclient import TestClient as TC

    monkeypatch.setattr(ms, "SAVE_DIR", str(tmp_path))
    app = FA()
    app.include_router(ms.router)
    body = TC(app).get("/models/status").json()
    names = [m["name"] for m in body["models"]]
    assert not [n for n in names if "fusion" in n], names
    assert "mobilenet" in names and "random_forest" in names and "xgboost" in names


def test_metrics_store_sections_exclude_fusion():
    """Nothing writes a 'fusion' section any more; the live ones still round-trip."""
    from models.train import metrics_store as ms

    assert not hasattr(ms, "FUSION")
    for section in ("sensor", "bac", "mobilenet"):
        # the generic API that those three sections rely on is untouched
        assert callable(ms.save_metrics) and callable(ms.load_metrics)


# ── (c) the rule decision still imports bare ──────────────────────────────────

def test_decision_module_still_imports_bare():
    """models.decision must import without tensorflow / cv2 / sqlalchemy / joblib."""
    code = (
        "import sys\n"
        "import models.decision\n"
        "heavy = [m for m in ('tensorflow', 'cv2', 'sqlalchemy', 'joblib') if m in sys.modules]\n"
        "print('HEAVY:' + ','.join(heavy))\n"
    )
    out = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True, cwd=".")
    assert out.returncode == 0, out.stderr
    assert "HEAVY:\n" in out.stdout + "\n", f"heavy deps pulled in: {out.stdout!r}"


def test_predict_module_does_not_import_fusion():
    """The live predict path must stay free of the retired model."""
    import pathlib
    src = pathlib.Path("routes/predict.py").read_text()
    assert "fusion_model" not in src
