# model_status.py
import os
import time
from fastapi import APIRouter

router = APIRouter(prefix="/models", tags=["Models"])

SAVE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "saved")

ARTIFACTS = {
    "random_forest":  ("random_forest.pkl",  "Sensor ensemble — MQ3 features"),
    "xgboost":        ("xgboost.pkl",        "Sensor ensemble — MQ3 features"),
    "scaler":         ("scaler.pkl",         "Sensor feature scaler"),
    "fusion":         ("fusion_model.pkl",   "Fusion classifier — sensor + visual"),
    "fusion_scaler":  ("fusion_scaler.pkl",  "Fusion feature scaler"),
    "mobilenet":      ("mobilenet.h5",       "Visual classifier — impaired vs sober (MobileNetV2)"),
}


@router.get("/status")
def models_status():
    from models.train.metrics_store import load_metrics
    real = load_metrics()

    # Which section of metrics.json feeds which artifact card
    SECTION_FOR = {
        "random_forest": ("sensor", "random_forest"),
        "xgboost":       ("sensor", "xgboost"),
        "fusion":        ("fusion", None),
        "mobilenet":     ("mobilenet", None),
    }

    models = []
    for name, (filename, description) in ARTIFACTS.items():
        path = os.path.join(SAVE_DIR, filename)
        exists = os.path.exists(path)
        entry = {
            "name":        name,
            "file":        filename,
            "description": description,
            "trained":     exists,
            "size_kb":     round(os.path.getsize(path) / 1024, 1) if exists else None,
            "modified":    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(path))) if exists else None,
            "metrics":     None,
        }
        if name in SECTION_FOR and exists:
            section, key = SECTION_FOR[name]
            data = real.get(section, {})
            m = data.get(key) if key else data
            if isinstance(m, dict):
                entry["metrics"] = m
        models.append(entry)
    return {"models": models, "trained_count": sum(1 for m in models if m["trained"]), "total": len(models)}
