import os
import cv2
import numpy as np
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, File, UploadFile, BackgroundTasks, Form, HTTPException
from sqlalchemy.orm import Session
from database.db import get_db
from database.models import Reading, DeploymentLog
from models.train import sensor_models
from models.train.cv_model import analyze_frame
from models.train.mobilenet import predict_frame
from models.train.fusion_model import predict_single

STALE_SECONDS = 5

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
async def full_predict(background_tasks: BackgroundTasks, file: UploadFile = File(...), reading_id: int = Form(...), db: Session = Depends(get_db)):
    contents = await file.read()
    np_arr = np.frombuffer(contents, np.uint8)
    frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    if frame is None: return {"error": "Invalid image"}
    ear_result = analyze_frame(frame)
    ear = ear_result["ear"] or 0.0

    try: mobile_result = predict_frame(frame)
    except FileNotFoundError: mobile_result = {"label": "no_model", "confidence": 0.0, "class_index": 0}

    reading = db.query(Reading).filter(Reading.id == reading_id).first()
    if not reading:
        return {"error": f"Reading {reading_id} not found", "final_label": "RECAPTURE_NEEDED", "reason": "reading_not_found"}
    # staleness check: reject > STALE_SECONDS old sensor data from different trial
    try:
        created = reading.date
        if created is not None:
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
        pass

    sensor_class = {"No alcohol": 0, "Breath alcohol": 1, "Sanitizer": 2}.get(reading.label, 0)
    bac_tier_stored = getattr(reading, "bac_tier", None)
    
    try:
        fusion_result = predict_single(sensor_class=sensor_class, sensor_confidence=0.5, visual_class=mobile_result["class_index"], visual_confidence=mobile_result["confidence"], ear=ear, blink_rate=0.0, temperature=reading.temperature or 0.0, humidity=reading.humidity or 0.0)
    except FileNotFoundError:
        fusion_result = {"class": 0, "label": "no_model", "risk": "unknown", "action": "unknown", "confidence": 0.0}

    reading.ear = ear
    reading.fusion_label = fusion_result["label"]
    try: reading.denial_reason = fusion_result.get("reason")
    except Exception: pass
    db.commit()

    # FIX 8: Upsert DeploymentLog for this reading_id — dedup vs sensor.py write
    try:
        existing = db.query(DeploymentLog).filter(DeploymentLog.reading_id == reading.id).first()
        if existing:
            existing.prediction = fusion_result["label"]
            existing.confidence = fusion_result.get("confidence")
            existing.risk_level = fusion_result.get("risk")
            existing.denial_reason = fusion_result.get("reason")
            existing.estimated_bac = getattr(reading, 'estimated_bac', None)
            existing.bac_tier = getattr(reading, 'bac_tier', None)
            existing.model_version = "fusion_v1"
            db.commit()
        else:
            # No sensor log yet (e.g., predict/live path) — create minimal log linked to reading
            log = DeploymentLog(
                reading_id=reading.id,
                temperature=reading.temperature or 0, humidity=reading.humidity or 0,
                mq3_1_max=0, mq3_1_avg=0, mq3_1_std=0, mq3_2_max=0, mq3_2_avg=0, mq3_2_std=0,
                mq3_3_max=0, mq3_3_avg=0, mq3_3_std=0, rise_time=0, decay_time=0, spatial_variance=0,
                prediction=fusion_result["label"], confidence=fusion_result.get("confidence"),
                risk_level=fusion_result.get("risk"), denial_reason=fusion_result.get("reason"),
                estimated_bac=getattr(reading, 'estimated_bac', None), bac_tier=getattr(reading, 'bac_tier', None),
                model_version="fusion_v1",
            )
            db.add(log); db.commit()
    except Exception as e:
        print(f"⚠️ [FIX 8] DeploymentLog upsert failed: {e}")

    is_impaired = fusion_result["label"] in ["Over Limit", "Near Limit"] or (bac_tier_stored == "over")
    is_drowsy = ear_result.get("impaired", False) or ear_result.get("status") in ["Drowsy", "Sleeping"]
    
    if is_impaired or is_drowsy:
        target_dir = IMPAIRED_DIR if is_impaired else DROWSY_DIR
        prefix = "alcohol" if is_impaired else "fatigue"
        file_path = os.path.join(target_dir, f"{prefix}_reading_{reading.id}.jpg")
        background_tasks.add_task(save_snapshot_task, file_path, frame)

    return {"reading_id": reading.id, "sensor_class": sensor_class, "sensor_label": reading.label, "ear": ear, "eye_status": ear_result["status"], "impaired": ear_result["impaired"], "final_label": fusion_result["label"], "final_risk": fusion_result["risk"], "denial_reason": fusion_result.get("reason"), "bac_driven": fusion_result.get("bac_driven"), "fatigue_driven": fusion_result.get("fatigue_driven")}

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

    # PH BAC estimate inside breath alcohol only
    bac_est = None
    bac_tier = None
    if sensor_result.get("class") == 1:
        try:
            est = sensor_models.predict_bac(w1, w2, w3, temperature, humidity)
            bac_est = est.get("estimated_bac")
            bac_tier = est.get("tier")
        except Exception:
            pass

    try:
        fusion_result = predict_single(sensor_class=sensor_result["class"], sensor_confidence=sensor_result["confidence"], visual_class=mobile_result["class_index"], visual_confidence=mobile_result["confidence"], ear=ear, blink_rate=0.0, temperature=temperature, humidity=humidity)
    except FileNotFoundError:
        fusion_result = {"class": 0, "label": "no_model", "risk": "unknown", "action": "unknown", "confidence": 0.0}

    new_reading = Reading(temperature=temperature, humidity=humidity, bac=None, estimated_bac=bac_est, bac_tier=bac_tier, ear=ear, label=sensor_result.get("label"), fusion_label=fusion_result["label"], denial_reason=fusion_result.get("reason"), model_used="ensemble_v1")
    db.add(new_reading)
    db.commit()
    db.refresh(new_reading)
    # FIX 8: one DeploymentLog per reading_id (dedup live path)
    try:
        log = DeploymentLog(
            reading_id=new_reading.id,
            temperature=temperature, humidity=humidity,
            mq3_1_max=0, mq3_1_avg=0, mq3_1_std=0, mq3_2_max=0, mq3_2_avg=0, mq3_2_std=0,
            mq3_3_max=0, mq3_3_avg=0, mq3_3_std=0, rise_time=0, decay_time=0, spatial_variance=0,
            prediction=fusion_result["label"], confidence=fusion_result.get("confidence"),
            risk_level=fusion_result.get("risk"), denial_reason=fusion_result.get("reason"),
            estimated_bac=bac_est, bac_tier=bac_tier, model_version="fusion_v1",
        )
        db.add(log); db.commit()
    except Exception as e:
        print(f"⚠️ [FIX 8] live DeploymentLog failed: {e}")

    is_impaired = fusion_result["label"] in ["Over Limit", "Near Limit"] or (bac_tier == "over")
    is_drowsy = ear_result.get("impaired", False) or ear_result.get("status") in ["Drowsy", "Sleeping"]
    
    if is_impaired or is_drowsy:
        target_dir = IMPAIRED_DIR if is_impaired else DROWSY_DIR
        prefix = "alcohol" if is_impaired else "fatigue"
        file_path = os.path.join(target_dir, f"{prefix}_reading_{new_reading.id}.jpg")
        background_tasks.add_task(save_snapshot_task, file_path, frame)

    return {"reading_id": new_reading.id, "sensor_label": sensor_result["label"], "ear": ear, "eye_status": ear_result["status"], "impaired": ear_result["impaired"], "final_label": fusion_result["label"], "final_risk": fusion_result["risk"], "denial_reason": fusion_result.get("reason"), "bac_driven": fusion_result.get("bac_driven"), "fatigue_driven": fusion_result.get("fatigue_driven"), "estimated_bac": bac_est, "bac_tier": bac_tier, "ph_verdict": "PH FAIL" if bac_tier=="over" else "PH PASS" if bac_tier else None}

@router.get("/reading/{reading_id}")
def predict_from_reading(reading_id: int, db: Session = Depends(get_db)):
    reading = db.query(Reading).filter(Reading.id == reading_id).first()
    if not reading: return {"error": "Reading not found"}
    sensor_class = {"No alcohol": 0, "Breath alcohol": 1, "Sanitizer": 2}.get(reading.label, 0)
    try: fusion_result = predict_single(sensor_class=sensor_class, sensor_confidence=0.5, visual_class=0, visual_confidence=0.0, ear=reading.ear or 0.0, blink_rate=0.0, temperature=reading.temperature or 0.0, humidity=reading.humidity or 0.0)
    except FileNotFoundError: fusion_result = {"label": "no_model", "risk": "unknown", "action": "unknown"}
    return {"reading_id": reading_id, "saved_label": reading.label, "final_label": fusion_result["label"]}