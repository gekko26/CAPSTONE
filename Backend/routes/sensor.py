from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database.db import get_db
from database.models import Reading, TrainingData, DeploymentLog
from models.train import sensor_models
from config.sensor_timing import (
    SENSOR_PREROLL_MS, SENSOR_WINDOW_MS, SENSOR_POSTROLL_MS,
    SENSOR_SETTLE_MARGIN_MS, SENSOR_READY_AFTER_MS, SENSOR_BUSY_UNTIL_MS, as_dict,
)
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

    Creates a pending Reading so /predict/full can bind to this trial with a staleness
    check. The Frontend never talks to the ESP32 directly.

    Order matters here and is deliberate:

      1. ask the device which mode it is in
      2. refuse a gate trigger while the device is collecting training data
      3. only THEN create the pending Reading
      4. forward to the ESP32, and roll the row back if it will not accept it

    Nothing touches the database until the device has confirmed it is in deployment
    mode, so a blocked or unreachable device can never leave an orphan pending row.
    """
    # 1. What mode is the device in?
    mode = None
    try:
        sr = requests.get(f"{ESP32_URL}/status", timeout=2)
        if sr.status_code == 200:
            mode = sr.json().get("mode")
    except Exception:
        mode = None

    # 2. Never mix a gate trigger into a training session.
    if mode == "training":
        return {
            "triggered":    False,
            "busy":         False,
            "blocked":      True,
            "reason":       "device_in_training_mode",
            "mode":         "training",
            "esp32_status": None,
            "message":      "Device is in training mode — gate trigger refused so it "
                            "cannot pollute the training data",
            "reading_id":   None,
        }
    if mode is None:
        # 3a. We cannot confirm the device is in deployment mode, so do not write.
        return {
            "triggered":    False,
            "busy":         False,
            "blocked":      True,
            "reason":       "device_mode_unknown",
            "esp32_status": None,
            "message":      "ESP32 unreachable — cannot confirm device mode, "
                            "so no reading was created",
            "reading_id":   None,
        }

    # 3b. First database write of the whole endpoint.
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

    def _drop_pending(reason, status):
        """Roll the row back so a failed trigger leaves no orphan pending reading."""
        db.delete(pending)
        db.commit()
        return {
            "triggered":    False,
            "busy":         status == 409,
            "blocked":      False,
            "reason":       reason,
            "esp32_status": status,
            "message":      reason,
            "reading_id":   None,
        }

    # 4. Forward to the device.
    try:
        r = requests.post(f"{ESP32_URL}/trigger", timeout=3, json={"reading_id": reading_id})
    except requests.exceptions.ConnectionError:
        return _drop_pending("ESP32 unreachable — check ESP32_URL in .env", None)
    except requests.exceptions.Timeout:
        return _drop_pending("ESP32 timeout — device did not answer", None)

    if r.status_code == 409:
        # Either the relay sequence is still running, or it is refusing a gate
        # trigger because it is in training mode. Neither should leave a row.
        body = {}
        try:
            body = r.json()
        except ValueError:
            body = {}
        if body.get("reason") == "device_in_training_mode":
            return _drop_pending(
                "Device is in training mode — gate trigger refused so it cannot "
                "pollute the training data", 409)
        # Busy: the device is mid-sequence and will never post this window, so the
        # row has to go too — otherwise every busy click strands a pending reading
        # that /predict/full could later bind to.
        result = _drop_pending(body.get("message",
                                        "ESP32 busy — relay sequence still running"),
                               409)
        result["busy"] = True
        result["busy_row_id"]      = body.get("busy_row_id")
        result["incoming_row_id"]  = body.get("incoming_row_id")
        result["sequence_state"]   = body.get("sequence_state")
        result["busy_until_ms"]    = SENSOR_BUSY_UNTIL_MS
        return result
    if r.status_code != 200:
        return _drop_pending(f"ESP32 refused the trigger ({r.status_code})", r.status_code)

    return {
        "triggered":    True,
        "busy":         False,
        "blocked":      False,
        "esp32_status": r.status_code,
        "message":      "ESP32 triggered — relay sequence started",
        "mode":         mode,
        "reading_id":   reading_id,
        "busy_until_ms": SENSOR_BUSY_UNTIL_MS,
    }


@router.get("/sensor/timing")
def sensor_timing():
    """Timing the web side uses, plus a best-effort comparison against the firmware.

    Read-only. A failure to reach the ESP32 is reported as firmware=None, matches=None
    with HTTP 200 — it never turns into an error status.
    """
    out = as_dict()
    firmware = None
    try:
        r = requests.get(f"{ESP32_URL}/status", timeout=1)
        r.raise_for_status()
        firmware = r.json()
    except Exception:
        return {**out, "firmware": None, "matches": None, "diff": None}

    diff = {}
    for key in ("preroll_ms", "buffer_ms", "postroll_ms"):
        if key in firmware and firmware[key] != out[key]:
            diff[key] = {"firmware": firmware[key], "backend": out[key]}
    return {**out, "firmware": firmware, "matches": len(diff) == 0, "diff": diff or None}


@router.get("/sensor/status")
def esp32_status():
    """Check if ESP32 is reachable. Frontend polls every 10s."""
    try:
        r = requests.get(f"{ESP32_URL}/status", timeout=3)
        # Additive only — the existing keys are unchanged. The firmware tells us which
        # table it will post a sampled window to, so the UI never has to guess.
        info = {}
        try:
            info = r.json() if r.status_code == 200 else {}
        except ValueError:
            info = {}
        return {
            "online": True,
            "esp32_status": r.status_code,
            "mode": info.get("mode"),
            "sequence_state": info.get("sequence_state"),
            "fan_on": info.get("fan_on"),
            "blower_on": info.get("blower_on"),
        }
    except Exception:
        return {"online": False, "esp32_url": ESP32_URL, "mode": None, "sequence_state": None}


# ── ESP32 device mode (training <-> deployment) ───────────────
# The firmware decides which table a sampled window lands in, so this pair of routes
# is how the UI changes that decision. They are proxies: the frontend never talks to
# the ESP32 directly.
VALID_MODES = {"training", "deployment"}


@router.get("/sensor/mode")
def get_sensor_mode():
    """Read the ESP32's current mode. Never errors on an unreachable device."""
    try:
        r = requests.get(f"{ESP32_URL}/status", timeout=2)
        info = r.json() if r.status_code == 200 else {}
        return {
            "mode": info.get("mode"),
            "online": r.status_code == 200,
            "sequence_state": info.get("sequence_state"),
            "writable": info.get("sequence_state") == "idle",
        }
    except Exception as e:
        return {"mode": None, "online": False, "sequence_state": None,
                "writable": False, "error": str(e)[:200]}


@router.post("/sensor/mode")
def set_sensor_mode(payload: dict):
    """Switch the ESP32 between training and deployment.

    400 invalid value, 409 while a relay sequence is running, 503 device unreachable.
    The choice is persisted by the firmware in NVS, so it survives a power cut.
    """
    mode = (payload or {}).get("mode")
    if mode not in VALID_MODES:
        raise HTTPException(status_code=400,
                            detail=f"mode must be one of {sorted(VALID_MODES)}")
    training = "1" if mode == "training" else "0"
    try:
        r = requests.get(f"{ESP32_URL}/mode?training={training}", timeout=3)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"ESP32 unreachable — {e}")
    if r.status_code == 409:
        raise HTTPException(status_code=409,
                            detail="Relay sequence running — wait until idle to change mode")
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"ESP32 refused the mode change ({r.status_code})")
    try:
        confirmed = r.json().get("mode")
    except ValueError:
        confirmed = mode
    return {"mode": confirmed, "message": "Mode updated", "persisted": True}


# ── Deployment indicator bench (PHASE 1) ───────────────────────────────────
# Protected endpoint used by Settings -> Sensors -> Indicators. It never touches the
# database and never influences a decision.
#
#   503  ESP32_DEVICE_TOKEN is not set in the backend environment (fail closed)
#   401  the device rejected the token (wrong build, or no token compiled in)
#   409  a relay sequence is running — refused so the bench cannot corrupt a run
#   502  the ESP32 could not be reached at all
#
# The token is read from the environment and is never logged or echoed back.
@router.post("/sensor/indicator/test")
def indicator_bench(payload: dict):
    from config.indicators import (
        ESP32_DEVICE_TOKEN,
        INDICATOR_TEST_VOLUME,
        INDICATOR_TEST_STATES,
        token_configured,
    )

    if not token_configured():
        raise HTTPException(
            status_code=503,
            detail="ESP32_DEVICE_TOKEN is not set on the backend — the bench is "
                   "disabled. Export it in the Backend environment (see "
                   "docs/INDICATORS_WIRING.md).",
        )

    state = payload.get("state")
    # Validate the name the UI actually sends FIRST, then translate it for the wire.
    # ("off" is the UI's word; the firmware's state name is "idle".)
    if state not in INDICATOR_TEST_STATES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown bench state. Allowed: {', '.join(INDICATOR_TEST_STATES)}",
        )
    if state == "off":
        state = "idle"

    volume = payload.get("volume", INDICATOR_TEST_VOLUME)
    try:
        volume = max(0, min(30, int(volume)))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="volume must be an integer 0..30")

    # Refuse while a run is in flight: the bench must not repaint a live run.
    try:
        st = requests.get(f"{ESP32_URL}/status", timeout=2)
        if st.status_code == 200:
            seq = st.json().get("sequence_state")
            if seq and seq != "idle":
                raise HTTPException(
                    status_code=409,
                    detail=f"Relay sequence is '{seq}' — the indicator bench is "
                           f"refused while a run is in progress",
                )
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"ESP32 unreachable — {type(e).__name__}")

    body = {"state": state, "volume": volume}
    try:
        r = requests.post(
            f"{ESP32_URL}/indicator",
            json=body,
            headers={"X-Device-Token": ESP32_DEVICE_TOKEN},
            timeout=2,
        )
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"ESP32 unreachable — {type(e).__name__}")

    if r.status_code == 401:
        raise HTTPException(
            status_code=401,
            detail="The device rejected the token. Check that secrets.h on the ESP32 "
                   "holds the same value as ESP32_DEVICE_TOKEN, and reflash.",
        )
    if r.status_code == 409:
        raise HTTPException(status_code=409, detail="Device refused the state — it is busy.")
    if r.status_code != 200:
        raise HTTPException(status_code=502,
                            detail=f"ESP32 refused the indicator command ({r.status_code})")
    try:
        return {"status": "ok", "state": state, "device": r.json()}
    except ValueError:
        return {"status": "ok", "state": state}


# ── Deployment sensor reading (ESP32 posts here in deployment) ─
class SensorData(BaseModel):
    temperature: float
    humidity:    float
    bac:         float | None = None
    mq3_1:       List[float]
    mq3_2:       List[float]
    mq3_3:       List[float]
    reading_id: int | None = None
    # Mode the firmware PINNED when /trigger was accepted. Optional so an older
    # firmware build still posts, but when present it must say "deployment" — this
    # endpoint owns sensor_readings.
    mode:        str | None = None



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
    # Defence in depth: this endpoint owns sensor_readings. A window sampled in
    # training mode belongs to the training table and must not be logged as a gate
    # reading here.
    if data.mode is not None and data.mode != "deployment":
        raise HTTPException(status_code=409,
                            detail=f"Refusing a '{data.mode}'-mode window at the deployment endpoint — "
                                   "switch the device to deployment mode and re-trigger")

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