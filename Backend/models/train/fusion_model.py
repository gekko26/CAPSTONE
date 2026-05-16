import joblib
import os
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, classification_report

# ── Features ───────────────────────────────────────────────────
# [0] sensor_class       — 0=no alcohol, 1=breath, 2=sanitizer
# [1] sensor_confidence  — 0.0 to 1.0
# [2] visual_class       — 0=sober, 1=drowsy, 2=impaired (from MobileNet)
# [3] visual_confidence  — 0.0 to 1.0
# [4] ear                — eye aspect ratio from MediaPipe
# [5] blink_rate         — blinks per second (0.0 if unavailable)
# [6] temperature        — from ESP32
# [7] humidity           — from ESP32

# ── Labels ─────────────────────────────────────────────────────
# 0 = pass        (sober, eyes normal)
# 1 = near_limit  (low alcohol or drowsy)
# 2 = over_limit  (drunk or impaired)

SAVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../saved")
os.makedirs(SAVE_DIR, exist_ok=True)

FUSION_LABELS = {
    0: {"label": "pass",       "risk": "none",   "action": "allow"},
    1: {"label": "near_limit", "risk": "medium", "action": "warn"},
    2: {"label": "over_limit", "risk": "high",   "action": "deny"},
}


def train(X, y):
    """
    Train fusion model on combined sensor + CV data.

    Parameters:
        X — list of 8-feature vectors:
            [sensor_class, sensor_confidence,
             visual_class, visual_confidence,
             ear, blink_rate,
             temperature, humidity]
        y — list of labels (0=pass, 1=near_limit, 2=over_limit)

    Usage:
        train(X, y)
    """
    X = np.array(X)
    y = np.array(y)

    if len(y) < 10:
        X_train, X_test = X, X
        y_train, y_test = y, y
    else:
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )

    scaler  = StandardScaler()
    X_train = scaler.fit_transform(X_train)
    X_test  = scaler.transform(X_test)

    joblib.dump(scaler, os.path.join(SAVE_DIR, "fusion_scaler.pkl"))
    print("✅ Fusion scaler saved")

    model = RandomForestClassifier(n_estimators=100, random_state=42)
    model.fit(X_train, y_train)

    preds  = model.predict(X_test)
    acc    = accuracy_score(y_test, preds)
    report = classification_report(
        y_test, preds,
        target_names=["Pass", "Near limit", "Over limit"]
    )

    joblib.dump(model, os.path.join(SAVE_DIR, "fusion_model.pkl"))
    print(f"✅ Fusion model saved — accuracy: {round(acc * 100, 2)}%")
    print(report)

    return {"accuracy": round(acc * 100, 2)}


def load():
    path = os.path.join(SAVE_DIR, "fusion_model.pkl")
    if not os.path.exists(path):
        raise FileNotFoundError("Fusion model not found. Train it first.")
    return joblib.load(path)


def predict_single(
    sensor_class,      sensor_confidence,
    visual_class,      visual_confidence,
    ear,               blink_rate,
    temperature,       humidity,
):
    """
    Make a fusion prediction combining sensor + MobileNet + EAR results.

    Parameters:
        sensor_class       — from RF/XGBoost ensemble (0, 1, or 2)
        sensor_confidence  — from RF/XGBoost ensemble (0.0 to 1.0)
        visual_class       — from MobileNet (0=sober, 1=drowsy, 2=impaired)
        visual_confidence  — from MobileNet (0.0 to 1.0)
        ear                — eye aspect ratio from MediaPipe
        blink_rate         — blinks per second (pass 0.0 if unavailable)
        temperature        — from ESP32
        humidity           — from ESP32

    Returns:
        {
            "class":      2,
            "label":      "over_limit",
            "risk":       "high",
            "action":     "deny",
            "confidence": 0.91
        }
    """
    model  = load()
    scaler = joblib.load(os.path.join(SAVE_DIR, "fusion_scaler.pkl"))

    X = scaler.transform([[
        sensor_class,     sensor_confidence,
        visual_class,     visual_confidence,
        ear,              blink_rate,
        temperature,      humidity,
    ]])

    class_index = int(model.predict(X)[0])
    probs       = model.predict_proba(X)[0]
    confidence  = round(float(probs[class_index]), 4)
    info        = FUSION_LABELS[class_index]

    return {
        "class":      class_index,
        "label":      info["label"],
        "risk":       info["risk"],
        "action":     info["action"],
        "confidence": confidence,
        "all_probs": {
            FUSION_LABELS[i]["label"]: round(float(p), 4)
            for i, p in enumerate(probs)
        }
    }


if __name__ == "__main__":
    print("Fusion model ready.")
    print("\nFeatures (8 total):")
    print("  [0] sensor_class       — 0=no alcohol, 1=breath, 2=sanitizer")
    print("  [1] sensor_confidence  — 0.0 to 1.0")
    print("  [2] visual_class       — 0=sober, 1=drowsy, 2=impaired")
    print("  [3] visual_confidence  — 0.0 to 1.0")
    print("  [4] ear                — eye aspect ratio")
    print("  [5] blink_rate         — blinks per second")
    print("  [6] temperature")
    print("  [7] humidity")
    print("\nLabels:")
    for idx, info in FUSION_LABELS.items():
        print(f"  {idx} = {info['label']} → action: {info['action']}")