#training.py

from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, Form
from fastapi.responses import StreamingResponse, Response
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
import time
import httpx                  # FIX: replaced requests with httpx for async-safe HTTP
import requests               # kept only for sync endpoints (collect_sober, collect_sanitizer)
from dotenv import load_dotenv

load_dotenv()

router = APIRouter(prefix="/training", tags=["Training"])

ESP32_URL = os.getenv("ESP32_URL", "http://192.168.69.18")
FACES_DIR = os.path.join(os.path.dirname(__file__), "../data/faces")

LABEL_NAMES = {
    -1: "Pending",
    0:  "No alcohol",
    1:  "Breath alcohol",
    2:  "Sanitizer",
}


# ── Helpers ───────────────────────────────────────────────────

def trigger_esp32() -> dict:
    """
    SYNC version — used by collect_sober and collect_sanitizer (sync endpoints).
    Safe to call from regular def functions.
    """
    try:
        r = requests.post(f"{ESP32_URL}/trigger", timeout=3)
        return {"triggered": True, "esp32_status": r.status_code}
    except requests.exceptions.ConnectionError:
        return {"triggered": False, "error": "ESP32 unreachable"}
    except requests.exceptions.Timeout:
        return {"triggered": False, "error": "ESP32 timeout"}


async def trigger_esp32_async() -> dict:
    """
    ASYNC version — used by collect_alcohol and collect_perfume (async endpoints).
    FIX: Using httpx async client prevents blocking the FastAPI event loop.
    Previously using sync requests.post() inside async def caused the deadlock —
    the event loop was blocked for up to 3s, queuing up ESP32 sensor-data posts
    and camera analyze requests until they timed out.
    """
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{ESP32_URL}/trigger", timeout=3.0)
        return {"triggered": True, "esp32_status": r.status_code}
    except httpx.ConnectError:
        return {"triggered": False, "error": "ESP32 unreachable"}
    except httpx.TimeoutException:
        return {"triggered": False, "error": "ESP32 timeout"}


def decode_frame(contents: bytes):
    """Decode uploaded JPEG bytes to OpenCV frame."""
    np_arr = np.frombuffer(contents, np.uint8)
    frame  = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    return frame if frame is not None else None


def save_face(frame, folder: str) -> str:
    """Save frame to MobileNet training folder. Returns path."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{int(time.time())}.jpg")
    cv2.imwrite(path, frame)
    return path


def build_pending_row(label: int, sub_label: str = None, bac: float = None) -> TrainingData:
    """Create a TrainingData row with no sensor features yet (pending ESP32 post)."""
    return TrainingData(
        label                = label,
        sub_label            = sub_label,
        bac                  = bac,
        mq3_1_max            = None,
        mq3_1_avg            = None,
        mq3_1_std            = None,
        mq3_2_max            = None,
        mq3_2_avg            = None,
        mq3_2_std            = None,
        mq3_3_max            = None,
        mq3_3_avg            = None,
        mq3_3_std            = None,
        rise_time            = None,
        decay_time           = None,
        spatial_variance     = None,
        spatial_variance_avg = None,
        temperature          = None,
        humidity             = None,
        confidence           = None,
    )


# ═════════════════════════════════════════════════════════════
# 4 Dedicated Collection Endpoints
# ═════════════════════════════════════════════════════════════

# ── Label 0 — Sober (manual, no camera, no BAC) ───────────────
@router.post("/collect/sober")
def collect_sober(db: Session = Depends(get_db)):
    """
    Manual trigger for sober event.
    Uses sync trigger_esp32() — safe because this is a sync def endpoint.

    Flow:
      1. Operator clicks "Trigger Sober" in UI
      2. Backend triggers ESP32 to start buffering MQ3
      3. Person stands/walks through gate normally
      4. ESP32 posts sensor data to /training/collect/sensor-data
      5. Row auto-labeled 0 (no alcohol), sub_label=NULL
    """
    trigger_result = trigger_esp32()

    row = build_pending_row(label=0, sub_label=None, bac=0.00)
    db.add(row)
    db.commit()
    db.refresh(row)

    return {
        "message":    "Sober collection triggered — waiting for ESP32 sensor data",
        "id":         row.id,
        "label":      0,
        "label_name": "No alcohol",
        "sub_label":  None,
        "trigger":    trigger_result,
    }


# ── Label 1 — Breath alcohol (camera, BAC required) ───────────
@router.post("/collect/alcohol")
async def collect_alcohol(
    file: UploadFile = File(...),
    bac:  float      = Form(...),
    db:   Session    = Depends(get_db),
):
    """
    Camera-triggered alcohol event.
    FIX: Uses async trigger_esp32_async() to avoid blocking the event loop.

    Flow:
      1. Frontend polls C200C RTSP stream
      2. is_close=True detected → frontend captures frame
      3. Frontend POSTs frame here with BAC value from breathalyzer
      4. Frame saved to faces/impaired/ for MobileNet training
      5. Row created as label=1, sub_label=NULL
      6. ESP32 trigger sent async — does NOT block camera analyze loop
      7. ESP32 posts sensor data to /training/collect/sensor-data to fill features

    IMPORTANT: drunk people frequently look drowsy via EAR.
    Image is ALWAYS saved to faces/impaired/ regardless of EAR status.
    Never redirect alcohol captures to faces/drowsy/ — it corrupts MobileNet data.
    """
    if bac is None or bac < 0:
        raise HTTPException(
            status_code=400,
            detail="BAC is required for alcohol events. Read from breathalyzer and enter."
        )

    contents   = await file.read()
    frame      = decode_frame(contents)
    image_path = None
    ear_result = None

    if frame is not None:
        ear_result = analyze_frame(frame)
        is_close   = ear_result.get("is_close", False)

        if is_close:
            # ALWAYS save to impaired/ for alcohol events
            # Never redirect to drowsy/ even if EAR says drowsy
            image_path = save_face(frame, os.path.join(FACES_DIR, "impaired"))

    # FIX: await async trigger — event loop stays free during the HTTP call
    trigger_result = await trigger_esp32_async()

    row = build_pending_row(label=1, sub_label=None, bac=bac)
    db.add(row)
    db.commit()
    db.refresh(row)

    return {
        "message":     "Alcohol event captured — ESP32 sensor data incoming",
        "id":          row.id,
        "label":       1,
        "label_name":  "Breath alcohol",
        "sub_label":   None,
        "bac":         bac,
        "image_saved": image_path,
        "ear_status":  ear_result["status"] if ear_result else None,
        "is_close":    ear_result.get("is_close") if ear_result else None,
        "trigger":     trigger_result,
    }


# ── Label 2a — Sanitizer / rubbing alcohol (manual, no camera) ─
@router.post("/collect/sanitizer")
def collect_sanitizer(db: Session = Depends(get_db)):
    """
    Manual trigger for direct sanitizer / rubbing alcohol spray event.
    Uses sync trigger_esp32() — safe because this is a sync def endpoint.

    Flow:
      1. Operator clicks "Trigger Sanitizer" in UI
      2. Backend triggers ESP32 to start buffering MQ3
      3. Operator sprays rubbing alcohol / sanitizer near sensors
      4. ESP32 posts sensor data to /training/collect/sensor-data
      5. Row auto-labeled 2, sub_label='sanitizer'
    """
    trigger_result = trigger_esp32()

    row = build_pending_row(label=2, sub_label="sanitizer", bac=0.00)
    db.add(row)
    db.commit()
    db.refresh(row)

    return {
        "message":    "Sanitizer collection triggered — spray near sensors now",
        "id":         row.id,
        "label":      2,
        "label_name": "Sanitizer",
        "sub_label":  "sanitizer",
        "trigger":    trigger_result,
        "next_step":  "Spray rubbing alcohol or hand sanitizer near sensors now",
    }


# ── Label 2b — Perfume / cologne (camera, no BAC) ─────────────
@router.post("/collect/perfume")
async def collect_perfume(
    file: UploadFile = File(...),
    db:   Session    = Depends(get_db),
):
    """
    Camera-triggered perfume / cologne event.
    FIX: Uses async trigger_esp32_async() to avoid blocking the event loop.

    Flow:
      1. Frontend polls C200C RTSP stream
      2. is_close=True detected → frontend captures frame
      3. Frontend POSTs frame here (no BAC needed)
      4. Row created as label=2, sub_label='perfume'
      5. ESP32 trigger sent async — does NOT block camera analyze loop
      6. ESP32 posts sensor data to /training/collect/sensor-data
    """
    contents   = await file.read()
    frame      = decode_frame(contents)
    ear_result = None

    if frame is not None:
        ear_result = analyze_frame(frame)

    # FIX: await async trigger — event loop stays free during the HTTP call
    trigger_result = await trigger_esp32_async()

    row = build_pending_row(label=2, sub_label="perfume", bac=0.00)
    db.add(row)
    db.commit()
    db.refresh(row)

    return {
        "message":    "Perfume event captured — ESP32 sensor data incoming",
        "id":         row.id,
        "label":      2,
        "label_name": "Sanitizer",
        "sub_label":  "perfume",
        "ear_status": ear_result["status"] if ear_result else None,
        "is_close":   ear_result.get("is_close") if ear_result else None,
        "trigger":    trigger_result,
    }


# ── ESP32 sensor data receiver ─────────────────────────────────
class SensorPayload(BaseModel):
    temperature: float
    humidity:    float
    mq3_1:       List[float]
    mq3_2:       List[float]
    mq3_3:       List[float]
    row_id:      Optional[int] = None


@router.post("/collect/sensor-data")
def receive_sensor_data(data: SensorPayload, db: Session = Depends(get_db)):
    """
    ESP32 posts MQ3 window here after being triggered.
    Fills sensor features into the pending row.

    If row_id provided → fills that specific row.
    If no row_id → fills most recent row with null sensor features.

    15 features (correct index mapping):
      [0]  mq3_1_max
      [1]  mq3_1_avg
      [2]  mq3_1_std
      [3]  mq3_2_max
      [4]  mq3_2_avg
      [5]  mq3_2_std
      [6]  mq3_3_max
      [7]  mq3_3_avg
      [8]  mq3_3_std
      [9]  rise_time
      [10] decay_time
      [11] spatial_variance_max
      [12] spatial_variance_avg
      [13] temperature
      [14] humidity
    """
    if not (len(data.mq3_1) == len(data.mq3_2) == len(data.mq3_3)):
        raise HTTPException(status_code=400, detail="MQ3 windows must all have same length")

    if len(data.mq3_1) < 5:
        raise HTTPException(
            status_code=400,
            detail=f"Window too short: {len(data.mq3_1)} samples, need at least 5"
        )

    features = sensor_models.extract_features(
        data.mq3_1, data.mq3_2, data.mq3_3,
        data.temperature, data.humidity,
    )

    if features is None:
        raise HTTPException(status_code=400, detail="Feature extraction failed")

    if data.row_id:
        row = db.query(TrainingData).filter(TrainingData.id == data.row_id).first()
        if not row:
            raise HTTPException(status_code=404, detail=f"Row {data.row_id} not found")
    else:
        row = (
            db.query(TrainingData)
            .filter(TrainingData.mq3_1_max == None)
            .order_by(TrainingData.date.desc())
            .first()
        )
        if not row:
            row = TrainingData(label=-1, sub_label=None)
            db.add(row)

    row.mq3_1_max            = features[0]
    row.mq3_1_avg            = features[1]
    row.mq3_1_std            = features[2]
    row.mq3_2_max            = features[3]
    row.mq3_2_avg            = features[4]
    row.mq3_2_std            = features[5]
    row.mq3_3_max            = features[6]
    row.mq3_3_avg            = features[7]
    row.mq3_3_std            = features[8]
    row.rise_time            = features[9]
    row.decay_time           = features[10]
    row.spatial_variance     = features[11]
    row.spatial_variance_avg = features[12]
    row.temperature          = features[13]
    row.humidity             = features[14]

    db.commit()
    db.refresh(row)

    return {
        "message":   "Sensor features saved",
        "id":        row.id,
        "label":     row.label,
        "sub_label": row.sub_label,
        "features": {
            "mq3_1":                {"max": features[0], "avg": features[1], "std": features[2]},
            "mq3_2":                {"max": features[3], "avg": features[4], "std": features[5]},
            "mq3_3":                {"max": features[6], "avg": features[7], "std": features[8]},
            "rise_time":            features[9],
            "decay_time":           features[10],
            "spatial_variance_max": features[11],
            "spatial_variance_avg": features[12],
            "temperature":          features[13],
            "humidity":             features[14],
        },
    }


# ═════════════════════════════════════════════════════════════
# ORIGINAL Endpoints (kept + updated for sub_label)
# ═════════════════════════════════════════════════════════════

@router.post("/collect")
def collect_sensor(data: SensorPayload, db: Session = Depends(get_db)):
    """
    Legacy endpoint — ESP32 posts MQ3 window directly.
    Kept for backward compatibility with old firmware.
    """
    if not (len(data.mq3_1) == len(data.mq3_2) == len(data.mq3_3)):
        raise HTTPException(
            status_code=400,
            detail="MQ3 windows must all have the same number of readings"
        )
    if len(data.mq3_1) < 5:
        raise HTTPException(
            status_code=400,
            detail=f"Window too short: {len(data.mq3_1)} samples, need at least 5"
        )

    features = sensor_models.extract_features(
        data.mq3_1, data.mq3_2, data.mq3_3,
        data.temperature, data.humidity,
    )
    if features is None:
        raise HTTPException(status_code=400, detail="Empty sensor windows")

    row = TrainingData(
        mq3_1_max            = features[0],
        mq3_1_avg            = features[1],
        mq3_1_std            = features[2],
        mq3_2_max            = features[3],
        mq3_2_avg            = features[4],
        mq3_2_std            = features[5],
        mq3_3_max            = features[6],
        mq3_3_avg            = features[7],
        mq3_3_std            = features[8],
        rise_time            = features[9],
        decay_time           = features[10],
        spatial_variance     = features[11],
        spatial_variance_avg = features[12],
        temperature          = features[13],
        humidity             = features[14],
        bac                  = None,
        label                = -1,
        sub_label            = None,
        confidence           = None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return {
        "message": "Sensor data saved — awaiting label",
        "id":      row.id,
    }


@router.post("/session")
async def training_session(
    file:        UploadFile = File(...),
    temperature: float      = Form(...),
    humidity:    float      = Form(...),
    mq3_1:       str        = Form(...),
    mq3_2:       str        = Form(...),
    mq3_3:       str        = Form(...),
    event_type:  str        = Form(...),
    db:          Session    = Depends(get_db),
):
    """Legacy combined session endpoint. Kept for compatibility."""
    if event_type not in ["sober", "alcohol", "sanitizer"]:
        raise HTTPException(status_code=400, detail="event_type must be sober, alcohol, or sanitizer")

    try:
        w1 = [float(x) for x in mq3_1.split(",") if x.strip()]
        w2 = [float(x) for x in mq3_2.split(",") if x.strip()]
        w3 = [float(x) for x in mq3_3.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid MQ3 values")

    if not (len(w1) == len(w2) == len(w3)) or len(w1) < 5:
        raise HTTPException(status_code=400, detail="Invalid MQ3 window sizes")

    contents   = await file.read()
    np_arr     = np.frombuffer(contents, np.uint8)
    frame      = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    image_path = None
    ear_result = None
    is_close   = None

    if frame is not None and event_type in ["sober", "alcohol"]:
        ear_result = analyze_frame(frame)
        ear_status = ear_result.get("status", "normal")
        is_close   = ear_result.get("is_close", False)

        if is_close:
            if event_type == "sober" and ear_status == "drowsy":
                folder = os.path.join(FACES_DIR, "drowsy")
            elif event_type == "alcohol":
                folder = os.path.join(FACES_DIR, "impaired")
            else:
                folder = os.path.join(FACES_DIR, "sober")

            image_path = save_face(frame, folder)

    features = sensor_models.extract_features(w1, w2, w3, temperature, humidity)
    if features is None:
        raise HTTPException(status_code=400, detail="Feature extraction failed")

    row = TrainingData(
        mq3_1_max            = features[0],
        mq3_1_avg            = features[1],
        mq3_1_std            = features[2],
        mq3_2_max            = features[3],
        mq3_2_avg            = features[4],
        mq3_2_std            = features[5],
        mq3_3_max            = features[6],
        mq3_3_avg            = features[7],
        mq3_3_std            = features[8],
        rise_time            = features[9],
        decay_time           = features[10],
        spatial_variance     = features[11],
        spatial_variance_avg = features[12],
        temperature          = features[13],
        humidity             = features[14],
        bac                  = None,
        label                = -1,
        sub_label            = None,
        confidence           = None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return {
        "message":    "Training session saved",
        "id":         row.id,
        "event_type": event_type,
        "image_saved": image_path,
        "ear_status":  ear_result["status"] if ear_result else None,
        "is_close":    is_close,
        "next_step":  f"PATCH /training/label/{row.id} with BAC reading",
    }


# ── Label ─────────────────────────────────────────────────────
class LabelPayload(BaseModel):
    bac:          float
    is_sanitizer: bool = False


@router.patch("/label/{row_id}")
def attach_label(row_id: int, payload: LabelPayload, db: Session = Depends(get_db)):
    """
    Operator submits BAC from breathalyzer.
    Label auto-assigned based on BAC + sanitizer flag + sensor pattern.
    Only works on pending rows (label = -1).
    """
    row = db.query(TrainingData).filter(TrainingData.id == row_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Row not found")
    if row.label != -1:
        raise HTTPException(
            status_code=400,
            detail=f"Already labeled as '{LABEL_NAMES[row.label]}'. Use PATCH /training/relabel/{row_id}."
        )

    stored_features = [
        row.mq3_1_max, row.mq3_1_avg, row.mq3_1_std,
        row.mq3_2_max, row.mq3_2_avg, row.mq3_2_std,
        row.mq3_3_max, row.mq3_3_avg, row.mq3_3_std,
        row.rise_time,            row.decay_time,
        row.spatial_variance,     row.spatial_variance_avg,
        row.temperature,          row.humidity,
    ]

    label = sensor_models.assign_label(
        payload.bac,
        payload.is_sanitizer,
        features=stored_features,
    )

    row.bac   = payload.bac
    row.label = label
    db.commit()
    db.refresh(row)

    return {
        "message":                 "Label assigned",
        "id":                      row.id,
        "bac":                     row.bac,
        "label":                   label,
        "label_name":              LABEL_NAMES[label],
        "auto_detected_sanitizer": label == 2 and not payload.is_sanitizer,
    }


# ── Relabel ───────────────────────────────────────────────────
class RelabelPayload(BaseModel):
    label:     int
    reason:    Optional[str] = None
    sub_label: Optional[str] = None


@router.patch("/relabel/{row_id}")
def relabel(row_id: int, payload: RelabelPayload, db: Session = Depends(get_db)):
    """Correct a mislabeled row. label must be 0, 1, or 2."""
    if payload.label not in [0, 1, 2]:
        raise HTTPException(
            status_code=400,
            detail="Label must be 0 (no alcohol), 1 (breath alcohol), or 2 (sanitizer)"
        )

    row = db.query(TrainingData).filter(TrainingData.id == row_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Row not found")
    if row.label == -1:
        raise HTTPException(
            status_code=400,
            detail="Row not yet labeled. Use PATCH /training/label/{row_id} first."
        )

    old_label = row.label
    row.label = payload.label
    if payload.sub_label is not None:
        row.sub_label = payload.sub_label

    db.commit()

    return {
        "message":   "Label corrected",
        "id":        row.id,
        "old_label": LABEL_NAMES[old_label],
        "new_label": LABEL_NAMES[payload.label],
        "sub_label": row.sub_label,
        "reason":    payload.reason,
    }


# ── Delete ────────────────────────────────────────────────────
@router.delete("/delete/{row_id}")
def delete_row(row_id: int, db: Session = Depends(get_db)):
    """Remove a corrupt or bad row from training data."""
    row = db.query(TrainingData).filter(TrainingData.id == row_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Row not found")
    db.delete(row)
    db.commit()
    return {"message": f"Row {row_id} deleted"}


# ── Data table ────────────────────────────────────────────────
@router.get("/data")
def get_training_data(db: Session = Depends(get_db)):
    """Returns all training rows as a datasheet including sub_label."""
    rows = db.query(TrainingData).order_by(TrainingData.date.asc()).all()
    return [
        {
            "id":                   r.id,
            "date":                 r.date,
            "label":                r.label,
            "label_name":           LABEL_NAMES.get(r.label, "Unknown"),
            "sub_label":            r.sub_label,
            "bac":                  r.bac,
            "mq3_1_max":            r.mq3_1_max,
            "mq3_1_avg":            r.mq3_1_avg,
            "mq3_1_std":            r.mq3_1_std,
            "mq3_2_max":            r.mq3_2_max,
            "mq3_2_avg":            r.mq3_2_avg,
            "mq3_2_std":            r.mq3_2_std,
            "mq3_3_max":            r.mq3_3_max,
            "mq3_3_avg":            r.mq3_3_avg,
            "mq3_3_std":            r.mq3_3_std,
            "rise_time":            r.rise_time,
            "decay_time":           r.decay_time,
            "spatial_variance_max": r.spatial_variance,
            "spatial_variance_avg": r.spatial_variance_avg,
            "temperature":          r.temperature,
            "humidity":             r.humidity,
        }
        for r in rows
    ]


# ── Summary ───────────────────────────────────────────────────
@router.get("/summary")
def training_summary(db: Session = Depends(get_db)):
    """Class distribution with sub_label breakdown for label 2."""
    rows   = db.query(TrainingData).all()
    counts = {-1: 0, 0: 0, 1: 0, 2: 0}
    sub_counts = {"sanitizer": 0, "perfume": 0}

    for r in rows:
        counts[r.label] = counts.get(r.label, 0) + 1
        if r.sub_label in sub_counts:
            sub_counts[r.sub_label] += 1

    ready     = counts[0] + counts[1] + counts[2]
    min_class = min(counts[0], counts[1], counts[2])

    face_counts = {}
    for folder in ["sober", "drowsy", "impaired"]:
        path = os.path.join(FACES_DIR, folder)
        face_counts[folder] = len(os.listdir(path)) if os.path.exists(path) else 0

    return {
        "total":          len(rows),
        "pending":        counts[-1],
        "no_alcohol":     counts[0],
        "breath_alcohol": counts[1],
        "sanitizer":      counts[2],
        "sanitizer_breakdown": {
            "rubbing_alcohol": sub_counts["sanitizer"],
            "perfume":         sub_counts["perfume"],
        },
        "ready_to_train": ready,
        "balanced":       min_class >= 20,
        "recommendation": "Aim for at least 50 samples per class (150 total)",
        "class_balance": {
            "no_alcohol_%":     round(counts[0] / ready * 100, 1) if ready else 0,
            "breath_alcohol_%": round(counts[1] / ready * 100, 1) if ready else 0,
            "sanitizer_%":      round(counts[2] / ready * 100, 1) if ready else 0,
        },
        "face_images": {
            "sober":           face_counts.get("sober",    0),
            "drowsy":          face_counts.get("drowsy",   0),
            "impaired":        face_counts.get("impaired", 0),
            "mobilenet_ready": all(v >= 50 for v in face_counts.values()),
            "recommendation":  "Aim for at least 50 images per class (150 total)",
        },
    }


# ── CSV export ────────────────────────────────────────────────
@router.get("/export")
def export_csv(db: Session = Depends(get_db)):
    """Export all labeled training data as CSV including sub_label."""
    rows = db.query(TrainingData).filter(TrainingData.label >= 0).all()
    if not rows:
        raise HTTPException(status_code=404, detail="No labeled data to export")

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "id", "date", "label", "label_name", "sub_label", "bac",
        "mq3_1_max", "mq3_1_avg", "mq3_1_std",
        "mq3_2_max", "mq3_2_avg", "mq3_2_std",
        "mq3_3_max", "mq3_3_avg", "mq3_3_std",
        "rise_time", "decay_time",
        "spatial_variance_max", "spatial_variance_avg",
        "temperature", "humidity",
    ])

    for r in rows:
        writer.writerow([
            r.id, r.date, r.label, LABEL_NAMES[r.label], r.sub_label, r.bac,
            r.mq3_1_max, r.mq3_1_avg, r.mq3_1_std,
            r.mq3_2_max, r.mq3_2_avg, r.mq3_2_std,
            r.mq3_3_max, r.mq3_3_avg, r.mq3_3_std,
            r.rise_time, r.decay_time,
            r.spatial_variance, r.spatial_variance_avg,
            r.temperature, r.humidity,
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=training_data.csv"},
    )


# ── Trigger training ──────────────────────────────────────────
@router.post("/train")
def trigger_training(db: Session = Depends(get_db)):
    """
    Train RF + XGBoost on labeled rows.
    Requires at least 20 samples per class (60 total minimum).
    """
    rows = db.query(TrainingData).filter(TrainingData.label >= 0).all()

    if len(rows) < 60:
        raise HTTPException(
            status_code=400,
            detail=f"Not enough data. Have {len(rows)} labeled rows, need at least 60."
        )

    counts = {0: 0, 1: 0, 2: 0}
    for r in rows:
        counts[r.label] = counts.get(r.label, 0) + 1

    for label, count in counts.items():
        if count < 20:
            raise HTTPException(
                status_code=400,
                detail=f"Not enough samples for class '{LABEL_NAMES[label]}': have {count}, need at least 20."
            )

    X, y = [], []
    for r in rows:
        if r.mq3_1_max is None:
            continue
        X.append([
            r.mq3_1_max, r.mq3_1_avg, r.mq3_1_std,
            r.mq3_2_max, r.mq3_2_avg, r.mq3_2_std,
            r.mq3_3_max, r.mq3_3_avg, r.mq3_3_std,
            r.rise_time,            r.decay_time,
            r.spatial_variance,     r.spatial_variance_avg,
            r.temperature,          r.humidity,
        ])
        y.append(r.label)

    if len(X) < 60:
        raise HTTPException(
            status_code=400,
            detail=f"Only {len(X)} rows have complete sensor features. {len(rows) - len(X)} rows still awaiting ESP32 data."
        )

    results = sensor_models.train(X, y)

    return {
        "message": "Training complete",
        "samples": len(X),
        "classes": counts,
        "results": results,
    }