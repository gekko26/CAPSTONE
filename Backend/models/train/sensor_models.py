import os
import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, classification_report
from xgboost import XGBClassifier

# ── Paths ──────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(__file__)
SAVE_DIR = os.path.join(BASE_DIR, "../saved")
os.makedirs(SAVE_DIR, exist_ok=True)

# ── Label definitions ──────────────────────────────────────────
# 0 = no alcohol event
# 1 = breath alcohol event
# 2 = sanitizer event

LABELS = {
    0: {"name": "No alcohol",     "risk": "none",   "color": "green"},
    1: {"name": "Breath alcohol", "risk": "high",   "color": "red"},
    2: {"name": "Sanitizer",      "risk": "low",    "color": "yellow"},
}

# ── Models ─────────────────────────────────────────────────────
MODELS = {
    "random_forest": RandomForestClassifier(
        n_estimators=100,
        random_state=42
    ),
    "xgboost": XGBClassifier(
        n_estimators=100,
        random_state=42,
        eval_metric="mlogloss",
        use_label_encoder=False
    ),
}

# ── Feature extraction ─────────────────────────────────────────
def extract_features(window_1, window_2, window_3, temp, humidity):
    """
    Extracts 15 features from 3 MQ3 sensor windows (2-3 seconds of readings).

    Features:
        [0]  mq3_1_max
        [1]  mq3_1_avg
        [2]  mq3_1_std
        [3]  mq3_2_max
        [4]  mq3_2_avg
        [5]  mq3_2_std
        [6]  mq3_3_max
        [7]  mq3_3_avg
        [8]  mq3_3_std
        [9]  rise_time          — avg samples from start to peak across sensors
        [10] decay_time         — avg samples from peak to end across sensors
        [11] spatial_variance_max — std of peak values across 3 sensors
        [12] spatial_variance_avg — std of avg values across 3 sensors
        [13] temperature
        [14] humidity
    """

    def sensor_stats(values):
        if len(values) == 0:
            return 0.0, 0.0, 0.0
        mx  = float(max(values))
        avg = round(sum(values) / len(values), 4)
        std = round(float(np.std(values)), 4)
        return mx, avg, std

    def rise_time_of(window):
        """Samples from start to peak — slow for breath, fast for sanitizer."""
        if len(window) < 2:
            return 0.0
        return float(window.index(max(window)))

    def decay_time_of(window):
        """Samples from peak to end — slow for breath, fast for sanitizer."""
        if len(window) < 2:
            return 0.0
        peak_idx = window.index(max(window))
        return float(len(window) - peak_idx - 1)

    w1 = list(window_1)
    w2 = list(window_2)
    w3 = list(window_3)

    if not w1 and not w2 and not w3:
        return None

    # Per-sensor stats
    s1_max, s1_avg, s1_std = sensor_stats(w1)
    s2_max, s2_avg, s2_std = sensor_stats(w2)
    s3_max, s3_avg, s3_std = sensor_stats(w3)

    # Rise & decay — per sensor then averaged
    rise_time  = round(float(np.mean([
        rise_time_of(w1), rise_time_of(w2), rise_time_of(w3)
    ])), 4)

    decay_time = round(float(np.mean([
        decay_time_of(w1), decay_time_of(w2), decay_time_of(w3)
    ])), 4)

    # Spatial variance — how spread the 3 sensors are from each other
    # Sanitizer: only 1 sensor spikes → high variance
    # Breath:    all 3 sensors trigger → low variance
    # Sober:     all 3 sensors flat    → near zero variance
    max_values           = [s1_max, s2_max, s3_max]
    avg_values           = [s1_avg, s2_avg, s3_avg]
    spatial_variance_max = round(float(np.std(max_values)), 4)
    spatial_variance_avg = round(float(np.std(avg_values)), 4)

    return [
        s1_max, s1_avg, s1_std,     # [0-2]  sensor 1
        s2_max, s2_avg, s2_std,     # [3-5]  sensor 2
        s3_max, s3_avg, s3_std,     # [6-8]  sensor 3
        rise_time,                   # [9]
        decay_time,                  # [10]
        spatial_variance_max,        # [11]
        spatial_variance_avg,        # [12]
        float(temp),                 # [13]
        float(humidity),             # [14]
    ]


# ── Label assignment ───────────────────────────────────────────
def assign_label(bac_value, is_sanitizer_event=False, features=None):
    """
    Assigns class label. Used ONLY during training data collection.

    Priority order:
        1. Operator explicitly flags sanitizer          → 2
        2. BAC > 0.00 (breathalyzer confirmed alcohol)  → 1
        3. Sensor pattern looks like sanitizer          → 2  (auto-detect)
        4. Everything else                              → 0

    Parameters:
        bac_value          — reading from BACtrack S80
        is_sanitizer_event — operator manually flagged it
        features           — 15-feature list from extract_features()
                             used for auto-detect fallback
    """

    # 1. Operator explicitly flagged sanitizer
    if is_sanitizer_event:
        return 2

    # 2. Breathalyzer confirmed alcohol
    if bac_value > 0.00:
        return 1

    # 3. Auto-detect sanitizer from sensor pattern
    #    Sanitizer = big spike on 1 sensor only (high spatial variance)
    if features is not None:
        spatial_variance_max = features[11]
        overall_max          = max(features[0], features[3], features[6])
        is_high_spike        = overall_max > 300        # big spike happened
        is_uneven            = spatial_variance_max > 80 # only 1 sensor triggered

        if is_high_spike and is_uneven:
            return 2

    # 4. No alcohol
    return 0


# ── Training ───────────────────────────────────────────────────
def train(X, y):
    """
    Train all models on collected dataset.

    Parameters:
        X — list of 15-feature vectors from extract_features()
        y — list of labels (0, 1, or 2)
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

    joblib.dump(scaler, os.path.join(SAVE_DIR, "scaler.pkl"))
    print("✅ Scaler saved")

    results = {}

    for name, model in MODELS.items():
        print(f"\nTraining {name}...")
        model.fit(X_train, y_train)

        preds    = model.predict(X_test)
        accuracy = accuracy_score(y_test, preds)
        report   = classification_report(
            y_test, preds,
            target_names=["No alcohol", "Breath alcohol", "Sanitizer"]
        )

        results[name] = {"accuracy": round(accuracy * 100, 2)}

        joblib.dump(model, os.path.join(SAVE_DIR, f"{name}.pkl"))
        print(f"✅ {name} saved — accuracy: {results[name]['accuracy']}%")
        print(report)

    return results


# ── Load model ─────────────────────────────────────────────────
def load(model_name="random_forest"):
    path = os.path.join(SAVE_DIR, f"{model_name}.pkl")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Model '{model_name}' not found. Train it first by calling train(X, y)"
        )
    return joblib.load(path)


# ── Prediction ─────────────────────────────────────────────────
def predict(window_1, window_2, window_3,
            temp, humidity,
            model_name="random_forest"):
    """
    Make a prediction for one event using 3 sensor windows.
    Only called in deployment mode (after model is trained).
    """
    features = extract_features(window_1, window_2, window_3, temp, humidity)

    if features is None:
        return {"error": "No readings in window"}

    model  = load(model_name)
    scaler = joblib.load(os.path.join(SAVE_DIR, "scaler.pkl"))

    X           = scaler.transform([features])
    class_index = int(model.predict(X)[0])
    probs       = model.predict_proba(X)[0]
    confidence  = round(float(probs[class_index]), 4)
    label_info  = LABELS[class_index]

    return {
        "class":      class_index,
        "label":      label_info["name"],
        "risk":       label_info["risk"],
        "confidence": confidence,
        "all_probs": {
            LABELS[i]["name"]: round(float(p), 4)
            for i, p in enumerate(probs)
        }
    }


if __name__ == "__main__":
    print("Sensor models ready.")
    print("\nLabels:")
    for idx, info in LABELS.items():
        print(f"  {idx} = {info['name']} (risk: {info['risk']})")
    print("\n15 Features:")
    print("  [0-2]  Sensor 1: max, avg, std")
    print("  [3-5]  Sensor 2: max, avg, std")
    print("  [6-8]  Sensor 3: max, avg, std")
    print("  [9]    rise_time")
    print("  [10]   decay_time")
    print("  [11]   spatial_variance_max")
    print("  [12]   spatial_variance_avg")
    print("  [13]   temperature")
    print("  [14]   humidity")