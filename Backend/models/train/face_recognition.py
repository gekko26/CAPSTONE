import os
import cv2
from deepface import DeepFace

# ── Paths ──────────────────────────────────────────────────────
BASE_DIR     = os.path.dirname(__file__)
REGISTERED   = os.path.join(BASE_DIR, "../../data/faces/registered")
SNAPSHOTS    = os.path.join(BASE_DIR, "../../data/snapshots")
os.makedirs(REGISTERED, exist_ok=True)
os.makedirs(SNAPSHOTS,  exist_ok=True)

def enroll(name, images):
    """
    Register a new subject with their face images.

    Parameters:
        name    — subject name e.g. "john_doe"
        images  — list of image file paths or numpy arrays

    Usage:
        enroll("john_doe", ["img1.jpg", "img2.jpg", "img3.jpg"])
    """
    subject_dir = os.path.join(REGISTERED, name)
    os.makedirs(subject_dir, exist_ok=True)

    for i, img in enumerate(images):
        if isinstance(img, str):
            # img is a file path — copy it
            import shutil
            shutil.copy(img, os.path.join(subject_dir, f"{i+1:02d}.jpg"))
        else:
            # img is a numpy array — save it
            cv2.imwrite(os.path.join(subject_dir, f"{i+1:02d}.jpg"), img)

    print(f"✅ {name} enrolled with {len(images)} images")
    return subject_dir


def identify(frame):
    """
    Identify who is in the frame by comparing against registered faces.

    Returns:
        {
            "identified": True,
            "name": "john_doe",
            "confidence": 0.92
        }
        or
        {
            "identified": False,
            "name": None,
            "confidence": None
        }

    Usage:
        import cv2
        frame = cv2.imread("test.jpg")
        result = identify(frame)
    """

    # Save frame temporarily for DeepFace
    temp_path = os.path.join(SNAPSHOTS, "temp_identify.jpg")
    cv2.imwrite(temp_path, frame)

    try:
        results = DeepFace.find(
            img_path=temp_path,
            db_path=REGISTERED,
            model_name="Facenet",
            enforce_detection=False,
            silent=True
        )

        if len(results) > 0 and len(results[0]) > 0:
            best      = results[0].iloc[0]
            # Extract name from file path
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

    return {
        "identified": False,
        "name":       None,
        "confidence": None
    }


def verify(frame, name):
    """
    Verify if the person in the frame is a specific registered subject.

    Usage:
        result = verify(frame, "john_doe")
        # {"verified": True, "confidence": 0.95}
    """
    temp_path    = os.path.join(SNAPSHOTS, "temp_verify.jpg")
    subject_path = os.path.join(REGISTERED, name)

    cv2.imwrite(temp_path, frame)

    if not os.path.exists(subject_path):
        return {"verified": False, "error": f"{name} not registered"}

    # Compare against first registered image
    ref_img = os.path.join(subject_path, "01.jpg")

    try:
        result = DeepFace.verify(
            img1_path=temp_path,
            img2_path=ref_img,
            model_name="Facenet",
            enforce_detection=False,
            silent=True
        )
        return {
            "verified":   result["verified"],
            "confidence": round(1 - result["distance"], 4)
        }
    except Exception as e:
        return {"verified": False, "error": str(e)}


if __name__ == "__main__":
    print("Face recognition ready.")
    print("Registered faces directory:", REGISTERED)
    print("\nUsage:")
    print("  enroll('john_doe', ['img1.jpg', 'img2.jpg'])")
    print("  identify(frame)")
    print("  verify(frame, 'john_doe')")