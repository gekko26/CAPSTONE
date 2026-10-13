from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, Form, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from database.db import get_db
from database.models import Subject, TrainingData
from models.train import sensor_models
from models.train.sensor_models import BAC_NEAR_LIMIT, BAC_OVER_LIMIT
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

ESP32_URL = os.getenv("ESP32_URL", "http://192.168.69.2")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOTS_DIR = os.path.join(BASE_DIR, "data", "snapshots")
IMPAIRED_DIR  = os.path.join(SNAPSHOTS_DIR, "impaired")
FATIGUE_DIR   = os.path.join(SNAPSHOTS_DIR, "fatigue")  # 3-class fatigue folder: drowsy EAR + yawning MAR aggregated
SOBER_DIR     = os.path.join(SNAPSHOTS_DIR, "sober")
# No snapshots/yawning folder — yawning is a MAR measurement stored under snapshots/fatigue/

os.makedirs(IMPAIRED_DIR, exist_ok=True)
os.makedirs(FATIGUE_DIR, exist_ok=True)
os.makedirs(SOBER_DIR, exist_ok=True)

LABEL_NAMES = {-1: "Pending", 0: "No alcohol", 1: "Breath alcohol", 2: "Others"}

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

async def trigger_esp32_async(row_id: int = None) -> dict:
    try:
        payload = {"row_id": row_id} if row_id else None
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{ESP32_URL}/trigger", json=payload, timeout=3.0)
        # FIX: 409 busy must NOT be treated as success — don't overwrite targetRowId
        body = {}
        try:
            body = r.json()
        except Exception:
            pass
        if r.status_code == 409:
            return {"triggered": False, "busy": True, "status": 409, "row_id": row_id, "busy_row_id": body.get("busy_row_id"), "error": body.get("error") or "ESP32 busy — buffering in progress"}
        if r.status_code == 200:
            return {"triggered": True, "status": 200, "row_id": row_id}
        return {"triggered": False, "status": r.status_code, "row_id": row_id, "error": r.text[:200]}
    except Exception as e:
        return {"triggered": False, "error": f"ESP32 offline: {e}"}

def decode_frame(contents: bytes):
    np_arr = np.frombuffer(contents, np.uint8)
    frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    return frame

def build_pending_row(label: int, sub_label: str = None, bac: float = None,
                      subject_id: int = None, db: Session = None) -> TrainingData:
    # trial_id is assigned after insert (id == trial_id for first row of trial)
    return TrainingData(label=label, sub_label=sub_label, bac=bac, trial_id=None,
                        subject_id=validate_subject_id(subject_id, db))


def validate_subject_id(subject_id, db: Session = None):
    """Validate a subject_id and, when a session is available, that it is enrolled.

    training_data.subject_id carries a real FK to subjects(id) on MariaDB, so an
    unknown id would otherwise surface as a raw IntegrityError (error 1452). Check
    it up front and return an actionable 400 instead. subject_id=None stays
    allowed — untagged rows are legitimate.
    """
    if subject_id is None:
        return None
    subject_id = int(subject_id)
    if subject_id < 0:
        raise HTTPException(status_code=400, detail="subject_id must be >= 0")
    if db is not None:
        exists = db.query(Subject.id).filter(Subject.id == subject_id).first()
        if exists is None:
            raise HTTPException(
                status_code=400,
                detail=f"subject_id {subject_id} is not enrolled - enroll the person first")
    return subject_id


def resolve_group_by(rows):
    """Pick ONE grouping key for a whole training run — never a mix.

    Prefers subject_id so GroupKFold keeps every row from the same person on the
    same side of the split. Falls back to trial_id (and finally the row id for
    legacy rows) only when at least one row has no subject_id, because mixing the
    two keys inside one run would silently destroy the grouping.

    Returns (group_by_name, key_fn).
    """
    if rows and all(getattr(r, "subject_id", None) is not None for r in rows):
        return "subject_id", (lambda r: int(r.subject_id))
    return "trial_id", (lambda r: r.trial_id if r.trial_id is not None else r.id)


def partial_tagging_warning(rows, group_by):
    """Loud warning when grouping silently degrades to trial_id despite partial tags.

    Returns None for a fully tagged run (group_by == subject_id) and for a fully
    untagged run (no subject_id anywhere) — neither is a surprise. Returns the
    warning payload only when SOME but NOT ALL rows are tagged, because that is the
    case where the operator believes grouping is person-scoped when it is not.
    """
    if group_by == "subject_id":
        return None
    tagged = [r for r in rows if getattr(r, "subject_id", None) is not None]
    if not tagged:
        return None
    untagged = [r for r in rows if getattr(r, "subject_id", None) is None]
    return {
        "partial_subject_tagging": True,
        "tagged": len(tagged),
        "untagged": len(untagged),
        "untagged_row_ids": [r.id for r in untagged[:50]],
    }

@router.post("/collect/clear_air")
async def collect_clear_air(subject_id: Optional[int] = Form(None), db: Session = Depends(get_db)):
    row = build_pending_row(label=0, sub_label="clear_air", bac=0.00, subject_id=subject_id, db=db)
    db.add(row)
    db.commit()
    db.refresh(row)
    # trial_id = id for first row of trial (grouped k-fold)
    if row.trial_id is None:
        row.trial_id = row.id
        db.commit()
    trigger_result = await trigger_esp32_async(row_id=row.id)
    return {"message": "Clear air baseline triggered", "id": row.id, "subject_id": row.subject_id, "trigger": trigger_result}

@router.post("/collect/sober")
async def collect_sober(background_tasks: BackgroundTasks, file: UploadFile = File(...), subject_id: Optional[int] = Form(None), db: Session = Depends(get_db)):
    contents = await file.read()
    frame = decode_frame(contents)
    row = TrainingData(label=0, sub_label=None, bac=0.00, subject_id=validate_subject_id(subject_id, db))
    try:
        cv = analyze_frame(frame) if frame is not None else {}
        row.ear = cv.get("ear"); row.mar = cv.get("mar"); row.head_pitch = cv.get("pitch")
    except: pass
    db.add(row); db.commit(); db.refresh(row)
    if row.trial_id is None:
        row.trial_id = row.id
        db.commit()
    if frame is not None:
        path = os.path.join(SOBER_DIR, f"sober_{row.id}.jpg")
        row.image_path = path
        db.commit()
        background_tasks.add_task(save_snapshot_task, path, frame)
        try:
            from pathlib import Path as _P
            faces_sober = os.path.join(BASE_DIR, "data", "faces", "sober")
            _P(faces_sober).mkdir(parents=True, exist_ok=True)
            import cv2 as _cv2
            _cv2.imwrite(os.path.join(faces_sober, f"sober_{row.id}.jpg"), frame)
        except: pass
    trigger_result = await trigger_esp32_async(row_id=row.id)
    return {"message": "Sober baseline captured", "id": row.id, "image_path": row.image_path, "trigger": trigger_result}

@router.post("/collect/drowsy")
async def collect_drowsy(background_tasks: BackgroundTasks, file: UploadFile = File(...), subject_id: Optional[int] = Form(None), db: Session = Depends(get_db)):
    contents = await file.read()
    frame = decode_frame(contents)
    row = TrainingData(label=0, sub_label="drowsy", bac=0.00, subject_id=validate_subject_id(subject_id, db))
    # capture EAR at collection time for DB consistency
    try:
        cv = analyze_frame(frame) if frame is not None else {}
        row.ear = cv.get("ear"); row.mar = cv.get("mar"); row.head_pitch = cv.get("pitch")
    except: pass
    db.add(row); db.commit(); db.refresh(row)
    if row.trial_id is None:
        row.trial_id = row.id
        db.commit()
    if frame is not None:
        path = os.path.join(FATIGUE_DIR, f"fatigue_{row.id}.jpg")
        row.image_path = path
        db.commit()
        background_tasks.add_task(save_snapshot_task, path, frame)
        try:
            from pathlib import Path as _P
            faces_fatigue = os.path.join(BASE_DIR, "data", "faces", "fatigue")
            _P(faces_fatigue).mkdir(parents=True, exist_ok=True)
            import cv2 as _cv2
            _cv2.imwrite(os.path.join(faces_fatigue, f"fatigue_{row.id}.jpg"), frame)
            faces_sober = os.path.join(BASE_DIR, "data", "faces", "sober")
            _P(faces_sober).mkdir(parents=True, exist_ok=True)
        except: pass
    trigger_result = await trigger_esp32_async(row_id=row.id)
    return {"message": "Fatigue (drowsy) baseline captured", "id": row.id, "image_path": row.image_path, "trigger": trigger_result}

@router.post("/collect/yawning")
async def collect_yawning(background_tasks: BackgroundTasks, file: UploadFile = File(...), subject_id: Optional[int] = Form(None), db: Session = Depends(get_db)):
    # Deprecated: yawning is mar measurement, not class. Keep endpoint for compat but route to fatigue (drowsy) class.
    contents = await file.read()
    frame = decode_frame(contents)
    row = TrainingData(label=0, sub_label="yawning", bac=0.00, subject_id=validate_subject_id(subject_id, db))
    try:
        cv = analyze_frame(frame) if frame is not None else {}
        row.ear = cv.get("ear"); row.mar = cv.get("mar"); row.head_pitch = cv.get("pitch")
    except: pass
    db.add(row); db.commit(); db.refresh(row)
    if row.trial_id is None:
        row.trial_id = row.id
        db.commit()
    if frame is not None:
        path = os.path.join(FATIGUE_DIR, f"yawning_{row.id}.jpg")
        row.image_path = path
        db.commit()
        background_tasks.add_task(save_snapshot_task, path, frame)
        # Harmony: also copy to faces/fatigue for mobilenet 3-class
        try:
            from pathlib import Path as _P
            faces_fatigue = os.path.join(BASE_DIR, "data", "faces", "fatigue")
            _P(faces_fatigue).mkdir(parents=True, exist_ok=True)
            import cv2 as _cv2
            _cv2.imwrite(os.path.join(faces_fatigue, f"yawning_{row.id}.jpg"), frame)
        except: pass
    trigger_result = await trigger_esp32_async(row_id=row.id)
    return {"message": "Yawning (fatigue) captured — yawning is measurement, stored as fatigue", "id": row.id, "image_path": row.image_path, "trigger": trigger_result}

@router.post("/collect/alcohol")
async def collect_alcohol(background_tasks: BackgroundTasks, file: UploadFile = File(...), bac: float = Form(...), subject_id: Optional[int] = Form(None), db: Session = Depends(get_db)):
    contents = await file.read()
    frame = decode_frame(contents)
    row = TrainingData(label=1, sub_label=None, bac=bac, subject_id=validate_subject_id(subject_id, db))
    try:
        cv = analyze_frame(frame) if frame is not None else {}
        row.ear = cv.get("ear"); row.mar = cv.get("mar"); row.head_pitch = cv.get("pitch")
    except: pass
    db.add(row); db.commit(); db.refresh(row)
    if row.trial_id is None:
        row.trial_id = row.id
        db.commit()
    if frame is not None:
        path = os.path.join(IMPAIRED_DIR, f"impaired_{row.id}.jpg")
        row.image_path = path
        db.commit()
        background_tasks.add_task(save_snapshot_task, path, frame)
        # H3: mirror into data/faces/impaired/ so the MobileNet trainer can see the
        # class. Without this the impaired class can never accumulate training
        # images, because the trainer globs data/faces/ and not data/snapshots/.
        try:
            from pathlib import Path as _P
            faces_impaired = os.path.join(BASE_DIR, "data", "faces", "impaired")
            _P(faces_impaired).mkdir(parents=True, exist_ok=True)
            import cv2 as _cv2
            _cv2.imwrite(os.path.join(faces_impaired, f"impaired_{row.id}.jpg"), frame)
        except Exception as _e:
            print(f"⚠️ [H3] faces/impaired mirror failed for row {row.id}: {_e}")
    trigger_result = await trigger_esp32_async(row_id=row.id)
    return {"message": "Alcohol captured", "id": row.id, "subject_id": row.subject_id, "image_path": row.image_path, "trigger": trigger_result}

@router.post("/collect/sanitizer")
async def collect_sanitizer(subject_id: Optional[int] = Form(None), db: Session = Depends(get_db)):
    row = build_pending_row(label=2, sub_label="sanitizer", bac=0.00, subject_id=subject_id, db=db)
    db.add(row); db.commit(); db.refresh(row)
    if row.trial_id is None:
        row.trial_id = row.id
        db.commit()
    trigger_result = await trigger_esp32_async(row_id=row.id)
    return {"message": "Sanitizer context triggered", "id": row.id, "subject_id": row.subject_id, "trigger": trigger_result}

@router.post("/collect/perfume")
async def collect_perfume(background_tasks: BackgroundTasks, file: UploadFile = File(...), subject_id: Optional[int] = Form(None), db: Session = Depends(get_db)):
    contents = await file.read()
    frame = decode_frame(contents)
    row = TrainingData(label=2, sub_label="perfume", bac=0.00, subject_id=validate_subject_id(subject_id, db))
    try:
        cv = analyze_frame(frame) if frame is not None else {}
        row.ear = cv.get("ear"); row.mar = cv.get("mar"); row.head_pitch = cv.get("pitch")
    except: pass
    db.add(row); db.commit(); db.refresh(row)
    if row.trial_id is None:
        row.trial_id = row.id
        db.commit()
    if frame is not None:
        path = os.path.join(IMPAIRED_DIR, f"impaired_perfume_{row.id}.jpg")
        row.image_path = path
        db.commit()
        background_tasks.add_task(save_snapshot_task, path, frame)
    trigger_result = await trigger_esp32_async(row_id=row.id)
    return {"message": "Perfume vapor captured", "id": row.id, "image_path": row.image_path, "trigger": trigger_result}

class SensorPayload(BaseModel):
    temperature: float; humidity: float; mq3_1: List[float]; mq3_2: List[float]; mq3_3: List[float]; row_id: Optional[int] = None

@router.post("/collect/sensor-data")
def receive_sensor_data(data: SensorPayload, db: Session = Depends(get_db)):
    features = sensor_models.extract_features(data.mq3_1, data.mq3_2, data.mq3_3, data.temperature, data.humidity)
    if features is None:
        raise HTTPException(status_code=400, detail="No sensor data")
    # Generate 6-8 time-window slices from the single 5s window for expanded rows per trial
    # Each slice yields a feature vector sharing the same trial_id (grouped k-fold)
    def slice_features(windows):
        # windows is list of 3 lists (mq1, mq2, mq3)
        # For now return single feature set; future: slice into overlapping sub-windows
        # Keeping single row per POST for now, but trial_id grouping preserves correlation
        return [features]

    # FIX 5: Strict row_id binding — no fallback to "latest pending" (corrupts trial_id GroupKFold grouping)
    if data.row_id is None:
        raise HTTPException(status_code=400, detail="row_id is required — sensor data must be bound to a specific training trial")
    parent = db.query(TrainingData).filter(TrainingData.id == data.row_id).first()
    if not parent:
        print(f"⚠️ [FIX 5 ALERT] Rejected sensor-data POST with unknown row_id={data.row_id} — no fallback")
        raise HTTPException(status_code=404, detail=f"TrainingData row_id={data.row_id} not found — cannot attach sensor data")
    # Also reject if row already has sensor features and is not pending expansion? Allow expansion only if same trial (trial_id match)
    # If parent already has mq3 data, we will create new rows sharing trial_id (window expansion) — that is allowed

    # If parent already has features, this is an additional window sample for same trial (6-8 per trial)
    # Create new row sharing trial_id instead of overwriting to preserve grouped structure
    target_rows = slice_features([data.mq3_1, data.mq3_2, data.mq3_3])
    created_ids = []
    for feat in target_rows:
        if parent.mq3_1_max is None and not created_ids:
            row = parent
        else:
            row = TrainingData(
                label=parent.label,
                sub_label=parent.sub_label,
                bac=parent.bac,
                trial_id=parent.trial_id,
                # H8: an expanded window row must stay attached to the same person,
                # otherwise subject-level GroupKFold grouping silently degrades.
                subject_id=parent.subject_id,
                ear=parent.ear,
                mar=parent.mar,
                head_pitch=parent.head_pitch,
                image_path=None,
            )
            db.add(row); db.flush()

        row.mq3_1_max, row.mq3_1_avg, row.mq3_1_std = feat[0], feat[1], feat[2]
        row.mq3_2_max, row.mq3_2_avg, row.mq3_2_std = feat[3], feat[4], feat[5]
        row.mq3_3_max, row.mq3_3_avg, row.mq3_3_std = feat[6], feat[7], feat[8]
        row.rise_time, row.decay_time = feat[9], feat[10]
        row.spatial_variance, row.spatial_variance_avg = feat[11], feat[12]
        row.temperature, row.humidity = feat[13], feat[14]
        # Plan A directional — store if columns exist (backward compat for old DB)
        if len(feat) > 15:
            if hasattr(row, 'breath_ratio'):  # handled via getattr for migration-safety, will store if column exists
                try:
                    row.breath_ratio = feat[15]
                    row.sanitizer_ratio = feat[16]
                    row.spatial_direction = feat[17]
                except Exception:
                    pass
        # Also handle legacy TrainingData without new columns via set attempt on dynamic attr fallback
        for _extra_idx, _extra_col in [(15, 'breath_ratio'), (16, 'sanitizer_ratio'), (17, 'spatial_direction')]:
            if len(feat) > _extra_idx:
                try:
                    setattr(row, _extra_col, feat[_extra_idx])
                except Exception:
                    pass
        if row.trial_id is None:
            row.trial_id = row.id
        created_ids.append(row)

    db.commit()
    # ensure all created rows have trial_id = parent trial_id
    for r in created_ids:
        if r.trial_id is None:
            r.trial_id = parent.trial_id
    db.commit()
    return {"message": "Features synced", "id": parent.id, "trial_id": parent.trial_id, "rows_created": len(created_ids)}

class LabelPayload(BaseModel):
    bac: float; is_sanitizer: bool = False; subject_id: Optional[int] = None

@router.patch("/label/{row_id}")
def attach_label(row_id: int, payload: LabelPayload, db: Session = Depends(get_db)):
    row = db.query(TrainingData).filter(TrainingData.id == row_id).first()
    if not row: raise HTTPException(status_code=404, detail="Not found")
    validate_subject_id(payload.subject_id, db)
    if row.label == 1:
        row.bac = payload.bac
        if payload.subject_id is not None:
            row.subject_id = payload.subject_id
        db.commit()
        return {"id": row.id, "label": row.label, "label_name": LABEL_NAMES[row.label]}
    # Support 18D stored vector for assign_label (needs at least 15)
    stored = [row.mq3_1_max, row.mq3_1_avg, row.mq3_1_std, row.mq3_2_max, row.mq3_2_avg, row.mq3_2_std, row.mq3_3_max, row.mq3_3_avg, row.mq3_3_std, row.rise_time, row.decay_time, row.spatial_variance, row.spatial_variance_avg, row.temperature, row.humidity]
    # append Plan A directional if available
    try:
        if getattr(row, 'breath_ratio', None) is not None:
            stored.extend([row.breath_ratio, row.sanitizer_ratio, row.spatial_direction])
    except Exception:
        pass
    label = sensor_models.assign_label(payload.bac, payload.is_sanitizer, features=stored)
    row.bac, row.label = payload.bac, label
    if payload.subject_id is not None:
        row.subject_id = payload.subject_id
    # FIX 4: heuristic auto-label is disabled by default — any label 2 here is manual, mark confirmed
    try:
        row.auto_labeled = False
        row.label_confirmed = True
    except Exception: pass
    db.commit()
    return {"id": row.id, "label": label, "label_name": LABEL_NAMES[label]}

class RelabelPayload(BaseModel):
    label: int; sub_label: Optional[str] = None; subject_id: Optional[int] = None; reason: Optional[str] = None

@router.patch("/relabel/{row_id}")
def relabel(row_id: int, payload: RelabelPayload, db: Session = Depends(get_db)):
    row = db.query(TrainingData).filter(TrainingData.id == row_id).first()
    if not row: raise HTTPException(status_code=404, detail="Not found")
    validate_subject_id(payload.subject_id, db)
    row.label = payload.label
    if payload.sub_label: row.sub_label = payload.sub_label
    if payload.subject_id is not None:
        row.subject_id = payload.subject_id
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

@router.get("/subject-coverage")
def subject_coverage(db: Session = Depends(get_db)):
    """How many training rows are tagged, and what the grouping will key on.

    Uses the SAME row filter and the SAME resolve_group_by() as trigger_training, so
    will_group_by is a real prediction of the next run rather than a separate guess.
    """
    rows = db.query(TrainingData).filter(TrainingData.label >= 0).all()
    rows = [r for r in rows if not getattr(r, 'auto_labeled', False) or getattr(r, 'label_confirmed', True)]

    group_by, _ = resolve_group_by(rows)
    tagged = [r for r in rows if getattr(r, "subject_id", None) is not None]
    untagged = [r for r in rows if getattr(r, "subject_id", None) is None]
    per_label = {}
    for r in rows:
        key = str(r.label)
        per_label[key] = per_label.get(key, 0) + 1

    return {
        "rows_considered": len(rows),
        "rows_with_subject_id": len(tagged),
        "distinct_subjects": len({r.subject_id for r in tagged}),
        "untagged_row_ids": [r.id for r in untagged[:200]],
        "untagged_truncated": len(untagged) > 200,
        "per_label": per_label,
        "will_group_by": group_by,
        "warning": partial_tagging_warning(rows, group_by),
    }


@router.get("/summary")
def training_summary(db: Session = Depends(get_db)):
    rows = db.query(TrainingData).all()
    counts = {-1:0, 0:0, 1:0, 2:0}
    for r in rows: counts[r.label] = counts.get(r.label, 0) + 1

    # Sanitizer sub-label breakdown (Frontend expects rubbing_alcohol vs perfume)
    sanitizer_breakdown = {
        "rubbing_alcohol": sum(1 for r in rows if r.label==2 and r.sub_label=="sanitizer"),
        "perfume": sum(1 for r in rows if r.label==2 and r.sub_label=="perfume"),
        # also track label 1 sub break if needed (all label1 are breath alcohol, sub_label NULL)
        "breath_alcohol": counts[1],
        "alcohol_rows": sum(1 for r in rows if r.label==1),
    }

    # BAC stats inside label 1 — PH tiers
    bac_rows = [r for r in rows if r.label==1 and r.bac is not None and r.bac>0 and r.mq3_1_max is not None]
    bac_levels = sorted(set(round(float(r.bac),2) for r in bac_rows))
    bac_by_tier = {"trace":0,"light":0,"over":0}
    for r in bac_rows:
        if r.bac < BAC_NEAR_LIMIT: bac_by_tier["trace"] += 1
        elif r.bac < BAC_OVER_LIMIT: bac_by_tier["light"] += 1
        else: bac_by_tier["over"] += 1
    # Load bac regressor metrics if trained
    bac_metrics = None
    try:
        from models.train.metrics_store import load_metrics
        m = load_metrics()
        # sensor metrics store may contain bac_regressor under sensor
        if "sensor" in m and isinstance(m["sensor"], dict) and "bac_regressor" in m["sensor"]:
            bac_metrics = m["sensor"]["bac_regressor"]
        elif "bac" in m:
            bac_metrics = m["bac"]
    except Exception:
        pass

    # Face images — 3-class sober/fatigue/impaired (fatigue = drowsy+yawning, yawning is mar measurement)
    def count_dir(p): 
        return len([f for f in os.listdir(p) if f.lower().endswith(('.jpg','.jpeg','.png'))]) if os.path.exists(p) else 0
    # snapshots: fatigue aggregated; faces: mobilenet source
    snapshots_fatigue = count_dir(FATIGUE_DIR)  # yawning measurements are stored in the fatigue dir
    faces_dir = lambda c: os.path.join(BASE_DIR, "data", "faces", c)
    face_counts = {
        "sober": max(count_dir(SOBER_DIR), count_dir(faces_dir("sober"))),
        "fatigue": max(snapshots_fatigue, count_dir(faces_dir("fatigue")), count_dir(faces_dir("drowsy"))),
        "drowsy": snapshots_fatigue,
        "yawning": 0,  # deprecated: yawning is measurement
        "impaired": max(count_dir(IMPAIRED_DIR), count_dir(faces_dir("impaired"))),
    }
    # also compute ready_to_train (rows with complete sensor features) and balanced flag
    # Plan: 1000 per sensor event (3000 total) + 200 per face (600 total: sober/fatigue/impaired)
    ready = sum(1 for r in rows if r.label>=0 and r.mq3_1_max is not None)
    # Sensor: 1000 each, Face: 200 each (sober/fatigue/impaired, fatigue = drowsy+yawning, 600 total)
    balanced = (
        counts[0] >= 1000 and counts[1] >= 1000 and counts[2] >= 1000
        and face_counts["sober"] >= 200
        and face_counts["fatigue"] >= 200
        and face_counts["impaired"] >= 200
    ) if ready >= 3000 else False

    # trial counts per label for grouped k-fold visibility (frontend mirror)
    trial_counts = {}
    for lbl in [-1,0,1,2]:
        trial_counts[lbl] = len({r.trial_id for r in rows if r.label==lbl and r.trial_id is not None})
    return {
        "total": len(rows),
        "pending": counts[-1],
        "no_alcohol": counts[0],
        "breath_alcohol": counts[1],
        "sanitizer": counts[2],
        # alias for frontend Others rename
        "others": counts[2],
        "face_images": face_counts,
        "sanitizer_breakdown": {
            "rubbing_alcohol": sanitizer_breakdown["rubbing_alcohol"],
            "perfume": sanitizer_breakdown["perfume"],
        },
        "others_breakdown": {
            "rubbing_alcohol": sanitizer_breakdown["rubbing_alcohol"],
            "perfume": sanitizer_breakdown["perfume"],
        },
        "ready_to_train": ready,
        "ready_trials": len({r.trial_id for r in rows if r.label>=0 and r.mq3_1_max is not None and r.trial_id is not None}),
        "balanced": balanced,
        "trial_counts": {str(k): v for k,v in trial_counts.items()},
        "label_names": LABEL_NAMES,
        "bac_stats": {"count": len(bac_rows), "levels": bac_levels, "by_tier": bac_by_tier, "metrics": bac_metrics, "ph_limit": BAC_OVER_LIMIT},
    }

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
    # FIX 4: exclude auto_labeled unconfirmed rows from sensor training
    rows = [r for r in rows if not getattr(r, 'auto_labeled', False) or getattr(r, 'label_confirmed', True)]
    # One grouping key for the whole run: subject_id when every row has one,
    # otherwise trial_id. Used for both the classifier and the BAC regressor.
    group_by, group_key = resolve_group_by(rows)
    X = []
    y = []
    groups = []
    X_bac = []
    y_bac = []
    g_bac = []
    for r in rows:
        if r.mq3_1_max is None:
            continue
        # Build 18D feat (fallback to 15D if new cols missing/null)
        base = [r.mq3_1_max, r.mq3_1_avg, r.mq3_1_std, r.mq3_2_max, r.mq3_2_avg, r.mq3_2_std, r.mq3_3_max, r.mq3_3_avg, r.mq3_3_std, r.rise_time, r.decay_time, r.spatial_variance, r.spatial_variance_avg, r.temperature, r.humidity]
        # Plan A directional — try DB columns first, else derive from base
        try:
            br = getattr(r, 'breath_ratio', None)
            sr = getattr(r, 'sanitizer_ratio', None)
            sd = getattr(r, 'spatial_direction', None)
            if br is not None and sr is not None and sd is not None:
                feat = base + [float(br), float(sr), float(sd)]
            else:
                # derive on fly from avg/max
                from models.train.sensor_models import EPSILON as _EPS
                br_calc = round(((r.mq3_1_avg + r.mq3_2_avg)/2.0) / max(r.mq3_3_avg or 1.0, _EPS), 4) if r.mq3_3_avg else 0.0
                sr_calc = round(((r.mq3_2_avg + r.mq3_3_avg)/2.0) / max(r.mq3_1_avg or 1.0, _EPS), 4) if r.mq3_1_avg else 0.0
                sd_calc = round(float((r.mq3_3_max or 0) - (r.mq3_1_max or 0)), 4)
                feat = base + [br_calc, sr_calc, sd_calc]
        except Exception:
            feat = base + [0.0, 0.0, 0.0]
        X.append(feat)
        y.append(r.label)
        groups.append(group_key(r))
        # BAC regressor — only breath alcohol with measured BAC
        if r.label == 1 and r.bac is not None and r.bac > 0.001:
            X_bac.append(feat)
            y_bac.append(float(r.bac))
            g_bac.append(group_key(r))
    if not X:
        return {"message": "No data with sensor features", "samples": 0, "group_by": group_by}
    clf_results = sensor_models.train(X, y, groups=groups)
    if isinstance(clf_results, dict) and clf_results.get("error"):
        return {"error": clf_results["error"], "samples": len(X), "trials": len(set(groups)),
                "group_by": group_by, "warning": partial_tagging_warning(rows, group_by),
                "results": clf_results}
    bac_results = None
    if X_bac:
        bac_results = sensor_models.train_bac(X_bac, y_bac, groups=g_bac)
    return {"message": "Training running", "samples": len(X), "trials": len(set(groups)),
            "group_by": group_by, "warning": partial_tagging_warning(rows, group_by),
            "results": clf_results, "bac_results": bac_results,
            "bac_samples": len(X_bac)}


# ── Hidden trainers (URL only) + transparent statistics ────────────────────
class MobilenetPayload(BaseModel):
    batch_size: int = 8  # 7GB RAM safe, was 16
    epochs: int = 5
    incremental: bool = False

@router.post("/train/mobilenet")
def train_mobilenet(payload: MobilenetPayload):
    """Hidden /mobilenet trainer — incremental for 7GB RAM. Accessible only via hidden URL."""
    try:
        from models.train import mobilenet
        # Apply payload batch/epochs for 7GB
        orig_bs = mobilenet.BATCH_SIZE
        orig_ep = mobilenet.EPOCHS
        mobilenet.BATCH_SIZE = max(4, min(payload.batch_size, 32))
        mobilenet.EPOCHS = max(1, min(payload.epochs, 50))
        try:
            # Check data availability
            import os
            face_dirs = {c: os.path.join(mobilenet.DATA_DIR, c) for c in mobilenet.CLASSES}
            counts = {k: len([f for f in os.listdir(p) if f.lower().endswith(('.jpg','.jpeg','.png'))]) if os.path.exists(p) else 0 for k,p in face_dirs.items()}
            total = sum(counts.values())
            if total < 10:
                return {"error": f"Not enough face data: {counts} — need 200 each (600 total sober/fatigue/impaired)", "counts": counts}
            history = mobilenet.train()
            # history returned, metrics saved inside train()
            from models.train.metrics_store import load_metrics
            m = load_metrics().get("mobilenet", {})
            return {"message": "MobileNet training complete", "counts": counts, "metrics": m, "batch_size": mobilenet.BATCH_SIZE, "epochs": mobilenet.EPOCHS}
        finally:
            mobilenet.BATCH_SIZE = orig_bs
            mobilenet.EPOCHS = orig_ep
    except Exception as e:
        import traceback
        return {"error": str(e), "trace": traceback.format_exc()[:2000]}

class FusionPayload(BaseModel):
    pass  # future: custom thresholds

@router.post("/train/fusion")
def train_fusion(db: Session = Depends(get_db)):
    """Fusion trainer from training_data pairs — FIX 3 OOF: sensor/visual inputs are out-of-fold predictions, not ground truth."""
    try:
        from models.train import fusion_model
        from database.models import Subject, TrainingData
        import numpy as np
        import cv2, os

        all_rows = db.query(TrainingData).filter(TrainingData.label>=0, TrainingData.mq3_1_max != None).all()
        # Fusion needs a (sensor window, face image) PAIR per row. clear_air and
        # sanitizer captures have no face image by design, so they are EXCLUDED,
        # not refused — only the count of usable pairs gates the run.
        rows = []
        excluded = []
        for r in all_rows:
            _p = getattr(r, "image_path", None) or ""
            if _p and os.path.exists(_p):
                rows.append(r)
            else:
                excluded.append(r)
        pair_counts = {"rows_total": len(all_rows), "rows_paired": len(rows),
                       "rows_excluded_no_image": len(excluded)}
        if len(rows) < 10:
            return {"error": (f"Not enough paired training_data: {len(rows)} of {len(all_rows)} rows have an "
                              "image on disk — need 10+ paired rows (sensor features + face image) for fusion."),
                    "counts": pair_counts, "group_by": resolve_group_by(rows)[0],
                    "warning": partial_tagging_warning(rows, resolve_group_by(rows)[0])}

        # FIX A7: refuse a single-class set up front rather than deep inside a
        # half-built feature matrix.
        distinct_row_labels = sorted({int(r.label) for r in rows})
        if len(distinct_row_labels) < 2:
            return {"error": (f"Fusion refused: training rows carry {len(distinct_row_labels)} distinct "
                              f"label(s) {distinct_row_labels} — need >= 2 (e.g. clear air and breath "
                              "alcohol). Collect and label more data first."),
                    "labels": distinct_row_labels, "rows": len(rows)}

        # One grouping key for sensor OOF, BAC OOF and the final fusion fit.
        group_by, group_key = resolve_group_by(rows)

        # ---- Build raw sensor feature matrix for OOF ----
        X_sensor = []
        y_sensor = []
        groups_arr = []
        for r in rows:
            base = [r.mq3_1_max, r.mq3_1_avg, r.mq3_1_std, r.mq3_2_max, r.mq3_2_avg, r.mq3_2_std, r.mq3_3_max, r.mq3_3_avg, r.mq3_3_std, r.rise_time, r.decay_time, r.spatial_variance, r.spatial_variance_avg, r.temperature, r.humidity]
            try:
                br = getattr(r, 'breath_ratio', None); sr = getattr(r, 'sanitizer_ratio', None); sd = getattr(r, 'spatial_direction', None)
                if br is not None and sr is not None and sd is not None:
                    feat = base + [float(br), float(sr), float(sd)]
                else:
                    from models.train.sensor_models import EPSILON as _EPS
                    br_calc = round(((r.mq3_1_avg + r.mq3_2_avg)/2.0) / max(r.mq3_3_avg or 1.0, _EPS), 4) if r.mq3_3_avg else 0.0
                    sr_calc = round(((r.mq3_2_avg + r.mq3_3_avg)/2.0) / max(r.mq3_1_avg or 1.0, _EPS), 4) if r.mq3_1_avg else 0.0
                    sd_calc = round(float((r.mq3_3_max or 0) - (r.mq3_1_max or 0)), 4)
                    feat = base + [br_calc, sr_calc, sd_calc]
            except Exception:
                feat = base + [0.0, 0.0, 0.0]
            X_sensor.append(feat); y_sensor.append(int(r.label)); groups_arr.append(group_key(r))

        X_sensor = np.array(X_sensor, dtype=float); y_sensor = np.array(y_sensor); groups_arr = np.array(groups_arr)

        # ---- OOF sensor predictions (GroupKFold RF) ----
        from sklearn.model_selection import GroupKFold
        from sklearn.preprocessing import StandardScaler
        from sklearn.ensemble import RandomForestClassifier
        oof_sensor_class = [None]*len(rows)
        oof_sensor_conf = [None]*len(rows)
        use_oof = len(np.unique(groups_arr)) >= 3 and len(rows) >= 10
        if use_oof:
            n_splits = min(5, len(np.unique(groups_arr)))
            gkf = GroupKFold(n_splits=n_splits)
            for train_idx, test_idx in gkf.split(X_sensor, y_sensor, groups_arr):
                scaler = StandardScaler(); X_tr = scaler.fit_transform(X_sensor[train_idx]); X_te = scaler.transform(X_sensor[test_idx])
                clf = RandomForestClassifier(n_estimators=100, random_state=42)
                # balanced weights handled via class_weight if needed, but RF balanced covers small data
                try:
                    clf.set_params(class_weight='balanced')
                except: pass
                clf.fit(X_tr, y_sensor[train_idx])
                probs = clf.predict_proba(X_te)
                preds = np.argmax(probs, axis=1)
                for i, idx in enumerate(test_idx):
                    oof_sensor_class[idx] = int(preds[i])
                    oof_sensor_conf[idx] = float(np.max(probs[i]))
        # FIX A7: refuse rather than substitute ground truth into feature[0].
        missing_oof = [i for i in range(len(rows)) if oof_sensor_class[i] is None]
        if missing_oof:
            return {"error": (f"Fusion refused: {len(missing_oof)} of {len(rows)} rows have no out-of-fold "
                              f"sensor prediction (needs >= 3 distinct {group_by} values and >= 10 "
                              f"rows). Refusing to substitute the ground-truth label into feature[0]."),
                    "missing_oof_rows": len(missing_oof)}
        use_oof = True

        # ---- OOF BAC regressor estimate (feature[6]) — FIX A6 ----
        # feature[6] must be a MODEL ESTIMATE. Feeding the measured breathalyzer value
        # that also builds the label makes the target a pure function of the feature
        # (RF scores ~100% from feature[6] alone and learns nothing). If no OOF
        # estimate can be produced for any row, refuse.
        # Runs BEFORE the per-row MobileNet pass: this is a cheap data-level check and
        # should not cost N model forward passes before it fires.
        from sklearn.ensemble import RandomForestRegressor
        MIN_BAC_OOF_ROWS = 10
        n_missing_bac = sum(1 for r in rows if r.bac is None)
        if n_missing_bac:
            return {"error": (f"Fusion refused: {n_missing_bac} of {len(rows)} rows have no measured BAC, so "
                              "no out-of-fold BAC estimate exists for feature[6]. Attach a breathalyzer value "
                              "via PATCH /training/label/{row_id} first."),
                    "rows_missing_bac": n_missing_bac}
        if len(rows) < MIN_BAC_OOF_ROWS:
            return {"error": (f"Fusion refused: need >= {MIN_BAC_OOF_ROWS} rows for the grouped OOF BAC "
                              f"estimate, have {len(rows)}.")}
        y_bac = np.array([float(r.bac) for r in rows], dtype=float)
        distinct_bac = np.unique(np.round(y_bac, 4))
        if len(distinct_bac) < 2:
            return {"error": (f"Fusion refused: need >= 2 distinct measured BAC levels to fit the OOF regressor, "
                              f"have {len(distinct_bac)} ({distinct_bac.tolist()[:5]})."),
                    "distinct_bac_levels": int(len(distinct_bac))}
        n_bac_splits = min(5, len(np.unique(groups_arr)))
        if n_bac_splits < 2:
            return {"error": (f"Fusion refused: need >= 2 distinct {group_by} values for the grouped OOF BAC "
                              f"estimate, have {len(np.unique(groups_arr))}.")}
        oof_bac = [None] * len(rows)
        for tr_i, te_i in GroupKFold(n_splits=n_bac_splits).split(X_sensor, y_bac, groups_arr):
            reg = RandomForestRegressor(n_estimators=100, random_state=42)
            reg.fit(X_sensor[tr_i], y_bac[tr_i])
            for j, idx in enumerate(te_i):
                oof_bac[idx] = float(reg.predict(X_sensor[te_i][j:j + 1])[0])
        if any(v is None for v in oof_bac):
            return {"error": "Fusion refused: incomplete out-of-fold BAC estimate."}

        # ---- Visual predictions: real MobileNet inference only (FIX A7) ----
        # No ground-truth fallback: a row with no image, an unreadable image, or an
        # out-of-range class index refuses the run rather than borrowing r.label.
        try:
            from models.train.mobilenet import (
                predict_frame as _predict_frame, SAVE_DIR as _mob_save, CLASSES as _mob_classes,
            )
            mobilenet_available = os.path.exists(os.path.join(_mob_save, "mobilenet.h5"))
        except Exception as e:
            return {"error": f"Fusion refused: MobileNet inference unavailable ({e})."}
        if not mobilenet_available:
            return {"error": ("Fusion refused: models/saved/mobilenet.h5 not found. Train the 3-class face "
                              "model first (POST /training/train/mobilenet). Fusion will not substitute "
                              "ground-truth labels for visual features.")}
        oof_visual_class = []
        oof_visual_conf = []
        for r in rows:
            p = r.image_path or ""
            if not p or not os.path.exists(p):
                return {"error": (f"Fusion refused: training row id={getattr(r, 'id', '?')} has no image on "
                                  f"disk (image_path={p!r}); cannot run MobileNet for visual features.")}
            img = cv2.imread(p)
            if img is None:
                return {"error": f"Fusion refused: could not read image for row id={getattr(r, 'id', '?')} ({p!r})."}
            try:
                res = _predict_frame(img)
            except Exception as e:
                return {"error": f"Fusion refused: MobileNet inference failed for row id={getattr(r, 'id', '?')}: {e}"}
            vc, vconf = int(res["class_index"]), float(res["confidence"])
            if not (0 <= vc < len(_mob_classes)):
                return {"error": (f"Fusion refused: MobileNet returned out-of-range class_index {vc} for row "
                                  f"id={getattr(r, 'id', '?')} (expected 0..{len(_mob_classes) - 1}).")}
            oof_visual_class.append(vc)
            oof_visual_conf.append(vconf)

        # ---- Build fusion X from OOF predictions ----
        X = []; y = []; groups = []
        class_counts = {0: 0, 1: 0, 2: 0}
        for idx, r in enumerate(rows):
            ear = float(r.ear) if r.ear is not None else 0.28
            mar = float(r.mar) if r.mar is not None else 0.30
            bac_val = float(r.bac)            # ground truth — the ONLY input to the label
            bac_est = float(oof_bac[idx])     # FIX A6: feature[6] is the regressor's OOF estimate
            # Label is derived purely from measured BAC. ear/mar and the sensor /
            # visual classes are INPUT FEATURES ONLY — letting them into the label
            # rule would make the target depend on the model's own inputs.
            if bac_val >= BAC_OVER_LIMIT:   fl = 2   # over_limit
            elif bac_val >= BAC_NEAR_LIMIT: fl = 1   # near_limit
            else:                           fl = 0   # pass
            class_counts[fl] += 1
            # feature[6] = bac_est (estimate). bac_val deliberately does NOT appear here.
            X.append([oof_sensor_class[idx], oof_sensor_conf[idx], oof_visual_class[idx], oof_visual_conf[idx], ear, mar, bac_est, float(r.temperature or 27.0)])
            y.append(fl)
            groups.append(group_key(r))

        if sum(1 for c in class_counts.values() if c > 0) < 2:
            return {"error": (f"Fusion refused: rows collapse to a single fusion class. per-class counts "
                              f"{class_counts}. Need >= 2 distinct fusion labels."),
                    "class_counts": class_counts}
        if len(X) < 10:
            return {"error": f"Not enough OOF fusion rows: {len(X)}", "counts": {"rows": len(X)}}
        res = fusion_model.train(X, y, groups=groups if len(set(groups))>=3 else None)
        from models.train.metrics_store import load_metrics
        m = load_metrics().get("fusion", {})
        return {"message": "Fusion training complete via OOF (sensor_oof, mobilenet_infer, bac_oof — label from measured BAC only)",
                "samples": len(X), "source": "training_data_oof", "counts": pair_counts,
                "group_by": group_by, "groups": int(len(np.unique(groups_arr))),
                "warning": partial_tagging_warning(rows, group_by),
                "oof": {"sensor_oof": use_oof, "visual_oof": True, "bac_oof": True},
                "label_rule": {"over_limit": f">={BAC_OVER_LIMIT}",
                                  "near_limit": f">={BAC_NEAR_LIMIT}",
                                  "pass": f"<{BAC_NEAR_LIMIT}"},
                "class_counts": class_counts, "metrics": m, "results": res}
    except Exception as e:
        import traceback
        return {"error": str(e), "trace": traceback.format_exc()[:2000]}

@router.get("/statistics")
def training_statistics(db: Session = Depends(get_db)):
    """Transparent statistics for Models Statistics page — includes accuracy+reliability+failures."""
    from models.train.metrics_store import load_metrics
    metrics = load_metrics()
    # Reuse training_summary for counts/bac
    summary = training_summary(db)
    # Enrich with feature importances history etc
    # Add reliability: pending ratio, trial coverage
    total = summary.get("total",0)
    ready = summary.get("ready_to_train",0)
    pending = summary.get("pending",0)
    reliability = {
        "pending_ratio": round(pending/total*100,1) if total else 0,
        "ready_ratio": round(ready/total*100,1) if total else 0,
        "trial_coverage": summary.get("trial_counts", {}),
        "face_coverage": summary.get("face_images", {}),
        "balanced": summary.get("balanced", False),
    }
    # Failures
    failures = []
    if pending > 0:
        failures.append(f"{pending} pending rows without sensor features — waiting for ESP32")
    sensor_m = metrics.get("sensor", {})
    if not sensor_m:
        failures.append("Sensor models not trained yet")
    else:
        for k,v in sensor_m.items():
            if isinstance(v, dict) and v.get("accuracy",100) < 70:
                failures.append(f"Sensor {k} low accuracy {v.get('accuracy')}%")
    bac_m = sensor_m.get("bac_regressor") if isinstance(sensor_m, dict) else None
    if bac_m and isinstance(bac_m, dict) and bac_m.get("error"):
        failures.append(f"BAC regressor: {bac_m.get('error')}")
    if not metrics.get("mobilenet"):
        failures.append("MobileNet not trained — /mobilenet hidden trainer needed (200 each)")
    if not metrics.get("fusion"):
        failures.append("Fusion not trained — /fusion hidden trainer needed")
    # Check stale
    try:
        import os, time
        for name in ["random_forest.pkl","xgboost.pkl","mobilenet.h5"]:
            p = os.path.join(os.path.dirname(__file__), "../models/saved", name)
            if os.path.exists(p) and time.time() - os.path.getmtime(p) > 30*24*3600:
                failures.append(f"{name} stale >30 days")
    except Exception:
        pass
    return {
        "metrics": metrics,
        "summary": summary,
        "reliability": reliability,
        "failures": failures,
        "artifacts": list(metrics.keys()),
    }