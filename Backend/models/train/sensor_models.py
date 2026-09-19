import os
import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.model_selection import train_test_split, GroupKFold
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, mean_absolute_error, mean_squared_error, r2_score
from sklearn.utils.class_weight import compute_class_weight
from xgboost import XGBClassifier, XGBRegressor

# ── Sensor-priority prior constants (Plan A: nose + jaw + upperChest clavicle) ──
# mq1 = nose (GPIO34, ~0-3cm from source)
# mq2 = jaw  (GPIO35, mandible — covers mouth+neck, ~5-7cm below nose)
# mq3 = upperChest clavicle (GPIO32, ~12-15cm below jaw, covers neck+chest)
# Plan A weighting: Breath (1) = nose+jaw high, Others (2) = jaw+upperChest high, Clear (0) = all 3 equal
EPSILON = 1e-6  # avoid division by zero
MAX_BOOST = 2.0  # capped max boost (Plan A shorter baseline needs slightly higher cap, was 1.8)
BREATH_STEEPNESS = 2.0  # sigmoid steepness for breath weight curve
OTHERS_STEEPNESS = 2.0  # mirrored for others weight
# Physical justification: vapor concentration decays ~1/r^2; jaw 5-7cm, clavicle 12-15cm below source
# ratio >1.0 indicates directional gradient; sigmoid 1.0→2.0 smooth, no hard threshold.

# ── Paths ──────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(__file__)
SAVE_DIR = os.path.join(BASE_DIR, "../saved")
os.makedirs(SAVE_DIR, exist_ok=True)

# ── Label definitions ──────────────────────────────────────────
# 0 = no alcohol event (clear/open air — all 3 sensors matter equally)
# 1 = breath alcohol event — Plan A: nose (mq1) + jaw (mq2) weighted high
# 2 = Others (sanitizer+perfume combined) — Plan A: jaw (mq2) + upperChest clavicle (mq3) weighted high

LABELS = {
    0: {"name": "No alcohol",     "risk": "none",   "color": "green"},
    1: {"name": "Breath alcohol", "risk": "high",   "color": "red"},
    2: {"name": "Others",         "risk": "low",    "color": "yellow"},
}

# ── PH BAC tiers (inside label 1 only) — RA 10586 0.05% is gate ──────────
PH_LIMIT = 0.05

BAC_TIERS = {
    "sober": {"range": (0.00, 0.00), "risk": "none",     "ph_verdict": "PASS",    "color": "green",  "label": "Sober"},
    "trace": {"range": (0.01, 0.02), "risk": "low",      "ph_verdict": "PASS",    "color": "yellow", "label": "Trace"},
    "light": {"range": (0.02, 0.05), "risk": "moderate", "ph_verdict": "PASS",    "color": "yellow", "label": "Light"},
    "over":  {"range": (0.05, 0.40), "risk": "high",     "ph_verdict": "PH FAIL", "color": "red",    "label": "Over PH Limit"},
}

def classify_bac_tier(bac: float):
    """Deterministic PH tier inside label 1. Called after regressor."""
    if bac is None or bac <= 0.001:
        return {"tier": "sober", **BAC_TIERS["sober"], "bac": 0.0}
    if bac < 0.02:
        return {"tier": "trace", **BAC_TIERS["trace"], "bac": float(bac)}
    if bac < PH_LIMIT:
        return {"tier": "light", **BAC_TIERS["light"], "bac": float(bac)}
    return {"tier": "over",  **BAC_TIERS["over"],  "bac": float(bac)}

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
# Plan A topology: window_1=nose, window_2=jaw (mouth+neck), window_3=upperChest clavicle
# Backward compat: 15 base features + 3 directional Plan A features = 18 total
# Old 15-feature models can still load via fallback; new models use 18D
NUM_BASE_FEATURES = 15
NUM_FEATURES = 18

def extract_features(window_1, window_2, window_3, temp, humidity):
    """
    Extracts 18 features from 3 MQ3 sensor windows (Plan A).

    Base 15:
        [0]  mq3_1_max (nose)
        [1]  mq3_1_avg
        [2]  mq3_1_std
        [3]  mq3_2_max (jaw mouth+neck)
        [4]  mq3_2_avg
        [5]  mq3_2_std
        [6]  mq3_3_max (upperChest clavicle)
        [7]  mq3_3_avg
        [8]  mq3_3_std
        [9]  rise_time          — avg samples from start to peak across sensors
        [10] decay_time         — avg samples from peak to end across sensors
        [11] spatial_variance_max — std of peak values across 3 sensors
        [12] spatial_variance_avg — std of avg values across 3 sensors
        [13] temperature
        [14] humidity
    Plan A directional (new):
        [15] breath_ratio       — (nose_avg+jaw_avg)/2 / max(chest_avg, EPSILON)  (>1 = breath-like)
        [16] sanitizer_ratio    — (jaw_avg+chest_avg)/2 / max(nose_avg, EPSILON) (>1 = sanitizer-like chest+mouth)
        [17] spatial_direction  — chest_max - nose_max (signed, positive = chest dominates)
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

    # Plan A directional features
    breath_ratio = round(((s1_avg + s2_avg) / 2.0) / max(s3_avg, EPSILON), 4) if s3_avg > 0 else round((s1_avg + s2_avg) / 2.0, 4)
    sanitizer_ratio = round(((s2_avg + s3_avg) / 2.0) / max(s1_avg, EPSILON), 4) if s1_avg > 0 else round((s2_avg + s3_avg) / 2.0, 4)
    spatial_direction = round(float(s3_max - s1_max), 4)

    return [
        s1_max, s1_avg, s1_std,     # [0-2]  nose
        s2_max, s2_avg, s2_std,     # [3-5]  jaw (mouth+neck)
        s3_max, s3_avg, s3_std,     # [6-8]  upperChest clavicle
        rise_time,                   # [9]
        decay_time,                  # [10]
        spatial_variance_max,        # [11]
        spatial_variance_avg,        # [12]
        float(temp),                 # [13]
        float(humidity),             # [14]
        breath_ratio,                # [15] Plan A
        sanitizer_ratio,             # [16] Plan A (jaw+chest vs nose)
        spatial_direction,           # [17] Plan A
    ]


# ── Label assignment ───────────────────────────────────────────
def assign_label(bac_value, is_sanitizer_event=False, features=None):
    """
    Assigns class label. Used ONLY during training data collection.
    Mirrors frontend Others (label 2) — sanitizer+perfume combined.

    Priority order:
        1. Operator explicitly flags Others (sanitizer/perfume) → 2
        2. BAC > 0.00 (breathalyzer confirmed alcohol)           → 1
        3. Sensor pattern looks like Others (high variance)      → 2  (auto-detect)
        4. Everything else                                       → 0

    Parameters:
        bac_value          — reading from BACtrack S80
        is_sanitizer_event — operator manually flagged Others event (sanitizer/perfume)
        features           — 15-feature list from extract_features()
                             used for auto-detect fallback
    """

    # 1. Operator explicitly flagged Others
    if is_sanitizer_event:
        return 2

    # 2. Breathalyzer confirmed alcohol
    if bac_value > 0.00:
        return 1

    # 3. Auto-detect Others from sensor pattern
    #    Others = big spike on 1 sensor only (high spatial variance, e.g. sanitizer/perfume upperChest)
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
def train(X, y, groups=None):
    """
    Train all models on collected dataset.

    Parameters:
        X — list of 15-feature vectors from extract_features()
        y — list of labels (0, 1, or 2)
        groups — list of trial_id per row for GroupKFold (required for expanded
                 dataset where 6-8 rows share same trial). If None, falls back
                 to stratified random split (legacy, not recommended for new data).
                 Grouped splitting prevents leakage: rows from same trial never
                 in both train and test.
    """
    X = np.array(X)
    y = np.array(y)
    groups = np.array(groups) if groups is not None else None

    # Verify class balance before training (item 6)
    unique, counts = np.unique(y, return_counts=True)
    print(f"Class distribution rows: {dict(zip(unique, counts))}")
    if groups is not None:
        ug, gc = np.unique(groups, return_counts=True)
        print(f"Trials: {len(ug)} distinct trial_id, rows per trial avg {len(y)/len(ug):.1f}")

    # Use GroupKFold when groups available, else stratified
    if groups is not None and len(np.unique(groups)) >= 3:
        n_splits = min(5, len(np.unique(groups)))
        gkf = GroupKFold(n_splits=n_splits)
        # For final model fit, use all data with scaler; evaluation via grouped CV separately
        # Here we keep simple hold-out for scaler fit using last fold
        splits = list(gkf.split(X, y, groups))
        train_idx, test_idx = splits[-1]  # last fold as hold-out for metrics
        X_train, X_test = X[train_idx], X[test_idx]
        y_train, y_test = y[train_idx], y[test_idx]
        print(f"Grouped split: train {len(y_train)} rows ({len(np.unique(groups[train_idx]))} trials), test {len(y_test)} rows ({len(np.unique(groups[test_idx]))} trials)")
    elif len(y) < 10:
        X_train, X_test = X, X
        y_train, y_test = y, y
    else:
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )
        print("Random stratified split (no groups) — not recommended for correlated rows")

    # Class weight handling for imbalance (item 6)
    # Compute balanced weights; RF will use class_weight, XGB via sample_weight
    class_weights = None
    if len(y_train) >= 3:
        try:
            cw = compute_class_weight('balanced', classes=np.unique(y_train), y=y_train)
            class_weights = {int(c): float(w) for c, w in zip(np.unique(y_train), cw)}
            print(f"Class weights balanced: {class_weights}")
        except Exception:
            pass

    scaler  = StandardScaler()
    X_train = scaler.fit_transform(X_train)
    X_test  = scaler.transform(X_test)

    joblib.dump(scaler, os.path.join(SAVE_DIR, "scaler.pkl"))
    print("✅ Scaler saved")

    results = {}

    for name, base_model in MODELS.items():
        print(f"\nTraining {name}...")
        # clone model to apply class_weight per run
        import copy
        model = copy.deepcopy(base_model)
        if class_weights is not None:
            if name == "random_forest":
                model.set_params(class_weight='balanced')
            elif name == "xgboost":
                # sample_weight per row
                sample_weight = np.array([class_weights[int(c)] for c in y_train])
                model.fit(X_train, y_train, sample_weight=sample_weight)
                preds    = model.predict(X_test)
                accuracy = accuracy_score(y_test, preds)
                report   = classification_report(
                    y_test, preds,
            target_names=["No alcohol", "Breath alcohol", "Others"],
                    output_dict=True,
                    zero_division=0,
                )
                cm = confusion_matrix(y_test, preds, labels=[0,1,2])
                results[name] = {
                    "accuracy":  round(accuracy * 100, 2),
                    "precision": round(report["macro avg"]["precision"] * 100, 2),
                    "recall":    round(report["macro avg"]["recall"] * 100, 2),
                    "f1":        round(report["macro avg"]["f1-score"] * 100, 2),
                    "samples":   len(y),
                    "trials":    int(len(np.unique(groups))) if groups is not None else None,
                    "confusion_matrix": cm.tolist(),
                    "per_class": {
                        cls: {k: round(v * 100, 1) if k != "support" else v for k, v in stats.items()}
                        for cls, stats in report.items()
                if cls in ("No alcohol", "Breath alcohol", "Others")
                    },
                }
                joblib.dump(model, os.path.join(SAVE_DIR, f"{name}.pkl"))
                print(f"✅ {name} saved — accuracy: {results[name]['accuracy']}%")
                continue

        model.fit(X_train, y_train)

        preds    = model.predict(X_test)
        accuracy = accuracy_score(y_test, preds)
        report   = classification_report(
            y_test, preds,
            target_names=["No alcohol", "Breath alcohol", "Others"],
            output_dict=True,
            zero_division=0,
        )
        cm = confusion_matrix(y_test, preds, labels=[0,1,2])

        results[name] = {
            "accuracy":  round(accuracy * 100, 2),
            "precision": round(report["macro avg"]["precision"] * 100, 2),
            "recall":    round(report["macro avg"]["recall"] * 100, 2),
            "f1":        round(report["macro avg"]["f1-score"] * 100, 2),
            "samples":   len(y),
            "trials":    int(len(np.unique(groups))) if groups is not None else None,
            "confusion_matrix": cm.tolist(),
            "per_class": {
                cls: {k: round(v * 100, 1) if k != "support" else v for k, v in stats.items()}
                for cls, stats in report.items()
                if cls in ("No alcohol", "Breath alcohol", "Others")
            },
        }

        joblib.dump(model, os.path.join(SAVE_DIR, f"{name}.pkl"))
        print(f"✅ {name} saved — accuracy: {results[name]['accuracy']}%")

    # Persist REAL evaluation metrics for the frontend
    from models.train.metrics_store import save_metrics
    save_metrics("sensor", results)
    print("✅ Metrics saved to metrics.json")

    return results


# ── Load model ─────────────────────────────────────────────────
def load(model_name="random_forest"):
    path = os.path.join(SAVE_DIR, f"{model_name}.pkl")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Model '{model_name}' not found. Train it first by calling train(X, y)"
        )
    return joblib.load(path)


# ── Continuous sensor-priority prior (Plan A: nose/jaw vs jaw+chest) ──
def _smooth_scale(ratio, steepness=BREATH_STEEPNESS):
    """Sigmoid 1.0 → MAX_BOOST, smooth, no hard threshold.
    ratio = directional avg / opponent avg. 1.0 = equal, →∞ → MAX_BOOST."""
    return 1.0 + (MAX_BOOST - 1.0) / (1.0 + np.exp(-steepness * (ratio - 1.0)))


def _apply_sensor_prior(probs, features, use_prior=False):
    """Option A: rescale only p1 vs p2, leave p0 (clear air) untouched, then renormalize.
    Plan A: breath = (nose+jaw)/2 vs chest,  sanitizer = (jaw+chest)/2 vs nose.
    probs [p0,p1,p2], features 18 list (or 15 legacy). Returns renormalized probs."""
    if not use_prior or features is None or len(probs) < 3:
        return probs
    n = len(features)
    # Support both 18D (new) and 15D legacy (fallback to raw max)
    if n >= 18:
        breath_ratio = float(features[15])
        sanitizer_ratio = float(features[16])
    else:
        # legacy 15D: derive from raw max/avg
        nose_avg = float(features[1]) if n > 1 else float(features[0])
        jaw_avg = float(features[4]) if n > 4 else float(features[3])
        chest_avg = float(features[7]) if n > 7 else float(features[6])
        breath_ratio = ((nose_avg + jaw_avg) / 2.0) / max(chest_avg, EPSILON) if chest_avg else 1.0
        sanitizer_ratio = ((jaw_avg + chest_avg) / 2.0) / max(nose_avg, EPSILON) if nose_avg else 1.0
    breath_w = _smooth_scale(breath_ratio, BREATH_STEEPNESS)
    others_w = _smooth_scale(sanitizer_ratio, OTHERS_STEEPNESS)
    p0, p1, p2 = float(probs[0]), float(probs[1]), float(probs[2])
    p1 *= breath_w
    p2 *= others_w
    s = p0 + p1 + p2
    if s == 0:
        return probs
    return np.array([p0 / s, p1 / s, p2 / s])


# ── BAC Regressor — estimated BAC inside label 1 (PH) ─────────
BAC_REGRESSOR_FILE = "bac_regressor.pkl"
BAC_SCALER_FILE = "bac_scaler.pkl"

def train_bac(X, bac_y, groups=None):
    """
    Train BAC regressor on breath-alcohol trials only.
    X: list of 15-feature vectors, bac_y: list of %BAC floats (0.01-0.40), groups: trial_id
    Returns metrics dict or {"error":...}. Requires >=15 rows and >=3 distinct BAC levels.
    """
    X = np.array(X, dtype=float)
    bac_y = np.array(bac_y, dtype=float)
    groups = np.array(groups) if groups is not None else None
    # Filter valid
    mask = ~np.isnan(bac_y) & (bac_y > 0.001)
    X, bac_y = X[mask], bac_y[mask]
    if groups is not None:
        groups = groups[mask]
    uniq = np.unique(np.round(bac_y, 2))
    if len(bac_y) < 15:
        return {"error": f"Need >=15 BAC rows, have {len(bac_y)}", "samples": int(len(bac_y))}
    if len(uniq) < 3:
        return {"error": f"Need >=3 distinct BAC levels, have {len(uniq)} ({uniq.tolist()})", "samples": int(len(bac_y)), "levels": uniq.tolist()}
    print(f"BAC regressor: {len(bac_y)} rows, levels {uniq.tolist()}")
    # Split
    if groups is not None and len(np.unique(groups)) >= 3:
        n_splits = min(5, len(np.unique(groups)))
        gkf = GroupKFold(n_splits=n_splits)
        train_idx, test_idx = list(gkf.split(X, bac_y, groups))[-1]
        X_train, X_test = X[train_idx], X[test_idx]
        y_train, y_test = bac_y[train_idx], bac_y[test_idx]
        print(f"BAC grouped split: train {len(y_train)} ({len(np.unique(groups[train_idx]))} trials), test {len(y_test)} ({len(np.unique(groups[test_idx]))} trials)")
    else:
        X_train, X_test, y_train, y_test = train_test_split(X, bac_y, test_size=0.2, random_state=42)
    scaler = StandardScaler()
    X_train = scaler.fit_transform(X_train)
    X_test = scaler.transform(X_test)
    joblib.dump(scaler, os.path.join(SAVE_DIR, BAC_SCALER_FILE))
    # RandomForest regressor — robust to small tabular data, no heavy tuning
    reg = RandomForestRegressor(n_estimators=150, random_state=42, n_jobs=-1)
    reg.fit(X_train, y_train)
    preds = reg.predict(X_test)
    # Clip to legal range for metrics
    preds_c = np.clip(preds, 0.0, 0.40)
    mae = mean_absolute_error(y_test, preds_c)
    rmse = float(np.sqrt(mean_squared_error(y_test, preds_c)))
    r2 = r2_score(y_test, preds_c) if len(y_test) >= 2 else 0.0
    # Tier accuracy (PH)
    def tier_of(v): 
        if v < 0.02: return "trace"
        if v < PH_LIMIT: return "light"
        return "over"
    tier_acc = sum(tier_of(a) == tier_of(p) for a, p in zip(y_test, preds_c)) / max(len(y_test), 1)
    joblib.dump(reg, os.path.join(SAVE_DIR, BAC_REGRESSOR_FILE))
    print(f"✅ BAC regressor saved — MAE {mae:.4f} RMSE {rmse:.4f} R2 {r2:.4f} tier_acc {tier_acc*100:.1f}%")
    from models.train.metrics_store import save_metrics
    res = {"mae": round(float(mae), 4), "rmse": round(float(rmse), 4), "r2": round(float(r2), 4), "tier_accuracy": round(float(tier_acc*100), 1), "samples": int(len(bac_y)), "levels": uniq.tolist(), "trials": int(len(np.unique(groups))) if groups is not None else None}
    # Merge into sensor metrics under bac key
    try:
        from models.train.metrics_store import load_metrics
        existing = load_metrics().get("sensor", {})
        save_metrics("sensor", {**existing, "bac_regressor": res})
    except Exception:
        save_metrics("bac", res)
    return res

def load_bac_regressor():
    path = os.path.join(SAVE_DIR, BAC_REGRESSOR_FILE)
    if not os.path.exists(path):
        raise FileNotFoundError("BAC regressor not found. Train with label-1 BAC range first.")
    return joblib.load(path)

def predict_bac(window_1, window_2, window_3, temp, humidity):
    """Direct BAC regression — returns clipped %BAC float. Use predict_bac_and_tier for PH verdict."""
    features = extract_features(window_1, window_2, window_3, temp, humidity)
    if features is None:
        return {"error": "No readings in window"}
    reg = load_bac_regressor()
    # Prefer bac_scaler if exists else fallback to scaler.pkl (shared)
    scaler_path = os.path.join(SAVE_DIR, BAC_SCALER_FILE)
    if not os.path.exists(scaler_path):
        scaler_path = os.path.join(SAVE_DIR, "scaler.pkl")
    scaler = joblib.load(scaler_path)
    est = float(np.clip(reg.predict(scaler.transform([features]))[0], 0.0, 0.40))
    tier = classify_bac_tier(est)
    return {"estimated_bac": round(est, 4), **tier}

def predict_bac_and_tier(window_1, window_2, window_3, temp, humidity):
    """Wrapper — always returns est_bac + PH tier, even if regressor missing (fallback)."""
    try:
        return predict_bac(window_1, window_2, window_3, temp, humidity)
    except FileNotFoundError as e:
        return {"error": str(e), "estimated_bac": None, "tier": "unknown"}

# ── Prediction ─────────────────────────────────────────────────
def predict(window_1, window_2, window_3,
            temp, humidity,
            model_name="random_forest",
            use_prior=False):
    """
    Make a prediction for one event using 3 sensor windows.
    Only called in deployment mode (after model is trained).
    use_prior: if True apply sensor-priority prior (items 2-4), else pure model.
    Default False until ablation (item 5) shows benefit — do not enable in live POST /sensor until go.
    """
    features = extract_features(window_1, window_2, window_3, temp, humidity)

    if features is None:
        return {"error": "No readings in window"}

    model  = load(model_name)
    scaler = joblib.load(os.path.join(SAVE_DIR, "scaler.pkl"))

    X           = scaler.transform([features])
    probs_raw   = model.predict_proba(X)[0]
    # apply prior if requested (renormalized, p0 untouched per Option A)
    probs       = _apply_sensor_prior(probs_raw, features, use_prior=use_prior)
    class_index = int(np.argmax(probs))
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
        },
        "all_probs_raw": {
            LABELS[i]["name"]: round(float(p), 4)
            for i, p in enumerate(probs_raw)
        } if use_prior else None,
        "prior_applied": bool(use_prior),
    }


if __name__ == "__main__":
    print("Sensor models ready. Plan A: nose (mq1) + jaw mouth+neck (mq2) + upperChest clavicle (mq3)")
    print("\nLabels:")
    for idx, info in LABELS.items():
        print(f"  {idx} = {info['name']} (risk: {info['risk']})")
    print(f"\nPH limit: {PH_LIMIT} — tiers: trace <0.02 / light <0.05 / over ≥0.05")
    print(f"\n{NUM_FEATURES} Features (Plan A):")
    print("  [0-2]  Nose: max, avg, std")
    print("  [3-5]  Jaw (mouth+neck): max, avg, std")
    print("  [6-8]  UpperChest clavicle: max, avg, std")
    print("  [9]    rise_time")
    print("  [10]   decay_time")
    print("  [11]   spatial_variance_max")
    print("  [12]   spatial_variance_avg")
    print("  [13]   temperature")
    print("  [14]   humidity")
    print("  [15]   breath_ratio (nose+jaw)/2 / chest")
    print("  [16]   sanitizer_ratio (jaw+chest)/2 / nose")
    print("  [17]   spatial_direction chest_max - nose_max")