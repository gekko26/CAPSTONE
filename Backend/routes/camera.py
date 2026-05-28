# File: Backend/routes/camera.py
from fastapi import APIRouter, Depends, File, UploadFile, Form, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session
from dotenv import load_dotenv
from database.db import get_db
from database.models import Reading, Subject
from models.train.cv_model import analyze_frame
from models.train.face_recognition import identify, enroll
import numpy as np
import cv2
import os
import time
import threading
import logging

load_dotenv()

router = APIRouter(prefix="/camera", tags=["Camera"])

REGISTERED = os.path.join(os.path.dirname(__file__), "../data/faces/registered")
os.makedirs(REGISTERED, exist_ok=True)

RTSP_URL = os.getenv("RTSP_URL")

# Configure explicit debugging logs to output to stdout
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
                    cap = cv2.VideoCapture(self.url)
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
                    cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000)

                ret, frame = cap.read()
                if ret and frame is not None:
                    with self.lock:
                        self.frame     = frame
                        self.connected = True
                    time.sleep(0.01)  # Yield GIL to prevent starving Uvicorn worker threads
                else:
                    logger.warning("[RTSP THREAD] Failed to grab frame. Reconnecting...")
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
@router.get("/stream/frame")
def stream_frame():
    """
    Returns a single JPEG frame from the C200C RTSP stream.
    """
    logger.debug("[GET /stream/frame] Fetching raw frame buffer...")
    frame = get_rtsp_frame()

    if frame is None:
        logger.warning("[GET /stream/frame] Frame buffer empty. Returning 503.")
        raise HTTPException(
            status_code=503,
            detail="Camera unavailable — connecting in background"
        )

    _, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
    logger.debug("[GET /stream/frame] Frame successfully encoded and served.")
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
def analyze(
    file: UploadFile = File(None),
    db:   Session    = Depends(get_db)
):
    """
    Analyzes a camera frame for EAR + proximity + face identity.
    """
    global _last_analysis
    logger.info("[POST /analyze] Request received.")

    if file is not None:
        logger.info("[POST /analyze] Parsing frame uploaded via request.")
        contents = file.file.read()
        np_arr   = np.frombuffer(contents, np.uint8)
        frame    = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    else:
        logger.info("[POST /analyze] Pulling frame from passive RTSP buffer.")
        frame = get_rtsp_frame()

    if frame is None:
        logger.warning("[POST /analyze] No frame content available.")
        if _last_analysis:
            return _last_analysis
        return {"error": "No frame available — check camera connection"}

    logger.info("[POST /analyze] Attempting to acquire non-blocking analysis lock...")
    acquired = _analyze_lock.acquire(blocking=False)
    if not acquired:
        logger.warning("[POST /analyze] Lock acquisition failed. Analysis is busy. Returning cache.")
        if _last_analysis:
            return _last_analysis
        return {"error": "Analysis busy — try again"}

    logger.info("[POST /analyze] Lock secured successfully.")
    try:
        logger.info("[POST /analyze] Line Check: Entering MediaPipe 'analyze_frame'...")
        start_mp = time.time()
        ear_result = analyze_frame(frame)
        logger.info(f"[POST /analyze] Line Check: MediaPipe finished in {time.time() - start_mp:.4f}s.")

        logger.info("[POST /analyze] Line Check: Entering DeepFace 'identify'...")
        start_df = time.time()
        face_result = identify(frame)
        logger.info(f"[POST /analyze] Line Check: DeepFace finished in {time.time() - start_df:.4f}s.")

        result = {
            "proximity":  ear_result["proximity"],
            "is_close":   ear_result["is_close"],
            "ear":        ear_result["ear"],
            "status":     ear_result["status"],
            "impaired":   ear_result["impaired"],
            "left_ear":   ear_result["left_ear"],
            "right_ear":  ear_result["right_ear"],
            "identified": face_result["identified"],
            "name":       face_result["name"],
            "confidence": face_result["confidence"],
        }
        _last_analysis = result
        logger.info("[POST /analyze] Analysis sequence completed safely.")
        return result
    except Exception as exc:
        logger.error(f"[POST /analyze] CRITICAL exception caught during processing loop: {exc}")
        return {"error": f"Internal processing crash: {str(exc)}"}
    finally:
        _analyze_lock.release()
        logger.info("[POST /analyze] Analysis lock released clean.")


# ── Register subject ONLY if alcohol detected ──────────────────
@router.post("/register")
def register_subject(
    file:       UploadFile = File(None),
    name:       str        = Form(None),
    reading_id: int        = Form(None),
    db:         Session    = Depends(get_db)
):
    logger.info("[POST /register] Processing enrolment sequence...")
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

    logger.info(f"[POST /register] Enrolling face for: {subject_name}")
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
    