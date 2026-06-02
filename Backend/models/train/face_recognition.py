import os
import sys
import threading
import tempfile
import cv2
import numpy as np

# 1. Aggressively restrict threading before importing ML libraries
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["TF_NUM_INTRAOP_THREADS"] = "1"
os.environ["TF_NUM_INTEROP_THREADS"] = "1"
os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"
os.environ["CUDA_VISIBLE_DEVICES"] = "-1" 

from deepface import DeepFace
import tensorflow.keras.backend as K

# ── Paths ──────────────────────────────────────────────────────
BASE_DIR     = os.path.dirname(__file__)
REGISTERED   = os.path.join(BASE_DIR, "../../data/faces/registered")
SNAPSHOTS    = os.path.join(BASE_DIR, "../../data/snapshots")
os.makedirs(REGISTERED, exist_ok=True)
os.makedirs(SNAPSHOTS,  exist_ok=True)

_deepface_lock = threading.RLock()

print("[FACE RECOGNITION] Pre-warming DeepFace models on main thread...")
try:
    with _deepface_lock:
        DeepFace.build_model("Facenet")
        
        # We use 'skip' to force it to ignore OpenCV/MTCNN detectors completely 
        # and just compile the Keras graph in memory.
        dummy_img = np.zeros((224, 224, 3), dtype=np.uint8)
        _ = DeepFace.represent(img_path=dummy_img, model_name="Facenet", enforce_detection=False, detector_backend='skip')
        
    print("[FACE RECOGNITION] Models fully pre-warmed and ready.")
except Exception as e:
    print(f"[FACE RECOGNITION] Pre-warm notice: {e}")


def enroll(name, images):
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
    if not os.path.exists(REGISTERED) or not os.listdir(REGISTERED):
        return {"identified": False, "name": "Unknown", "confidence": 0.0}

    fd, temp_path = tempfile.mkstemp(suffix=".jpg", dir=SNAPSHOTS)
    os.close(fd) 

    try:
        cv2.imwrite(temp_path, frame)
    except Exception as e:
        print(f"Failed to write identify temp frame: {e}")
        return {"identified": False, "name": "Unknown", "confidence": 0.0}

    result_data = {"identified": False, "name": "Unknown", "confidence": 0.0}

    with _deepface_lock:
        try:
            K.clear_session()
            
            # FIX: Bypass OpenCV detector entirely using 'skip'
            results = DeepFace.find(
                img_path=temp_path, 
                db_path=REGISTERED,
                model_name="Facenet",
                enforce_detection=False,
                detector_backend="skip", 
                silent=True
            )

            if len(results) > 0 and not results[0].empty:
                best      = results[0].iloc[0]
                name      = os.path.basename(os.path.dirname(best["identity"]))
                distance  = best.get("distance", 1.0)
                confidence = max(0.0, min(1.0, 1.0 - (distance / 0.4)))

                result_data = {
                    "identified": True,
                    "name":       name,
                    "confidence": confidence
                }

        except Exception as e:
            print(f"DeepFace processing error: {e}")
        
        finally:
            K.clear_session()

    if os.path.exists(temp_path):
        try:
            os.remove(temp_path)
        except:
            pass

    return result_data


def verify(frame, name):
    subject_path = os.path.join(REGISTERED, name)

    if not os.path.exists(subject_path):
        return {"verified": False, "error": f"{name} not registered"}

    ref_img = os.path.join(subject_path, "01.jpg")
    
    fd, temp_path = tempfile.mkstemp(suffix=".jpg", dir=SNAPSHOTS)
    os.close(fd)

    try:
        cv2.imwrite(temp_path, frame)
    except Exception as e:
        return {"verified": False, "error": f"Write failed: {e}"}

    result_data = {"verified": False, "error": "Unknown verification error"}

    with _deepface_lock:
        try:
            K.clear_session()
            
            # FIX: Bypass OpenCV detector entirely using 'skip'
            result = DeepFace.verify(
                img1_path=temp_path,
                img2_path=ref_img,
                model_name="Facenet",
                enforce_detection=False,
                detector_backend="skip",
                silent=True
            )
                
            result_data = {
                "verified":   result["verified"],
                "confidence": round(1 - result["distance"], 4)
            }
            
        except Exception as e:
            result_data = {"verified": False, "error": str(e)}
            
        finally:
            K.clear_session()
            
    if os.path.exists(temp_path):
        try:
            os.remove(temp_path)
        except:
            pass

    return result_data