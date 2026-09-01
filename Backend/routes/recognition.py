from fastapi import APIRouter, Depends, File, UploadFile, Form
from sqlalchemy.orm import Session
from database.db import get_db
from database.models import Subject
from models.train.face_recognition import enroll, identify, verify
from pydantic import BaseModel
from typing import Optional
import numpy as np
import cv2

router = APIRouter(prefix="/recognition", tags=["recognition"])


@router.post("/enroll")
async def enroll_subject(
    name:  str     = Form(...),
    files: list[UploadFile] = File(...),
    id_number: str = Form(None),
    group: str = Form(None),
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

    # 3. Save subject to DB if not already registered — path-based image storage (not blob)
    import os
    face_ref = id_number.strip() if id_number and id_number.strip() else name
    # avatar_path: first image saved to data/faces/{name} via enroll()
    avatar_path = None
    try:
        from pathlib import Path
        base = Path(__file__).resolve().parent.parent / "data" / "faces" / name
        if base.exists():
            pics = sorted(base.glob("*.jpg")) + sorted(base.glob("*.png"))
            if pics:
                avatar_path = str(pics[-1])
    except: pass

    existing = db.query(Subject).filter(Subject.name == name).first()
    if not existing:
        new_subject = Subject(
            name        = name,
            face_id     = face_ref,
            group       = group or "Staff",
            avatar_path = avatar_path,
        )
        db.add(new_subject)
        db.commit()
        db.refresh(new_subject)
        subject_id = new_subject.id
    else:
        # update group/avatar if provided
        if group:
            existing.group = group
        if avatar_path:
            existing.avatar_path = avatar_path
        if id_number and id_number.strip():
            existing.face_id = face_ref
        db.commit()
        subject_id = existing.id

    return {
        "message":    f"{name} enrolled successfully",
        "subject_id": subject_id,
        "id_number":  face_ref,
        "group":      group,
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
            "id":          s.id,
            "name":        s.name,
            "face_id":     s.face_id,
            "id_number":   s.face_id,
            "group":       getattr(s, "group", "Staff") or "Staff",
            "avatar_path": getattr(s, "avatar_path", None),
            "created_at":  s.created_at,
        }
        for s in subjects
    ]


class SubjectUpdate(BaseModel):
    name: Optional[str] = None
    id_number: Optional[str] = None
    face_id: Optional[str] = None
    group: Optional[str] = None

@router.patch("/subjects/{subject_id}")
def update_subject(subject_id: int, payload: SubjectUpdate, db: Session = Depends(get_db)):
    """
    Edit a subject (fix wrong info).

    Body JSON: {name?, id_number?, group?}
    """
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if not subject:
        return {"error": "Subject not found"}
    name = payload.name
    id_number = payload.id_number or payload.face_id
    group = payload.group
    if name and name.strip():
        dup = db.query(Subject).filter(Subject.name == name.strip(), Subject.id != subject_id).first()
        if dup:
            return {"error": f"Name '{name.strip()}' already exists"}
        subject.name = name.strip()
    if id_number is not None and str(id_number).strip():
        subject.face_id = str(id_number).strip()
    if group is not None:
        subject.group = group
    db.commit()
    db.refresh(subject)
    return {"message": f"{subject.name} updated", "subject": {"id": subject.id, "name": subject.name, "face_id": subject.face_id, "group": getattr(subject, "group", "Staff")}}

@router.delete("/subjects/{subject_id}")
def delete_subject(subject_id: int, db: Session = Depends(get_db)):
    """
    Remove a subject from the DB.

    Usage:
        DELETE /recognition/subjects/1
    """
    from database.models import TrainingData, Reading, DeploymentLog
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if not subject:
        return {"error": "Subject not found"}

    # nullify FKs to keep history intact (logs stay, person shows Unknown)
    db.query(TrainingData).filter(TrainingData.subject_id == subject_id).update({TrainingData.subject_id: None})
    db.query(Reading).filter(Reading.subject_id == subject_id).update({Reading.subject_id: None})
    db.query(DeploymentLog).filter(DeploymentLog.subject_id == subject_id).update({DeploymentLog.subject_id: None})
    db.delete(subject)
    db.commit()
    return {"message": f"{subject.name} removed"}