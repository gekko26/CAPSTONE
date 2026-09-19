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

ESP32_URL = os.getenv("ESP32_URL", "http://192.168.69.2")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOTS_DIR = os.path.join(BASE_DIR, "data", "snapshots")
IMPAIRED_DIR  = os.path.join(SNAPSHOTS_DIR, "impaired")
DROWSY_DIR    = os.path.join(SNAPSHOTS_DIR, "drowsy")
SOBER_DIR     = os.path.join(SNAPSHOTS_DIR, "sober")
YAWNING_DIR   = os.path.join(SNAPSHOTS_DIR, "yawning")

os.makedirs(IMPAIRED_DIR, exist_ok=True)
os.makedirs(DROWSY_DIR, exist_ok=True)
os.makedirs(SOBER_DIR, exist_ok=True)
os.makedirs(YAWNING_DIR, exist_ok=True)

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
        # 409 means already buffering — treat as triggered (ESP32 busy is ok for training)
        if r.status_code in (200, 409):
            return {"triggered": True, "status": r.status_code, "row_id": row_id}
        return {"triggered": False, "status": r.status_code, "error": r.text[:200]}
    except Exception as e:
        return {"triggered": False, "error": f"ESP32 offline: {e}"}

def decode_frame(contents: bytes):
    np_arr = np.frombuffer(contents, np.uint8)
    frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    return frame

def build_pending_row(label: int, sub_label: str = None, bac: float = None) -> TrainingData:
    # trial_id is assigned after insert (id == trial_id for first row of trial)
    return TrainingData(label=label, sub_label=sub_label, bac=bac, trial_id=None)

@router.post("/collect/clear_air")
def collect_clear_air(db: Session = Depends(get_db)):
    trigger_result = trigger_esp32()
    row = build_pending_row(label=0, sub_label="clear_air", bac=0.00)
    db.add(row)
    db.commit()
    db.refresh(row)
    # trial_id = id for first row of trial (grouped k-fold)
    if row.trial_id is None:
        row.trial_id = row.id
        db.commit()
    return {"message": "Clear air baseline triggered", "id": row.id, "trigger": trigger_result}

@router.post("/collect/sober")
async def collect_sober(background_tasks: BackgroundTasks, file: UploadFile = File(...), db: Session = Depends(get_db)):
    contents = await file.read()
    frame = decode_frame(contents)
    row = TrainingData(label=0, sub_label=None, bac=0.00)
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
    trigger_result = await trigger_esp32_async(row_id=row.id)
    return {"message": "Sober baseline captured", "id": row.id, "image_path": row.image_path, "trigger": trigger_result}

@router.post("/collect/drowsy")
async def collect_drowsy(background_tasks: BackgroundTasks, file: UploadFile = File(...), db: Session = Depends(get_db)):
    contents = await file.read()
    frame = decode_frame(contents)
    row = TrainingData(label=0, sub_label="drowsy", bac=0.00)
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
        path = os.path.join(DROWSY_DIR, f"drowsy_{row.id}.jpg")
        row.image_path = path
        db.commit()
        background_tasks.add_task(save_snapshot_task, path, frame)
    trigger_result = await trigger_esp32_async(row_id=row.id)
    return {"message": "Drowsy baseline captured", "id": row.id, "image_path": row.image_path, "trigger": trigger_result}

@router.post("/collect/yawning")
async def collect_yawning(background_tasks: BackgroundTasks, file: UploadFile = File(...), db: Session = Depends(get_db)):
    contents = await file.read()
    frame = decode_frame(contents)
    row = TrainingData(label=0, sub_label="yawning", bac=0.00)
    try:
        cv = analyze_frame(frame) if frame is not None else {}
        row.ear = cv.get("ear"); row.mar = cv.get("mar"); row.head_pitch = cv.get("pitch")
    except: pass
    db.add(row); db.commit(); db.refresh(row)
    if row.trial_id is None:
        row.trial_id = row.id
        db.commit()
    if frame is not None:
        path = os.path.join(YAWNING_DIR, f"yawning_{row.id}.jpg")
        row.image_path = path
        db.commit()
        background_tasks.add_task(save_snapshot_task, path, frame)
    trigger_result = await trigger_esp32_async(row_id=row.id)
    return {"message": "Yawning baseline captured", "id": row.id, "image_path": row.image_path, "trigger": trigger_result}

@router.post("/collect/alcohol")
async def collect_alcohol(background_tasks: BackgroundTasks, file: UploadFile = File(...), bac: float = Form(...), db: Session = Depends(get_db)):
    contents = await file.read()
    frame = decode_frame(contents)
    row = TrainingData(label=1, sub_label=None, bac=bac)
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
    trigger_result = await trigger_esp32_async(row_id=row.id)
    return {"message": "Alcohol captured", "id": row.id, "image_path": row.image_path, "trigger": trigger_result}

@router.post("/collect/sanitizer")
def collect_sanitizer(db: Session = Depends(get_db)):
    trigger_result = trigger_esp32()
    row = build_pending_row(label=2, sub_label="sanitizer", bac=0.00)
    db.add(row); db.commit(); db.refresh(row)
    if row.trial_id is None:
        row.trial_id = row.id
        db.commit()
        # propagate trial_id to ESP32 for grouped sensor-data
        # ESP32 will include row_id in its POST to /collect/sensor-data
    return {"message": "Sanitizer context triggered", "id": row.id, "trigger": trigger_result}

@router.post("/collect/perfume")
async def collect_perfume(background_tasks: BackgroundTasks, file: UploadFile = File(...), db: Session = Depends(get_db)):
    contents = await file.read()
    frame = decode_frame(contents)
    row = TrainingData(label=2, sub_label="perfume", bac=0.00)
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

    parent = db.query(TrainingData).filter(TrainingData.id == data.row_id).first() if data.row_id else db.query(TrainingData).filter(TrainingData.mq3_1_max == None).order_by(TrainingData.date.desc()).first()
    if not parent:
        parent = TrainingData(label=-1, trial_id=None)
        db.add(parent); db.commit(); db.refresh(parent)
        parent.trial_id = parent.id
        db.commit()

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
    bac: float; is_sanitizer: bool = False

@router.patch("/label/{row_id}")
def attach_label(row_id: int, payload: LabelPayload, db: Session = Depends(get_db)):
    row = db.query(TrainingData).filter(TrainingData.id == row_id).first()
    if not row: raise HTTPException(status_code=404, detail="Not found")
    if row.label == 1:
        row.bac = payload.bac
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
        if r.bac < 0.02: bac_by_tier["trace"] += 1
        elif r.bac < 0.05: bac_by_tier["light"] += 1
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

    # Face images — count all snapshot dirs including yawning (was missing)
    def count_dir(p): 
        return len([f for f in os.listdir(p) if f.lower().endswith(('.jpg','.jpeg','.png'))]) if os.path.exists(p) else 0
    face_counts = {
        "sober": count_dir(SOBER_DIR),
        "drowsy": count_dir(DROWSY_DIR),
        "yawning": count_dir(YAWNING_DIR),
        "impaired": count_dir(IMPAIRED_DIR),
    }
    # keep legacy impaired as alias for backwards compat, frontend uses sober/drowsy/impaired
    # also compute ready_to_train (rows with complete sensor features) and balanced flag
    # Plan: 1000 per sensor event (3000 total) + 200 per face (800 total with yawning as 4th class)
    ready = sum(1 for r in rows if r.label>=0 and r.mq3_1_max is not None)
    # Sensor: 1000 each, Face: 200 each (sober/drowsy/yawning/impaired)
    balanced = (
        counts[0] >= 1000 and counts[1] >= 1000 and counts[2] >= 1000
        and face_counts["sober"] >= 200
        and face_counts["drowsy"] >= 200
        and face_counts["yawning"] >= 200
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
        "bac_stats": {"count": len(bac_rows), "levels": bac_levels, "by_tier": bac_by_tier, "metrics": bac_metrics, "ph_limit": 0.05},
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
    # Only rows with trial_id can be grouped; fallback to None for legacy rows
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
        groups.append(r.trial_id if r.trial_id is not None else r.id)
        # BAC regressor — only breath alcohol with measured BAC
        if r.label == 1 and r.bac is not None and r.bac > 0.001:
            X_bac.append(feat)
            y_bac.append(float(r.bac))
            g_bac.append(r.trial_id if r.trial_id is not None else r.id)
    if not X:
        return {"message": "No data with sensor features", "samples": 0}
    clf_results = sensor_models.train(X, y, groups=groups)
    bac_results = None
    if X_bac:
        bac_results = sensor_models.train_bac(X_bac, y_bac, groups=g_bac)
    return {"message": "Training running", "samples": len(X), "trials": len(set(groups)), "results": clf_results, "bac_results": bac_results, "bac_samples": len(X_bac)}


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
                return {"error": f"Not enough face data: {counts} — need 200 each (800 total)", "counts": counts}
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
    """Hidden /fusion trainer — NO deployment_logs required. Uses logs if available, else synthetic demo."""
    try:
        from models.train import fusion_model
        from database.models import DeploymentLog
        import numpy as np
        logs = db.query(DeploymentLog).limit(200).all()
        X = []
        y = []
        source = "deployment_logs"
        if len(logs) >= 10:
            for l in logs:
                if l.prediction is None:
                    continue
                label_map = {"pass":0, "near_limit":1, "over_limit":2, "Pass":0, "Near Limit":1, "Over Limit":2}
                fl = label_map.get(l.prediction, 0)
                vc = 0
                X.append([fl, float(l.confidence or 0.5), vc, 0.5, 0.25, 0.0, float(l.temperature or 27.0), float(l.humidity or 60.0)])
                y.append(fl)
        # If not enough real logs, synthesize demo (so fusion does NOT require deployment_logs)
        if len(X) < 10:
            source = "synthetic_demo"
            rng = np.random.default_rng(42)
            # 60 samples balanced across 3 fusion labels
            for _ in range(60):
                fl = int(rng.integers(0,3))
                # sensor_class mirrors fusion label approx
                sensor_conf = float(rng.uniform(0.6,0.95))
                visual = int(rng.integers(0,4))  # 0-3 mobilenet 4-class
                visual_conf = float(rng.uniform(0.5,0.95))
                ear = float(rng.uniform(0.15,0.35) if fl==2 else rng.uniform(0.25,0.40))
                X.append([fl, sensor_conf, visual, visual_conf, ear, 0.0, 27.0, 60.0])
                y.append(fl)
        res = fusion_model.train(X, y)
        from models.train.metrics_store import load_metrics
        m = load_metrics().get("fusion", {})
        return {"message": f"Fusion training complete via {source}", "samples": len(X), "source": source, "metrics": m, "results": res}
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