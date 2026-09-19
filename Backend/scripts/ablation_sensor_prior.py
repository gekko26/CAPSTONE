"""
Ablation test for sensor-priority prior (items 1,4,5).

Grouped k-fold by trial_id — rows from same physical trial never in both train/test.
Compares:
  WITHOUT prior: spatial_variance feature alone (model as trained, predict use_prior=False)
  WITH prior:    same model + continuous smooth prior (Option A p0 untouched, renormalized)

Reports accuracy/confusion matrix per fold and mean, plus go/no-go.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from collections import Counter
import numpy as np
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, confusion_matrix, classification_report
from sklearn.ensemble import RandomForestClassifier
from xgboost import XGBClassifier

from database.db import SessionLocal
from database.models import TrainingData
from models.train.sensor_models import extract_features, LABELS, _apply_sensor_prior

# Use same model configs as sensor_models
MODELS = {
    "random_forest": RandomForestClassifier(n_estimators=100, random_state=42, class_weight='balanced'),
    "xgboost": XGBClassifier(n_estimators=100, random_state=42, eval_metric="mlogloss", use_label_encoder=False),
}

def load_data():
    db = SessionLocal()
    rows = db.query(TrainingData).filter(TrainingData.label >= 0, TrainingData.mq3_1_max != None).all()
    db.close()
    X = []
    y = []
    groups = []
    meta = []
    for r in rows:
        feat = [r.mq3_1_max, r.mq3_1_avg, r.mq3_1_std, r.mq3_2_max, r.mq3_2_avg, r.mq3_2_std, r.mq3_3_max, r.mq3_3_avg, r.mq3_3_std, r.rise_time, r.decay_time, r.spatial_variance, r.spatial_variance_avg, r.temperature, r.humidity]
        X.append(feat)
        y.append(r.label)
        groups.append(r.trial_id if r.trial_id is not None else r.id)
        meta.append((r.id, r.trial_id, r.label, r.sub_label))
    return np.array(X), np.array(y), np.array(groups), meta

def run_ablation(n_splits=5):
    X, y, groups, meta = load_data()
    print(f"Rows: {len(y)}, Trials: {len(set(groups))}, Class rows: {Counter(y)}, Trials per class: need sub_label breakdown")
    # Also report sub-label diversity
    db = SessionLocal()
    rows = db.query(TrainingData).filter(TrainingData.label >= 0).all()
    sub_counts = Counter((r.label, r.sub_label) for r in rows if r.mq3_1_max is not None)
    print(f"Sub-label rows: {dict(sub_counts)}")
    trial_sub = Counter()
    for r in rows:
        if r.mq3_1_max is not None:
            trial_sub[(r.label, r.sub_label, r.trial_id)] = 1
    # count distinct trials per (label, sub_label)
    from collections import defaultdict
    trial_per_sub = defaultdict(set)
    for r in rows:
        if r.mq3_1_max is not None and r.trial_id is not None:
            trial_per_sub[(r.label, r.sub_label)].add(r.trial_id)
    print(f"Distinct trials per (label, sub_label): { {k: len(v) for k,v in trial_per_sub.items()} }")
    db.close()

    if len(set(groups)) < 3:
        print("Not enough distinct trials for GroupKFold (need >=3) — need more collection (80-100 trials)")
        return

    n_splits = min(n_splits, len(set(groups)))
    gkf = GroupKFold(n_splits=n_splits)
    results = {"without": [], "with": []}
    cms_without = []
    cms_with = []

    for fold, (train_idx, test_idx) in enumerate(gkf.split(X, y, groups)):
        X_train, X_test = X[train_idx], X[test_idx]
        y_train, y_test = y[train_idx], y[test_idx]
        groups_test = groups[test_idx]

        scaler = StandardScaler()
        X_train_s = scaler.fit_transform(X_train)
        X_test_s = scaler.transform(X_test)

        # Train RF for this fold
        model = RandomForestClassifier(n_estimators=100, random_state=42, class_weight='balanced')
        model.fit(X_train_s, y_train)
        probs = model.predict_proba(X_test_s)
        preds_without = np.argmax(probs, axis=1)
        # With prior: apply continuous prior per sample using its features (X_test raw before scaling)
        preds_with = []
        for i, feat in enumerate(X_test):
            p = probs[i].copy()
            p_prior = _apply_sensor_prior(p, feat, use_prior=True)
            preds_with.append(int(np.argmax(p_prior)))
        preds_with = np.array(preds_with)

        acc_without = accuracy_score(y_test, preds_without)
        acc_with = accuracy_score(y_test, preds_with)
        results["without"].append(acc_without)
        results["with"].append(acc_with)
        cms_without.append(confusion_matrix(y_test, preds_without, labels=[0,1,2]))
        cms_with.append(confusion_matrix(y_test, preds_with, labels=[0,1,2]))
        print(f"Fold {fold+1}: without {acc_without:.3f} with {acc_with:.3f}  test trials {len(set(groups_test))} rows {len(y_test)}")

    mean_without = np.mean(results["without"])
    mean_with = np.mean(results["with"])
    print(f"\nMean accuracy WITHOUT prior: {mean_without:.3f} ({[f'{x:.3f}' for x in results['without']]})")
    print(f"Mean accuracy WITH prior   : {mean_with:.3f} ({[f'{x:.3f}' for x in results['with']]})")
    cm_without_mean = np.sum(cms_without, axis=0)
    cm_with_mean = np.sum(cms_with, axis=0)
    print(f"\nConfusion WITHOUT (rows true 0/1/2):\n{cm_without_mean}")
    print(f"Confusion WITH:\n{cm_with_mean}")
    # Also check p0 untouched: ensure no No-alcohol flips to alcohol due to prior
    # We already used Option A, but verify via per-fold where true 0
    # Go/no-go
    diff = mean_with - mean_without
    if diff > 0.02:
        print(f"\nGO: prior improves +{diff:.3f} meaningfully (>2%) — consider enabling in POST /sensor after review")
    elif diff < -0.02:
        print(f"\nNO-GO: prior hurts {diff:.3f} — spatial_variance already captures signal, double-counting")
    else:
        print(f"\nNO-GO (neutral): prior diff {diff:.3f} within noise — do NOT ship, keep pure model")

if __name__ == "__main__":
    run_ablation()
