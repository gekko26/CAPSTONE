import joblib
import os
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split, GroupKFold
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, classification_report

# ── Features ───────────────────────────────────────────────────
# [0] sensor_class       — 0=no alcohol, 1=breath, 2=sanitizer (Others)
# [1] sensor_confidence  — 0.0 to 1.0
# [2] visual_class       — 0=sober, 1=fatigue, 2=impaired (from MobileNet 3-class, yawning mar is measurement)
# [3] visual_confidence  — 0.0 to 1.0
# [4] ear                — eye aspect ratio from MediaPipe
# [5] mar                — mouth aspect ratio (yawning measurement continuous)
# [6] estimated_bac      — from BAC regressor 0.00-0.40 (primary)
# [7] temperature        — from ESP32

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


def train(X, y, groups=None):
    """
    Train fusion model on combined sensor + CV data — no fitted leakage.

    Parameters:
        X — list of 8-feature vectors:
            [sensor_class, sensor_confidence,
             visual_class, visual_confidence,
             ear, mar, estimated_bac, temperature]
             (humidity removed; use estimated_bac primary; mar is yawning measurement)
        y — list of labels (0=pass, 1=near_limit, 2=over_limit)
        groups — trial_id per row for GroupKFold (prevents same trial in train+test)

    Usage:
        train(X, y, groups)
    """
    X = np.array(X, dtype=float)
    y = np.array(y)
    groups = np.array(groups) if groups is not None else None

    # GroupKFold when groups available (like sensor), else stratified — prevents trial leakage
    if groups is not None and len(np.unique(groups)) >= 3:
        n_splits = min(5, len(np.unique(groups)))
        gkf = GroupKFold(n_splits=n_splits)
        train_idx, test_idx = list(gkf.split(X, y, groups))[-1]
        X_train, X_test = X[train_idx], X[test_idx]
        y_train, y_test = y[train_idx], y[test_idx]
    elif len(y) < 10:
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
        target_names=["Pass", "Near limit", "Over limit"],
        output_dict=True,
    )

    cm = None
    try:
        from sklearn.metrics import confusion_matrix as _cm
        cm = _cm(y_test, preds, labels=[0,1,2]).tolist()
    except Exception:
        cm = None
    try:
        fi = model.feature_importances_.tolist() if hasattr(model, 'feature_importances_') else None
    except Exception:
        fi = None
    joblib.dump(model, os.path.join(SAVE_DIR, "fusion_model.pkl"))
    print(f"✅ Fusion model saved — accuracy: {round(acc * 100, 2)}%")

    # Persist REAL evaluation metrics for the frontend (transparent)
    from models.train.metrics_store import save_metrics
    save_metrics("fusion", {
        "accuracy":  round(acc * 100, 2),
        "precision": round(report["macro avg"]["precision"] * 100, 2),
        "recall":    round(report["macro avg"]["recall"] * 100, 2),
        "f1":        round(report["macro avg"]["f1-score"] * 100, 2),
        "samples":   len(y),
        "trials":    int(len(np.unique(groups))) if groups is not None else None,
        "confusion_matrix": cm,
        "per_class": {k: {kk: round(vv*100,1) if kk!='support' else vv for kk,vv in vs.items()} for k,vs in report.items() if k in ("Pass","Near limit","Over limit")},
        "feature_importances": [round(float(x),4) for x in fi] if fi else None,
        "feature_names": ["sensor_class","sensor_conf","visual_class","visual_conf","ear","mar","estimated_bac","temp"][:len(fi)] if fi else None,
    })

    return {"accuracy": round(acc * 100, 2), "confusion_matrix": cm}


def load():
    path = os.path.join(SAVE_DIR, "fusion_model.pkl")
    if not os.path.exists(path):
        raise FileNotFoundError("Fusion model not found. Train it first.")
    return joblib.load(path)


def _compute_reason(ear, mar, estimated_bac, visual_class, sensor_class):
    """FIX 7: Keep underlying trigger visible even though both map to DENY."""
    bac_driven = bool(estimated_bac is not None and estimated_bac >= 0.05) or sensor_class == 1
    fatigue_driven = bool((ear is not None and ear < 0.20) or (mar is not None and mar > 0.55) or visual_class == 1)
    if bac_driven and fatigue_driven:
        reason = "both"
    elif bac_driven:
        reason = "alcohol"
    elif fatigue_driven:
        reason = "fatigue"
    else:
        reason = "none" if sensor_class == 0 else "alcohol" if sensor_class == 1 else "other"
    return reason, bac_driven, fatigue_driven


def predict_single(
    sensor_class,      sensor_confidence,
    visual_class,      visual_confidence,
    ear,               mar,
    estimated_bac,     temperature,
    humidity=60.0,
    blink_rate=0.0,
):
    """
    Make a fusion prediction combining sensor + MobileNet + EAR+MAR+BAC.
    FIX 7: returns reason field so fatigue vs alcohol is not conflated.

    Returns:
        {
            "class":      2,
            "label":      "over_limit",
            "risk":       "high",
            "action":     "deny",
            "confidence": 0.91,
            "reason":     "alcohol" | "fatigue" | "both" | "none",
            "bac_driven": bool,
            "fatigue_driven": bool
        }
    """
    model  = load()
    scaler = joblib.load(os.path.join(SAVE_DIR, "fusion_scaler.pkl"))

    # Fusion 8D: sensor_class, sensor_conf, visual_class, visual_conf, ear, mar, estimated_bac, temperature
    # humidity kept for backward compat but not used in new 8D (old 8D had blink_rate/hum)
    n_feat = getattr(scaler, 'n_features_in_', 8)
    if n_feat == 8:
        # New 8D: mar + estimated_bac replace blink_rate/humidity
        X = scaler.transform([[
            sensor_class,     sensor_confidence,
            visual_class,     visual_confidence,
            ear,              mar,
            estimated_bac,    temperature,
        ]])
    else:
        # Legacy fallback: blink_rate/humidity
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
    reason, bac_driven, fatigue_driven = _compute_reason(ear, mar, estimated_bac, visual_class, sensor_class)

    return {
        "class":      class_index,
        "label":      info["label"],
        "risk":       info["risk"],
        "action":     info["action"],
        "confidence": confidence,
        "reason":     reason,
        "bac_driven": bac_driven,
        "fatigue_driven": fatigue_driven,
        "all_probs": {
            FUSION_LABELS[i]["label"]: round(float(p), 4)
            for i, p in enumerate(probs)
        }
    }


if __name__ == "__main__":
    print("Fusion model ready. Visual 3-class sober/fatigue/impaired, yawning is mar measurement.")
    print("\nFeatures (8 total):")
    print("  [0] sensor_class       — 0=no alcohol, 1=breath, 2=sanitizer")
    print("  [1] sensor_confidence  — 0.0 to 1.0")
    print("  [2] visual_class       — 0=sober, 1=fatigue, 2=impaired")
    print("  [3] visual_confidence  — 0.0 to 1.0")
    print("  [4] ear                — eye aspect ratio")
    print("  [5] mar                — mouth aspect ratio (yawning measurement)")
    print("  [6] estimated_bac      — 0.00-0.40 primary")
    print("  [7] temperature")
    print("\nLabels:")
    for idx, info in FUSION_LABELS.items():
        print(f"  {idx} = {info['label']} → action: {info['action']}")