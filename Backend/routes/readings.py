#readings.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database.db import get_db
from database.models import Reading

router = APIRouter()

@router.get("/readings")
def get_readings(db: Session = Depends(get_db)):
    readings = db.query(Reading).order_by(Reading.date.desc()).limit(50).all()
    return readings

@router.get("/readings/latest")
def get_latest(db: Session = Depends(get_db)):
    latest = db.query(Reading).order_by(Reading.date.desc()).first()
    return latest

@router.get("/readings/stats")
def get_stats(db: Session = Depends(get_db)):
    readings = db.query(Reading).all()
    total = len(readings)
    above = len([r for r in readings if r.bac and r.bac >= 0.08])
    avg_bac = round(sum(r.bac for r in readings if r.bac) / total, 3) if total else 0
    pass_rate = round(((total - above) / total) * 100, 1) if total else 0

    return {
        "total": total,
        "above_limit": above,
        "avg_bac": avg_bac,
        "pass_rate": f"{pass_rate}%"
    }