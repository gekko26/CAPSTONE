#sensor.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database.db import get_db
from database.models import Reading, TrainingData, DeploymentLog
from models.train import sensor_models
from pydantic import BaseModel
from typing import List

router = APIRouter()

class SensorData(BaseModel):
    temperature: float
    humidity:    float
    bac:         float
    mq3_1:       List[float]
    mq3_2:       List[float]
    mq3_3:       List[float]

@router.post("/sensor")
def receive_sensor(data: SensorData, db: Session = Depends(get_db)):

    # 1. Predict first so we have label + confidence
    try:
        result = sensor_models.predict(
            window_1 = data.mq3_1,
            window_2 = data.mq3_2,
            window_3 = data.mq3_3,
            temp     = data.temperature,
            humidity = data.humidity,
        )
    except FileNotFoundError:
        result = {"label": "No model", "risk": "unknown",
                  "confidence": 0, "class": -1}

    # 2. Save to sensor_readings
    new_reading = Reading(
        temperature = data.temperature,
        humidity    = data.humidity,
        bac         = data.bac,
        label       = result["label"],
        model_used  = "random_forest",
    )
    db.add(new_reading)

    # 3. Extract features
    features = sensor_models.extract_features(
        data.mq3_1, data.mq3_2, data.mq3_3,
        data.temperature, data.humidity
    )

    if features:
        # 4. Save to training_data
        new_training = TrainingData(
            mq3_1_max        = features[0],
            mq3_1_avg        = features[1],
            mq3_1_std        = features[2],
            mq3_2_max        = features[3],
            mq3_2_avg        = features[4],
            mq3_2_std        = features[5],
            mq3_3_max        = features[6],
            mq3_3_avg        = features[7],
            mq3_3_std        = features[8],
            rise_time        = features[9],
            decay_time       = features[10],
            spatial_variance = features[11],
            temperature      = features[12],
            humidity         = features[13],
            bac              = data.bac,
            label            = result["class"],
            confidence       = result["confidence"],
        )
        db.add(new_training)

        # 5. Save to deployment_logs
        new_log = DeploymentLog(
            subject_id       = None,
            mq3_1_max        = features[0],
            mq3_1_avg        = features[1],
            mq3_1_std        = features[2],
            mq3_2_max        = features[3],
            mq3_2_avg        = features[4],
            mq3_2_std        = features[5],
            mq3_3_max        = features[6],
            mq3_3_avg        = features[7],
            mq3_3_std        = features[8],
            rise_time        = features[9],
            decay_time       = features[10],
            spatial_variance = features[11],
            temperature      = features[12],
            humidity         = features[13],
            prediction       = result["label"],
            confidence       = result["confidence"],
            risk_level       = result["risk"],
            model_version    = "random_forest_v1",
        )
        db.add(new_log)

    db.commit()

    return {
        "message":    "Saved",
        "label":      result["label"],
        "risk":       result["risk"],
        "confidence": result["confidence"],
    }