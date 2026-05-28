# File: Backend/models/train/face_recognition.py
import os
import sys

# FIX: Prevent OpenMP and TensorFlow thread collisions with OpenCV.
# Both libraries try to hog all CPU cores for matrix algebra. Forcing them 
# to single-thread mode prevents CPU scheduling deadlocks inside web servers.
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["TF_NUM_INTRAOP_THREADS"] = "1"
os.environ["TF_NUM_INTEROP_THREADS"] = "1"
os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"

import cv2
import threading
from deepface import DeepFace

# ── Paths ──────────────────────────────────────────────────────
BASE_DIR     = os.path.dirname(__file__)
REGISTERED   = os.path.join(BASE_DIR, "../../data/faces/registered")
SNAPSHOTS    = os.path.join(BASE_DIR, "../../data/snapshots")
os.makedirs(REGISTERED, exist_ok=True)
os.makedirs(SNAPSHOTS,  exist_ok=True)

_deepface_lock = threading.Lock()

# FIX: Pre-warm the model graphs on the main thread during startup.
# This forces TensorFlow to allocate memory handles and compile configurations 
# right now, instead of lazily executing it inside an execution pool thread later.
print("[FACE RECOGNITION] Pre-warming Facenet architecture on main thread...")
try:
    DeepFace.build_model("Facenet")
    print("[FACE RECOGNITION] Facenet model ready and pre-warmed.")
except Exception as e:
    print(f"[FACE RECOGNITION] Pre-warm notice: {e}")


def enroll(name, images):
    """
    Register a new subject with their face images.
    """
    subject_dir = os.path.join(REGISTERED, name)
    os.makedirs(subject_dir, exist_ok=True)

    for i, img in enumerate(images):
        if isinstance(img, str):
            import shutil
            shutil.copy(img, os.path.join(subject_dir, f"{i+1:02d}.jpg"))
        else:
            cv2.imwrite(os.path.join(subject_dir, f"{i+1:02d}.jpg"), img)

    pkl_path = os.path.join(REGISTERED, "representations_facenet.pkl")
    if os.path.exists(pkl_path):
        try:
            os.remove(pkl_path)
        except Exception:
            pass

    print(f"✅ {name} enrolled with {len(images)} images")
    return subject_dir


def identify(frame):
    """
    Identify who is in the frame by comparing against registered faces.
    """
    # FIX: Use unique file markers per thread to guarantee file I/O isolation.
    # DeepFace.find requires clean file paths to process structural identity indexing 
    # cleanly without falling back to unstable internal type inferences.
    tid = threading.get_ident()
    temp_path = os.path.join(SNAPSHOTS, f"temp_identify_{tid}.jpg")
    cv2.imwrite(temp_path, frame)

    with _deepface_lock:
        try:
            results = DeepFace.find(
                img_path=temp_path,
                db_path=REGISTERED,
                model_name="Facenet",
                enforce_detection=False,
                silent=True
            )

            # Clean up the thread-isolated temporary file immediately after inference
            if os.path.exists(temp_path):
                os.remove(temp_path)

            if len(results) > 0 and len(results[0]) > 0:
                best      = results[0].iloc[0]
                name      = os.path.basename(os.path.dirname(best["identity"]))
                distance  = best["distance"]
                confidence = round(1 - distance, 4)

                return {
                    "identified": True,
                    "name":       name,
                    "confidence": confidence
                }

        except Exception as e:
            print(f"Recognition error: {e}")
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except:
                    pass

        return {
            "identified": False,
            "name":       None,
            "confidence": None
        }


def verify(frame, name):
    """
    Verify if the person in the frame is a specific registered subject.
    """
    subject_path = os.path.join(REGISTERED, name)

    if not os.path.exists(subject_path):
        return {"verified": False, "error": f"{name} not registered"}

    ref_img = os.path.join(subject_path, "01.jpg")
    tid = threading.get_ident()
    temp_path = os.path.join(SNAPSHOTS, f"temp_verify_{tid}.jpg")
    cv2.imwrite(temp_path, frame)

    with _deepface_lock:
        try:
            result = DeepFace.verify(
                img1_path=temp_path,
                img2_path=ref_img,
                model_name="Facenet",
                enforce_detection=False,
                silent=True
            )
            if os.path.exists(temp_path):
                os.remove(temp_path)
                
            return {
                "verified":   result["verified"],
                "confidence": round(1 - result["distance"], 4)
            }
        except Exception as e:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except:
                    pass
            return {"verified": False, "error": str(e)}