from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database.db import get_db
from database.models import Reading, TrainingData, DeploymentLog
from models.train import sensor_models
from pydantic import BaseModel
from typing import List
import os
import requests
from dotenv import load_dotenv

load_dotenv()

router = APIRouter()

ESP32_URL = os.getenv("ESP32_URL", "http://192.168.69.2")  # aligned with Backend/.env


# ── ESP32 trigger (backend middleman) ─────────────────────────
@router.post("/sensor/trigger")
def trigger_esp32(db: Session = Depends(get_db)):
    """
    Frontend calls this → backend forwards to ESP32.
    Creates a pending Reading so /predict/full can bind to this trial with staleness check.
    ESP32 starts buffering MQ3 window on receive.
    Frontend never talks to ESP32 directly.
    """
    pending = Reading(
        temperature=None,
        humidity=None,
        label="pending",
        model_used="pending_trigger",
    )
    db.add(pending)
    db.commit()
    db.refresh(pending)
    reading_id = pending.id
    try:
        r = requests.post(f"{ESP32_URL}/trigger", timeout=3, json={"reading_id": reading_id})
        return {
            "triggered":    True,
            "esp32_status": r.status_code,
            "message":      "ESP32 triggered — buffering MQ3 window",
            "reading_id":   reading_id,
        }
    except requests.exceptions.ConnectionError:
        return {
            "triggered":    False,
            "esp32_status": None,
            "message":      "ESP32 unreachable — check ESP32_URL in .env",
            "reading_id":   reading_id,
        }
    except requests.exceptions.Timeout:
        return {
            "triggered":    False,
            "esp32_status": None,
            "message":      "ESP32 timeout — device may be busy",
            "reading_id":   reading_id,
        }


@router.get("/sensor/status")
def esp32_status():
    """Check if ESP32 is reachable. Frontend polls every 10s."""
    try:
        r = requests.get(f"{ESP32_URL}/status", timeout=3)
        return {"online": True, "esp32_status": r.status_code}
    except Exception:
        return {"online": False, "esp32_url": ESP32_URL}


# ── Deployment sensor reading (ESP32 posts here in deployment) ─
class SensorData(BaseModel):
    temperature: float
    humidity:    float
    bac:         float
    mq3_1:       List[float]
    mq3_2:       List[float]
    mq3_3:       List[float]
    reading_id: int | None = None


@router.post("/sensor")
def receive_sensor(data: SensorData, db: Session = Depends(get_db)):
    """
    Deployment mode — ESP32 posts MQ3 window here after proximity triggers.
    Runs prediction, saves to sensor_readings and deployment_logs.

    Plan A topology: mq1=nose, mq2=jaw(mouth+neck), mq3=upperChest clavicle
    NOTE: 18 features (15 base + 3 Plan A directional):
      [0]  mq3_1_max (nose)
      [1]  mq3_1_avg
      [2]  mq3_1_std
      [3]  mq3_2_max (jaw)
      [4]  mq3_2_avg
      [5]  mq3_2_std
      [6]  mq3_3_max (clavicle)
      [7]  mq3_3_avg
      [8]  mq3_3_std
      [9]  rise_time
      [10] decay_time
      [11] spatial_variance_max
      [12] spatial_variance_avg
      [13] temperature
      [14] humidity
      [15] breath_ratio (nose+jaw)/2 / chest
      [16] sanitizer_ratio (jaw+chest)/2 / nose
      [17] spatial_direction chest_max - nose_max
    """

    # 1. Predict (classifier 0/1/2)
    try:
        result = sensor_models.predict(
            window_1=data.mq3_1,
            window_2=data.mq3_2,
            window_3=data.mq3_3,
            temp=data.temperature,
            humidity=data.humidity,
        )
    except FileNotFoundError:
        result = {
            "label":      "No model",
            "risk":       "unknown",
            "confidence": 0,
            "class":      -1,
        }

    # 1b. PH BAC estimate — only if breath alcohol predicted
    bac_est = None
    bac_tier = None
    ph_verdict = None
    if result.get("class") == 1:
        try:
            est = sensor_models.predict_bac(data.mq3_1, data.mq3_2, data.mq3_3, data.temperature, data.humidity)
            if "estimated_bac" in est and est["estimated_bac"] is not None:
                bac_est = est["estimated_bac"]
                bac_tier = est.get("tier")
                ph_verdict = est.get("ph_verdict")
                # Override risk with PH tier when over limit
                if bac_tier == "over":
                    result["risk"] = "high"
                    result["ph_verdict"] = "PH FAIL"
                elif bac_tier in ("trace", "light"):
                    result["ph_verdict"] = "PH PASS"
        except FileNotFoundError:
            pass
        except Exception:
            pass

    # 2. Save to sensor_readings — update pending trigger row if reading_id provided, else create new
    if data.reading_id is not None:
        pending = db.query(Reading).filter(Reading.id == data.reading_id).first()
        if pending is not None and pending.label == "pending":
            pending.temperature = data.temperature
            pending.humidity = data.humidity
            pending.bac = data.bac
            pending.estimated_bac = bac_est
            pending.bac_tier = bac_tier
            pending.label = result["label"]
            pending.model_used = "random_forest"
            new_reading = pending
        else:
            new_reading = Reading(
                temperature=data.temperature,
                humidity=data.humidity,
                bac=data.bac,
                estimated_bac=bac_est,
                bac_tier=bac_tier,
                label=result["label"],
                model_used="random_forest",
            )
            db.add(new_reading)
    else:
        new_reading = Reading(
            temperature=data.temperature,
            humidity=data.humidity,
            bac=data.bac,
            estimated_bac=bac_est,
            bac_tier=bac_tier,
            label=result["label"],
            model_used="random_forest",
        )
        db.add(new_reading)

    # 3. Extract features (18 Plan A features)
    features = sensor_models.extract_features(
        data.mq3_1, data.mq3_2, data.mq3_3,
        data.temperature, data.humidity,
    )

    if features:
        # 4. Save to deployment_logs (18D) — FIX 8 dedup: one log per reading_id
        # Ensure Reading is flushed so id is available
        db.flush()
        rid = new_reading.id if 'new_reading' in locals() and getattr(new_reading, 'id', None) else data.reading_id
        existing_log = db.query(DeploymentLog).filter(DeploymentLog.reading_id == rid).first() if rid else None
        if existing_log:
            # Update existing (upsert from predict/full race)
            existing_log.mq3_1_max = features[0]; existing_log.mq3_1_avg = features[1]; existing_log.mq3_1_std = features[2]
            existing_log.mq3_2_max = features[3]; existing_log.mq3_2_avg = features[4]; existing_log.mq3_2_std = features[5]
            existing_log.mq3_3_max = features[6]; existing_log.mq3_3_avg = features[7]; existing_log.mq3_3_std = features[8]
            existing_log.rise_time = features[9]; existing_log.decay_time = features[10]
            existing_log.spatial_variance = features[11]; existing_log.spatial_variance_avg = features[12] if len(features) > 12 else None
            existing_log.breath_ratio = features[15] if len(features) > 15 else None; existing_log.sanitizer_ratio = features[16] if len(features) > 16 else None; existing_log.spatial_direction = features[17] if len(features) > 17 else None
            existing_log.temperature = features[13] if len(features) > 13 else data.temperature; existing_log.humidity = features[14] if len(features) > 14 else data.humidity
            existing_log.prediction = result["label"]; existing_log.confidence = result["confidence"]; existing_log.risk_level = result.get("ph_verdict") or result["risk"]
            existing_log.estimated_bac = bac_est; existing_log.bac_tier = bac_tier; existing_log.model_version = "random_forest_v1_18D"
        else:
            new_log = DeploymentLog(
                reading_id           = rid,
                subject_id           = None,
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
                spatial_variance_avg = features[12] if len(features) > 12 else None,
                breath_ratio         = features[15] if len(features) > 15 else None,
                sanitizer_ratio      = features[16] if len(features) > 16 else None,
                spatial_direction    = features[17] if len(features) > 17 else None,
                temperature          = features[13] if len(features) > 13 else data.temperature,
                humidity             = features[14] if len(features) > 14 else data.humidity,
                prediction           = result["label"],
                confidence           = result["confidence"],
                risk_level           = result.get("ph_verdict") or result["risk"],
                estimated_bac        = bac_est,
                bac_tier             = bac_tier,
                model_version        = "random_forest_v1_18D",
            )
            db.add(new_log)

    db.commit()

    return {
        "message":    "Saved",
        "label":      result["label"],
        "risk":       result.get("ph_verdict") or result["risk"],
        "confidence": result["confidence"],
        "estimated_bac": bac_est,
        "bac_tier":      bac_tier,
        "ph_verdict":    result.get("ph_verdict") or ph_verdict,
    }