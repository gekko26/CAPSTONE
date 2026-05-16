from fastapi import APIRouter, Depends, File, UploadFile, Form
from sqlalchemy.orm import Session
from database.db import get_db
from database.models import Subject
from models.train.face_recognition import enroll, identify, verify
import numpy as np
import cv2

router = APIRouter(prefix="/recognition", tags=["recognition"])


@router.post("/enroll")
async def enroll_subject(
    name:  str     = Form(...),
    files: list[UploadFile] = File(...),
    db:    Session = Depends(get_db)
):
    """
    Register a new subject with their face images.

    Usage:
        POST /recognition/enroll
        Body: form-data
            name  = "john_doe"
            files = <image1>, <image2>, <image3>
    """

    # 1. Decode all uploaded images into numpy arrays
    frames = []
    for file in files:
        contents = await file.read()
        np_arr   = np.frombuffer(contents, np.uint8)
        frame    = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        if frame is not None:
            frames.append(frame)

    if not frames:
        return {"error": "No valid images uploaded"}

    # 2. Save face images to disk
    enroll(name, frames)

    # 3. Save subject to DB if not already registered
    existing = db.query(Subject).filter(Subject.name == name).first()
    if not existing:
        new_subject = Subject(
            name    = name,
            face_id = name,       # use name as face_id reference
        )
        db.add(new_subject)
        db.commit()
        db.refresh(new_subject)
        subject_id = new_subject.id
    else:
        subject_id = existing.id

    return {
        "message":    f"{name} enrolled successfully",
        "subject_id": subject_id,
        "images":     len(frames),
    }


@router.post("/identify")
async def identify_subject(
    file: UploadFile = File(...),
    db:   Session    = Depends(get_db)
):
    """
    Identify who is in the frame.

    Usage:
        POST /recognition/identify
        Body: form-data, key=file, value=<image>
    """

    contents = await file.read()
    np_arr   = np.frombuffer(contents, np.uint8)
    frame    = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    if frame is None:
        return {"error": "Invalid image"}

    # 1. Run face recognition
    result = identify(frame)

    # 2. Look up subject_id from DB if identified
    subject_id = None
    if result["identified"]:
        subject = db.query(Subject).filter(
            Subject.name == result["name"]
        ).first()
        if subject:
            subject_id = subject.id

    return {
        "identified": result["identified"],
        "name":       result["name"],
        "confidence": result["confidence"],
        "subject_id": subject_id,
    }


@router.post("/verify/{name}")
async def verify_subject(
    name: str,
    file: UploadFile = File(...),
):
    """
    Verify if the person in the frame matches a registered subject.

    Usage:
        POST /recognition/verify/john_doe
        Body: form-data, key=file, value=<image>
    """

    contents = await file.read()
    np_arr   = np.frombuffer(contents, np.uint8)
    frame    = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    if frame is None:
        return {"error": "Invalid image"}

    result = verify(frame, name)

    return {
        "name":       name,
        "verified":   result["verified"],
        "confidence": result.get("confidence"),
        "error":      result.get("error"),
    }


@router.get("/subjects")
def get_subjects(db: Session = Depends(get_db)):
    """
    List all registered subjects.

    Usage:
        GET /recognition/subjects
    """
    subjects = db.query(Subject).all()
    return [
        {
            "id":         s.id,
            "name":       s.name,
            "face_id":    s.face_id,
            "created_at": s.created_at,
        }
        for s in subjects
    ]


@router.delete("/subjects/{subject_id}")
def delete_subject(subject_id: int, db: Session = Depends(get_db)):
    """
    Remove a subject from the DB.

    Usage:
        DELETE /recognition/subjects/1
    """
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if not subject:
        return {"error": "Subject not found"}

    db.delete(subject)
    db.commit()
    return {"message": f"{subject.name} removed"}