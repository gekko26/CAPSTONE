#readings.py
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from database.db import get_db
from database.models import Reading, DeploymentLog

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

@router.get("/deployment-logs")
def get_deployment_logs(
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    logs = db.query(DeploymentLog).order_by(DeploymentLog.date.desc()).limit(limit).all()
    over  = sum(1 for l in logs if l.prediction == "Over Limit")
    near  = sum(1 for l in logs if l.prediction == "Near Limit")

    # Last-7-day categorical breakdown for the Report weekly chart
    from datetime import datetime, timedelta
    from sqlalchemy import func as sqlfunc
    week_start = datetime.now() - timedelta(days=6)
    rows = (
        db.query(
            sqlfunc.date(DeploymentLog.date).label("day"),
            DeploymentLog.prediction,
            sqlfunc.count(DeploymentLog.id),
        )
        .filter(DeploymentLog.date >= week_start)
        .group_by(sqlfunc.date(DeploymentLog.date), DeploymentLog.prediction)
        .all()
    )
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    per_day = {}
    for day, prediction, count in rows:
        key = day.isoformat() if hasattr(day, "isoformat") else str(day)
        bucket = per_day.setdefault(key, {"clear": 0, "alert": 0, "breach": 0, "intercepted": 0})
        if prediction == "Over Limit":
            bucket["breach"] += count
        elif prediction == "Near Limit":
            bucket["alert"] += count
        elif prediction and "Sanitizer" in prediction:
            bucket["intercepted"] += count
        else:
            bucket["clear"] += count
    weekly = []
    for i in range(6, -1, -1):
        d = datetime.now() - timedelta(days=i)
        key = d.date().isoformat()
        b = per_day.get(key, {"clear": 0, "alert": 0, "breach": 0, "intercepted": 0})
        weekly.append({"day": day_names[d.weekday()], **b})

    # Last-6-month breakdown for the Report archive table
    month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    months = []
    cursor = month_start
    for _ in range(6):
        months.append(cursor)
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    months.reverse()

    mrows = (
        db.query(
            sqlfunc.date_format(DeploymentLog.date, "%Y-%m").label("month"),
            DeploymentLog.prediction,
            sqlfunc.count(DeploymentLog.id),
        )
        .filter(DeploymentLog.date >= months[0])
        .group_by(sqlfunc.date_format(DeploymentLog.date, "%Y-%m"), DeploymentLog.prediction)
        .all()
    )
    per_month = {}
    for month_key, prediction, count in mrows:
        bucket = per_month.setdefault(month_key, {"total": 0, "breach": 0})
        bucket["total"] += count
        if prediction == "Over Limit":
            bucket["breach"] += count

    monthly = []
    for m in months:
        key = m.strftime("%Y-%m")
        b = per_month.get(key, {"total": 0, "breach": 0})
        monthly.append({
            "id":      f"LOG-{m.strftime('%Y-%m')}",
            "label":   m.strftime("%B %Y"),
            "total":   b["total"],
            "flagged": b["breach"],
        })

    return {
        "logs": [
            {
                "id":          l.id,
                "date":        l.date.isoformat() if l.date else None,
                "subject_id":  l.subject_id,
                "prediction":  l.prediction,
                "confidence":  l.confidence,
                "risk_level":  l.risk_level,
                "temperature": l.temperature,
                "humidity":    l.humidity,
                "model_version": l.model_version,
            }
            for l in logs
        ],
        "stats": {
            "total":      len(logs),
            "over_limit": over,
            "near_limit": near,
            "sanitizer":  sum(1 for l in logs if l.prediction and "Sanitizer" in l.prediction),
            "pass_rate":  round(((len(logs) - over - near) / len(logs)) * 100, 1) if logs else 0,
        },
        "weekly": weekly,
        "monthly": monthly,
    }