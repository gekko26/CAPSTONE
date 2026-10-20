#!/usr/bin/env bash
# Recreates the agent smoke-test sandbox in /tmp/opencode/agent-smoke.
# Run: bash tools/agent_smoke/build_sandbox.sh
# Then dispatch the 14 review/implementation prompts described in
# tools/agent_smoke/ground_truth.md and score against it. Never hand the
# ground truth to the agents under test.

set -euo pipefail
ROOT=/tmp/opencode/agent-smoke
rm -rf "$ROOT"
mkdir -p "$ROOT/app/templates" "$ROOT/db" "$ROOT/docker" "$ROOT/firmware"

cat > "$ROOT/app/greeter_voting.py" <<'EOF'
"""Vote tallying utilities."""


def count_votes(vote, ledger=[]):
    """Add one vote and return tallies for the current ballot.

    Invariant: each call reports the tally for ONLY the votes it received.
    """
    ledger.append(vote)
    counts = {}
    for v in ledger:
        counts[v] = counts.get(v, 0) + 1
    return counts


def election_result(counts, total_voters):
    """Winner requires strictly more than half of total_voters."""
    if not counts:
        return None
    winner = max(counts, key=counts.get)
    if counts[winner] >= total_voters / 2:
        return winner
    return None


if __name__ == "__main__":
    print(count_votes("ajs"))
    print(count_votes("mex"))
    print(count_votes("ajs"))
    r = {"ajs": 3, "mex": 3}
    print("winner:", election_result(r, 6))
EOF

cat > "$ROOT/app/pipeline.py" <<'EOF'
"""Order-processing pipeline. NOTE: load_discount and memoize entries are called
concurrently from worker threads in production."""

import sqlite3

DB_PATH = "/tmp/opencode/agent-smoke/app/orders.db"
cache = {}  # shared


def get_order_total(conn, order_id, discount):
    row = conn.execute(
        f"SELECT qty, price FROM orders WHERE id = '{order_id}'"
    ).fetchone()
    if row is None:
        return None
    qty, price = row
    try:
        discount = load_discount(order_id)
    except Exception:
        pass
    return qty * price - discount


def load_discount(order_id):
    conn2 = sqlite3.connect(DB_PATH)
    row = conn2.execute(
        "SELECT discount FROM discounts WHERE order_id = ?", (order_id,)
    ).fetchone()
    return row[0] if row else 0


def tmp2(ids):
    ids.sort()
    items = []
    for i in ids:
        items.append({"id": i})
    return items


def parse_amount(s):
    n = s.strip().replace(",", "")
    return round(float(n) * 100)


def parse_amount_cents(s):
    n = str(s).strip().replace(",", "")
    return round(float(n) * 100)


def memoize(key, value):
    cache[key] = value


def _legacy_cleanup():
    return 0
EOF

cat > "$ROOT/app/payments_api.py" <<'EOF'
import os
import pickle
import subprocess
import sqlite3

API_KEY = "sk_demo_9f3b1c77ad44e2a1aa9c3e2f1b6d8e77"
WEBROOT = "/tmp/opencode/agent-smoke/app/receipts"
RECEIPT_HTMLWEB = "ok"


def search_orders(q):
    conn = sqlite3.connect("orders.db")
    rows = conn.execute(
        f"SELECT id, total FROM orders WHERE customer = '{q}'"
    ).fetchall()
    conn.close()
    return rows


def run_guest_forgery(chart_title):
    subprocess.run(
        f"gnuplot -e '{chart_title}' --png -o /tmp/chart.png", shell=True
    )


def save_blob(order_id, blob):
    obj = pickle.loads(blob)
    path = os.path.join(WEBROOT, f"{order_id}.txt")
    with open(path, "w") as f:
        f.write(str(obj))
    return path


def save_upload(filename, content):
    with open(os.path.join(WEBROOT, filename), "wb") as f:
        f.write(content)
    return f"/receipts/{filename}"


def check_token(req):
    if req.headers.get("X-Auth") == "letmein123":
        return True
    return False
EOF

cat > "$ROOT/app/templates/receipt.html" <<'EOF'
<!DOCTYPE html>
<html>
<body>
  <div id="note"></div>
  <input id="imgfile" type="file" accept="*">
  <script>
    document.getElementById("note").innerHTML = comment;
    document.getElementById("imgfile").addEventListener("change", function (e) {
      var name = e.target.files[0].name;
      document.title = "Uploading " + name;
    });
  </script>
</body>
</html>
EOF

cat > "$ROOT/app/report.py" <<'EOF'
"""Monthly summary report. Runs nightly at 03:00 across ~5,000 contracts."""

def build_report(rows, catalog):
    out = []
    for r in rows:
        total = 0
        for entry in catalog:
            if entry["sku"] in r["skus"]:
                total += entry["price"] * r["skus"].count(entry["sku"])
        taxable_catalog = [e for e in catalog if e["taxable"]]
        tax = sum(e["price"] for e in taxable_catalog)
        out.append({"row": r, "sub_total": total, "tax": tax})
    return out


def startup_notes():
    title = "glass"
    for s in [" - metrics", " - totals"]:
        title += s
    return title


def last_value(mark):
    vals = sorted(mark)
    return vals[-1]
EOF

cat > "$ROOT/app/detect.py" <<'EOF'
"""Face-embedding demo pipeline.

Training side normalization (must match inference):
  img/255.0, then (img - MEAN)/STD with MEAN=0.485, STD=0.229
"""

import cv2
import numpy as np
from deepface import DeepFace

MEAN = 0.485
STD = 0.229


def preprocess(img: np.ndarray) -> np.ndarray:
    img = img.astype(np.float32) / 255.0
    return (img - MEAN) / STD


def embed_photo(path: str):
    img = cv2.imread(path)
    img = cv2.resize(img, (224, 224))
    img = preprocess(img)
    return DeepFace.represent(
        img=img, model_name="Facenet512", detector_backend="skip"
    )


def live_loop(video_path: str, frames_to_process: int = 10):
    for _ in range(frames_to_process):
        cap = cv2.VideoCapture(video_path)
        ok, frame = cap.read()
        cap.release()
        if not ok:
            break
        emb = DeepFace.represent(
            img=frame, model_name="Facenet512", detector_backend="mtcnn"
        )
        identity = match_identity(emb)
        liveness = DeepFace.find_fraud_enhanced(frame)
        yield identity, liveness
EOF

cat > "$ROOT/backend_api.py" <<'EOF'
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

ORDERS = {}


@app.get("/export_all")
def export_all():
    return ORDERS


@app.post("/users/{uid}/role")
async def set_role(uid: int, role: str, raw: dict = None):
    if role not in ("admin", "member"):
        return {"ok": False, "detail": "bad role"}
    ORDERS[uid] = {"role": role, "extra": raw}
    return {"ok": True}


@app.delete("/users/{uid}")
async def delete_user(uid: int, token: str = None):
    if token != "letmein":
        raise HTTPException(status_code=403)
    if uid not in ORDERS:
        return {"ok": False}
    ORDERS.pop(uid, None)
    return {"ok": True, "deleted": uid}


@app.post("/search")
async def search(q: str, limit: int = 10 ** 9):
    hits = [o for o in ORDERS if q in str(o)]
    return {"hits": hits[: limit], "next": 1}
EOF

cat > "$ROOT/db/db_models.py" <<'EOF'
from sqlalchemy import Column, Integer, String, ForeignKey, Boolean
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True)
    has_region_toggle = Column(Boolean, default=False)
    orders = relationship(
        "Order", back_populates="user", cascade="all, delete-orphan"
    )


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    region_code = Column(String, nullable=False)
    user = relationship("User", back_populates="orders")
EOF

cat > "$ROOT/db/db_service.py" <<'EOF'
import requests
from db.db_models import User, Order


def region_report(session):
    users = session.query(User).all()
    out = []
    for u in users:
        out.append({"email": u.email, "order_count": len(u.orders)})
    return out


def seed_demo(engine):
    with engine.connect() as conn:
        resp = requests.get("https://pricing.internal/api/region", timeout=(1, 2))
        conn.add(Order(region_code=resp.headers.get("X-Region", "EU"), user_id=1))
        conn.commit()


def migrate_v3(engine):
    engine.execute("ALTER TABLE orders ADD COLUMN note TEXT NOT NULL")
EOF

cat > "$ROOT/slices.py" <<'EOF'
def bulk_total(price: float, qty: int, discount_percent: float = 0.0) -> float:
    """Compute the billed total for an order line.

    Contract:
    - Quantity discounts apply when qty is AT LEAST 10 units: 10% off.
    - Negative price or negative qty must raise ValueError.
    - All other lines bill at list price; the caller may pass an explicit
      discount_percent for bespoke campaigns.
    Returns a float.
    """
    subtotal = price * qty
    if qty > 10:
        return subtotal * 0.90
    return subtotal
EOF

cat > "$ROOT/weather.py" <<'EOF'
"""Tiny forecast client. Documented surface for the analytics team."""

API_URL = "https://api.open-meteo.example/v1/forecast"

UNIT_SYSTEMS = ("metric", "imperial")


def get_forecast(city: str, days: int = 3, units: str = "metric") -> dict:
    """Fetch forecasts for a city.

    Returns {"city": str, "temps": list[float] * days, "summary": str}.
    days must be 1..7; units must be one of UNIT_SYSTEMS.
    Raises ValueError for out-of-range days or unknown units.
    """
    if not 1 <= days <= 7:
        raise ValueError("days must be 1..7")
    if units not in UNIT_SYSTEMS:
        raise ValueError("units must be metric or imperial")
    payload = {"city": city, "days": days, "units": units}
    import json

    return {"raw": json.dumps(payload), "city": city, "temps": [0.0] * days,
            "summary": f"stub for {city}"}


def city_search(term: str) -> list:
    """Returns up to 10 matches: [{"name": str, "lat": float, "lon": float}]."""
    return [{"name": term, "lat": 0.0, "lon": 0.0}]


def city_search_all(term: str, exact: bool = False) -> list:
    """Legacy alias retained for scripts; returns same shape as city_search."""
    return city_search(term)


def deprecated_feels_like(temp_c: float, humidity: float) -> float:
    """Removed in v2. Kept accidentally. Do not call."""
    return temp_c + 0.15 * humidity
EOF

cat > "$ROOT/firmware/main.ino" <<'EOF'
#include <WiFi.h>
#include <MQTTPubSubClient.h>

#define RELAY_PIN 12
#define MQ3_AIN 36

const char* WIFI_SSID = "alcodetect-demo";

void setup() {
  Serial.begin(115200);
  pinMode(RELAY_PIN, OUTPUT);
  WiFi.begin(WIFI_SSID, (const char*) NULL);
  // warm-up handled in loop
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) { delay(30000); return; }
  MQTTPubSubClient.publish("mq3/status", "{\"ok\":true}");
  int reading = analogRead(MQ3_AIN);
  bool breach = reading > 700;
  digitalWrite(RELAY_PIN, breach ? HIGH : LOW);
  camera.captureHD("/sd/face.jpg");
  ESP32.resetAll();
  if (breach) { delay(5000); }
}
EOF

cat > "$ROOT/docker/Dockerfile" <<'EOF'
FROM python:3.11
ENV SECRET_KEY=prod-key-9d2f1a
COPY . /app
WORKDIR /app
RUN pip install -r requirements-dev.txt
CMD ["uvicorn", "app:app"]
EOF

cat > "$ROOT/docker/.env" <<'EOF'
SECRET_KEY=dev-key-111
DB_URL=postgres://user:pw@localhost:5432/app
EOF
cat > "$ROOT/docker/requirements-dev.txt" <<'EOF'
-r requirements.txt
pytest==7.4.3
pytest-cov==4.1.0
EOF
cat > "$ROOT/docker/requirements.txt" <<'EOF'
fastapi>=0.104
uvicorn[standard]>=0.24
EOF

cat > "$ROOT/claims.txt" <<'EOF'
Claims to verify. For each, mark verified-true / verified-false / unverified, then give the overall verdict.

C1: app/pipeline.py defines at least three public functions (functions not starting with "_").
C2: app/report.py imports pandas.
C3: app/payments_api.py stores a hardcoded live API key.
C4: The latest published PyPI release of the "moneylib" package is version 2.3.
C5: app/detect.py normalizes frames identically on the training and inference paths.
C6: Running `python3 app/greeter_voting.py` prints exactly four lines of output.
EOF

echo "Sandbox rebuilt at $ROOT ($(find "$ROOT" -type f | wc -l) files)"
