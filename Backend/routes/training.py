from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, Form
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
import time

router = APIRouter(prefix="/training", tags=["Training"])

LABEL_NAMES = {
    -1: "Pending",
    0:  "No alcohol",
    1:  "Breath alcohol",
    2:  "Sanitizer",
}

FACES_DIR = os.path.join(os.path.dirname(__file__), "../data/faces")


# ── Step 1: ESP32 posts MQ3 window ────────────────────────────
class SensorPayload(BaseModel):
    temperature: float
    humidity:    float
    mq3_1:       List[float]
    mq3_2:       List[float]
    mq3_3:       List[float]


@router.post("/collect")
def collect_sensor(data: SensorPayload, db: Session = Depends(get_db)):
    """
    ESP32 sends MQ3 windows triggered by camera proximity detection.
    Extracts 15 features and saves as a PENDING row (label = -1).
    Returns row ID for operator to attach BAC later.
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
        data.temperature, data.humidity
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
        confidence           = None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return {
        "message": "Sensor data saved — awaiting BAC input",
        "id":      row.id,
        "features": {
            "mq3_1": {"max": features[0], "avg": features[1], "std": features[2]},
            "mq3_2": {"max": features[3], "avg": features[4], "std": features[5]},
            "mq3_3": {"max": features[6], "avg": features[7], "std": features[8]},
            "rise_time":            features[9],
            "decay_time":           features[10],
            "spatial_variance_max": features[11],
            "spatial_variance_avg": features[12],
            "temperature":          features[13],
            "humidity":             features[14],
        },
    }


# ── Training session — camera + sensor together ────────────────
@router.post("/session")
async def training_session(
    file:        UploadFile = File(...),
    temperature: float      = Form(...),
    humidity:    float      = Form(...),
    mq3_1:       str        = Form(...),   # comma-separated
    mq3_2:       str        = Form(...),
    mq3_3:       str        = Form(...),
    event_type:  str        = Form(...),   # "sober", "alcohol", "sanitizer"
    db:          Session    = Depends(get_db)
):
    """
    Coordinated training session — handles camera + sensor in one shot.

    Saves:
        - Camera frame to correct MobileNet folder based on event_type
        - MQ3 features to training_data as pending row
        - Auto-tags drowsy frames via EAR (only during sober sessions)

    event_type:
        "sober"     → image saved to data/faces/sober/ (or drowsy/ if EAR detects drowsy)
        "alcohol"   → image saved to data/faces/impaired/
        "sanitizer" → no image saved (no person involved)

    Returns row ID for BAC labeling via PATCH /training/label/{id}
    """

    # 1. Validate event type
    if event_type not in ["sober", "alcohol", "sanitizer"]:
        raise HTTPException(
            status_code=400,
            detail="event_type must be 'sober', 'alcohol', or 'sanitizer'"
        )

    # 2. Parse MQ3 windows
    try:
        w1 = [float(x) for x in mq3_1.split(",") if x.strip()]
        w2 = [float(x) for x in mq3_2.split(",") if x.strip()]
        w3 = [float(x) for x in mq3_3.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid MQ3 values — must be comma-separated numbers"
        )

    # 3. Validate window sizes
    if not (len(w1) == len(w2) == len(w3)):
        raise HTTPException(
            status_code=400,
            detail="MQ3 windows must all have the same number of readings"
        )
    if len(w1) < 5:
        raise HTTPException(
            status_code=400,
            detail=f"Window too short: {len(w1)} samples, need at least 5"
        )

    # 4. Decode camera frame
    contents   = await file.read()
    np_arr     = np.frombuffer(contents, np.uint8)
    frame      = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    # FIX 1: initialize these before the conditional block so they're
    # always defined when referenced in the return statement below
    image_path = None
    ear_result = None
    is_close   = None

    # 5. Save image to correct MobileNet folder
    if frame is not None and event_type in ["sober", "alcohol"]:

        ear_result = analyze_frame(frame)
        ear_status = ear_result.get("status", "normal")

        # FIX 2: use the actual proximity flag from cv_model, not EAR value.
        # EAR is the Eye Aspect Ratio (0.15–0.35 range), not a proximity measure.
        # cv_model.analyze_frame() returns is_close=True when face width > threshold.
        is_close = ear_result.get("is_close", False)

        if is_close:
            # FIX 3: only auto-redirect to drowsy/ during SOBER sessions.
            # An impaired/drunk person will frequently look drowsy — routing
            # their image to drowsy/ instead of impaired/ corrupts the MobileNet
            # training data. Drowsy auto-tagging only makes sense for sober sessions.
            if event_type == "sober" and ear_status == "drowsy":
                folder = os.path.join(FACES_DIR, "drowsy")
                tag    = "drowsy"
            elif event_type == "alcohol":
                folder = os.path.join(FACES_DIR, "impaired")
                tag    = "impaired"
            else:
                folder = os.path.join(FACES_DIR, "sober")
                tag    = "sober"

            os.makedirs(folder, exist_ok=True)
            filename   = f"{tag}_{int(time.time())}.jpg"
            image_path = os.path.join(folder, filename)
            cv2.imwrite(image_path, frame)

    # 6. Extract MQ3 features
    features = sensor_models.extract_features(w1, w2, w3, temperature, humidity)
    if features is None:
        raise HTTPException(status_code=400, detail="Feature extraction failed")

    # 7. Save to training_data as pending
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
        "features": {
            "mq3_1": {"max": features[0], "avg": features[1], "std": features[2]},
            "mq3_2": {"max": features[3], "avg": features[4], "std": features[5]},
            "mq3_3": {"max": features[6], "avg": features[7], "std": features[8]},
            "rise_time":            features[9],
            "decay_time":           features[10],
            "spatial_variance_max": features[11],
            "spatial_variance_avg": features[12],
            "temperature":          features[13],
            "humidity":             features[14],
        },
    }


# ── Step 2: Operator attaches BAC + label ─────────────────────
class LabelPayload(BaseModel):
    bac:          float
    is_sanitizer: bool = False


@router.patch("/label/{row_id}")
def attach_label(row_id: int, payload: LabelPayload,
                 db: Session = Depends(get_db)):
    """
    Operator submits BAC from BACtrack breathalyzer.
    Label auto-assigned based on BAC + sanitizer flag + sensor pattern.
    """
    row = db.query(TrainingData).filter(TrainingData.id == row_id).first()

    if not row:
        raise HTTPException(status_code=404, detail="Row not found")
    if row.label != -1:
        raise HTTPException(
            status_code=400,
            detail=f"Already labeled as '{LABEL_NAMES[row.label]}'. "
                   f"Use PATCH /training/relabel/{row_id} to correct it."
        )

    stored_features = [
        row.mq3_1_max, row.mq3_1_avg, row.mq3_1_std,
        row.mq3_2_max, row.mq3_2_avg, row.mq3_2_std,
        row.mq3_3_max, row.mq3_3_avg, row.mq3_3_std,
        row.rise_time,        row.decay_time,
        row.spatial_variance, row.spatial_variance_avg,
        row.temperature,      row.humidity,
    ]

    label = sensor_models.assign_label(
        payload.bac,
        payload.is_sanitizer,
        features=stored_features
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


# ── Correct a wrong label ──────────────────────────────────────
class RelabelPayload(BaseModel):
    label:  int
    reason: Optional[str] = None


@router.patch("/relabel/{row_id}")
def relabel(row_id: int, payload: RelabelPayload,
            db: Session = Depends(get_db)):
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
    db.commit()

    return {
        "message":   "Label corrected",
        "id":        row.id,
        "old_label": LABEL_NAMES[old_label],
        "new_label": LABEL_NAMES[payload.label],
        "reason":    payload.reason,
    }


# ── Delete a bad row ───────────────────────────────────────────
@router.delete("/delete/{row_id}")
def delete_row(row_id: int, db: Session = Depends(get_db)):
    """Remove a corrupt or bad row from training data."""
    row = db.query(TrainingData).filter(TrainingData.id == row_id).first()

    if not row:
        raise HTTPException(status_code=404, detail="Row not found")

    db.delete(row)
    db.commit()

    return {"message": f"Row {row_id} deleted"}


# ── View datasheet ─────────────────────────────────────────────
@router.get("/data")
def get_training_data(db: Session = Depends(get_db)):
    """Returns all training rows as a datasheet."""
    rows = db.query(TrainingData).order_by(TrainingData.date.asc()).all()

    return [
        {
            "id":                   r.id,
            "date":                 r.date,
            "label":                r.label,
            "label_name":           LABEL_NAMES.get(r.label, "Unknown"),
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


# ── Summary ────────────────────────────────────────────────────
@router.get("/summary")
def training_summary(db: Session = Depends(get_db)):
    """Class distribution — check balance before training."""
    rows   = db.query(TrainingData).all()
    counts = {-1: 0, 0: 0, 1: 0, 2: 0}

    for r in rows:
        counts[r.label] = counts.get(r.label, 0) + 1

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
        "ready_to_train": ready,
        "balanced":       min_class >= 20,
        "recommendation": "Aim for at least 50 samples per class (150 total)",
        "class_balance": {
            "no_alcohol_%":     round(counts[0] / ready * 100, 1) if ready else 0,
            "breath_alcohol_%": round(counts[1] / ready * 100, 1) if ready else 0,
            "sanitizer_%":      round(counts[2] / ready * 100, 1) if ready else 0,
        },
        "face_images": {
            "sober":    face_counts.get("sober",    0),
            "drowsy":   face_counts.get("drowsy",   0),
            "impaired": face_counts.get("impaired", 0),
            "mobilenet_ready": all(v >= 50 for v in face_counts.values()),
            "recommendation": "Aim for at least 50 images per class (150 total)",
        },
    }


# ── CSV export ─────────────────────────────────────────────────
@router.get("/export")
def export_csv(db: Session = Depends(get_db)):
    """Export all labeled training data as CSV for backup."""
    rows = db.query(TrainingData).filter(TrainingData.label >= 0).all()

    if not rows:
        raise HTTPException(status_code=404, detail="No labeled data to export")

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "id", "date", "label", "label_name", "bac",
        "mq3_1_max", "mq3_1_avg", "mq3_1_std",
        "mq3_2_max", "mq3_2_avg", "mq3_2_std",
        "mq3_3_max", "mq3_3_avg", "mq3_3_std",
        "rise_time", "decay_time",
        "spatial_variance_max", "spatial_variance_avg",
        "temperature", "humidity",
    ])

    for r in rows:
        writer.writerow([
            r.id, r.date, r.label, LABEL_NAMES[r.label], r.bac,
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
        headers={
            "Content-Disposition": "attachment; filename=training_data.csv"
        }
    )


# ── Trigger training ───────────────────────────────────────────
@router.post("/train")
def trigger_training(db: Session = Depends(get_db)):
    """
    Reads all labeled rows from DB and trains the model.
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
                detail=f"Not enough samples for class {LABEL_NAMES[label]}: "
                       f"have {count}, need at least 20."
            )

    X, y = [], []
    for r in rows:
        X.append([
            r.mq3_1_max, r.mq3_1_avg, r.mq3_1_std,
            r.mq3_2_max, r.mq3_2_avg, r.mq3_2_std,
            r.mq3_3_max, r.mq3_3_avg, r.mq3_3_std,
            r.rise_time,        r.decay_time,
            r.spatial_variance, r.spatial_variance_avg,
            r.temperature,      r.humidity,
        ])
        y.append(r.label)

    results = sensor_models.train(X, y)

    return {
        "message":  "Training complete",
        "samples":  len(rows),
        "classes":  counts,
        "results":  results,
    }