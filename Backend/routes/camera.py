#camera.py
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

load_dotenv()  # loads .env from Backend/ root — picks up RTSP_URL, DB_* etc.

router = APIRouter(prefix="/camera", tags=["Camera"])

REGISTERED = os.path.join(os.path.dirname(__file__), "../data/faces/registered")
os.makedirs(REGISTERED, exist_ok=True)

# ── RTSP config — read from .env ──────────────────────────────
# .env entry:  RTSP_URL=rtsp://admin:yourpassword@192.168.x.x:554/stream2
# stream2 = 360p, faster than stream1 (1080p), good enough for face detection
RTSP_URL = os.getenv("RTSP_URL")

if not RTSP_URL:
    raise RuntimeError(
        "RTSP_URL not set — add it to your .env file:\n"
        "RTSP_URL=rtsp://admin:yourpassword@192.168.x.x:554/stream2"
    )


# ── Persistent RTSP capture — runs in background thread ──────
# Opens the RTSP connection ONCE and keeps reading frames into
# a buffer. get_rtsp_frame() just returns the latest buffered
# frame instantly — no connection overhead per request.
import threading

class RTSPStream:
    def __init__(self, url: str):
        self.url    = url
        self.frame  = None
        self.lock   = threading.Lock()
        self.active = False
        self._thread = None

    def start(self):
        self.active  = True
        self._thread = threading.Thread(target=self._capture, daemon=True)
        self._thread.start()

    def _capture(self):
        cap = None
        while self.active:
            try:
                if cap is None or not cap.isOpened():
                    cap = cv2.VideoCapture(self.url)
                    # reduce internal buffer to 1 frame — kills stale frame lag
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

                ret, frame = cap.read()
                if ret and frame is not None:
                    with self.lock:
                        self.frame = frame
                else:
                    # stream dropped — reconnect
                    cap.release()
                    cap = None
                    time.sleep(1)
            except Exception:
                if cap:
                    cap.release()
                cap = None
                time.sleep(1)

        if cap:
            cap.release()

    def read(self) -> np.ndarray | None:
        with self.lock:
            return self.frame.copy() if self.frame is not None else None

    def stop(self):
        self.active = False


# Start the stream immediately when the module loads
_stream = RTSPStream(RTSP_URL)
_stream.start()

# Wait up to 8s for the first frame before accepting requests
# Prevents the "unavailable → stream" flash on startup
_waited = 0
while _stream.read() is None and _waited < 8:
    time.sleep(0.5)
    _waited += 0.5


def get_rtsp_frame() -> np.ndarray | None:
    """Returns the latest buffered frame — no connection overhead."""
    return _stream.read()


# ── Stream frame endpoint — frontend polls this for live feed ──
@router.get("/stream/frame")
def stream_frame():
    """
    Returns a single JPEG frame from the C200C RTSP stream.
    Frontend polls this every 500ms to display the live feed.

    Frontend usage:
        const res = await fetch("/camera/stream/frame");
        const blob = await res.blob();
        setFrameUrl(URL.createObjectURL(blob));
    """
    frame = get_rtsp_frame()

    if frame is None:
        raise HTTPException(status_code=503, detail="Camera unavailable — check RTSP_URL and camera connection")

    _, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return Response(
        content=buffer.tobytes(),
        media_type="image/jpeg",
        headers={
            # Prevent browser caching so every poll gets a fresh frame
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        }
    )


# ── Analyze endpoint — accepts uploaded frame OR grabs from RTSP
@router.post("/analyze")
async def analyze(
    file: UploadFile = File(None),  # optional — if None, grabs from RTSP
    db:   Session    = Depends(get_db)
):
    """
    Analyzes a camera frame for EAR + proximity + face identity.

    Two modes:
      - file provided → use uploaded frame (training mode, browser webcam)
      - file is None  → grab from RTSP directly (deployment mode, C200C)

    Frontend should call this continuously while displaying the stream.
    When is_close = True, frontend triggers ESP32 to buffer MQ3 data.
    """
    if file is not None:
        # Training mode — frame uploaded from browser webcam
        contents = await file.read()
        np_arr   = np.frombuffer(contents, np.uint8)
        frame    = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    else:
        # Deployment mode — grab frame from C200C RTSP
        frame = get_rtsp_frame()

    if frame is None:
        return {"error": "No frame available — check camera connection"}

    ear_result  = analyze_frame(frame)
    face_result = identify(frame)

    return {
        # Proximity — frontend uses this to trigger ESP32
        "proximity":  ear_result["proximity"],
        "is_close":   ear_result["is_close"],

        # EAR
        "ear":        ear_result["ear"],
        "status":     ear_result["status"],
        "impaired":   ear_result["impaired"],
        "left_ear":   ear_result["left_ear"],
        "right_ear":  ear_result["right_ear"],

        # Face identity
        "identified": face_result["identified"],
        "name":       face_result["name"],
        "confidence": face_result["confidence"],
    }


# ── Register subject ONLY if alcohol detected ──────────────────
@router.post("/register")
async def register_subject(
    file:       UploadFile = File(None),  # optional — grabs from RTSP if None
    name:       str        = Form(None),
    reading_id: int        = Form(None),
    db:         Session    = Depends(get_db)
):
    """
    Called ONLY when alcohol is detected.
    Saves subject to DB + enrolls face via DeepFace.

    Flow:
        1. Sensor detects alcohol
        2. Frontend calls this endpoint with the captured frame
        3. Subject is saved to subjects table
        4. Face enrolled via enroll() so DeepFace can identify later
        5. Reading is linked to subject_id
    """
    if file is not None:
        contents = await file.read()
        np_arr   = np.frombuffer(contents, np.uint8)
        frame    = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    else:
        frame = get_rtsp_frame()

    if frame is None:
        return {"error": "No frame available"}

    # 1. Check if already registered via face recognition
    face_result = identify(frame)

    if face_result["identified"] and face_result["confidence"] > 0.7:
        existing = db.query(Subject).filter(
            Subject.face_id == face_result["name"]
        ).first()

        if existing and reading_id:
            reading = db.query(Reading).filter(
                Reading.id == reading_id
            ).first()
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

    # 2. New person — register them
    subject_name = name or f"subject_{int(time.time())}"

    new_subject = Subject(
        name    = subject_name,
        face_id = subject_name,
    )
    db.add(new_subject)
    db.commit()
    db.refresh(new_subject)

    # enroll() saves image + builds DeepFace index
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