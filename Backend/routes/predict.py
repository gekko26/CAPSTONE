import os
import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, UploadFile, BackgroundTasks
from sqlalchemy.orm import Session
from database.db import get_db
from database.models import Reading
from models.train import sensor_models
from models.train.cv_model import analyze_frame
from models.train.mobilenet import predict_frame
from models.train.fusion_model import predict_single

router = APIRouter(prefix="/predict", tags=["predict"])

# ── Storage Isolation Configuration ───────────────────────────
BASE_DIR = os.path.dirname(__file__)
SNAPSHOTS_DIR = os.path.normpath(os.path.join(BASE_DIR, "../../data/snapshots"))
os.makedirs(SNAPSHOTS_DIR, exist_ok=True)


def save_snapshot_task(file_path: str, frame_data: np.ndarray):
    """
    Executes entirely in the background to prevent disk I/O from 
    blocking the high-speed RTSP asynchronous inference loop.
    """
    try:
        cv2.imwrite(file_path, frame_data)
        print(f"[SECURITY LOCKOUT] Async Snapshot written to disk: {file_path}")
    except Exception as e:
        print(f"[ERROR] Failed to write background snapshot: {e}")


def sensor_ensemble(reading):
    return {
        "class":      0,
        "confidence": 0.0,
        "agreed":     False,
        "note":       "no_live_window"
    }


def run_sensor_ensemble(window_1, window_2, window_3, temp, humidity):
    try:
        rf_result = sensor_models.predict(window_1, window_2, window_3, temp, humidity, model_name="random_forest")
    except FileNotFoundError:
        rf_result = {"class": 0, "confidence": 0.0, "label": "No model"}

    try:
        xgb_result = sensor_models.predict(window_1, window_2, window_3, temp, humidity, model_name="xgboost")
    except FileNotFoundError:
        xgb_result = {"class": 0, "confidence": 0.0, "label": "No model"}

    agreed = rf_result["class"] == xgb_result["class"]

    if agreed:
        final_class = rf_result["class"]
        confidence  = round((rf_result["confidence"] + xgb_result["confidence"]) / 2, 4)
    else:
        final_class = rf_result["class"]
        confidence  = round(rf_result["confidence"] * 0.7, 4)

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
    background_tasks: BackgroundTasks,  # <--- FIX: Injected FastAPI Background Thread Pool
    file: UploadFile = File(...),
    reading_id: int = None,
    db: Session = Depends(get_db)
):
    contents = await file.read()
    np_arr   = np.frombuffer(contents, np.uint8)
    frame    = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    if frame is None:
        return {"error": "Invalid image"}

    ear_result = analyze_frame(frame)
    ear        = ear_result["ear"] or 0.0

    try:
        mobile_result = predict_frame(frame)
    except FileNotFoundError:
        mobile_result = {"label": "no_model", "confidence": 0.0, "class_index": 0}

    if reading_id:
        reading = db.query(Reading).filter(Reading.id == reading_id).first()
    else:
        reading = db.query(Reading).order_by(Reading.date.desc()).first()

    if not reading:
        return {"error": "No sensor reading found"}

    sensor_label_map = {"No alcohol": 0, "Breath alcohol": 1, "Sanitizer": 2}
    sensor_class      = sensor_label_map.get(reading.label, 0)
    sensor_confidence = 0.5   

    try:
        fusion_result = predict_single(
            sensor_class=sensor_class, sensor_confidence=sensor_confidence,
            visual_class=mobile_result["class_index"], visual_confidence=mobile_result["confidence"],
            ear=ear, blink_rate=0.0,
            temperature=reading.temperature or 0.0, humidity=reading.humidity or 0.0,
        )
    except FileNotFoundError:
        fusion_result = {"class": 0, "label": "no_model", "risk": "unknown", "action": "unknown", "confidence": 0.0}

    reading.ear   = ear
    reading.label = fusion_result["label"]
    db.commit()

    # FIX: Push the file write to a non-blocking background thread
    if fusion_result["label"] in ["Over Limit", "Near Limit"]:
        label_slug = fusion_result["label"].replace(" ", "_").lower()
        file_path = os.path.join(SNAPSHOTS_DIR, f"flagged_{reading.id}_{label_slug}.jpg")
        background_tasks.add_task(save_snapshot_task, file_path, frame)

    return {
        "reading_id": reading.id,
        "sensor_class": sensor_class, "sensor_label": reading.label,
        "ear": ear, "eye_status": ear_result["status"], "impaired": ear_result["impaired"],
        "visual_label": mobile_result["label"], "visual_confidence": mobile_result["confidence"],
        "final_label": fusion_result["label"], "final_risk": fusion_result["risk"],
        "final_action": fusion_result["action"], "final_confidence": fusion_result["confidence"],
        "bac": reading.bac, "temperature": reading.temperature, "humidity": reading.humidity,
    }


# ── Full prediction with live MQ3 windows ─────────────────────
@router.post("/live")
async def live_predict(
    background_tasks: BackgroundTasks, # <--- FIX: Injected FastAPI Background Thread Pool
    file: UploadFile = File(...),
    temperature: float = 0.0,
    humidity: float = 0.0,
    mq3_1: str = "", mq3_2: str = "", mq3_3: str = "",
    db: Session = Depends(get_db)
):
    contents = await file.read()
    np_arr   = np.frombuffer(contents, np.uint8)
    frame    = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    if frame is None:
        return {"error": "Invalid image"}

    try:
        w1 = [float(x) for x in mq3_1.split(",") if x.strip()]
        w2 = [float(x) for x in mq3_2.split(",") if x.strip()]
        w3 = [float(x) for x in mq3_3.split(",") if x.strip()]
    except ValueError:
        return {"error": "Invalid MQ3 values — must be comma-separated numbers"}

    ear_result = analyze_frame(frame)
    ear        = ear_result["ear"] or 0.0

    try:
        mobile_result = predict_frame(frame)
    except FileNotFoundError:
        mobile_result = {"label": "no_model", "confidence": 0.0, "class_index": 0}

    sensor_result = run_sensor_ensemble(w1, w2, w3, temperature, humidity)

    try:
        fusion_result = predict_single(
            sensor_class=sensor_result["class"], sensor_confidence=sensor_result["confidence"],
            visual_class=mobile_result["class_index"], visual_confidence=mobile_result["confidence"],
            ear=ear, blink_rate=0.0,
            temperature=temperature, humidity=humidity,
        )
    except FileNotFoundError:
        fusion_result = {"class": 0, "label": "no_model", "risk": "unknown", "action": "unknown", "confidence": 0.0}

    new_reading = Reading(
        temperature=temperature, humidity=humidity, bac=None,
        ear=ear, label=fusion_result["label"], model_used="ensemble_v1",
    )
    db.add(new_reading)
    db.commit()
    db.refresh(new_reading)

    # FIX: Push the file write to a non-blocking background thread
    if fusion_result["label"] in ["Over Limit", "Near Limit"]:
        label_slug = fusion_result["label"].replace(" ", "_").lower()
        file_path = os.path.join(SNAPSHOTS_DIR, f"flagged_{new_reading.id}_{label_slug}.jpg")
        background_tasks.add_task(save_snapshot_task, file_path, frame)

    return {
        "reading_id": new_reading.id,
        "sensor_class": sensor_result["class"], "sensor_label": sensor_result["label"],
        "sensor_confidence": sensor_result["confidence"], "sensor_agreed": sensor_result["agreed"],   
        "rf_class": sensor_result["rf_class"], "xgb_class": sensor_result["xgb_class"],
        "ear": ear, "eye_status": ear_result["status"], "impaired": ear_result["impaired"],
        "proximity": ear_result["proximity"],
        "visual_label": mobile_result["label"], "visual_confidence": mobile_result["confidence"],
        "final_label": fusion_result["label"], "final_risk": fusion_result["risk"],
        "final_action": fusion_result["action"], "final_confidence": fusion_result["confidence"],
        "all_probs": fusion_result.get("all_probs", {}),
        "temperature": temperature, "humidity": humidity,
    }


# ── Re-evaluate from saved reading ────────────────────────────
@router.get("/reading/{reading_id}")
def predict_from_reading(reading_id: int, db: Session = Depends(get_db)):
    reading = db.query(Reading).filter(Reading.id == reading_id).first()
    if not reading:
        return {"error": "Reading not found"}

    sensor_label_map = {"No alcohol": 0, "Breath alcohol": 1, "Sanitizer": 2}
    sensor_class = sensor_label_map.get(reading.label, 0)

    try:
        fusion_result = predict_single(
            sensor_class=sensor_class, sensor_confidence=0.5,
            visual_class=0, visual_confidence=0.0,
            ear=reading.ear or 0.0, blink_rate=0.0,
            temperature=reading.temperature or 0.0, humidity=reading.humidity or 0.0,
        )
    except FileNotFoundError:
        fusion_result = {"label": "no_model", "risk": "unknown", "action": "unknown"}

    return {
        "reading_id": reading_id, "bac": reading.bac, "ear": reading.ear,
        "temperature": reading.temperature, "humidity": reading.humidity,
        "saved_label": reading.label,
        "final_label": fusion_result["label"], "final_risk": fusion_result["risk"],
        "final_action": fusion_result["action"],
    }