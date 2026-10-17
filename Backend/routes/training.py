from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, Form, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from database.db import get_db
from database.models import Subject, TrainingData
from models.train import sensor_models
from models.train.sensor_models import (
    BAC_NEAR_LIMIT,
    BAC_OVER_LIMIT,
    IMPAIRED_FACE_MIN_BAC,
)
from models.train.cv_model import analyze_frame
from pydantic import BaseModel
from typing import List, Optional
import numpy as np
import cv2
import os
import csv
import io
import logging
import json
import httpx                  
import requests               
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("AlcoDetect.Training")

# ── Collection readiness targets ─────────────────────────────────────────────
# Defined once here and echoed to the frontend so no page hardcodes a number.
# FACE_MIN_IMAGES_PER_CLASS mirrors mobilenet.MIN_IMAGES_PER_CLASS, which is the
# value the trainer itself enforces.
SENSOR_MIN_ROWS_PER_CLASS   = 30
SENSOR_TARGET_ROWS_PER_CLASS = 60
FACE_TARGET_IMAGES_PER_CLASS = 100


def FACE_MIN_IMAGES_PER_CLASS():
    """Sourced from mobilenet.MIN_IMAGES_PER_CLASS so the two can never drift.

    Imported lazily: a module-level mobilenet import would pull TensorFlow into every
    process (and every test run) that merely imports this router.
    """
    from models.train import mobilenet
    return int(mobilenet.MIN_IMAGES_PER_CLASS)
MIN_SUBJECTS   = 3
TARGET_SUBJECTS = 5
BAC_BAND_MIN_ROWS = 8
BAC_BAND_MIN_SUBJECTS = 2

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

def readiness_targets():
    """Single source of truth for the frontend. No page hardcodes a number."""
    return {
        "sensor_min_rows_per_class": SENSOR_MIN_ROWS_PER_CLASS,
        "sensor_target_rows_per_class": SENSOR_TARGET_ROWS_PER_CLASS,
        "face_min_images_per_class": FACE_MIN_IMAGES_PER_CLASS(),
        "face_target_images_per_class": FACE_TARGET_IMAGES_PER_CLASS,
        "min_subjects": MIN_SUBJECTS,
        "target_subjects": TARGET_SUBJECTS,
        "bac_band_min_rows": BAC_BAND_MIN_ROWS,
        "bac_band_min_subjects": BAC_BAND_MIN_SUBJECTS,
    }


# ── Face-mirror ownership ────────────────────────────────────────────────────
# TrainingData rows own their images through a filename suffix: "<prefix>_<row.id>.jpg".
# Only the three classes MobileNet trains on are ever candidates for a mirror.
FACE_TRAINED_CLASSES = ("sober", "fatigue", "impaired")

# Snapshot prefixes that can appear per class. mirrors are only ever written for the
# class the row belongs to, but the snapshot folder holds a couple of extra prefixes.
SNAPSHOT_PREFIXES = {
    "sober":     ("sober",),
    "fatigue":   ("fatigue", "drowsy", "yawning"),
    "impaired":  ("impaired", "impaired_perfume"),
}


def _faces_dir(class_name):
    return os.path.realpath(os.path.join(BASE_DIR, "data", "faces", class_name))


def _snapshots_dir(class_name):
    return os.path.realpath(os.path.join(BASE_DIR, "data", "snapshots", class_name))


def _contained(path, allowed_root):
    """True only when `path` resolves inside `allowed_root`. Guards every delete."""
    try:
        root = os.path.realpath(allowed_root)
        target = os.path.realpath(path)
        return os.path.commonpath([root, target]) == root and target != root
    except (ValueError, OSError):
        return False


def safe_delete(path):
    """Delete one image, but only inside a trained faces/ or snapshots/ class folder.

    Never touches data/faces/registered, never deletes a directory, and returns
    whether the file was actually removed.
    """
    if not path or not os.path.isfile(path):
        return False
    allowed = [_faces_dir(c) for c in FACE_TRAINED_CLASSES] + \
              [_snapshots_dir(c) for c in FACE_TRAINED_CLASSES]
    if not any(_contained(path, root) for root in allowed):
        logger.warning("refusing to delete %s — outside the permitted class folders", path)
        return False
    try:
        os.remove(path)
        logger.info("removed %s", path)
        return True
    except OSError:
        logger.exception("could not remove %s", path)
        return False


def _owned_face_files(row):
    """Every data/faces file this row could own. Names are derived from the row id, so
    a row never touches another row's image."""
    out = []
    for class_name in FACE_TRAINED_CLASSES:
        for prefix in SNAPSHOT_PREFIXES[class_name]:
            out.append((class_name, os.path.join(_faces_dir(class_name), f"{prefix}_{row.id}.jpg")))
    return out


def _owned_snapshot_files(row):
    """Snapshot files this row owns, taken from the recorded image_path and from the
    class naming patterns."""
    out = []
    if getattr(row, "image_path", None):
        out.append(row.image_path)
    return out


def _remove_row_images(row):
    """Delete this row's mirrored face copies and snapshot. Returns the list removed."""
    removed = []
    for _cls, path in _owned_face_files(row):
        if safe_delete(path):
            removed.append(path)
    for path in _owned_snapshot_files(row):
        if safe_delete(path):
            removed.append(path)
    return removed


def reconcile_face_mirror(row, frame=None):
    """Re-evaluate whether this row's image belongs in the MobileNet face dataset.

    Runs after the caller has applied the new label/bac, so the "still valid" test
    always reflects the CURRENT row state.

    * sober / fatigue mirrors are valid only while the row is a non-alcohol capture
      of that sub_label. Anything else is stale and gets removed.
    * an alcohol row contributes an "impaired" image only at or above
      IMPAIRED_FACE_MIN_BAC. Below that the mirror is removed.
    * a new class folder is never guessed: if a stale mirror is removed the caller is
      told, so the operator can re-capture into the right class.

    Removals only need the filename. The frame is read solely when a NEW mirror has to
    be written.
    """
    # ---- sweep the sober / fatigue mirrors against the current label ----------
    still_valid = {
        # clear_air is the pre-drinking baseline: still a sober-class capture, so its
        # mirror must never be treated as stale just because sub_label is not "sober".
        ("sober", "sober"):      (row.label == 0 and row.sub_label in (None, "sober", "clear_air")),
        ("fatigue", "drowsy"):   (row.label == 0 and row.sub_label == "drowsy"),
        ("fatigue", "yawning"):  (row.label == 0 and row.sub_label == "yawning"),
    }
    stale = []
    for (class_name, prefix), valid in still_valid.items():
        if valid:
            continue
        dest = os.path.join(_faces_dir(class_name), f"{prefix}_{row.id}.jpg")
        if safe_delete(dest):
            stale.append(dest)
    if stale:
        return {"face_dataset": "removed_from_face_dataset", "removed": stale}

    # ---- alcohol rows: the impaired mirror is BAC-gated ----------------------
    if row.label == 1:
        mirror = os.path.join(_faces_dir("impaired"), f"impaired_{row.id}.jpg")
        at_or_over = row.bac is not None and float(row.bac) >= IMPAIRED_FACE_MIN_BAC
        if at_or_over:
            if os.path.isfile(mirror):
                return {"face_dataset": "impaired", "removed": []}
            if frame is None:
                snap = getattr(row, "image_path", None)
                if not (snap and os.path.isfile(snap)):
                    return {"face_dataset": "no_image", "removed": []}
                try:
                    import cv2 as _cv2
                    frame = _cv2.imread(snap)
                except Exception:
                    logger.exception("could not read snapshot for row %s", row.id)
                    return {"face_dataset": "no_image", "removed": []}
            if frame is None:
                return {"face_dataset": "no_image", "removed": []}
            try:
                os.makedirs(os.path.dirname(mirror), exist_ok=True)
                import cv2 as _cv2
                _cv2.imwrite(mirror, frame)
                return {"face_dataset": "impaired", "removed": []}
            except Exception:
                logger.exception("could not mirror row %s into faces/impaired", row.id)
                return {"face_dataset": "impaired", "removed": [], "error": "mirror_failed"}
        removed = [mirror] if safe_delete(mirror) else []
        return {"face_dataset": "excluded_below_threshold", "removed": removed}

    return {"face_dataset": "unchanged", "removed": []}


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
    face_dataset = "excluded_below_threshold"
    if frame is not None:
        path = os.path.join(IMPAIRED_DIR, f"impaired_{row.id}.jpg")
        row.image_path = path
        db.commit()
        background_tasks.add_task(save_snapshot_task, path, frame)
        # Only a genuinely high-BAC capture is an "impaired" face. Below
        # IMPAIRED_FACE_MIN_BAC the face usually still looks sober, so mirroring it
        # would poison the MobileNet class. No other faces folder is written.
        if bac >= IMPAIRED_FACE_MIN_BAC:
            try:
                from pathlib import Path as _P
                faces_impaired = os.path.join(BASE_DIR, "data", "faces", "impaired")
                _P(faces_impaired).mkdir(parents=True, exist_ok=True)
                import cv2 as _cv2
                _cv2.imwrite(os.path.join(faces_impaired, f"impaired_{row.id}.jpg"), frame)
                face_dataset = "impaired"
            except Exception as _e:
                logger.warning("faces/impaired mirror failed for row %s: %s", row.id, _e)
        else:
            logger.info("alcohol row %s at bac=%s is below IMPAIRED_FACE_MIN_BAC=%s — not mirrored to data/faces",
                        row.id, bac, IMPAIRED_FACE_MIN_BAC)
    trigger_result = await trigger_esp32_async(row_id=row.id)
    return {"message": "Alcohol captured", "id": row.id, "subject_id": row.subject_id,
            "image_path": row.image_path, "face_dataset": face_dataset,
            "trigger": trigger_result}

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
    # Mode the firmware PINNED when /trigger was accepted. Optional so an older
    # firmware build still posts, but when present it must say "training" — otherwise
    # a live gate reading would be written into the training table.
    mode: Optional[str] = None

@router.post("/collect/sensor-data")
def receive_sensor_data(data: SensorPayload, db: Session = Depends(get_db)):
    # Defence in depth: refuse a window that was sampled in the wrong mode. Without
    # this, a deployment reading would be attached to the training row whose id happens
    # to match the reading id, silently poisoning the dataset.
    if data.mode is not None and data.mode != "training":
        raise HTTPException(status_code=409,
                            detail=f"Refusing a '{data.mode}'-mode window at the training endpoint — "
                                   "switch the device to training mode and re-trigger")
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
        old_bac = row.bac
        row.bac = payload.bac
        if payload.subject_id is not None:
            row.subject_id = payload.subject_id
        db.commit()
        # A BAC crossing IMPAIRED_FACE_MIN_BAC moves the image in or out of the
        # impaired face class.
        face = reconcile_face_mirror(row) if old_bac != payload.bac else {"face_dataset": "unchanged", "removed": []}
        return {"id": row.id, "label": row.label, "label_name": LABEL_NAMES[row.label],
                "face_dataset": face["face_dataset"], "removed_files": face["removed"]}
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
    face = reconcile_face_mirror(row)
    return {"id": row.id, "label": label, "label_name": LABEL_NAMES[label],
            "face_dataset": face["face_dataset"], "removed_files": face["removed"]}

class RelabelPayload(BaseModel):
    label: int; sub_label: Optional[str] = None; subject_id: Optional[int] = None; reason: Optional[str] = None

@router.patch("/relabel/{row_id}")
def relabel(row_id: int, payload: RelabelPayload, db: Session = Depends(get_db)):
    row = db.query(TrainingData).filter(TrainingData.id == row_id).first()
    if not row: raise HTTPException(status_code=404, detail="Not found")
    validate_subject_id(payload.subject_id, db)
    old_label = row.label
    old_sub = row.sub_label
    row.label = payload.label
    if payload.sub_label: row.sub_label = payload.sub_label
    if payload.subject_id is not None:
        row.subject_id = payload.subject_id
    db.commit()
    # The new class folder is NOT guessed. A stale mirror is removed and reported so
    # the operator can re-capture into the right class.
    face = {"face_dataset": "unchanged", "removed": []}
    if old_label != payload.label or old_sub != row.sub_label:
        face = reconcile_face_mirror(row)
    return {"id": row.id, "new_label": LABEL_NAMES[payload.label],
            "face_dataset": face["face_dataset"], "removed_files": face["removed"]}

@router.delete("/delete/{row_id}")
def delete_row(row_id: int, db: Session = Depends(get_db)):
    row = db.query(TrainingData).filter(TrainingData.id == row_id).first()
    if not row: raise HTTPException(status_code=404, detail="Not found")
    # Collect the row's own files before the delete: after it, image_path is gone.
    removed_files = _remove_row_images(row)
    db.delete(row); db.commit()
    return {"message": "deleted", "id": row_id, "removed_files": removed_files}

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


# Rows that are SUPPOSED to have a captured frame. clear_air and sanitizer are
# manual gas triggers with no camera frame, so they are never "missing" an image.
_NO_IMAGE_SUB_LABELS = {"clear_air", "sanitizer"}


def _row_expects_image(row):
    if getattr(row, "sub_label", None) in _NO_IMAGE_SUB_LABELS:
        return False
    return True


@router.get("/collection-progress")
def collection_progress(db: Session = Depends(get_db)):
    """What is still missing before training. Read-only: no writes, no deletes."""
    rows = db.query(TrainingData).filter(TrainingData.label >= 0).all()
    row_ids = {r.id for r in rows}

    # ── sensor rows per class ──
    sensor = {}
    for cls in (0, 1, 2):
        n = sum(1 for r in rows if r.label == cls and r.mq3_1_max is not None)
        sensor[str(cls)] = {
            "class": LABEL_NAMES.get(cls, cls),
            "rows": n,
            "min": SENSOR_MIN_ROWS_PER_CLASS,
            "target": SENSOR_TARGET_ROWS_PER_CLASS,
            "met_min": n >= SENSOR_MIN_ROWS_PER_CLASS,
            "met_target": n >= SENSOR_TARGET_ROWS_PER_CLASS,
        }

    # ── face images on disk + which subjects own them ──
    import glob
    from models.train import face_uploads as _fu
    faces = {}
    subjects_by_class = {}
    subject_classes = {}
    orphan_files = []
    owned_by_row = {}
    # Subjects that appear ONLY through an upload filename. They own no row, so the
    # row-derived map below can never see them.
    uploaded_subjects_by_class = {}
    for class_name in FACE_TRAINED_CLASSES:
        d = _faces_dir(class_name)
        files = sorted(glob.glob(os.path.join(d, "*"))) if os.path.isdir(d) else []
        files = [f for f in files if f.lower().endswith((".jpg", ".jpeg", ".png"))
                 and os.path.isfile(f)]
        n_captured = 0
        n_uploaded = 0
        uploaded_subjects_by_class[class_name] = set()
        for f in files:
            # An uploaded photo is identified by its own name and is NOT an orphan: it
            # never had a row and never needs one.
            parsed_upload = _fu.parse_upload_filename(os.path.basename(f))
            if parsed_upload is not None:
                n_uploaded += 1
                if parsed_upload[0] == class_name:
                    uploaded_subjects_by_class[class_name].add(parsed_upload[1])
                continue
            n_captured += 1
            stem = os.path.splitext(os.path.basename(f))[0]
            rid = None
            for prefix in SNAPSHOT_PREFIXES[class_name]:
                if stem.startswith(f"{prefix}_"):
                    tail = stem[len(prefix) + 1:]
                    if tail.isdigit():
                        rid = int(tail)
                    break
            if rid is None or rid not in row_ids:
                orphan_files.append(f)
            else:
                owned_by_row.setdefault(rid, set()).add(class_name)
        n = n_captured + n_uploaded
        faces[class_name] = {
            "images": n,
            "captured": n_captured,
            "uploaded": n_uploaded,
            "min": FACE_MIN_IMAGES_PER_CLASS(),
            "target": FACE_TARGET_IMAGES_PER_CLASS,
            "met_min": n >= FACE_MIN_IMAGES_PER_CLASS(),
            "met_target": n >= FACE_TARGET_IMAGES_PER_CLASS,
        }
        subjects_by_class[class_name] = set()

    for r in rows:
        for class_name in owned_by_row.get(r.id, set()):
            subjects_by_class[class_name].add(r.subject_id)
    # A subject whose only photos came from uploads still counts towards that class.
    for class_name in FACE_TRAINED_CLASSES:
        subjects_by_class[class_name] |= uploaded_subjects_by_class[class_name]
    for class_name in FACE_TRAINED_CLASSES:
        subjects_by_class[class_name].discard(None)

    all_subjects = set()
    for class_name in FACE_TRAINED_CLASSES:
        all_subjects |= subjects_by_class[class_name]
    per_class_subject_count = {c: len(subjects_by_class[c]) for c in FACE_TRAINED_CLASSES}
    single_class = sorted(
        sid for sid in all_subjects
        if sum(1 for c in FACE_TRAINED_CLASSES if sid in subjects_by_class[c]) == 1
    )

    # ── BAC bands over label-1 rows with a measured BAC ──
    bands = {
        "below_near":  {"label": f"< {BAC_NEAR_LIMIT}",     "rows": 0, "subjects": 0, "min": BAC_BAND_MIN_ROWS, "min_subjects": BAC_BAND_MIN_SUBJECTS},
        "near":        {"label": f"{BAC_NEAR_LIMIT}-{BAC_OVER_LIMIT}", "rows": 0, "subjects": 0, "min": BAC_BAND_MIN_ROWS, "min_subjects": BAC_BAND_MIN_SUBJECTS},
        "over":        {"label": f">= {BAC_OVER_LIMIT}",    "rows": 0, "subjects": 0, "min": BAC_BAND_MIN_ROWS, "min_subjects": BAC_BAND_MIN_SUBJECTS},
    }
    band_subs = {"below_near": set(), "near": set(), "over": set()}
    for r in rows:
        if r.label != 1 or r.bac is None:
            continue
        try:
            v = float(r.bac)
        except (TypeError, ValueError):
            continue
        key = "over" if v >= BAC_OVER_LIMIT else ("near" if v >= BAC_NEAR_LIMIT else "below_near")
        bands[key]["rows"] += 1
        if r.subject_id is not None:
            band_subs[key].add(r.subject_id)
    for key in band_subs:
        bands[key]["subjects"] = len(band_subs[key])
        bands[key]["met_min"] = bands[key]["rows"] >= BAC_BAND_MIN_ROWS
        bands[key]["met_min_subjects"] = bands[key]["subjects"] >= BAC_BAND_MIN_SUBJECTS

    # ── grouping (same helper as the trainers) ──
    usable = [r for r in rows if not getattr(r, 'auto_labeled', False) or getattr(r, 'label_confirmed', True)]
    group_by, _key = resolve_group_by(usable)

    # ── rows whose image is missing ──
    rows_missing = []
    for r in rows:
        if not _row_expects_image(r):
            continue
        snap_ok = bool(r.image_path) and os.path.isfile(r.image_path)
        if not snap_ok:
            rows_missing.append(r.id)
            continue
        if r.label == 1 and r.bac is not None and float(r.bac) >= IMPAIRED_FACE_MIN_BAC:
            if "impaired" not in owned_by_row.get(r.id, set()):
                rows_missing.append(r.id)

    # ── readiness ──
    sensor_missing = [f"{LABEL_NAMES.get(c)} rows: {sensor[str(c)]['rows']} of {SENSOR_MIN_ROWS_PER_CLASS}"
                      for c in (0, 1, 2) if not sensor[str(c)]["met_min"]]
    bac_missing = []
    for key, band in bands.items():
        if not band["met_min"]:
            bac_missing.append(f"BAC band {band['label']}: {band['rows']} of {BAC_BAND_MIN_ROWS} rows")
        elif not band["met_min_subjects"]:
            bac_missing.append(f"BAC band {band['label']}: {band['subjects']} of {BAC_BAND_MIN_SUBJECTS} subjects")
    face_missing = [f"{c} images: {faces[c]['images']} of {FACE_MIN_IMAGES_PER_CLASS()}"
                    for c in FACE_TRAINED_CLASSES if not faces[c]["met_min"]]
    subject_missing = (["need %d subjects with data, have %d" % (MIN_SUBJECTS, len(all_subjects))]
                       if len(all_subjects) < MIN_SUBJECTS else [])

    return {
        "targets": readiness_targets(),
        "sensor": sensor,
        "faces": faces,
        "subjects": {
            "total_with_data": len(all_subjects),
            "per_class": per_class_subject_count,
            "single_class_subject_ids": single_class,
            "single_class_subjects_warning": bool(single_class),
        },
        "bac_bands": bands,
        "grouping": {"group_by": group_by,
                     "warning": partial_tagging_warning(usable, group_by)},
        "ready": {
            "sensor_models": {"ready": not sensor_missing, "missing": sensor_missing},
            "bac_regressor": {"ready": not bac_missing, "missing": bac_missing},
            "mobilenet": {"ready": not (face_missing or subject_missing),
                          "missing": face_missing + subject_missing},
        },
        "integrity": {
            "orphan_files": len(orphan_files),
            "orphan_file_names": [os.path.basename(f) for f in orphan_files[:50]],
            "rows_missing_image": len(rows_missing),
            "rows_missing_image_ids": rows_missing[:50],
        },
    }


# ── Face photo uploads (PHASE A) ─────────────────────────────────────────────
# Training images no longer have to be captured one-by-one from the gate camera.
# These images train the SAME MobileNet classes and end up in the SAME folder,
# data/faces/<class>/, but they are owned by their FILENAME, not by a row.
#
# Nothing here creates a training_data row, touches sensor data, or writes to
# data/snapshots. See models/train/face_uploads.py for the naming contract that keeps
# these files disjoint from every row-owned image.
UPLOAD_LOG_PATH = os.path.join(BASE_DIR, "data", "upload_log.jsonl")

# Built from the constant so it can never drift from the BAC gate.
_IMPAIRED_UPLOAD_MESSAGE = (
    f"impaired uploads need a measured BAC of at least {IMPAIRED_FACE_MIN_BAC} and "
    f"confirmation that the person was really impaired"
)


def _append_upload_log(face_class, subject_id, stored_as, bac=None):
    """One JSON line per accepted file. Never fails the request.

    Contains no name, no image bytes and no credentials — only the class, the subject
    id, the stored filename, the BAC (impaired only) and a timestamp.
    """
    from datetime import datetime, timezone
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "face_class": face_class,
        "subject_id": subject_id,
        "stored_as": stored_as,
    }
    if face_class == "impaired" and bac is not None:
        record["bac"] = bac
    try:
        os.makedirs(os.path.dirname(UPLOAD_LOG_PATH), exist_ok=True)
        with open(UPLOAD_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        # The upload itself already succeeded on disk; a log failure must not turn a
        # good upload into a failed request.
        logger.exception("could not append to the upload log; upload itself is fine")


@router.post("/upload-faces")
async def upload_faces(
    face_class: str = Form(...),
    subject_id: int = Form(...),
    files: List[UploadFile] = File(...),
    bac: Optional[float] = Form(None),
    confirm_real: Optional[bool] = Form(None),
    db: Session = Depends(get_db),
):
    """Accept one or more face photos into data/faces/<face_class>/.

    Whole-request validation happens FIRST and rejects the entire batch, so a request
    that is wrong in some global way never writes anything. After that, files are
    processed independently: one unreadable photo never blocks the good ones.
    """
    from models.train import face_uploads as fu
    from models.train.face_recognition import FACE_MIN_SIZE

    # ---- whole-request validation (nothing is saved if any of this fails) --------
    if face_class not in fu.UPLOAD_CLASSES:
        raise HTTPException(
            status_code=400,
            detail=f"face_class must be one of {', '.join(fu.UPLOAD_CLASSES)}",
        )
    if subject_id is None:
        raise HTTPException(status_code=400, detail="subject_id is required")
    # Reuse the enrolment check so the message is byte-identical to every other route.
    subject_id = validate_subject_id(subject_id, db)

    if not files or len(files) < 1:
        raise HTTPException(status_code=400, detail="select at least one photo")
    if len(files) > fu.MAX_FILES_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"too many files: {len(files)} (max {fu.MAX_FILES_PER_REQUEST})",
        )

    if face_class == "impaired":
        # The only way to teach "impaired" is a real measured reading. Never acted.
        threshold = IMPAIRED_FACE_MIN_BAC
        if bac is None:
            raise HTTPException(status_code=400, detail=_IMPAIRED_UPLOAD_MESSAGE)
        if bac < threshold:
            raise HTTPException(status_code=400, detail=_IMPAIRED_UPLOAD_MESSAGE)
        if confirm_real is not True:
            raise HTTPException(status_code=400, detail=_IMPAIRED_UPLOAD_MESSAGE)

    target_dir = _faces_dir(face_class)
    results = []
    n_accepted = n_duplicate = n_rejected = 0

    for upload in files:
        original = (upload.filename or "upload")[:80]
        status, reason, stored_as = "rejected", None, None
        try:
            data = upload.file.read()
        except OSError:
            data = b""

        if not data:
            reason = "not_an_image"
        elif len(data) > fu.MAX_FILE_BYTES:
            reason = "too_large"
        else:
            # Decode the bytes, never trust the extension.
            frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                reason = "not_an_image"
            else:
                # Reuse the shipped detector and the shipped minimum-size constant.
                try:
                    analysis = analyze_frame(frame)
                except Exception:
                    logger.exception("analyze_frame failed on an upload; treating as no face")
                    analysis = {"status": "no_face"}
                if analysis.get("status") == "no_face":
                    reason = "no_face"
                else:
                    width = float(analysis.get("face_width") or 0.0)
                    if width < FACE_MIN_SIZE:
                        reason = "too_small"

        if reason is None:
            # Downscale the longest side only; never upscale.
            h, w = frame.shape[:2]
            longest = max(h, w)
            if longest > fu.MAX_SIDE_PX:
                scale = fu.MAX_SIDE_PX / float(longest)
                frame = cv2.resize(frame, (max(1, int(round(w * scale))),
                                            max(1, int(round(h * scale)))),
                                   interpolation=cv2.INTER_AREA)

            digest = fu.upload_digest(data)          # digest of the ORIGINAL bytes
            stored_as = fu.upload_filename(face_class, subject_id, digest)
            target = fu.safe_upload_path(BASE_DIR, face_class, stored_as)
            if target is None:
                reason = "write_failed"
            elif os.path.exists(target):
                status, reason, stored_as = "duplicate", "already_uploaded", stored_as
            else:
                ok, encoded = cv2.imencode(".jpg", frame,
                                           [int(cv2.IMWRITE_JPEG_QUALITY), fu.JPEG_QUALITY])
                if not ok:
                    status, reason, stored_as = "rejected", "write_failed", None
                else:
                    tmp = target + ".part"
                    try:
                        os.makedirs(target_dir, exist_ok=True)
                        with open(tmp, "wb") as fh:
                            fh.write(encoded.tobytes())
                        # Atomic within the same folder: a reader never sees a partial file.
                        os.replace(tmp, target)
                        status = "accepted"
                    except OSError:
                        logger.exception("could not store an upload for subject %s", subject_id)
                        if os.path.exists(tmp):
                            try:
                                os.remove(tmp)
                            except OSError:
                                logger.exception("could not remove the temporary upload file")
                        status, reason, stored_as = "rejected", "write_failed", None

        if status == "accepted":
            n_accepted += 1
            _append_upload_log(face_class, subject_id, stored_as,
                               bac=bac if face_class == "impaired" else None)
        elif status == "duplicate":
            n_duplicate += 1
        else:
            n_rejected += 1
            stored_as = None

        results.append({"name": original, "status": status,
                        "reason": reason, "stored_as": stored_as})

    return {
        "face_class": face_class,
        "subject_id": subject_id,
        "accepted": n_accepted,
        "duplicates": n_duplicate,
        "rejected": n_rejected,
        "files": results,
    }


@router.get("/uploaded-faces")
def list_uploaded_faces(face_class: Optional[str] = None):
    """List uploaded photos by filename only. Never returns image bytes."""
    from models.train import face_uploads as fu

    classes = [face_class] if face_class else list(fu.UPLOAD_CLASSES)
    for c in classes:
        if c not in fu.UPLOAD_CLASSES:
            raise HTTPException(status_code=400,
                                detail=f"unknown face_class {c!r}")
    out = []
    per_class = {}
    per_subject = {}
    for c in classes:
        d = _faces_dir(c)
        try:
            names = os.listdir(d) if os.path.isdir(d) else []
        except OSError:
            logger.exception("could not list %s", d)
            names = []
        found = 0
        for name in names:
            parsed = fu.parse_upload_filename(name)
            # ONLY upload-pattern names: capture-owned and registered files are invisible.
            if parsed is None:
                continue
            full = os.path.join(d, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            out.append({"face_class": parsed[0], "subject_id": parsed[1],
                        "name": name, "bytes": st.st_size,
                        "modified": st.st_mtime})
            found += 1
            per_subject[parsed[1]] = per_subject.get(parsed[1], 0) + 1
        per_class[c] = found
    out.sort(key=lambda r: r["modified"], reverse=True)
    return {"files": out[:500], "truncated": len(out) > 500,
            "per_class": per_class, "per_subject": per_subject,
            "total": len(out)}


@router.delete("/uploaded-faces/{face_class}/{filename}")
def delete_uploaded_face(face_class: str, filename: str):
    """Delete ONE uploaded photo.

    Everything else is refused: capture-owned names, traversal, an encoded separator,
    a name that names a different class, and anything in data/faces/registered/.
    Directories are never removed.
    """
    from models.train import face_uploads as fu
    if face_class not in fu.UPLOAD_CLASSES:
        raise HTTPException(status_code=400,
                            detail=f"unknown face_class {face_class!r}")
    target = fu.safe_upload_path(BASE_DIR, face_class, filename)
    if target is None:
        raise HTTPException(
            status_code=400,
            detail="refused: that filename is not an uploaded photo of this class",
        )
    if not os.path.isfile(target):
        raise HTTPException(status_code=404, detail="no such uploaded photo")
    if os.path.isdir(target):
        raise HTTPException(status_code=400, detail="refused: directories are never removed")
    try:
        os.remove(target)
    except OSError:
        logger.exception("could not delete the uploaded photo")
        raise HTTPException(status_code=500, detail="could not delete the uploaded photo")
    return {"status": "deleted", "face_class": face_class, "name": os.path.basename(target)}


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
    # ready_to_train = rows with complete sensor features.
    ready = sum(1 for r in rows if r.label>=0 and r.mq3_1_max is not None)
    # balanced = every MIN target met (not the aspirational TARGET values).
    balanced = (
        counts[0] >= SENSOR_MIN_ROWS_PER_CLASS
        and counts[1] >= SENSOR_MIN_ROWS_PER_CLASS
        and counts[2] >= SENSOR_MIN_ROWS_PER_CLASS
        and face_counts["sober"] >= FACE_MIN_IMAGES_PER_CLASS()
        and face_counts["fatigue"] >= FACE_MIN_IMAGES_PER_CLASS()
        and face_counts["impaired"] >= FACE_MIN_IMAGES_PER_CLASS()
    )

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
        "targets": readiness_targets(),
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
                _fmin = FACE_MIN_IMAGES_PER_CLASS()
                return {"error": (f"Not enough face data: {counts} — need {_fmin} each "
                                  f"in every class ({_fmin * len(mobilenet.CLASSES)} total)"),
                        "counts": counts, "targets": readiness_targets()}
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
        failures.append(f"MobileNet not trained — /mobilenet hidden trainer needed ({FACE_MIN_IMAGES_PER_CLASS()} images per class)")
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