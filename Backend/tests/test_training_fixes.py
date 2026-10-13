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

class _FakeRow:
    def __init__(self, i, label, bac, trial_id, **kw):
        self.id = i
        self.label = label
        self.bac = bac
        self.trial_id = trial_id
        self.sub_label = None
        self.image_path = None
        self.ear = None
        self.mar = None
        self.temperature = 27.0
        self.mq3_1_max = 1.0
        self.mq3_1_avg = 1.0
        self.mq3_1_std = 0.1
        self.mq3_2_max = 1.0
        self.mq3_2_avg = 1.0
        self.mq3_2_std = 0.1
        self.mq3_3_max = 1.0
        self.mq3_3_avg = 1.0
        self.mq3_3_std = 0.1
        self.rise_time = 1.0
        self.decay_time = 1.0
        self.spatial_variance = 1.0
        self.spatial_variance_avg = 1.0
        self.humidity = 60.0
        self.breath_ratio = 1.0
        self.sanitizer_ratio = 1.0
        self.spatial_direction = 0.0
        self.__dict__.update(kw)


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def all(self):
        return self._rows


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def query(self, *a, **k):
        return _FakeQuery(self._rows)


def _two_label_rows(n=12):
    rows = []
    for i in range(n):
        rows.append(_FakeRow(i, label=0 if i % 2 == 0 else 1,
                             bac=0.0 if i % 2 == 0 else 0.08,
                             trial_id=i))
    return rows


def test_a7_fusion_refuses_without_mobilenet(tmp_path, monkeypatch):
    """A7: with no mobilenet.h5 on disk, fusion must refuse — not fall back to labels."""
    import routes.training as tr

    empty_save = tmp_path / "empty"
    empty_save.mkdir()
    monkeypatch.setattr(mobilenet, "SAVE_DIR", str(empty_save))
    assert not (empty_save / "mobilenet.h5").exists()

    out = tr.train_fusion(_FakeDB(_two_label_rows()))
    assert "error" in out, f"expected refusal, got {out}"
    assert "mobilenet.h5" in out["error"]


def test_a7_fusion_refuses_single_label(tmp_path, monkeypatch):
    """A7: refuse when the training rows carry fewer than 2 distinct labels."""
    import routes.training as tr
    rows = [_FakeRow(i, label=0, bac=0.0, trial_id=i) for i in range(12)]
    out = tr.train_fusion(_FakeDB(rows))
    assert "error" in out
    assert "distinct" in out["error"]


def test_a7_fusion_refuses_rows_without_measured_bac(tmp_path, monkeypatch):
    """A6 follow-on: no measured BAC -> no OOF estimate -> refuse."""
    import routes.training as tr

    save = tmp_path / "mob"
    save.mkdir()
    (save / "mobilenet.h5").write_bytes(b"stub")
    monkeypatch.setattr(mobilenet, "SAVE_DIR", str(save))

    rows = [_FakeRow(i, label=0 if i % 2 == 0 else 1, bac=None, trial_id=i) for i in range(12)]
    out = tr.train_fusion(_FakeDB(rows))
    assert "error" in out
    assert "BAC" in out["error"]


def test_a7_fusion_refuses_single_distinct_bac_level(tmp_path, monkeypatch):
    """A6: a constant BAC column cannot fit an OOF regressor -> refuse."""
    import routes.training as tr

    save = tmp_path / "mob"
    save.mkdir()
    (save / "mobilenet.h5").write_bytes(b"stub")
    monkeypatch.setattr(mobilenet, "SAVE_DIR", str(save))

    rows = [_FakeRow(i, label=0 if i % 2 == 0 else 1, bac=0.0, trial_id=i) for i in range(12)]
    out = tr.train_fusion(_FakeDB(rows))
    assert "error" in out
    assert "distinct measured BAC levels" in out["error"]


# ----------------------------------------------------------------------------------
# H1/H2 — mobilenet class indices and gating
# ----------------------------------------------------------------------------------

def test_h1_class_indices_pinned_and_registered_excluded(tmp_path, monkeypatch):
    """H1: class_indices must be {sober:0, fatigue:1, impaired:2} even when the
    trained root also contains a registered/ folder (Keras sorts subdirs, so
    without classes= it becomes {fatigue:0, impaired:1, registered:2, sober:3})."""
    from tensorflow.keras.preprocessing.image import ImageDataGenerator

    tree = tmp_path / "faces"
    _write_tree(tree, {"sober": 3, "fatigue": 3, "impaired": 3},
                extra_dirs=("registered",))
    # put an image in registered/ to prove it is excluded, not merely empty
    import cv2
    cv2.imwrite(str(tree / "registered" / "enrolled.jpg"),
                np.full((32, 32, 3), 9, dtype=np.uint8))

    monkeypatch.setattr(mobilenet, "BATCH_SIZE", 2)

    # demonstrate the bug shape without classes=
    naive = ImageDataGenerator().flow_from_directory(
        str(tree), target_size=mobilenet.IMG_SIZE, batch_size=2,
        class_mode="categorical", shuffle=False, seed=42)
    assert naive.num_classes == 4, "precondition: naive scan should see 4 classes"

    # the fixed helper
    fixed = mobilenet._build_directory_iterator(
        str(tree), ImageDataGenerator(rescale=1.0 / 255), shuffle=False)

    assert dict(fixed.class_indices) == {"sober": 0, "fatigue": 1, "impaired": 2}
    assert fixed.num_classes == 3
    assert "registered" not in fixed.class_indices


def test_h2_class_image_counts_ignores_registered(tmp_path):
    counts = mobilenet._class_image_counts(str(tmp_path))
    _write_tree(tmp_path, {"sober": 2, "fatigue": 1, "impaired": 0},
                extra_dirs=("registered",))
    assert counts == {"sober": 0, "fatigue": 0, "impaired": 0}  # before writing

    counts = mobilenet._class_image_counts(str(tmp_path))
    assert counts["sober"] == 2
    assert counts["fatigue"] == 1
    assert counts["impaired"] == 0
    assert "registered" not in counts


def test_h2_train_refuses_starved_tree(tmp_path, monkeypatch):
    """H2: training must refuse with a per-class count listing."""
    tree = tmp_path / "faces"
    _write_tree(tree, {"sober": 2, "fatigue": 0, "impaired": 1})
    monkeypatch.setattr(mobilenet, "DATA_DIR", str(tree))
    monkeypatch.setattr(mobilenet, "MIN_IMAGES_PER_CLASS", 30)

    with pytest.raises(ValueError) as exc:
        mobilenet.train()

    msg = str(exc.value)
    assert "MIN" not in msg or ">= 30" in msg
    assert "'sober': 2" in msg
    assert "'fatigue': 0" in msg
    assert "'impaired': 1" in msg


# ----------------------------------------------------------------------------------
# H1 — predict_frame out-of-range guard
# ----------------------------------------------------------------------------------

def test_predict_frame_handles_out_of_range_index(monkeypatch):
    """H1: a model head wider than len(CLASSES) must not raise IndexError."""
    class _FakeModel:
        def predict(self, img):
            return np.array([[0.05, 0.05, 0.05, 0.85]])  # argmax = 3, CLASSES has 3

    monkeypatch.setattr(mobilenet, "load", lambda: _FakeModel())

    frame = np.zeros((40, 40, 3), dtype=np.uint8)
    out = mobilenet.predict_frame(frame)   # must not raise

    assert out["label"] == "unknown"
    assert out["class_index"] == 3
    assert out["out_of_range"] is True
    assert out["num_outputs"] == 4


def test_predict_frame_in_range_still_maps(monkeypatch):
    """The guard must not disturb the normal path."""
    class _FakeModel:
        def predict(self, img):
            return np.array([[0.1, 0.7, 0.2]])  # argmax = 1 -> "fatigue"

    monkeypatch.setattr(mobilenet, "load", lambda: _FakeModel())

    out = mobilenet.predict_frame(np.zeros((40, 40, 3), dtype=np.uint8))
    assert out["label"] == "fatigue"
    assert out["class_index"] == 1
    assert "out_of_range" not in out