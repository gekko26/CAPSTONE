import os

# 1. THE STABLE SPEED HACK: TCP for reliability, nobuffer for speed.
# This prevents the av_frame_get_buffer Out of Memory crash.
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay"

from fastapi import APIRouter, Depends, File, UploadFile, Form, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session
from dotenv import load_dotenv
from database.db import get_db
from database.models import Reading, Subject
from models.train.cv_model import analyze_frame, draw_overlay
from models.train.face_recognition import identify, enroll
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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AlcoDetect.Camera")
logger.setLevel(logging.INFO)

if not RTSP_URL:
    logger.warning("RTSP_URL not set in .env — camera endpoints will return 503.")


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
        logger.info("[RTSP THREAD] Capture loop initiated.")
        while self.active:
            try:
                if cap is None or not cap.isOpened():
                    logger.info(f"[RTSP THREAD] Connecting to: {self.url}")
                    cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
                    # Force OpenCV to hold only the absolute newest frame in memory
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

                # Use standard read() instead of grab/retrieve to prevent H.264 header corruption
                ret, frame = cap.read()
                if ret and frame is not None:
                    
                    # 2. Downscale instantly to 360p (640x360) to keep AI processing lightning fast
                    frame = cv2.resize(frame, (640, 360), interpolation=cv2.INTER_AREA)

                    with self.lock:
                        self.frame = frame
                        self.connected = True
                else:
                    logger.warning("[RTSP THREAD] Frame dropped. Reconnecting...")
                    self.connected = False
                    if cap:
                        cap.release()
                    cap = None
                    time.sleep(2)
                    
            except Exception as e:
                logger.error(f"[RTSP THREAD] Critical capture error: {e}")
                self.connected = False
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


# ── Stream frame endpoint ─────────────────────────────────────
_overlay_cache = {"result": None, "ts": 0.0}
_OVERLAY_INTERVAL = 0.1  # max CV analyses per second for the live HUD


def _analysis_for_overlay(frame):
    """Throttled analyze_frame — reuses the /analyze lock non-blocking
    so the stream and the analyze endpoint never block each other."""
    now = time.time()
    if now - _overlay_cache["ts"] >= _OVERLAY_INTERVAL:
        if _analyze_lock.acquire(blocking=False):
            try:
                _overlay_cache["result"] = analyze_frame(frame)
                _overlay_cache["ts"] = time.time()
            except Exception as exc:
                logger.error(f"[OVERLAY] analysis failed: {exc}")
                _overlay_cache["ts"] = now
            finally:
                _analyze_lock.release()
    return _overlay_cache["result"]


@router.get("/stream/frame")
def stream_frame(overlay: str = "1"):
    frame = get_rtsp_frame()

    if frame is None:
        raise HTTPException(
            status_code=503,
            detail="Camera unavailable — connecting in background"
        )

    if overlay.lower() not in ("0", "false", "off"):
        result = _analysis_for_overlay(frame)
        if result:
            draw_overlay(frame, result)

    _, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return Response(
        content=buffer.tobytes(),
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        }
    )


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
            return _last_analysis
        return {"error": "No frame available — check camera connection"}

    acquired = _analyze_lock.acquire(blocking=False)
    if not acquired:
        if _last_analysis:
            return _last_analysis
        return {"error": "Analysis busy — try again"}

    try:
        ear_result = analyze_frame(frame)
        face_result = identify(frame)

        result = {
            "proximity":  ear_result["proximity"],
            "is_close":   ear_result["is_close"],
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
        }
        _last_analysis = result
        return result
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

    face_result = identify(frame)

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