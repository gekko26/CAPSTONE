#mobilenet.py

import os
import numpy as np
from tensorflow.keras.applications import MobileNetV2
from tensorflow.keras import layers, models
from tensorflow.keras.preprocessing.image import ImageDataGenerator
from tensorflow.keras.callbacks import EarlyStopping, ModelCheckpoint

# ── Paths ──────────────────────────────────────────────────────
BASE_DIR  = os.path.dirname(__file__)
DATA_DIR  = os.path.join(BASE_DIR, "../../data/faces")
SAVE_DIR  = os.path.join(BASE_DIR, "../saved")
os.makedirs(SAVE_DIR, exist_ok=True)

# ── Config ─────────────────────────────────────────────────────
IMG_SIZE   = (224, 224)    # MobileNetV2 input size
BATCH_SIZE = 16
EPOCHS     = 20
CLASSES    = ["sober", "fatigue", "impaired"]  # 3-class: fatigue = drowsy(ear) + yawning(mar) aggregated, yawning mar is measurement not class

# FIX H2: refuse to train on a starved tree. Keras happily "trains" on 1 image
# belonging to 4 classes, which is what produced the silent 4-class split.
MIN_IMAGES_PER_CLASS = 30

# FIX H1: data/faces/registered/ holds enrolled identities for DeepFace, not
# training samples. It must never be treated as a class. Keras derives
# class_indices from sorted(os.listdir(root)) — see
# keras/src/legacy/preprocessing/image.py:456 — so an unfiltered root yields
# {'fatigue':0, 'impaired':1, 'registered':2, 'sober':3} and predict_frame maps
# every face to the wrong label (sober -> CLASSES[3] -> IndexError).
# Every flow_from_directory call therefore passes classes=CLASSES explicitly.
def _build_directory_iterator(root, datagen, shuffle=False, subset=None):
    """Single entry point for flow_from_directory so CLASSES is always pinned."""
    kwargs = dict(
        target_size=IMG_SIZE,
        batch_size=BATCH_SIZE,
        class_mode="categorical",
        classes=list(CLASSES),   # FIX H1: fixes index order, excludes registered/
        shuffle=shuffle,
        seed=42,
    )
    if subset is not None:
        kwargs["subset"] = subset
    return datagen.flow_from_directory(root, **kwargs)


def _class_image_counts(root=None):
    """FIX H2: count usable images per class under the trained root only."""
    import glob
    root = root or DATA_DIR
    counts = {}
    for cls in CLASSES:
        n = 0
        for fp in glob.glob(os.path.join(root, cls, "*")):
            if os.path.isdir(fp):
                continue
            if fp.lower().endswith((".jpg", ".jpeg", ".png")):
                n += 1
        counts[cls] = n
    return counts

def build_model():
    """
    Builds MobileNetV2 with custom classifier on top.
    Uses transfer learning — base weights from ImageNet.
    """

    # Load pretrained base — frozen so we don't overwrite ImageNet weights
    base = MobileNetV2(
        input_shape=(*IMG_SIZE, 3),
        include_top=False,        # remove ImageNet classifier
        weights="imagenet"        # start with pretrained weights
    )
    base.trainable = False        # freeze base layers

    # Add our own classifier on top
    model = models.Sequential([
        base,
        layers.GlobalAveragePooling2D(),
        layers.Dense(128, activation="relu"),
        layers.Dropout(0.3),
        layers.Dense(len(CLASSES), activation="softmax")  # 3 output classes
    ])

    model.compile(
        optimizer="adam",
        loss="categorical_crossentropy",
        metrics=["accuracy"]
    )

    return model

def _collect_subject_split():
    """
    FIX 6: Partition by subject_id so no subject appears in both train and val.
    Returns (train_tuples, val_tuples) where each tuple is (filepath, class_name, subject_key)
    Falls back to image-level split if no subject_id available.
    """
    import glob
    from collections import defaultdict
    # Try DB-backed mapping via TrainingData subject_id -> image_path
    subject_map = {}  # filepath -> subject_key
    try:
        from database.db import SessionLocal
        from database.models import TrainingData
        db = SessionLocal()
        try:
            rows = db.query(TrainingData).filter(TrainingData.image_path != None).all()
            for r in rows:
                if r.image_path:
                    # normalize path for glob comparison
                    key = f"subject_{r.subject_id}" if r.subject_id is not None else f"trial_{r.trial_id}" if r.trial_id is not None else f"row_{r.id}"
                    subject_map[os.path.abspath(r.image_path)] = key
                    subject_map[r.image_path] = key
        finally:
            db.close()
    except Exception as e:
        print(f"⚠️ [FIX 6] DB subject_map failed: {e}")

    # Enumerate files on disk
    file_tuples = []
    for cls in CLASSES:
        pattern = os.path.join(DATA_DIR, cls, "*")
        for fp in glob.glob(pattern):
            if os.path.isdir(fp):
                continue
            if not fp.lower().endswith((".jpg", ".jpeg", ".png")):
                continue
            subj = subject_map.get(fp) or subject_map.get(os.path.abspath(fp)) or f"unknown_{os.path.basename(fp)}"
            file_tuples.append((fp, cls, subj))

    if not file_tuples:
        return [], []

    # Group by subject
    subj_to_indices = defaultdict(list)
    for i, (_, _, subj) in enumerate(file_tuples):
        subj_to_indices[subj].append(i)

    unique_subjects = list(subj_to_indices.keys())
    # If every file is its own subject (no real subject_id), fall back to image-level warning
    distinct_subjects = len([s for s in unique_subjects if not s.startswith("unknown_")])
    if distinct_subjects < 3:
        print(f"⚠️ [FIX 6] Only {distinct_subjects} distinct subject_id found — falling back to image-level split with leakage warning")
        return None, None  # signal fallback

    # GroupShuffleSplit by subject
    try:
        from sklearn.model_selection import GroupShuffleSplit
        groups = [subj for _, _, subj in file_tuples]
        gss = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42)
        train_idx, val_idx = next(gss.split(file_tuples, groups=groups))
        train_tuples = [file_tuples[i] for i in train_idx]
        val_tuples = [file_tuples[i] for i in val_idx]
        print(f"✅ [FIX 6] Subject split: {len(unique_subjects)} subjects → train {len(train_tuples)} imgs / val {len(val_tuples)} imgs, no subject overlap")
        # sanity check no overlap
        train_subj = set(s for _, _, s in train_tuples)
        val_subj = set(s for _, _, s in val_tuples)
        assert len(train_subj & val_subj) == 0, "subject leakage"
        return train_tuples, val_tuples
    except Exception as e:
        print(f"⚠️ [FIX 6] GroupShuffleSplit failed: {e} — fallback to image split")
        return None, None


def train():
    """
    Train MobileNetV2 on your face dataset — FIX 6 subject-level split.

    Expected folder structure (3-class: yawning is mar measurement not class):
        data/faces/
            sober/      ← images of sober people (200)
            fatigue/    ← drowsy(ear) + yawning(mar) aggregated (200)
            impaired/   ← images of impaired people (200)

    Call this when you have collected face images. Split is by subject_id, not by image.
    """

    # FIX H2: gate on per-class image counts before touching Keras at all, so a
    # starved or mis-shaped tree fails with a readable error instead of silently
    # training a 4-class model or crashing on a Dense(3) mismatch.
    counts = _class_image_counts()
    starved = {c: n for c, n in counts.items() if n < MIN_IMAGES_PER_CLASS}
    if starved:
        raise ValueError(
            f"Cannot train MobileNetV2: every class needs >= {MIN_IMAGES_PER_CLASS} images. "
            f"Counts: {counts}. Missing/short: {starved}. "
            f"(data/faces/registered/ is intentionally excluded — it is for DeepFace enrollment.)"
        )
    print(f"✅ Pre-flight OK — images per class: {counts}")

    # FIX 6: subject-level split
    _split = _collect_subject_split()
    use_subject_split = _split[0] is not None

    if use_subject_split:
        import tempfile, shutil, pathlib
        # create temp split dirs
        tmp_root = tempfile.mkdtemp(prefix="faces_subject_split_")
        tmp_train = os.path.join(tmp_root, "train")
        tmp_val = os.path.join(tmp_root, "val")
        try:
            for split_tuples, split_root in [(_split[0], tmp_train), (_split[1], tmp_val)]:
                for fp, cls, _ in split_tuples:
                    dest_dir = os.path.join(split_root, cls)
                    os.makedirs(dest_dir, exist_ok=True)
                    # symlink (or copy) to avoid duplicate disk
                    dest = os.path.join(dest_dir, os.path.basename(fp) + f"_{abs(hash(fp))%10000}.jpg")
                    try:
                        if not os.path.exists(dest):
                            os.symlink(os.path.abspath(fp), dest)
                    except Exception:
                        shutil.copy2(fp, dest)
            train_datagen = ImageDataGenerator(
                rescale=1.0/255, rotation_range=10, zoom_range=0.1, horizontal_flip=True,
                brightness_range=[0.8, 1.2], width_shift_range=0.1, height_shift_range=0.1,
            )
            val_datagen = ImageDataGenerator(rescale=1.0/255)
            # FIX H1: classes pinned on both sides so train/val indices agree
            train_data = _build_directory_iterator(tmp_train, train_datagen, shuffle=True)
            val_data   = _build_directory_iterator(tmp_val, val_datagen, shuffle=False)
            if dict(train_data.class_indices) != {c: i for i, c in enumerate(CLASSES)}:
                raise ValueError(f"unexpected class_indices {train_data.class_indices}")
        except Exception as e:
            print(f"⚠️ [FIX 6] subject split flow failed: {e} — falling back to image split")
            use_subject_split = False
            shutil.rmtree(tmp_root, ignore_errors=True)
            tmp_root = None
        # if fallback triggered below, tmp_root will be cleaned after training
    if not use_subject_split:
        # Fallback: legacy image-level split (with warning)
        print("⚠️ [FIX 6] Using legacy image-level validation_split=0.2 (subject leakage possible)")
        train_datagen = ImageDataGenerator(
            rescale=1.0/255, rotation_range=10, zoom_range=0.1, horizontal_flip=True,
            brightness_range=[0.8, 1.2], width_shift_range=0.1, height_shift_range=0.1,
            validation_split=0.2
        )
        val_datagen = ImageDataGenerator(rescale=1.0/255, validation_split=0.2)
        # FIX H1/H2: classes=CLASSES both fixes index order and excludes
        # data/faces/registered/ from the trained root.
        train_data = _build_directory_iterator(DATA_DIR, train_datagen, shuffle=True, subset="training")
        val_data   = _build_directory_iterator(DATA_DIR, val_datagen, shuffle=False, subset="validation")
        if dict(train_data.class_indices) != {c: i for i, c in enumerate(CLASSES)}:
            raise ValueError(f"unexpected class_indices {train_data.class_indices}")
        tmp_root = None

    assert train_data.num_classes == len(CLASSES), (
        f"generator reports {train_data.num_classes} classes, model head has {len(CLASSES)}"
    )

    model = build_model()

    # Callbacks
    early_stop = EarlyStopping(
        monitor="val_loss",
        patience=5,               # stop if no improvement for 5 epochs
        restore_best_weights=True
    )

    checkpoint = ModelCheckpoint(
        os.path.join(SAVE_DIR, "mobilenet.h5"),
        monitor="val_accuracy",
        save_best_only=True       # only save if accuracy improved
    )

    print("Starting MobileNetV2 training...")
    try:
        history = model.fit(
            train_data,
            validation_data=val_data,
            epochs=EPOCHS,
            callbacks=[early_stop, checkpoint]
        )
    finally:
        if tmp_root and os.path.exists(tmp_root):
            import shutil as _sh
            _sh.rmtree(tmp_root, ignore_errors=True)

    print(f"✅ MobileNetV2 trained and saved to models/saved/mobilenet.h5")

    # Persist REAL evaluation metrics for the frontend (transparent — includes low scores too)
    val_accs = history.history.get("val_accuracy", [])
    val_losses = history.history.get("val_loss", [])
    accs = history.history.get("accuracy", [])
    losses = history.history.get("loss", [])
    if val_accs:
        from models.train.metrics_store import save_metrics
        # Report the FINAL epoch, not max(): max(val_accs) is a selection over ~20
        # noisy evaluations and is biased upward. Keep the max for reference only.
        final_acc = float(val_accs[-1])
        best_acc  = max(val_accs)
        save_metrics("mobilenet", {
            "accuracy":  round(final_acc * 100, 2),
            "val_loss":  round(float(val_losses[-1]), 4) if val_losses else None,
            "best_val_accuracy": round(best_acc * 100, 2),
            "best_epoch": int(int(__import__('numpy').argmax(val_accs)) + 1),
            "epochs":    len(val_accs),
            "classes":   list(train_data.class_indices.keys()),
            "class_indices": dict(train_data.class_indices),
            "history_val_accuracy": [round(float(x)*100,2) for x in val_accs],
            "history_val_loss": [round(float(x),4) for x in val_losses] if val_losses else None,
            "history_accuracy": [round(float(x)*100,2) for x in accs] if accs else None,
            "history_loss": [round(float(x),4) for x in losses] if losses else None,
            "final_val_accuracy": round(final_acc*100,2),
        })

    return history

def load():
    """
    Load the saved MobileNetV2 model.
    """
    path = os.path.join(SAVE_DIR, "mobilenet.h5")
    if not os.path.exists(path):
        raise FileNotFoundError("MobileNetV2 not found. Train it first.")
    return models.load_model(path)

def predict_frame(frame):
    """
    Predict impairment level from a single camera frame.

    Returns:
        {
            "label": "sober" / "fatigue" / "impaired",
            "confidence": 0.94,
            "class_index": 0 / 1 / 2
        }

    Usage:
        import cv2
        frame = cv2.imread("face.jpg")
        result = predict_frame(frame)
    """
    import cv2

    model = load()

    # Preprocess frame
    img = cv2.resize(frame, IMG_SIZE)
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    img = img / 255.0
    img = np.expand_dims(img, axis=0)   # add batch dimension

    preds      = model.predict(img)[0]
    class_idx  = int(np.argmax(preds))
    confidence = round(float(preds[class_idx]), 4)

    # FIX H1: never index CLASSES out of range. A model whose head does not match
    # len(CLASSES) (e.g. trained on a root that Keras read as 4 classes) returns
    # an index >= 3, which used to raise IndexError and take the whole request
    # down with a 500.
    if not (0 <= class_idx < len(CLASSES)):
        print(f"⚠️ [H1] out-of-range class index {class_idx} (model head has "
              f"{preds.shape[0]} outputs, CLASSES has {len(CLASSES)}) — reporting 'unknown'")
        return {
            "label":       "unknown",
            "confidence":  round(float(np.max(preds)), 4),
            "class_index": class_idx,
            "num_outputs": int(preds.shape[0]),
            "out_of_range": True,
        }

    return {
        "label":       CLASSES[class_idx],
        "confidence":  confidence,
        "class_index": class_idx
    }

if __name__ == "__main__":
    print("MobileNetV2 ready to be trained. Plan: 3 classes sober/fatigue/impaired (fatigue=drowsy+yawning, 600 total)")
    print("Add face images to data/faces/sober, data/faces/fatigue, data/faces/impaired")
    print("Then call train() to start training.")