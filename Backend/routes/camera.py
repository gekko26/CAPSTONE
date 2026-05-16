from fastapi import APIRouter, Depends, File, UploadFile, Form
from sqlalchemy.orm import Session
from database.db import get_db
from database.models import Reading, Subject
from models.train.cv_model import analyze_frame
from models.train.face_recognition import identify, enroll
import numpy as np
import cv2
import os

router = APIRouter(prefix="/camera", tags=["Camera"])

REGISTERED = os.path.join(os.path.dirname(__file__), "../data/faces/registered")
os.makedirs(REGISTERED, exist_ok=True)


# ── Main analyze endpoint — called by frontend on every frame ──
@router.post("/analyze")
async def analyze(
    file: UploadFile = File(...),
    db:   Session    = Depends(get_db)
):
    """
    Analyzes a camera frame.
    Returns EAR + proximity + face identity.

    Frontend should call this continuously while streaming.
    When is_close = True, frontend should trigger ESP32 to
    start sending MQ3 window to POST /training/collect
    """
    contents = await file.read()
    np_arr   = np.frombuffer(contents, np.uint8)
    frame    = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    if frame is None:
        return {"error": "Invalid image"}

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
    file:       UploadFile = File(...),
    # FIX 2: name and reading_id must be Form() fields since
    # this is a multipart request (file upload). Using bare
    # defaults makes FastAPI treat them as query params instead,
    # mixing two request styles.
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
    contents = await file.read()
    np_arr   = np.frombuffer(contents, np.uint8)
    frame    = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    if frame is None:
        return {"error": "Invalid image"}

    # 1. Check if already registered via face recognition
    face_result = identify(frame)

    if face_result["identified"] and face_result["confidence"] > 0.7:
        # Already registered — just link reading to existing subject
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
    subject_name = name or f"subject_{int(__import__('time').time())}"

    # Save to subjects table
    new_subject = Subject(
        name    = subject_name,
        face_id = subject_name,
    )
    db.add(new_subject)
    db.commit()
    db.refresh(new_subject)

    # FIX 1: use enroll() instead of manually writing the image.
    # enroll() saves the image AND builds the DeepFace index so
    # identify() can actually find this person on future calls.
    # Manually writing a jpg with cv2.imwrite skips the indexing step.
    enroll(subject_name, [frame])

    # Link reading to subject
    if reading_id:
        reading = db.query(Reading).filter(Reading.id == reading_id).first()
        if reading:
            reading.subject_id = new_subject.id
            db.commit()

    # Build enrolled image path for response
    img_path = os.path.join(REGISTERED, subject_name, "01.jpg")

    return {
        "registered":    True,
        "already_known": False,
        "subject_id":    new_subject.id,
        "name":          subject_name,
        "face_saved":    img_path,
        "message":       "Subject registered — alcohol incident logged",
    }