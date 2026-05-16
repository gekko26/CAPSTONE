from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session
from database.db import get_db
from database.models import Reading
from models.train import sensor_models
from models.train.cv_model import analyze_frame
from models.train.mobilenet import predict_frame
from models.train.fusion_model import predict_single
import numpy as np
import cv2

router = APIRouter(prefix="/predict", tags=["predict"])


def sensor_ensemble(reading):
    """
    Runs RF + XGBoost on the same reading and combines results.
    If both agree   → high confidence, use agreed class.
    If they disagree → lower confidence, use RF as tiebreaker.
    """
    # We don't have raw windows here — sensor was already saved
    # So we use saved reading values as a proxy signal
    # Real ensemble happens in /sensor route with live windows
    # Here we just use the saved label from the reading
    return {
        "class":      0,
        "confidence": 0.0,
        "agreed":     False,
        "note":       "no_live_window"
    }


def run_sensor_ensemble(window_1, window_2, window_3, temp, humidity):
    """
    Runs RF + XGBoost on live MQ3 windows and combines results.
    Called from /predict/full when live windows are available.

    Returns:
        {
            "class":      1,
            "confidence": 0.88,
            "agreed":     True,   ← both models agreed
            "rf_class":   1,
            "xgb_class":  1,
        }
    """
    try:
        rf_result = sensor_models.predict(
            window_1, window_2, window_3,
            temp, humidity,
            model_name="random_forest"
        )
    except FileNotFoundError:
        rf_result = {"class": 0, "confidence": 0.0, "label": "No model"}

    try:
        xgb_result = sensor_models.predict(
            window_1, window_2, window_3,
            temp, humidity,
            model_name="xgboost"
        )
    except FileNotFoundError:
        xgb_result = {"class": 0, "confidence": 0.0, "label": "No model"}

    agreed = rf_result["class"] == xgb_result["class"]

    if agreed:
        # Both agree — average their confidence
        final_class = rf_result["class"]
        confidence  = round(
            (rf_result["confidence"] + xgb_result["confidence"]) / 2, 4
        )
    else:
        # Disagree — use RF as tiebreaker, lower confidence
        final_class = rf_result["class"]
        confidence  = round(rf_result["confidence"] * 0.7, 4)  # penalize disagreement

    return {
        "class":      final_class,
        "label":      rf_result["label"],
        "confidence": confidence,
        "agreed":     agreed,
        "rf_class":   rf_result["class"],
        "xgb_class":  xgb_result["class"],
    }


# ── Full prediction pipeline ───────────────────────────────────
@router.post("/full")
async def full_predict(
    file:       UploadFile = File(...),
    reading_id: int        = None,
    db:         Session    = Depends(get_db)
):
    """
    Full prediction pipeline:
        1. Decode camera frame
        2. EAR from MediaPipe (cv_model)
        3. Visual impairment from MobileNet
        4. Sensor ensemble from DB reading (RF + XGBoost)
        5. Fusion model — combines all signals
        6. Save result back to reading

    Usage:
        POST /predict/full?reading_id=42
        Body: form-data, key=file, value=<image>
    """

    # 1. Decode image
    contents = await file.read()
    np_arr   = np.frombuffer(contents, np.uint8)
    frame    = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    if frame is None:
        return {"error": "Invalid image"}

    # 2. EAR from MediaPipe
    ear_result = analyze_frame(frame)
    ear        = ear_result["ear"] or 0.0

    # 3. MobileNet visual prediction
    try:
        mobile_result = predict_frame(frame)
    except FileNotFoundError:
        mobile_result = {
            "label":       "no_model",
            "confidence":  0.0,
            "class_index": 0
        }

    # 4. Get sensor reading from DB
    reading = None
    if reading_id:
        reading = db.query(Reading).filter(Reading.id == reading_id).first()
    else:
        reading = db.query(Reading).order_by(Reading.date.desc()).first()

    if not reading:
        return {"error": "No sensor reading found"}

    # 5. Get sensor class from reading label
    sensor_label_map = {"No alcohol": 0, "Breath alcohol": 1, "Sanitizer": 2}
    sensor_class      = sensor_label_map.get(reading.label, 0)
    sensor_confidence = 0.5   # fallback — no confidence stored in reading

    # 6. Fusion model
    try:
        fusion_result = predict_single(
            sensor_class      = sensor_class,
            sensor_confidence = sensor_confidence,
            visual_class      = mobile_result["class_index"],
            visual_confidence = mobile_result["confidence"],
            ear               = ear,
            blink_rate        = 0.0,
            temperature       = reading.temperature or 0.0,
            humidity          = reading.humidity    or 0.0,
        )
    except FileNotFoundError:
        fusion_result = {
            "class":      0,
            "label":      "no_model",
            "risk":       "unknown",
            "action":     "unknown",
            "confidence": 0.0,
        }

    # 7. Save EAR + fusion result back to reading
    reading.ear   = ear
    reading.label = fusion_result["label"]
    db.commit()

    return {
        "reading_id": reading.id,

        # Sensor result
        "sensor_class":      sensor_class,
        "sensor_label":      reading.label,

        # EAR result
        "ear":               ear,
        "eye_status":        ear_result["status"],
        "impaired":          ear_result["impaired"],

        # MobileNet result
        "visual_label":      mobile_result["label"],
        "visual_confidence": mobile_result["confidence"],

        # Fusion result
        "final_label":       fusion_result["label"],
        "final_risk":        fusion_result["risk"],
        "final_action":      fusion_result["action"],
        "final_confidence":  fusion_result["confidence"],

        # Raw sensor values
        "bac":               reading.bac,
        "temperature":       reading.temperature,
        "humidity":          reading.humidity,
    }


# ── Full prediction with live MQ3 windows ─────────────────────
@router.post("/live")
async def live_predict(
    file:        UploadFile  = File(...),
    temperature: float       = 0.0,
    humidity:    float       = 0.0,
    mq3_1:       str         = "",   # comma-separated values
    mq3_2:       str         = "",
    mq3_3:       str         = "",
    db:          Session     = Depends(get_db)
):
    """
    Full prediction with live MQ3 windows + camera frame.
    Used during deployment when ESP32 + camera both send data together.

    Usage:
        POST /predict/live
        Form fields:
            file        — camera frame
            temperature — from ESP32
            humidity    — from ESP32
            mq3_1       — comma-separated readings e.g. "112,115,320,400,380"
            mq3_2       — comma-separated readings
            mq3_3       — comma-separated readings
    """

    # 1. Decode image
    contents = await file.read()
    np_arr   = np.frombuffer(contents, np.uint8)
    frame    = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    if frame is None:
        return {"error": "Invalid image"}

    # 2. Parse MQ3 windows from comma-separated strings
    try:
        w1 = [float(x) for x in mq3_1.split(",") if x.strip()]
        w2 = [float(x) for x in mq3_2.split(",") if x.strip()]
        w3 = [float(x) for x in mq3_3.split(",") if x.strip()]
    except ValueError:
        return {"error": "Invalid MQ3 values — must be comma-separated numbers"}

    # 3. EAR from MediaPipe
    ear_result = analyze_frame(frame)
    ear        = ear_result["ear"] or 0.0

    # 4. MobileNet visual prediction
    try:
        mobile_result = predict_frame(frame)
    except FileNotFoundError:
        mobile_result = {
            "label":       "no_model",
            "confidence":  0.0,
            "class_index": 0
        }

    # 5. Sensor ensemble — RF + XGBoost on live windows
    sensor_result = run_sensor_ensemble(w1, w2, w3, temperature, humidity)

    # 6. Fusion model
    try:
        fusion_result = predict_single(
            sensor_class      = sensor_result["class"],
            sensor_confidence = sensor_result["confidence"],
            visual_class      = mobile_result["class_index"],
            visual_confidence = mobile_result["confidence"],
            ear               = ear,
            blink_rate        = 0.0,
            temperature       = temperature,
            humidity          = humidity,
        )
    except FileNotFoundError:
        fusion_result = {
            "class":      0,
            "label":      "no_model",
            "risk":       "unknown",
            "action":     "unknown",
            "confidence": 0.0,
        }

    # 7. Save to DB
    new_reading = Reading(
        temperature = temperature,
        humidity    = humidity,
        bac         = None,
        ear         = ear,
        label       = fusion_result["label"],
        model_used  = "ensemble_v1",
    )
    db.add(new_reading)
    db.commit()
    db.refresh(new_reading)

    return {
        "reading_id": new_reading.id,

        # Sensor ensemble
        "sensor_class":      sensor_result["class"],
        "sensor_label":      sensor_result["label"],
        "sensor_confidence": sensor_result["confidence"],
        "sensor_agreed":     sensor_result["agreed"],   # did RF + XGBoost agree?
        "rf_class":          sensor_result["rf_class"],
        "xgb_class":         sensor_result["xgb_class"],

        # EAR
        "ear":               ear,
        "eye_status":        ear_result["status"],
        "impaired":          ear_result["impaired"],
        "proximity":         ear_result["proximity"],

        # MobileNet
        "visual_label":      mobile_result["label"],
        "visual_confidence": mobile_result["confidence"],

        # Fusion final decision
        "final_label":       fusion_result["label"],
        "final_risk":        fusion_result["risk"],
        "final_action":      fusion_result["action"],
        "final_confidence":  fusion_result["confidence"],
        "all_probs":         fusion_result.get("all_probs", {}),

        # Environment
        "temperature":       temperature,
        "humidity":          humidity,
    }


# ── Re-evaluate from saved reading ────────────────────────────
@router.get("/reading/{reading_id}")
def predict_from_reading(reading_id: int, db: Session = Depends(get_db)):
    """
    Re-run fusion prediction from an existing saved reading.
    Useful for re-evaluating old data after retraining.

    Usage:
        GET /predict/reading/42
    """
    reading = db.query(Reading).filter(Reading.id == reading_id).first()
    if not reading:
        return {"error": "Reading not found"}

    sensor_label_map  = {"No alcohol": 0, "Breath alcohol": 1, "Sanitizer": 2}
    sensor_class      = sensor_label_map.get(reading.label, 0)

    try:
        fusion_result = predict_single(
            sensor_class      = sensor_class,
            sensor_confidence = 0.5,
            visual_class      = 0,
            visual_confidence = 0.0,
            ear               = reading.ear or 0.0,
            blink_rate        = 0.0,
            temperature       = reading.temperature or 0.0,
            humidity          = reading.humidity    or 0.0,
        )
    except FileNotFoundError:
        fusion_result = {
            "label":  "no_model",
            "risk":   "unknown",
            "action": "unknown",
        }

    return {
        "reading_id":   reading_id,
        "bac":          reading.bac,
        "ear":          reading.ear,
        "temperature":  reading.temperature,
        "humidity":     reading.humidity,
        "saved_label":  reading.label,
        "final_label":  fusion_result["label"],
        "final_risk":   fusion_result["risk"],
        "final_action": fusion_result["action"],
    }