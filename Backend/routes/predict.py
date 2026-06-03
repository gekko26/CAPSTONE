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
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOTS_DIR = os.path.join(BASE_DIR, "data", "snapshots")
IMPAIRED_DIR  = os.path.join(SNAPSHOTS_DIR, "impaired")
DROWSY_DIR    = os.path.join(SNAPSHOTS_DIR, "drowsy")

os.makedirs(IMPAIRED_DIR, exist_ok=True)
os.makedirs(DROWSY_DIR, exist_ok=True)

def save_snapshot_task(file_path: str, frame_data: np.ndarray):
    try:
        target_dir = os.path.dirname(file_path)
        os.makedirs(target_dir, exist_ok=True)
        success = cv2.imwrite(file_path, frame_data)
        if success: print(f"✅ [SECURITY] Snapshot written: {file_path}")
    except Exception as e:
        print(f"❌ [CRITICAL ERROR] Failed to write background snapshot: {e}")

def run_sensor_ensemble(window_1, window_2, window_3, temp, humidity):
    try: rf_result = sensor_models.predict(window_1, window_2, window_3, temp, humidity, model_name="random_forest")
    except FileNotFoundError: rf_result = {"class": 0, "confidence": 0.0, "label": "No model"}

    try: xgb_result = sensor_models.predict(window_1, window_2, window_3, temp, humidity, model_name="xgboost")
    except FileNotFoundError: xgb_result = {"class": 0, "confidence": 0.0, "label": "No model"}

    agreed = rf_result["class"] == xgb_result["class"]
    final_class = rf_result["class"]
    confidence = round((rf_result["confidence"] + xgb_result["confidence"]) / 2, 4) if agreed else round(rf_result["confidence"] * 0.7, 4)

    return {"class": final_class, "label": rf_result["label"], "confidence": confidence, "agreed": agreed, "rf_class": rf_result["class"], "xgb_class": xgb_result["class"]}

@router.post("/full")
async def full_predict(background_tasks: BackgroundTasks, file: UploadFile = File(...), reading_id: int = None, db: Session = Depends(get_db)):
    contents = await file.read()
    np_arr = np.frombuffer(contents, np.uint8)
    frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    if frame is None: return {"error": "Invalid image"}
    ear_result = analyze_frame(frame)
    ear = ear_result["ear"] or 0.0

    try: mobile_result = predict_frame(frame)
    except FileNotFoundError: mobile_result = {"label": "no_model", "confidence": 0.0, "class_index": 0}

    reading = db.query(Reading).filter(Reading.id == reading_id).first() if reading_id else db.query(Reading).order_by(Reading.date.desc()).first()
    if not reading: return {"error": "No sensor reading found"}

    sensor_class = {"No alcohol": 0, "Breath alcohol": 1, "Sanitizer": 2}.get(reading.label, 0)
    
    try:
        fusion_result = predict_single(sensor_class=sensor_class, sensor_confidence=0.5, visual_class=mobile_result["class_index"], visual_confidence=mobile_result["confidence"], ear=ear, blink_rate=0.0, temperature=reading.temperature or 0.0, humidity=reading.humidity or 0.0)
    except FileNotFoundError:
        fusion_result = {"class": 0, "label": "no_model", "risk": "unknown", "action": "unknown", "confidence": 0.0}

    reading.ear, reading.label = ear, fusion_result["label"]
    db.commit()

    is_impaired = fusion_result["label"] in ["Over Limit", "Near Limit"]
    is_drowsy = ear_result.get("impaired", False) or ear_result.get("status") in ["Drowsy", "Sleeping"]
    
    if is_impaired or is_drowsy:
        target_dir = IMPAIRED_DIR if is_impaired else DROWSY_DIR
        prefix = "alcohol" if is_impaired else "fatigue"
        file_path = os.path.join(target_dir, f"{prefix}_reading_{reading.id}.jpg")
        background_tasks.add_task(save_snapshot_task, file_path, frame)

    return {"reading_id": reading.id, "sensor_class": sensor_class, "sensor_label": reading.label, "ear": ear, "eye_status": ear_result["status"], "impaired": ear_result["impaired"], "final_label": fusion_result["label"], "final_risk": fusion_result["risk"]}

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

    try: mobile_result = predict_frame(frame)
    except FileNotFoundError: mobile_result = {"label": "no_model", "confidence": 0.0, "class_index": 0}

    sensor_result = run_sensor_ensemble(w1, w2, w3, temperature, humidity)

    try:
        fusion_result = predict_single(sensor_class=sensor_result["class"], sensor_confidence=sensor_result["confidence"], visual_class=mobile_result["class_index"], visual_confidence=mobile_result["confidence"], ear=ear, blink_rate=0.0, temperature=temperature, humidity=humidity)
    except FileNotFoundError:
        fusion_result = {"class": 0, "label": "no_model", "risk": "unknown", "action": "unknown", "confidence": 0.0}

    new_reading = Reading(temperature=temperature, humidity=humidity, bac=None, ear=ear, label=fusion_result["label"], model_used="ensemble_v1")
    db.add(new_reading)
    db.commit()
    db.refresh(new_reading)

    is_impaired = fusion_result["label"] in ["Over Limit", "Near Limit"]
    is_drowsy = ear_result.get("impaired", False) or ear_result.get("status") in ["Drowsy", "Sleeping"]

    if is_impaired or is_drowsy:
        target_dir = IMPAIRED_DIR if is_impaired else DROWSY_DIR
        prefix = "alcohol" if is_impaired else "fatigue"
        file_path = os.path.join(target_dir, f"{prefix}_reading_{new_reading.id}.jpg")
        background_tasks.add_task(save_snapshot_task, file_path, frame)

    return {"reading_id": new_reading.id, "sensor_label": sensor_result["label"], "ear": ear, "eye_status": ear_result["status"], "impaired": ear_result["impaired"], "final_label": fusion_result["label"], "final_risk": fusion_result["risk"]}

@router.get("/reading/{reading_id}")
def predict_from_reading(reading_id: int, db: Session = Depends(get_db)):
    reading = db.query(Reading).filter(Reading.id == reading_id).first()
    if not reading: return {"error": "Reading not found"}
    sensor_class = {"No alcohol": 0, "Breath alcohol": 1, "Sanitizer": 2}.get(reading.label, 0)
    try: fusion_result = predict_single(sensor_class=sensor_class, sensor_confidence=0.5, visual_class=0, visual_confidence=0.0, ear=reading.ear or 0.0, blink_rate=0.0, temperature=reading.temperature or 0.0, humidity=reading.humidity or 0.0)
    except FileNotFoundError: fusion_result = {"label": "no_model", "risk": "unknown", "action": "unknown"}
    return {"reading_id": reading_id, "saved_label": reading.label, "final_label": fusion_result["label"]}