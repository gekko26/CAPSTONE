from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, Form, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from database.db import get_db
from database.models import TrainingData
from models.train import sensor_models
from models.train.cv_model import analyze_frame
from pydantic import BaseModel
from typing import List, Optional
import numpy as np
import cv2
import os
import csv
import io
import httpx                  
import requests               
from dotenv import load_dotenv

load_dotenv()

router = APIRouter(prefix="/training", tags=["Training"])

ESP32_URL = os.getenv("ESP32_URL", "http://192.168.69.18")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOTS_DIR = os.path.join(BASE_DIR, "data", "snapshots")
IMPAIRED_DIR  = os.path.join(SNAPSHOTS_DIR, "impaired")
DROWSY_DIR    = os.path.join(SNAPSHOTS_DIR, "drowsy")
SOBER_DIR     = os.path.join(SNAPSHOTS_DIR, "sober")

os.makedirs(IMPAIRED_DIR, exist_ok=True)
os.makedirs(DROWSY_DIR, exist_ok=True)
os.makedirs(SOBER_DIR, exist_ok=True)

LABEL_NAMES = {-1: "Pending", 0: "No alcohol", 1: "Breath alcohol", 2: "Sanitizer"}

def save_snapshot_task(file_path: str, frame_data: np.ndarray):
    try:
        target_dir = os.path.dirname(file_path)
        os.makedirs(target_dir, exist_ok=True)
        cv2.imwrite(file_path, frame_data)
        print(f"✅ [TRAINING STORAGE] Snapshot written to disk: {file_path}")
    except Exception as e:
        print(f"❌ [STORAGE CRITICAL] Background disk save failure: {e}")

def trigger_esp32() -> dict:
    try:
        r = requests.post(f"{ESP32_URL}/trigger", timeout=3)
        return {"triggered": True, "status": r.status_code}
    except:
        return {"triggered": False, "error": "ESP32 offline"}

async def trigger_esp32_async() -> dict:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{ESP32_URL}/trigger", timeout=3.0)
        return {"triggered": True, "status": r.status_code}
    except:
        return {"triggered": False, "error": "ESP32 offline"}

def decode_frame(contents: bytes):
    np_arr = np.frombuffer(contents, np.uint8)
    frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    return frame

def build_pending_row(label: int, sub_label: str = None, bac: float = None) -> TrainingData:
    return TrainingData(label=label, sub_label=sub_label, bac=bac)

@router.post("/collect/clear_air")
def collect_clear_air(db: Session = Depends(get_db)):
    trigger_result = trigger_esp32()
    row = build_pending_row(label=0, sub_label="clear_air", bac=0.00)
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"message": "Clear air baseline triggered", "id": row.id, "trigger": trigger_result}

@router.post("/collect/sober")
async def collect_sober(background_tasks: BackgroundTasks, file: UploadFile = File(...), db: Session = Depends(get_db)):
    contents = await file.read()
    frame = decode_frame(contents)
    row = build_pending_row(label=0, sub_label=None, bac=0.00)
    db.add(row); db.commit(); db.refresh(row)
    if frame is not None:
        background_tasks.add_task(save_snapshot_task, os.path.join(SOBER_DIR, f"sober_{row.id}.jpg"), frame)
    trigger_result = await trigger_esp32_async()
    return {"message": "Sober baseline captured", "id": row.id, "trigger": trigger_result}

@router.post("/collect/drowsy")
async def collect_drowsy(background_tasks: BackgroundTasks, file: UploadFile = File(...), db: Session = Depends(get_db)):
    contents = await file.read()
    frame = decode_frame(contents)
    row = build_pending_row(label=0, sub_label="drowsy", bac=0.00)
    db.add(row); db.commit(); db.refresh(row)
    if frame is not None:
        background_tasks.add_task(save_snapshot_task, os.path.join(DROWSY_DIR, f"drowsy_{row.id}.jpg"), frame)
    trigger_result = await trigger_esp32_async()
    return {"message": "Drowsy baseline captured", "id": row.id, "trigger": trigger_result}

@router.post("/collect/alcohol")
async def collect_alcohol(background_tasks: BackgroundTasks, file: UploadFile = File(...), bac: float = Form(...), db: Session = Depends(get_db)):
    contents = await file.read()
    frame = decode_frame(contents)
    row = build_pending_row(label=1, sub_label=None, bac=bac)
    db.add(row); db.commit(); db.refresh(row)
    if frame is not None:
        background_tasks.add_task(save_snapshot_task, os.path.join(IMPAIRED_DIR, f"impaired_{row.id}.jpg"), frame)
    trigger_result = await trigger_esp32_async()
    return {"message": "Alcohol captured", "id": row.id, "trigger": trigger_result}

@router.post("/collect/sanitizer")
def collect_sanitizer(db: Session = Depends(get_db)):
    trigger_result = trigger_esp32()
    row = build_pending_row(label=2, sub_label="sanitizer", bac=0.00)
    db.add(row); db.commit(); db.refresh(row)
    return {"message": "Sanitizer context triggered", "id": row.id, "trigger": trigger_result}

@router.post("/collect/perfume")
async def collect_perfume(background_tasks: BackgroundTasks, file: UploadFile = File(...), db: Session = Depends(get_db)):
    contents = await file.read()
    frame = decode_frame(contents)
    row = build_pending_row(label=2, sub_label="perfume", bac=0.00)
    db.add(row); db.commit(); db.refresh(row)
    if frame is not None:
        background_tasks.add_task(save_snapshot_task, os.path.join(IMPAIRED_DIR, f"impaired_perfume_{row.id}.jpg"), frame)
    trigger_result = await trigger_esp32_async()
    return {"message": "Perfume vapor captured", "id": row.id, "trigger": trigger_result}

class SensorPayload(BaseModel):
    temperature: float; humidity: float; mq3_1: List[float]; mq3_2: List[float]; mq3_3: List[float]; row_id: Optional[int] = None

@router.post("/collect/sensor-data")
def receive_sensor_data(data: SensorPayload, db: Session = Depends(get_db)):
    features = sensor_models.extract_features(data.mq3_1, data.mq3_2, data.mq3_3, data.temperature, data.humidity)
    row = db.query(TrainingData).filter(TrainingData.id == data.row_id).first() if data.row_id else db.query(TrainingData).filter(TrainingData.mq3_1_max == None).order_by(TrainingData.date.desc()).first()
    if not row:
        row = TrainingData(label=-1)
        db.add(row)
    row.mq3_1_max, row.mq3_1_avg, row.mq3_1_std = features[0], features[1], features[2]
    row.mq3_2_max, row.mq3_2_avg, row.mq3_2_std = features[3], features[4], features[5]
    row.mq3_3_max, row.mq3_3_avg, row.mq3_3_std = features[6], features[7], features[8]
    row.rise_time, row.decay_time = features[9], features[10]
    row.spatial_variance, row.spatial_variance_avg = features[11], features[12]
    row.temperature, row.humidity = features[13], features[14]
    db.commit()
    return {"message": "Features synced", "id": row.id}

class LabelPayload(BaseModel):
    bac: float; is_sanitizer: bool = False

@router.patch("/label/{row_id}")
def attach_label(row_id: int, payload: LabelPayload, db: Session = Depends(get_db)):
    row = db.query(TrainingData).filter(TrainingData.id == row_id).first()
    if not row: raise HTTPException(status_code=404, detail="Not found")
    if row.label == 1:
        row.bac = payload.bac
        db.commit()
        return {"id": row.id, "label": row.label, "label_name": LABEL_NAMES[row.label]}
    stored = [row.mq3_1_max, row.mq3_1_avg, row.mq3_1_std, row.mq3_2_max, row.mq3_2_avg, row.mq3_2_std, row.mq3_3_max, row.mq3_3_avg, row.mq3_3_std, row.rise_time, row.decay_time, row.spatial_variance, row.spatial_variance_avg, row.temperature, row.humidity]
    label = sensor_models.assign_label(payload.bac, payload.is_sanitizer, features=stored)
    row.bac, row.label = payload.bac, label
    db.commit()
    return {"id": row.id, "label": label, "label_name": LABEL_NAMES[label]}

class RelabelPayload(BaseModel):
    label: int; sub_label: Optional[str] = None

@router.patch("/relabel/{row_id}")
def relabel(row_id: int, payload: RelabelPayload, db: Session = Depends(get_db)):
    row = db.query(TrainingData).filter(TrainingData.id == row_id).first()
    if not row: raise HTTPException(status_code=404, detail="Not found")
    row.label = payload.label
    if payload.sub_label: row.sub_label = payload.sub_label
    db.commit()
    return {"id": row.id, "new_label": LABEL_NAMES[payload.label]}

@router.delete("/delete/{row_id}")
def delete_row(row_id: int, db: Session = Depends(get_db)):
    row = db.query(TrainingData).filter(TrainingData.id == row_id).first()
    if not row: raise HTTPException(status_code=404, detail="Not found")
    db.delete(row); db.commit()
    return {"message": "deleted"}

@router.get("/data")
def get_training_data(db: Session = Depends(get_db)):
    return db.query(TrainingData).order_by(TrainingData.date.asc()).all()

@router.get("/summary")
def training_summary(db: Session = Depends(get_db)):
    rows = db.query(TrainingData).all()
    counts = {-1:0, 0:0, 1:0, 2:0}
    for r in rows: counts[r.label] = counts.get(r.label, 0) + 1
    face_counts = {
        "sober": len(os.listdir(SOBER_DIR)) if os.path.exists(SOBER_DIR) else 0,
        "drowsy": len(os.listdir(DROWSY_DIR)) if os.path.exists(DROWSY_DIR) else 0,
        "impaired": len(os.listdir(IMPAIRED_DIR)) if os.path.exists(IMPAIRED_DIR) else 0
    }
    return {"total": len(rows), "pending": counts[-1], "no_alcohol": counts[0], "breath_alcohol": counts[1], "sanitizer": counts[2], "face_images": face_counts}

@router.get("/export")
def export_csv(db: Session = Depends(get_db)):
    rows = db.query(TrainingData).filter(TrainingData.label >= 0).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "label", "bac", "mq3_1_max", "temperature", "humidity"])
    for r in rows: writer.writerow([r.id, r.label, r.bac, r.mq3_1_max, r.temperature, r.humidity])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=training_data.csv"})

@router.post("/train")
def trigger_training(db: Session = Depends(get_db)):
    rows = db.query(TrainingData).filter(TrainingData.label >= 0).all()
    X = [[r.mq3_1_max, r.mq3_1_avg, r.mq3_1_std, r.mq3_2_max, r.mq3_2_avg, r.mq3_2_std, r.mq3_3_max, r.mq3_3_avg, r.mq3_3_std, r.rise_time, r.decay_time, r.spatial_variance, r.spatial_variance_avg, r.temperature, r.humidity] for r in rows if r.mq3_1_max is not None]
    y = [r.label for r in rows if r.mq3_1_max is not None]
    return {"message": "Training running", "samples": len(X), "results": sensor_models.train(X, y)}