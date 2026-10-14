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
from models.train.identity_matching import decide_identity, confidence_for_distance
import tensorflow.keras.backend as K

# ── Paths ──────────────────────────────────────────────────────
BASE_DIR     = os.path.dirname(__file__)
REGISTERED   = os.path.join(BASE_DIR, "../../data/faces/registered")
SNAPSHOTS    = os.path.join(BASE_DIR, "../../data/snapshots")
os.makedirs(REGISTERED, exist_ok=True)
os.makedirs(SNAPSHOTS,  exist_ok=True)

_deepface_lock = threading.RLock()

# ── Identity tuning — defined ONCE here, never duplicated ────────────────────
# Model in use is DeepFace Facenet with the library's default cosine distance
# metric (see MODEL_NAME / DISTANCE_METRIC). DeepFace's own documented default
# threshold for Facenet+cosine is 0.40.
#
# The defaults below are DELIBERATELY STRICTER than that library default: a gate
# that lets the wrong person through is worse than one that says "unknown", so we
# prefer false negatives. They are PLACEHOLDERS — run
# `python scripts/calibrate_face_threshold.py` against the real gallery and set the
# printed FACE_MATCH_THRESHOLD / FACE_MATCH_MARGIN in the environment.
FACE_MATCH_THRESHOLD = float(os.getenv("FACE_MATCH_THRESHOLD", "0.32"))
# Two different people must differ by at least this much to call one of them.
FACE_MATCH_MARGIN = float(os.getenv("FACE_MATCH_MARGIN", "0.08"))
# Minimum face width as a FRACTION of frame width (same units cv_model's
# estimate_face_width/estimate_proximity use). Below this the frame is not even
# sent to DeepFace.
FACE_MIN_SIZE = float(os.getenv("FACE_MIN_SIZE", "0.05"))
# Frames the same name must win before it is reported, and the HTT-request rate.
FACE_CONSECUTIVE_FRAMES = int(os.getenv("FACE_CONSECUTIVE_FRAMES", "3"))
FACE_IDENT_MAX_HZ = float(os.getenv("FACE_IDENT_MAX_HZ", "2"))

MODEL_NAME = "Facenet"
DISTANCE_METRIC = "cosine"
# A REAL detector: DeepFace must actually locate a face before it will embed
# anything, so an empty frame can never produce an embedding and a match.
DETECTOR_BACKEND = os.getenv("FACE_DETECTOR_BACKEND", "opencv")

print("[FACE RECOGNITION] Pre-warming DeepFace models on main thread...")
try:
    with _deepface_lock:
        DeepFace.build_model("Facenet")
        
        # We use 'skip' to force it to ignore OpenCV/MTCNN detectors completely 
        # and just compile the Keras graph in memory.
        dummy_img = np.zeros((224, 224, 3), dtype=np.uint8)
        # pre-warm with the SAME real detector the live path uses, so the detector
        # graph is hot too (a black dummy frame legitimately raises "no face")
        try:
            _ = DeepFace.represent(img_path=dummy_img, model_name=MODEL_NAME,
                                   detector_backend=DETECTOR_BACKEND,
                                   enforce_detection=False, silent=True)
        except Exception:
            pass
        
    print("[FACE RECOGNITION] Models fully pre-warmed and ready.")
except Exception as e:
    print(f"[FACE RECOGNITION] Pre-warm notice: {e}")


def _next_free_path(subject_dir, taken):
    """Lowest unused NN.jpg slot inside subject_dir.

    A slot is free only when it is neither already written by this call (`taken`) nor
    present on disk. Numbering from 01 on every call used to overwrite a person's
    earlier photos when they were enrolled again.
    """
    n = 1
    while True:
        path = os.path.join(subject_dir, f"{n:02d}.jpg")
        if path not in taken and not os.path.exists(path):
            return path
        n += 1


def enroll(name, images):
    subject_dir = os.path.join(REGISTERED, name)
    os.makedirs(subject_dir, exist_ok=True)

    taken = set()
    for img in images:
        dest = _next_free_path(subject_dir, taken)
        if isinstance(img, str):
            import shutil
            shutil.copy(img, dest)
        else:
            cv2.imwrite(dest, img)
        taken.add(dest)

    pkl_path = os.path.join(REGISTERED, "representations_facenet.pkl")
    if os.path.exists(pkl_path):
        try:
            os.remove(pkl_path)
        except Exception:
            pass

    print(f"✅ {name} enrolled with {len(images)} images")
    return subject_dir


def _unknown(reason="unknown"):
    """Fail-safe result: identified False, name None, plus the reason.

    The `name` key is unchanged, only its value for an unknown face is now None
    (the frontend already renders its own "Unknown" placeholder).
    """
    return {"identified": False, "name": None, "confidence": 0.0,
            "reason": reason, "face_name": None}


def identify(frame, face_info=None):
    """Identify the person in `frame`, or return unknown.

    `face_info` is the MediaPipe result from cv_model.analyze_frame (the caller
    already has it, so we do not pay for a second pass). When it says there is no
    face, or the face is too small to be trusted, DeepFace is NOT called at all.

    Thresholds: a candidate must be within FACE_MATCH_THRESHOLD, and if a SECOND
    distinct person is closer than FACE_MATCH_MARGIN the answer is "ambiguous" ->
    unknown. A wrong name is worse than no name.
    """
    # ---- gate 1: a face must actually be present and big enough -------------
    if face_info is not None:
        status = face_info.get("status")
        if status == "no_face":
            return _unknown("no_face")
        width = float(face_info.get("face_width") or 0.0)
        if width < FACE_MIN_SIZE:
            return _unknown("too_small")

    if not os.path.exists(REGISTERED) or not os.listdir(REGISTERED):
        return _unknown("no_candidates")

    fd, temp_path = tempfile.mkstemp(suffix=".jpg", dir=SNAPSHOTS)
    os.close(fd)

    try:
        cv2.imwrite(temp_path, frame)
    except Exception as e:
        print(f"Failed to write identify temp frame: {e}")
        return _unknown("write_failed")

    try:
        with _deepface_lock:
            # enforce_detection=True + a real detector: DeepFace itself must find a
            # face. With detector_backend="skip" it embedded whatever the camera saw,
            # which is why an empty room still produced an identity.
            results = DeepFace.find(
                img_path=temp_path,
                db_path=REGISTERED,
                model_name=MODEL_NAME,
                detector_backend=DETECTOR_BACKEND,
                distance_metric=DISTANCE_METRIC,
                enforce_detection=True,
                silent=True,
            )
            candidates = _collect_candidates(results)
    except Exception as e:
        # DeepFace raises ValueError("Face could not be detected") for a frame with
        # no face — that is an ordinary "unknown", never an error and never a guess.
        print(f"[IDENTIFY] DeepFace returned no match: {e}")
        return _unknown("no_face" if _is_no_face_error(e) else "error")
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception as e:
                print(f"[IDENTIFY] temp file cleanup failed: {e}")

    person, reason, info = decide_identity(
        candidates, FACE_MATCH_THRESHOLD, FACE_MATCH_MARGIN)
    if person is None:
        return _unknown(reason)

    distance = info.get("distance", FACE_MATCH_THRESHOLD)
    return {
        "identified": True,
        "name": person,
        "confidence": round(confidence_for_distance(distance, FACE_MATCH_THRESHOLD), 4),
        "reason": reason,
        "face_name": person,
        "distance": distance,
        "candidates": len(candidates),
    }


def _is_no_face_error(exc):
    """DeepFace's 'Face could not be detected' is an unknown, not a failure."""
    msg = str(exc).lower()
    return "could not be detected" in msg or "no face" in msg or "face could not" in msg


def _collect_candidates(results):
    """Turn DeepFace.find output into [(person, distance), ...], one per gallery IMAGE.

    Every row is kept (not just the best) so decide_identity can apply the margin
    against a genuinely different person. Distances are read from the `distance`
    column, which is already in DISTANCE_METRIC's units because find() was given the
    same metric.
    """
    if results is None or len(results) == 0:
        return []
    try:
        if results[0].empty:
            return []
    except (AttributeError, IndexError):
        return []

    out = []
    for _, row in results[0].iterrows():
        file_path = row.get("identity")
        if not file_path:
            continue
        person = _person_from_path(file_path)
        if person is None:
            continue
        out.append((person, row.get("distance")))
    return out


def _person_from_path(file_path):
    """Gallery images live at registered/<person>/NN.jpg; older ones at registered/<person>.jpg."""
    parent = os.path.basename(os.path.dirname(file_path))
    if parent.lower() == "registered":
        stem = os.path.splitext(os.path.basename(file_path))[0]
        return stem or None
    return parent or None


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
            # per-call K.clear_session() removed — same benchmark logic as identify() (no leak)

            # real detector + enforce_detection, same as identify()
            result = DeepFace.verify(
                img1_path=temp_path,
                img2_path=ref_img,
                model_name=MODEL_NAME,
                detector_backend=DETECTOR_BACKEND,
                distance_metric=DISTANCE_METRIC,
                enforce_detection=True,
                silent=True
            )

            distance = float(result.get("distance", 1.0))
            # verify() is a one-to-one claim, so only the threshold applies here.
            # A distance above it is NOT this person, whatever the library said.
            within = distance <= FACE_MATCH_THRESHOLD
            result_data = {
                "verified":   bool(result.get("verified")) and within,
                "confidence": round(confidence_for_distance(distance, FACE_MATCH_THRESHOLD), 4),
                "distance":   round(distance, 4),
                "reason":     "matched" if within else "above_threshold",
            }
            
        except Exception as e:
            result_data = {"verified": False, "error": str(e)}
            
        finally:
            # per-call K.clear_session() removed — keep pre-warm hot
            pass
            
    if os.path.exists(temp_path):
        try:
            os.remove(temp_path)
        except:
            pass

    return result_data