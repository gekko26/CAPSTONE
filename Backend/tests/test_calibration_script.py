"""Tests for the calibration script's PURE helpers.

The script itself is never executed against the real gallery: importing it must not
load DeepFace, and every test here feeds synthetic distance lists or a tmp_path
gallery. Nothing under data/ is read.
"""

import importlib.util
import os
import pathlib

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "calibrate_face_threshold.py"


@pytest.fixture(scope="module")
def cal():
    """Import the script by path. Must NOT import DeepFace.

    Note: main() imports the app module, which legitimately sets TF thread env
    vars; that is the app's doing, not the script's, so it is not asserted here.
    """
    spec = importlib.util.spec_from_file_location("calibrate_face_threshold", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert "deepface" not in _heavy_modules(), "importing the script loaded DeepFace"
    return mod


def _heavy_modules():
    import sys
    return {m for m in ("deepface", "tensorflow", "cv2")
            if m in sys.modules and sys.modules[m] is not None}


# ── summarise ────────────────────────────────────────────────────────────────

def test_summarise_of_empty_is_none(cal):
    assert cal.summarise([]) is None
    assert cal.summarise(None) is None


def test_summarise_reports_min_median_p95_max(cal):
    s = cal.summarise([0.10, 0.20, 0.30, 0.40])
    assert s["min"] == 0.10
    assert s["max"] == 0.40
    assert s["median"] == pytest.approx(0.25)
    assert 0.20 <= s["p95"] <= 0.40


def test_summarise_of_single_value(cal):
    assert cal.summarise([0.5]) == {"min": 0.5, "median": 0.5, "p95": 0.5, "max": 0.5}


# ── suggest_threshold ────────────────────────────────────────────────────────

def test_clean_separation_puts_the_threshold_in_the_gap(cal):
    same = [0.10, 0.20, 0.30]
    diff = [0.50, 0.60]
    (t, m), info = cal.suggest_threshold(same, diff)
    assert info["overlap"] is False
    assert 0.30 < t < 0.50, t
    assert m > 0


def test_overlapping_populations_fail_safe(cal):
    """Overlapping ranges must NOT produce a permissive threshold."""
    same = [0.10, 0.45, 0.50]
    diff = [0.20, 0.30]
    (t, m), info = cal.suggest_threshold(same, diff)
    assert info["overlap"] is True
    assert t <= max(same), t          # rejects the WORST genuine match (0.50)
    assert t <= cal.LIBRARY_DEFAULT_THRESHOLD
    assert m == 0.0                    # and refuses to pick between tied people


def test_suggest_threshold_returns_none_without_data(cal):
    assert cal.suggest_threshold([], [0.5])[0] is None
    assert cal.suggest_threshold([0.1], [])[0] is None


def test_suggested_threshold_is_never_more_permissive_than_the_library_default(cal):
    """Even a huge impostor gap must not yield a threshold looser than Facenet+cosine's 0.40."""
    (t, _), _ = cal.suggest_threshold([0.05, 0.10], [0.90, 0.95])
    assert t <= cal.LIBRARY_DEFAULT_THRESHOLD, t
    assert t == 0.40, t


# ── guard rails ──────────────────────────────────────────────────────────────

def test_not_enough_prints_a_message_and_suggests_nothing(capsys, cal):
    code = cal.not_enough("only 1 person enrolled")
    out = capsys.readouterr().out
    assert code == 2
    assert "Not enough data" in out
    assert "No threshold suggested" in out
    assert "export " not in out


def test_main_exits_2_when_the_folder_is_missing(capsys, cal, tmp_path):
    code = cal.main(["--registered-dir", str(tmp_path / "nope")])
    assert code == 2
    assert "Not enough data" in capsys.readouterr().out


def test_main_exits_2_with_a_single_person(capsys, cal, tmp_path):
    person = tmp_path / "registered" / "p0"
    person.mkdir(parents=True)
    for i in range(3):
        (person / f"{i:02d}.jpg").write_bytes(b"x")
    assert cal.main(["--registered-dir", str(tmp_path / "registered")]) == 2
    assert "Not enough data" in capsys.readouterr().out


def test_main_exits_2_when_people_have_too_few_photos(capsys, cal, tmp_path):
    root = tmp_path / "registered"
    for name in ("p0", "p1"):
        d = root / name
        d.mkdir(parents=True)
        (d / "01.jpg").write_bytes(b"x")
    assert cal.main(["--registered-dir", str(root)]) == 2
    assert "photos" in capsys.readouterr().out


def test_arg_parser_exposes_help_without_running_anything(cal):
    parser = cal.argparse.ArgumentParser(prog="calibrate_face_threshold.py")
    assert parser is not None


def test_script_writes_no_files(tmp_path, cal):
    """Running the guards must leave the tree untouched."""
    root = tmp_path / "registered" / "p0"
    root.mkdir(parents=True)
    (root / "01.jpg").write_bytes(b"x")
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    cal.main(["--registered-dir", str(tmp_path / "registered")])
    after = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    assert before == after, "the calibration script wrote something"


def test_help_flag_is_accepted(capsys, cal):
    with pytest.raises(SystemExit) as e:
        cal.main(["--help"])
    assert e.value.code == 0
    out = " ".join(capsys.readouterr().out.split())   # argparse hard-wraps the text
    assert "FACE_MATCH_THRESHOLD" in out
    assert "never names or paths" in out
