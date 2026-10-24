"""
LOPO (leave-one-person-out) readiness rehearsal.

Readiness artifact for stranger performance: every subject must be able to become a
fold's full hold-out without the evaluation crashing, silently skipping, or writing
anything to models/saved/. With real data in training_data these tests run the honest
metric end-to-end; with an empty corpus they exercise it on synthetic corpora.

LOPO must stay side-effect-free: deployed models come from sensor_models.train(),
never from a LOPO fold. These tests pin that property.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models import Subject, TrainingData  # noqa: E402  (populates Base metadata before create_all)
from models.train import sensor_models  # noqa: E402


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


def _corpus(subjects=4, classes=(0, 1, 2), rows_per_cell=6, seed=0):
    """18-feature, learnable rows where every subject spans every class.

    Each (subject, class) cell gets a class-separable blob on feature 3 plus
    per-subject noise on feature 7 — so a model that memorized the person scores
    higher than one that learned the classes, mirroring the real leakage risk.
    """
    rng = np.random.default_rng(seed)
    X, y, subjects_list = [], [], []
    for s_i, subj in enumerate(subjects):
        for cls in classes:
            X.append(rng.normal(size=(rows_per_cell, 18)) + s_i * 0.3)
            y += [cls] * rows_per_cell
            subjects_list += [subj] * rows_per_cell
    X = np.vstack(X)
    y = np.array(y)
    subjects_list = np.array(subjects_list)
    for cls in classes:
        mask = y == cls
        X[mask, 3] += [0, 4, 8][cls]
    return X, y, subjects_list


@pytest.fixture(autouse=True)
def _tiny_and_isolated(monkeypatch, tmp_path):
    """Swap in tiny models and redirect SAVE_DIR so saved artifacts are isolated."""
    monkeypatch.setattr(sensor_models, "MODELS", _tiny_models())
    monkeypatch.setattr(sensor_models, "SAVE_DIR", str(tmp_path))
    yield


# ----------------------------------------------------------------------------------
# the honest-metric cases
# ----------------------------------------------------------------------------------

def test_lopo_reports_per_subject_and_means():
    X, y, subjects = _corpus(subjects=["A", "B", "C", "D"])
    result = sensor_models.leave_one_person_out(X, y, subjects)
    assert "error" not in result
    assert result["result_kind"] == "lopo"
    assert result["subjects"] == ["A", "B", "C", "D"]
    assert result["classes_present"] == [0, 1, 2]
    for name in ("random_forest", "xgboost"):
        model_result = result[name]
        assert set(model_result["per_subject"]) == {"A", "B", "C", "D"}
        assert model_result["subjects_evaluated"] == 4
        for fold in model_result["per_subject"].values():
            assert 0.0 <= fold["accuracy"] <= 100.0
        assert 0.0 <= model_result["mean_accuracy"] <= 100.0
        assert 0.0 <= model_result["mean_f1"] <= 100.0
        assert model_result["worst_subject"]["subject"] in ("A", "B", "C", "D")
        assert model_result["best_subject"]["subject"] in ("A", "B", "C", "D")


def test_lopo_needs_at_least_two_subjects():
    X, y, subjects = _corpus(subjects=["only-A"])
    result = sensor_models.leave_one_person_out(X, y, subjects)
    assert "error" in result
    assert "at least 2 distinct subjects" in result["error"]


def test_lopo_skips_single_class_folds_with_warning():
    # Subject A (only label-0 rows) folds fine; holding out B leaves a 1-class train
    # fold, which must be skipped and reported — never crashing, never scoring.
    X, y, subjects = _corpus(subjects=["A", "B"], classes=(0, 1))
    y_b_only = (y == 1) | (subjects == "A")
    y = y.copy()
    y[(subjects == "A")] = 0
    result = sensor_models.leave_one_person_out(X, y, subjects)
    assert "error" not in result
    assert result["warnings"], "single-class fold must be reported, not silent"
    for name in ("random_forest", "xgboost"):
        assert result[name]["subjects_evaluated"] == 1


def test_lopo_rejects_unknown_model_and_length_mismatch():
    X, y, subjects = _corpus(subjects=["A", "B", "C", "D"])
    result = sensor_models.leave_one_person_out(X, y, subjects, model_names=["nope"])
    assert "Unknown model name" in result["error"]
    X2, y2, subjects2 = _corpus(subjects=["A", "B"])
    assert len(y2) == len(subjects2)
    result2 = sensor_models.leave_one_person_out(X2[:10], y2, subjects2)
    assert "error" in result2


def test_lopo_never_writes_saved_artifacts():
    """The deployed-model directory must be byte-identical before and after LOPO."""
    X, y, subjects = _corpus(subjects=["A", "B", "C", "D"])
    before = set(os.listdir(sensor_models.SAVE_DIR))
    result = sensor_models.leave_one_person_out(X, y, subjects)
    assert "error" not in result
    after = set(os.listdir(sensor_models.SAVE_DIR))
    assert before == after, "LOPO must not persist models, scalers or label maps"


def test_lopo_against_real_orm_rows(db, tmp_path, monkeypatch):
    """DB-backed rehearsal: subjects + training_data rows flow through LOPO.

    Builds features exactly the way the /training/train route does (base 15D +
    fallback-derived directional trio) so the harness matches production wiring.
    """
    subject_ids = {}
    for name in ("Ana", "Ben"):
        subj = Subject(name=name, group="Student")
        db.add(subj)
        db.flush()
        subject_ids[name] = subj.id

    label_map = {"Ana": {0, 1}, "Ben": {0, 1}}
    n = 0
    trial_counter = 0
    for name, labels in label_map.items():
        for label in labels:
            for _ in range(3):
                trial_counter += 1
                db.add(_training_row(subject_ids[name], label, trial_counter))
                n += 1
    db.commit()

    from models.train import sensor_models as sm
    db_rows = db.query(TrainingData).filter(TrainingData.label >= 0).all()
    X, y, subjects = [], [], []
    for r in db_rows:
        if r.mq3_1_max is None or r.temperature is None or r.humidity is None:
            continue
        base = [r.mq3_1_max, r.mq3_1_avg, r.mq3_1_std,
                r.mq3_2_max, r.mq3_2_avg, r.mq3_2_std,
                r.mq3_3_max, r.mq3_3_avg, r.mq3_3_std,
                r.rise_time, r.decay_time,
                r.spatial_variance, r.spatial_variance_avg,
                r.temperature, r.humidity]
        br = getattr(r, "breath_ratio", None)
        sr = getattr(r, "sanitizer_ratio", None)
        sd = getattr(r, "spatial_direction", None)
        if br is not None and sr is not None and sd is not None:
            feat = base + [float(br), float(sr), float(sd)]
        else:
            eps = sm.EPSILON
            br_calc = ((r.mq3_1_avg + r.mq3_2_avg) / 2.0) / max(r.mq3_3_avg or 1.0, eps)
            sr_calc = ((r.mq3_2_avg + r.mq3_3_avg) / 2.0) / max(r.mq3_1_avg or 1.0, eps)
            sd_calc = float((r.mq3_3_max or 0) - (r.mq3_1_max or 0))
            feat = base + [br_calc, sr_calc, sd_calc]
        X.append(feat)
        y.append(r.label)
        subjects.append(r.subject_id)
    assert len(X) == n
    result = sm.leave_one_person_out(X, y, subjects)
    assert "error" not in result
    assert sorted(result["subjects"]) == sorted({str(s) for s in subjects})
    for name in ("random_forest", "xgboost"):
        assert result[name]["subjects_evaluated"] == 2


def _training_row(subject_id, label, trial_id):
    """One synthetic training_data row with a learnable per-class sensor signature.

    Like the real corpus: breath (label 1) peaks on nose+jaw (mq1, mq2), plain air
    (label 0) is flat across channels, one trial_id per burst, temp/humidity drifting
    slowly over trials instead of carrying the DHT fallback constant.
    """
    class_peak = {0: (1.0, 1.02, 1.0), 1: (4.5, 4.8, 1.6)}[label]
    return TrainingData(
        subject_id=subject_id, label=label, trial_id=trial_id,
        mq3_1_max=class_peak[0], mq3_1_avg=class_peak[0] * 0.85, mq3_1_std=0.05,
        mq3_2_max=class_peak[1], mq3_2_avg=class_peak[1] * 0.85, mq3_2_std=0.05,
        mq3_3_max=class_peak[2], mq3_3_avg=class_peak[2] * 0.85, mq3_3_std=0.05,
        rise_time=1.2 + label * 0.1, decay_time=2.0 + label * 0.1,
        spatial_variance=0.5, spatial_variance_avg=0.4,
        temperature=26.5 + (trial_id % 3) * 0.4,
        humidity=68.0 - (trial_id % 4) * 0.5,
    )
