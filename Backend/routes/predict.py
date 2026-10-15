import logging
import os
import cv2
import joblib
import numpy as np
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, File, UploadFile, BackgroundTasks, Form, HTTPException
from sqlalchemy.orm import Session
from database.db import get_db
from database.models import Reading, DeploymentLog
from models.train import sensor_models
from models.train.cv_model import analyze_frame
from models.train.mobilenet import predict_frame
from models.decision import decide, decision_to_dict, missing_inputs
from config.sensor_timing import (SENSOR_PREROLL_MS, SENSOR_WINDOW_MS,
                                    SENSOR_SETTLE_MARGIN_MS)

logger = logging.getLogger("AlcoDetect.Predict")

# How old a reading row may be before it is rejected as stale.
#
# This is measured from Reading.date, and that column is stamped when the PENDING
# row is created by POST /sensor/trigger — i.e. at TRIGGER time, not when the ESP32's
# data arrives (the ESP32 later UPDATES that same row). With the relay pre-roll the
# window therefore no longer completes at trigger+5 s but at trigger+8 s, so the
# limit must grow by exactly the pre-roll:
#     10 s (old 5 s window + 5 s slack) + 3 s pre-roll = 13 s
# See Backend/config/sensor_timing.py for the full timeline.
STALE_SECONDS = 10 + SENSOR_PREROLL_MS / 1000.0

# Existing column widths (VARCHAR(20)); no schema change is made.
STATUS_COL_LEN = 20
REASON_COL_LEN = 20

router = APIRouter(prefix="/predict", tags=["predict"])

# ── Storage Isolation Configuration ───────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOTS_DIR = os.path.join(BASE_DIR, "data", "snapshots")
IMPAIRED_DIR  = os.path.join(SNAPSHOTS_DIR, "impaired")
FATIGUE_DIR   = os.path.join(SNAPSHOTS_DIR, "fatigue")

os.makedirs(IMPAIRED_DIR, exist_ok=True)
os.makedirs(FATIGUE_DIR, exist_ok=True)

# Frozen dataclass -> a plain dict for the response.
def save_snapshot_task(file_path: str, frame_data: np.ndarray):
    try:
        target_dir = os.path.dirname(file_path)
        os.makedirs(target_dir, exist_ok=True)
        success = cv2.imwrite(file_path, frame_data)
        if success: print(f"✅ [SECURITY] Snapshot written: {file_path}")
    except Exception as e:
        print(f"❌ [CRITICAL ERROR] Failed to write background snapshot: {e}")


def _trunc(value, length):
    if value is None:
        return None
    return str(value)[:length]


# ── Model access. Every failure yields None; nothing is ever substituted. ──────

def _class_from_features(features, model_name):
    """Run one sensor classifier over an 18-feature vector.

    Returns the class int, or None when the model or scaler is unavailable or the
    call fails. Never raises. sensor_models has no predict-from-features entry
    point, so this mirrors the tail of sensor_models.predict().
    """
    if features is None:
        return None
    try:
        model = sensor_models.load(model_name)
        scaler = joblib.load(os.path.join(sensor_models.SAVE_DIR, "scaler.pkl"))
        probs = model.predict_proba(scaler.transform([features]))[0]
        col = int(np.argmax(probs))
        classes = getattr(model, "classes_", None)
        cls = int(classes[col]) if classes is not None and col < len(classes) else col
        label_map = sensor_models.load_label_map(model_name)
        if label_map:
            cls = int(label_map.get(str(cls), cls))
        return cls
    except FileNotFoundError:
        logger.exception("sensor model %s is not available", model_name)
        return None
    except Exception:
        logger.exception("sensor model %s failed to predict", model_name)
        return None


def _bac_from_features(features):
    """BAC regressor over an 18-feature vector. Returns a float or None."""
    if features is None:
        return None
    try:
        reg = sensor_models.load_bac_regressor()
        scaler_path = os.path.join(sensor_models.SAVE_DIR, sensor_models.BAC_SCALER_FILE)
        if not os.path.exists(scaler_path):
            scaler_path = os.path.join(sensor_models.SAVE_DIR, "scaler.pkl")
        scaler = joblib.load(scaler_path)
        return float(np.clip(reg.predict(scaler.transform([features]))[0], 0.0, 0.40))
    except FileNotFoundError:
        logger.exception("BAC regressor is not available")
        return None
    except Exception:
        logger.exception("BAC regressor failed to predict")
        return None


def _class_from_windows(window_1, window_2, window_3, temp, humidity, model_name):
    """Run one sensor classifier over raw windows. Returns int or None."""
    try:
        result = sensor_models.predict(window_1, window_2, window_3, temp, humidity,
                                       model_name=model_name)
    except FileNotFoundError:
        logger.exception("sensor model %s is not available", model_name)
        return None
    except Exception:
        logger.exception("sensor model %s failed to predict", model_name)
        return None
    if not isinstance(result, dict) or result.get("error"):
        logger.warning("sensor model %s returned no prediction: %s",
                       model_name, (result or {}).get("error"))
        return None
    cls = result.get("class")
    return cls if isinstance(cls, (int, np.integer)) and not isinstance(cls, bool) else None


def _face_label(frame):
    """MobileNet face label, or None when the model is unavailable."""
    try:
        result = predict_frame(frame)
    except FileNotFoundError:
        logger.exception("MobileNet model is not available")
        return None
    except Exception:
        logger.exception("MobileNet failed to predict")
        return None
    if not isinstance(result, dict):
        return None
    label = result.get("label")
    if result.get("out_of_range"):
        return None
    return label if isinstance(label, str) else None


def _features_from_log(log):
    """Rebuild the 18-feature vector stored on deployment_logs for a reading."""
    if log is None:
        return None
    base = [log.mq3_1_max, log.mq3_1_avg, log.mq3_1_std,
            log.mq3_2_max, log.mq3_2_avg, log.mq3_2_std,
            log.mq3_3_max, log.mq3_3_avg, log.mq3_3_std,
            log.rise_time, log.decay_time,
            log.spatial_variance, log.spatial_variance_avg,
            log.temperature, log.humidity]
    if any(v is None for v in base):
        return None
    directional = [log.breath_ratio, log.sanitizer_ratio, log.spatial_direction]
    if all(v is not None for v in directional):
        return [float(v) for v in base + directional]
    try:
        eps = sensor_models.EPSILON
        br = ((log.mq3_1_avg + log.mq3_2_avg) / 2.0) / max(log.mq3_3_avg or 1.0, eps) if log.mq3_3_avg else 0.0
        sr = ((log.mq3_2_avg + log.mq3_3_avg) / 2.0) / max(log.mq3_1_avg or 1.0, eps) if log.mq3_1_avg else 0.0
        sd = float((log.mq3_3_max or 0) - (log.mq3_1_max or 0))
        return [float(v) for v in base] + [round(br, 4), round(sr, 4), round(sd, 4)]
    except Exception:
        logger.exception("could not rebuild sensor features from deployment log")
        return None


def _est_bac_if_both_alcohol(rf_class, xgb_class, getter):
    """The BAC regressor is meaningful only when BOTH classifiers read alcohol.
    Everywhere else it is not called at all."""
    if rf_class == 1 and xgb_class == 1:
        return getter()
    return None


def _persist_decision(db, reading, decision, labels_joined, model_version):
    """Write status + joined reason labels into EXISTING varchar(20) columns."""
    reading.fusion_label = _trunc(decision.status, STATUS_COL_LEN)
    reading.denial_reason = _trunc(labels_joined, REASON_COL_LEN)
    db.commit()

    existing = db.query(DeploymentLog).filter(DeploymentLog.reading_id == reading.id).first()
    if existing:
        existing.prediction = _trunc(decision.status, STATUS_COL_LEN)
        existing.confidence = None
        existing.denial_reason = _trunc(labels_joined, REASON_COL_LEN)
        existing.model_version = model_version
        db.commit()
    else:
        log = DeploymentLog(
            reading_id=reading.id,
            temperature=reading.temperature or 0, humidity=reading.humidity or 0,
            mq3_1_max=0, mq3_1_avg=0, mq3_1_std=0, mq3_2_max=0, mq3_2_avg=0, mq3_2_std=0,
            mq3_3_max=0, mq3_3_avg=0, mq3_3_std=0, rise_time=0, decay_time=0,
            spatial_variance=0,
            prediction=_trunc(decision.status, STATUS_COL_LEN),
            confidence=None,
            denial_reason=_trunc(labels_joined, REASON_COL_LEN),
            model_version=model_version,
        )
        db.add(log)
        db.commit()
    return existing


@router.post("/full")
async def full_predict(background_tasks: BackgroundTasks, file: UploadFile = File(...), reading_id: int = Form(...), db: Session = Depends(get_db)):
    contents = await file.read()
    np_arr = np.frombuffer(contents, np.uint8)
    frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    if frame is None: return {"error": "Invalid image"}
    ear_result = analyze_frame(frame)
    ear = ear_result["ear"] or 0.0

    face = _face_label(frame)

    reading = db.query(Reading).filter(Reading.id == reading_id).first()
    if not reading:
        return {"error": f"Reading {reading_id} not found", "final_label": "RECAPTURE_NEEDED", "reason": "reading_not_found"}

    # staleness check. A NULL date can never be proven fresh, so it is rejected as
    # stale rather than accepted.
    try:
        created = reading.date
        if created is None:
            return {"reading_id": reading.id, "error": "Sensor reading has no timestamp - treat as stale",
                    "final_label": "RECAPTURE_NEEDED", "reason": "stale_sensor_data", "age_seconds": None}
        now = datetime.now(timezone.utc)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age = (now - created).total_seconds()
        if age > STALE_SECONDS:
            return {"reading_id": reading.id, "error": f"Stale sensor reading ({age:.1f}s > {STALE_SECONDS}s)", "final_label": "RECAPTURE_NEEDED", "reason": "stale_sensor_data", "age_seconds": round(age, 1)}
        # also reject pending placeholder that ESP32 hasn't populated yet (no label yet)
        if reading.label == "pending" or reading.temperature is None:
            return {"reading_id": reading.id, "error": "Sensor data not yet available — ESP32 still buffering", "final_label": "RECAPTURE_NEEDED", "reason": "sensor_not_ready"}
    except Exception:
        logger.exception("staleness check failed for reading %s", reading.id)
        return {"reading_id": reading.id, "error": "Staleness check failed",
                "final_label": "RECAPTURE_NEEDED", "reason": "stale_sensor_data", "age_seconds": None}

    # The 18 sensor features live on deployment_logs, keyed by reading_id.
    feature_log = db.query(DeploymentLog).filter(DeploymentLog.reading_id == reading.id).first()
    features = _features_from_log(feature_log)

    rf_class = _class_from_features(features, "random_forest")
    xgb_class = _class_from_features(features, "xgboost")
    est_bac = _est_bac_if_both_alcohol(rf_class, xgb_class, lambda: _bac_from_features(features))

    decision = decide(rf_class=rf_class, xgb_class=xgb_class, est_bac=est_bac, face_label=face)
    payload = decision_to_dict(decision)
    payload["missing"] = missing_inputs(decision)
    labels_joined = "; ".join(payload["labels"])

    reading.ear = ear
    try:
        _persist_decision(db, reading, decision, labels_joined, "rules_v1")
    except Exception:
        logger.exception("failed to persist decision for reading %s", reading.id)

    if decision.status == "FAIL":
        background_tasks.add_task(save_snapshot_task,
                                  os.path.join(IMPAIRED_DIR, f"alcohol_reading_{reading.id}.jpg"), frame)
    elif "fatigue" in decision.reasons:
        background_tasks.add_task(save_snapshot_task,
                                  os.path.join(FATIGUE_DIR, f"fatigue_reading_{reading.id}.jpg"), frame)

    return {"reading_id": reading.id, "sensor_class": rf_class, "sensor_label": reading.label,
            "ear": ear, "eye_status": ear_result["status"], "impaired": ear_result["impaired"],
            "final_label": decision.status, "final_risk": decision.status,
            "denial_reason": decision.path,
            "bac_driven": "alcohol_confirmed" in decision.reasons,
            "fatigue_driven": "fatigue" in decision.reasons,
            "estimated_bac": est_bac,
            "rf_class": rf_class, "xgb_class": xgb_class, "face_label": face,
            "decision": payload}


@router.post("/live")
async def live_predict(background_tasks: BackgroundTasks, file: UploadFile = File(...), temperature: float = 0.0, humidity: float = 0.0, mq3_1: str = "", mq3_2: str = "", mq3_3: str = "", db: Session = Depends(get_db)):
    contents = await file.read()
    np_arr = np.frombuffer(contents, np.uint8)
    frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    if frame is None: return {"error": "Invalid image"}
    try: w1, w2, w3 = [[float(x) for x in mq.split(",") if x.strip()] for mq in [mq3_1, mq3_2, mq3_3]]
    except ValueError: return {"error": "Invalid MQ3 values"}

    ear_result = analyze_frame(frame)
    ear = ear_result["ear"] or 0.0

    face = _face_label(frame)

    rf_class = _class_from_windows(w1, w2, w3, temperature, humidity, "random_forest")
    xgb_class = _class_from_windows(w1, w2, w3, temperature, humidity, "xgboost")

    def _bac():
        try:
            return sensor_models.predict_bac(w1, w2, w3, temperature, humidity).get("estimated_bac")
        except FileNotFoundError:
            logger.exception("BAC regressor is not available")
            return None
        except Exception:
            logger.exception("BAC regressor failed to predict")
            return None

    est_bac = _est_bac_if_both_alcohol(rf_class, xgb_class, _bac)

    decision = decide(rf_class=rf_class, xgb_class=xgb_class, est_bac=est_bac, face_label=face)
    payload = decision_to_dict(decision)
    payload["missing"] = missing_inputs(decision)
    labels_joined = "; ".join(payload["labels"])

    try:
        bac_tier = (sensor_models.classify_bac_tier(est_bac) or {}).get("tier") if est_bac is not None else None
    except Exception:
        logger.exception("BAC tier classification failed")
        bac_tier = None

    new_reading = Reading(temperature=temperature, humidity=humidity, bac=None,
                          estimated_bac=est_bac, bac_tier=bac_tier, ear=ear,
                          label=_trunc(decision.status, STATUS_COL_LEN),
                          fusion_label=_trunc(decision.status, STATUS_COL_LEN),
                          denial_reason=_trunc(labels_joined, REASON_COL_LEN),
                          model_used="rules_v1")
    db.add(new_reading)
    db.commit()
    db.refresh(new_reading)
    try:
        _persist_decision(db, new_reading, decision, labels_joined, "rules_v1")
    except Exception:
        logger.exception("failed to persist live decision for reading %s", new_reading.id)

    if decision.status == "FAIL":
        background_tasks.add_task(save_snapshot_task,
                                  os.path.join(IMPAIRED_DIR, f"alcohol_reading_{new_reading.id}.jpg"), frame)
    elif "fatigue" in decision.reasons:
        background_tasks.add_task(save_snapshot_task,
                                  os.path.join(FATIGUE_DIR, f"fatigue_reading_{new_reading.id}.jpg"), frame)

    return {"reading_id": new_reading.id, "sensor_label": new_reading.label,
            "ear": ear, "eye_status": ear_result["status"], "impaired": ear_result["impaired"],
            "final_label": decision.status, "final_risk": decision.status,
            "denial_reason": decision.path,
            "bac_driven": "alcohol_confirmed" in decision.reasons,
            "fatigue_driven": "fatigue" in decision.reasons,
            "estimated_bac": est_bac, "bac_tier": bac_tier,
            "ph_verdict": "PH FAIL" if bac_tier == "over" else "PH PASS" if bac_tier else None,
            "rf_class": rf_class, "xgb_class": xgb_class, "face_label": face,
            "decision": payload}


@router.get("/reading/{reading_id}")
def predict_from_reading(reading_id: int, db: Session = Depends(get_db)):
    reading = db.query(Reading).filter(Reading.id == reading_id).first()
    if not reading: return {"error": "Reading not found"}

    feature_log = db.query(DeploymentLog).filter(DeploymentLog.reading_id == reading.id).first()
    features = _features_from_log(feature_log)
    rf_class = _class_from_features(features, "random_forest")
    xgb_class = _class_from_features(features, "xgboost")
    est_bac = _est_bac_if_both_alcohol(rf_class, xgb_class, lambda: _bac_from_features(features))

    # No frame is available on this route, so the face input is unavailable by
    # definition. That can never yield PASS.
    decision = decide(rf_class=rf_class, xgb_class=xgb_class, est_bac=est_bac, face_label=None)
    payload = decision_to_dict(decision)
    payload["missing"] = missing_inputs(decision)

    return {"reading_id": reading_id, "saved_label": reading.label,
            "final_label": decision.status, "decision": payload}