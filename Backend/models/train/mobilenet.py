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
CLASSES    = ["sober", "drowsy", "yawning", "impaired"]  # must match folder names — Plan: 200 each (800 total), yawning separate from drowsy (EAR vs MAR)

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

def train():
    """
    Train MobileNetV2 on your face dataset.

    Expected folder structure (Plan: 200 each, yawning separate):
        data/faces/
            sober/      ← images of sober people (200)
            drowsy/     ← images of drowsy people eyes closed EAR (200)
            yawning/    ← images of yawning people mouth open MAR (200) — separate class
            impaired/   ← images of impaired people (200)

    Call this when you have collected face images.
    """

    # Data augmentation — multiplies your dataset automatically
    datagen = ImageDataGenerator(
        rescale=1.0/255,
        rotation_range=10,
        zoom_range=0.1,
        horizontal_flip=True,
        brightness_range=[0.8, 1.2],
        width_shift_range=0.1,
        height_shift_range=0.1,
        validation_split=0.2      # 80% train, 20% validation
    )

    train_data = datagen.flow_from_directory(
        DATA_DIR,
        target_size=IMG_SIZE,
        batch_size=BATCH_SIZE,
        class_mode="categorical",
        subset="training"
    )

    val_data = datagen.flow_from_directory(
        DATA_DIR,
        target_size=IMG_SIZE,
        batch_size=BATCH_SIZE,
        class_mode="categorical",
        subset="validation"
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
    history = model.fit(
        train_data,
        validation_data=val_data,
        epochs=EPOCHS,
        callbacks=[early_stop, checkpoint]
    )

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
            "label": "sober" / "drowsy" / "yawning" / "impaired",
            "confidence": 0.94,
            "class_index": 0 / 1 / 2 / 3
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
    print("MobileNetV2 ready to be trained. Plan: 4 classes 200 each (sober/drowsy/yawning/impaired =800)")
    print("Add face images to data/faces/sober, data/faces/drowsy, data/faces/yawning, data/faces/impaired")
    print("Then call train() to start training.")