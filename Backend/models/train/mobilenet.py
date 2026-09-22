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
            train_data = train_datagen.flow_from_directory(tmp_train, target_size=IMG_SIZE, batch_size=BATCH_SIZE, class_mode="categorical", shuffle=True, seed=42)
            val_data = val_datagen.flow_from_directory(tmp_val, target_size=IMG_SIZE, batch_size=BATCH_SIZE, class_mode="categorical", shuffle=False, seed=42)
            # need manual class_indices alignment
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
        train_data = train_datagen.flow_from_directory(DATA_DIR, target_size=IMG_SIZE, batch_size=BATCH_SIZE, class_mode="categorical", subset="training", seed=42)
        val_data = val_datagen.flow_from_directory(DATA_DIR, target_size=IMG_SIZE, batch_size=BATCH_SIZE, class_mode="categorical", subset="validation", seed=42)
        tmp_root = None

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
        best_acc = max(val_accs)
        # reliability: per-epoch history
        save_metrics("mobilenet", {
            "accuracy":  round(best_acc * 100, 2),
            "val_loss":  round(min(val_losses) if val_losses else 0, 4),
            "epochs":    len(val_accs),
            "classes":   list(train_data.class_indices.keys()),
            "history_val_accuracy": [round(float(x)*100,2) for x in val_accs],
            "history_val_loss": [round(float(x),4) for x in val_losses] if val_losses else None,
            "history_accuracy": [round(float(x)*100,2) for x in accs] if accs else None,
            "history_loss": [round(float(x),4) for x in losses] if losses else None,
            "final_val_accuracy": round(float(val_accs[-1])*100,2),
            "best_epoch": int(int(__import__('numpy').argmax(val_accs)) + 1),
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

    return {
        "label":       CLASSES[class_idx],
        "confidence":  confidence,
        "class_index": class_idx
    }

if __name__ == "__main__":
    print("MobileNetV2 ready to be trained. Plan: 3 classes sober/fatigue/impaired (fatigue=drowsy+yawning, 600 total)")
    print("Add face images to data/faces/sober, data/faces/fatigue, data/faces/impaired")
    print("Then call train() to start training.")