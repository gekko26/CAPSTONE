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

ESP32_URL = os.getenv("ESP32_URL", "http://192.168.1.200")


# ── ESP32 trigger (backend middleman) ─────────────────────────
@router.post("/sensor/trigger")
def trigger_esp32():
    """
    Frontend calls this → backend forwards to ESP32.
    ESP32 starts buffering MQ3 window on receive.
    Frontend never talks to ESP32 directly.
    """
    try:
        r = requests.post(f"{ESP32_URL}/trigger", timeout=3)
        return {
            "triggered":    True,
            "esp32_status": r.status_code,
            "message":      "ESP32 triggered — buffering MQ3 window",
        }
    except requests.exceptions.ConnectionError:
        return {
            "triggered":    False,
            "esp32_status": None,
            "message":      "ESP32 unreachable — check ESP32_URL in .env",
        }
    except requests.exceptions.Timeout:
        return {
            "triggered":    False,
            "esp32_status": None,
            "message":      "ESP32 timeout — device may be busy",
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


@router.post("/sensor")
def receive_sensor(data: SensorData, db: Session = Depends(get_db)):
    """
    Deployment mode — ESP32 posts MQ3 window here after proximity triggers.
    Runs prediction, saves to sensor_readings and deployment_logs.

    NOTE: 15 features, correct index mapping:
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
      [12] spatial_variance_avg  ← was missing in original
      [13] temperature           ← was [12] in original (BUG FIXED)
      [14] humidity              ← was [13] in original (BUG FIXED)
    """

    # 1. Predict
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

    # 2. Save to sensor_readings
    new_reading = Reading(
        temperature=data.temperature,
        humidity=data.humidity,
        bac=data.bac,
        label=result["label"],
        model_used="random_forest",
    )
    db.add(new_reading)

    # 3. Extract features (15 features)
    features = sensor_models.extract_features(
        data.mq3_1, data.mq3_2, data.mq3_3,
        data.temperature, data.humidity,
    )

    if features:
        # 4. Save to deployment_logs
        # BUG FIX: original used features[12]=temp, features[13]=hum
        # Correct:  features[12]=spatial_variance_avg, [13]=temp, [14]=hum
        new_log = DeploymentLog(
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
            # spatial_variance_avg not in deployment_logs schema — skip
            prediction           = result["label"],
            confidence           = result["confidence"],
            risk_level           = result["risk"],
            model_version        = "random_forest_v1",
        )
        db.add(new_log)

    db.commit()

    return {
        "message":    "Saved",
        "label":      result["label"],
        "risk":       result["risk"],
        "confidence": result["confidence"],
    }