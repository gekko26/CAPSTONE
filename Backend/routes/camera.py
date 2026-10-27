import os

# 1. THE STABLE SPEED HACK: TCP for reliability, nobuffer for speed.
# This prevents the av_frame_get_buffer Out of Memory crash.
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay"

from fastapi import APIRouter, Depends, File, UploadFile, Form, HTTPException
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session
from dotenv import load_dotenv
from database.db import get_db
from database.models import Reading, Subject
from models.train.cv_model import analyze_frame, draw_overlay
from models.train.face_recognition import (
    identify, enroll, FACE_MATCH_THRESHOLD, FACE_MATCH_MARGIN,
    FACE_MIN_SIZE, FACE_CONSECUTIVE_FRAMES, FACE_IDENT_MAX_HZ,
)
from models.train.identity_matching import IdentitySmoother
import numpy as np
import cv2
import time
import threading
import logging

load_dotenv()

router = APIRouter(prefix="/camera", tags=["Camera"])

REGISTERED = os.path.join(os.path.dirname(__file__), "../data/faces/registered")
os.makedirs(REGISTERED, exist_ok=True)

RTSP_URL = os.getenv("RTSP_URL")
ESP32_URL = os.getenv("ESP32_URL", "http://192.168.69.2").rstrip("/")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AlcoDetect.Camera")
logger.setLevel(logging.INFO)

if not RTSP_URL:
    logger.warning("RTSP_URL not set in .env — camera endpoints will return 503.")
  
# ── Deployment indicators (PHASE 1) ─────────────────────────────────────────
# Read-only probe of the gate device, used only to decide whether an indicator
# command is appropriate. It is a 1 s call so the analyse endpoint keeps its
# timing budget. Returns (reachable, mode, idle) and NEVER raises, so an
# unreachable device simply makes the caller say nothing at all.
def _device_mode_and_idle():
    try:
        import requests
        r = requests.get(f"{ESP32_URL}/status", timeout=1)
        if r.status_code != 200:
            return False, None, False
        j = r.json()
        return True, j.get("mode"), j.get("sequence_state") == "idle"
    except Exception:                      # noqa: BLE001 - probe must never raise
        return False, None, False


# ── Persistent RTSP capture — runs in background thread ──────
class RTSPStream:
    def __init__(self, url: str):
        self.url     = url
        self.frame   = None
        self.lock    = threading.Lock()
        self.active  = False
        self._thread = None
        self.connected = False

    def start(self):
        if not self.url:
            return
        self.active  = True
        self._thread = threading.Thread(target=self._capture, daemon=True)
        self._thread.start()

    def _capture(self):
        cap = None
        fail_count = 0  # granularity: retry immediately for transient hiccups, sleep 2 only on genuine drop
        logger.info("[RTSP THREAD] Capture loop initiated.")
        while self.active:
            try:
                if cap is None or not cap.isOpened():
                    logger.info(f"[RTSP THREAD] Connecting to: {self.url}")
                    cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
                    # Force OpenCV to hold only the absolute newest frame in memory
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    fail_count = 0

                # Use standard read() instead of grab/retrieve to prevent H.264 header corruption
                ret, frame = cap.read()
                if ret and frame is not None:
                    fail_count = 0
                    # 2. Downscale instantly to 360p (640x360) to keep AI processing lightning fast
                    frame = cv2.resize(frame, (640, 360), interpolation=cv2.INTER_AREA)

                    with self.lock:
                        self.frame = frame
                        self.connected = True
                else:
                    fail_count += 1
                    if fail_count < 5:
                        # transient hiccup (WiFi retry, brief stall) — retry immediately, no blackout
                        continue
                    logger.warning(f"[RTSP THREAD] Frame dropped {fail_count}×. Reconnecting...")
                    self.connected = False
                    fail_count = 0
                    if cap:
                        cap.release()
                    cap = None
                    time.sleep(2)
                    
            except Exception as e:
                logger.error(f"[RTSP THREAD] Critical capture error: {e}")
                self.connected = False
                fail_count = 0
                if cap:
                    cap.release()
                cap = None
                time.sleep(2)

        if cap:
            cap.release()

    def read(self) -> np.ndarray | None:
        with self.lock:
            return self.frame.copy() if self.frame is not None else None

    def stop(self):
        self.active = False


_stream = RTSPStream(RTSP_URL)
_stream.start()

def get_rtsp_frame() -> np.ndarray | None:
    return _stream.read()

_analyze_lock = threading.Lock()
_last_analysis = None

# ── Identity smoothing + rate limiting ────────────────────────────────────────
# One smoother per camera stream. It is guarded by its own lock and is deliberately
# NOT the _analyze_lock, so identification can never stall the video stream or a
# concurrent /analyze call.
FACE_ANALYSIS_TTL_S = float(os.getenv("FACE_ANALYSIS_TTL_S", "2.0"))
_ident_state = {
    "smoother": IdentitySmoother(consecutive=FACE_CONSECUTIVE_FRAMES),
    "frame": 0,
    "last_ident_at": 0.0,
    "current": {"identified": False, "name": None, "confidence": 0.0, "reason": "no_face"},
}
_ident_lock = threading.Lock()


def _identify_gated(frame, ear_result):
    """Identify at a bounded rate and smooth the result.

    * no face / face too small -> unknown WITHOUT calling DeepFace
    * at most FACE_IDENT_MAX_HZ DeepFace calls per second; between calls the last
      smoothed identity is reused
    Returns (smoothed, raw). `smoothed` is what /camera/analyze reports.
    """
    status = ear_result.get("status")
    face_width = float(ear_result.get("face_width") or 0.0)

    if status == "no_face" or face_width < FACE_MIN_SIZE:
        reason = "no_face" if status == "no_face" else "too_small"
        raw = {"identified": False, "name": None, "confidence": 0.0, "reason": reason}
    else:
        with _ident_lock:
            now = time.time()
            interval = 1.0 / FACE_IDENT_MAX_HZ if FACE_IDENT_MAX_HZ > 0 else 0.0
            due = (now - _ident_state["last_ident_at"]) >= interval
            if due:
                _ident_state["last_ident_at"] = now
                _ident_state["frame"] += 1
                # pass the MediaPipe result down so identify() gates on it too
                raw = identify(frame, face_info=ear_result)
            else:
                raw = dict(_ident_state["current"])
                raw["reason"] = "throttled"
            smoother = _ident_state["smoother"]
            frame_idx = _ident_state["frame"]
            smoothed = smoother.update(frame_idx, raw.get("name"), raw.get("reason"))
            if raw.get("identified"):
                _ident_state["current"] = dict(raw)
            out = {
                "identified": smoothed["identified"],
                "name": smoothed["name"],
                "confidence": float(raw.get("confidence") or 0.0) if smoothed["identified"] else 0.0,
                "reason": smoothed["reason"],
            }
        return out, raw

    with _ident_lock:
        smoothed = _ident_state["smoother"].update(_ident_state["frame"], None, raw["reason"])
        _ident_state["current"] = dict(raw)
    return {
        "identified": False, "name": None, "confidence": 0.0,
        "reason": raw["reason"],
    }, raw


# ── Quality knob (D) — FINAL: 82 as sweet spot before 85→88 inflection
# Sweep: 60(29KB) 70(36KB) 78(45KB) 82(51KB) 85(57KB) 88(65KB) 92(74KB) BW@15 3.5→8.9 Mbps
# 82 gives +14% sharpness over 78 for +0.77 Mbps, still <9% of 72 Mbps AP, imencode ~1.8ms
# 85→88 is inflection (+15% size for +3 quality, diminishing). Keep override via ?quality= for sweep.
# Env var remains for tuning without redeploy; default now 82 (validated headroom).
JPEG_QUALITY = int(os.getenv("JPEG_QUALITY", "82"))
JPEG_QUALITY = max(50, min(JPEG_QUALITY, 85))

# ── Proximity config — runtime configurable via PUT /camera/proximity-config
from models.train import cv_model as _cv
_prox_lock = threading.Lock()

@router.get("/proximity-config")
def get_proximity_config():
    return {
        "close": float(_cv.FACE_CLOSE_THRESHOLD),
        "medium_ratio": float(_cv.FACE_MEDIUM_RATIO),
        "medium": float(_cv.FACE_CLOSE_THRESHOLD * _cv.FACE_MEDIUM_RATIO),
        "ear_normal": float(_cv.EAR_NORMAL_THRESHOLD),
        "ear_drowsy": float(_cv.EAR_DROWSY_THRESHOLD),
        "mar_yawn": float(_cv.MAR_YAWN_THRESHOLD),
        "pitch_down": float(_cv.HEAD_DOWN_PITCH_DEG),
    }

@router.put("/proximity-config")
def put_proximity_config(payload: dict):
    try:
        with _prox_lock:
            if "close" in payload:
                v = float(payload["close"])
                if not 0.03 <= v <= 0.30:
                    raise HTTPException(status_code=400, detail="close must be 0.03-0.30")
                _cv.FACE_CLOSE_THRESHOLD = v
            if "medium_ratio" in payload:
                _cv.FACE_MEDIUM_RATIO = float(payload["medium_ratio"])
            if "ear_normal" in payload:
                _cv.EAR_NORMAL_THRESHOLD = float(payload["ear_normal"])
            if "ear_drowsy" in payload:
                _cv.EAR_DROWSY_THRESHOLD = float(payload["ear_drowsy"])
            if "mar_yawn" in payload:
                _cv.MAR_YAWN_THRESHOLD = float(payload["mar_yawn"])
            if "pitch_down" in payload:
                _cv.HEAD_DOWN_PITCH_DEG = float(payload["pitch_down"])
        return get_proximity_config()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ── Overlay cache — decoupled from per-request analysis (C) ─────
# Background thread calls analyze_frame() at fixed 100-150ms and caches landmarks.
# GET /stream/frame and /stream/mjpeg read from this cache via lock, so
# per-request handlers never contend with POST /camera/analyze's _analyze_lock.
_overlay_cache = {"result": None, "ts": 0.0}
_overlay_lock = threading.Lock()
_OVERLAY_INTERVAL = 0.12  # 120ms ≈ 8 Hz
_overlay_thread = None
_overlay_active = False


def _overlay_cache_updater():
    while _overlay_active:
        frame = get_rtsp_frame()
        if frame is not None:
            try:
                result = analyze_frame(frame)
                with _overlay_lock:
                    _overlay_cache["result"] = result
                    _overlay_cache["ts"] = time.time()
            except Exception as exc:
                logger.error(f"[OVERLAY CACHE] analyze_frame failed: {exc}")
        time.sleep(_OVERLAY_INTERVAL)


def _start_overlay_cache():
    global _overlay_thread, _overlay_active
    if _overlay_active:
        return
    _overlay_active = True
    _overlay_thread = threading.Thread(target=_overlay_cache_updater, daemon=True)
    _overlay_thread.start()
    logger.info(f"[OVERLAY CACHE] started interval={_OVERLAY_INTERVAL}s quality={JPEG_QUALITY}")


def _get_cached_overlay():
    with _overlay_lock:
        return _overlay_cache["result"]


# Start cache on import (after RTSP thread)
_start_overlay_cache()


def _analysis_for_overlay(frame):
    """Legacy throttled path kept for backward compat but now delegates to cache.
    No longer calls analyze_frame() per-request — reads cached result only."""
    return _get_cached_overlay()


@router.get("/stream/mjpeg")
def stream_mjpeg(overlay: str = "0", fps: int = 15, quality: int | None = None):
    """
    MJPEG multipart stream — single persistent TCP, browser-native smooth (B).
    Reuses get_rtsp_frame() and same resize path (640x360) and JPEG encode.
    Overlay reads from cached analysis only (C), never calls analyze_frame() per-request,
    so it does not contend with POST /camera/analyze's _analyze_lock.
    Keeps GET /stream/frame intact for backward compat.
    """
    fps = max(5, min(int(fps), 25))
    q = int(quality) if quality is not None else JPEG_QUALITY
    q = max(50, min(q, 85))
    interval = 1.0 / fps

    def gen():
        while True:
            frame = get_rtsp_frame()
            if frame is None:
                time.sleep(0.05)
                continue
            if overlay.lower() not in ("0", "false", "off"):
                result = _get_cached_overlay()
                if result:
                    draw_overlay(frame, result)
            _, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, q])
            yield (b"--frame\r\n"
                   b"Content-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n")
            time.sleep(interval)

    return StreamingResponse(
        gen(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Connection": "keep-alive",
        }
    )


@router.get("/stream/frame")
def stream_frame(overlay: str = "1", quality: int | None = None):
    frame = get_rtsp_frame()

    if frame is None:
        raise HTTPException(
            status_code=503,
            detail="Camera unavailable — connecting in background"
        )

    if overlay.lower() not in ("0", "false", "off"):
        result = _get_cached_overlay()
        if result:
            draw_overlay(frame, result)

    q = int(quality) if quality is not None else JPEG_QUALITY
    q = max(50, min(q, 95))
    _, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, q])
    return Response(
        content=buffer.tobytes(),
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        }
    )


def _with_freshness(result):
    """Never hand back an identity computed from an old frame.

    An analysis older than FACE_ANALYSIS_TTL_S is reported as unknown with reason
    "stale". Every original key is preserved.
    """
    if not isinstance(result, dict):
        return result
    ts = result.get("ts") or 0.0
    if (time.time() - ts) > FACE_ANALYSIS_TTL_S and result.get("identified"):
        out = dict(result)
        out["identified"] = False
        out["name"] = None
        out["confidence"] = 0.0
        out["reason"] = "stale"
        return out
    return result



# ── Analyze endpoint ──────────────────────────────────────────
@router.post("/analyze")
def analyze(file: UploadFile = File(None), db: Session = Depends(get_db)):
    global _last_analysis

    if file is not None:
        contents = file.file.read()
        np_arr   = np.frombuffer(contents, np.uint8)
        frame    = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    else:
        frame = get_rtsp_frame()

    if frame is None:
        if _last_analysis:
            # Never hand back the shared dict: the caller must not be able to mutate
            # it, and the cached flag tells the deployment gate this is not fresh.
            cached = dict(_last_analysis)
            cached["cached"] = True
            return cached
        return {"error": "No frame available — check camera connection"}

    acquired = _analyze_lock.acquire(blocking=False)
    if not acquired:
        return {"error": "Analysis busy — try again", "busy": True}

    try:
        ear_result = analyze_frame(frame)
        face_result, identity_raw = _identify_gated(frame, ear_result)

        result = {
            "proximity":  ear_result["proximity"],
            "face_width": ear_result.get("face_width", 0.0),
            "is_close":   ear_result["is_close"],
            "face_bbox":  ear_result.get("face_bbox"),
            "ear":        ear_result["ear"],
            "mar":        ear_result.get("mar"),
            "yawning":    ear_result.get("yawning", False),
            "head_down":  ear_result.get("head_down", False),
            "pitch":      ear_result.get("pitch"),
            "yaw":        ear_result.get("yaw"),
            "roll":       ear_result.get("roll"),
            "status":     ear_result["status"],
            "impaired":   ear_result["impaired"],
            "left_ear":   ear_result["left_ear"],
            "right_ear":  ear_result["right_ear"],
            "identified": face_result["identified"],
            "name":       face_result["name"],
            "confidence": face_result["confidence"],
            "reason":     face_result.get("reason", "unknown"),
            "identity_raw": {"identified": identity_raw.get("identified", False),
                             "name": identity_raw.get("name"),
                             "reason": identity_raw.get("reason")},
            "ts": time.time(),
            "cached": False,
        }
        _last_analysis = result

        # ── Deployment indicators (PHASE 1) — DISPLAY ONLY ────────────────────
        # A gate device is only told someone is there when it is in DEPLOYMENT mode
        # and idle. In training mode this must never fire. The client debounces and
        # cools down internally and swallows its own errors, so this block cannot
        # change the analyse response above or its timing budget.
        try:
            from services.indicator import (
                indicators_enabled, person_cleared, person_detected,
            )
            if indicators_enabled():
                reachable, dev_mode, _idle = _device_mode_and_idle()
                if reachable and dev_mode == "deployment" and _idle:
                    if result.get("is_close"):
                        person_detected()
                    else:
                        person_cleared()
        except Exception:                      # noqa: BLE001 - never raise upward
            logger.warning("indicator person hook skipped", exc_info=False)

        return _with_freshness(result)
    except Exception as exc:
        logger.error(f"[POST /analyze] CRITICAL exception caught during processing loop: {exc}")
        return {"error": f"Internal processing crash: {str(exc)}"}
    finally:
        _analyze_lock.release()


# ── Register subject ONLY if alcohol detected ──────────────────
@router.post("/register")
def register_subject(
    file: UploadFile = File(None),
    name: str = Form(None),
    reading_id: int = Form(None),
    db: Session = Depends(get_db)
):
    if file is not None:
        contents = file.file.read()
        np_arr   = np.frombuffer(contents, np.uint8)
        frame    = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    else:
        frame = get_rtsp_frame()

    if frame is None:
        return {"error": "No frame available"}

    # gated the same way as /analyze: no face -> unknown, never a guess
    face_result = identify(frame, face_info=analyze_frame(frame))

    if face_result["identified"] and face_result["confidence"] > 0.7:
        existing = db.query(Subject).filter(Subject.face_id == face_result["name"]).first()
        if existing and reading_id:
            reading = db.query(Reading).filter(Reading.id == reading_id).first()
            if reading:
                reading.subject_id = existing.id
                db.commit()
        return {
            "registered":    False,
            "already_known": True,
            "subject_id":    existing.id if existing else None,
            "name":          face_result["name"],
            "message":       "Already registered — reading linked",
        }

    subject_name = name or f"subject_{int(time.time())}"
    new_subject = Subject(name=subject_name, face_id=subject_name)
    db.add(new_subject)
    db.commit()
    db.refresh(new_subject)

    enroll(subject_name, [frame])

    if reading_id:
        reading = db.query(Reading).filter(Reading.id == reading_id).first()
        if reading:
            reading.subject_id = new_subject.id
            db.commit()

    img_path = os.path.join(REGISTERED, subject_name, "01.jpg")
    return {
        "registered":    True,
        "already_known": False,
        "subject_id":    new_subject.id,
        "name":          subject_name,
        "face_saved":    img_path,
        "message":       "Subject registered — alcohol incident logged",
    }
