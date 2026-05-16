import cv2
import numpy as np
import mediapipe as mp

LEFT_EYE  = [362, 385, 387, 263, 373, 380]
RIGHT_EYE = [33,  160, 158, 133, 153, 144]

# Tune this based on your camera distance from the sensor gate
# 0.18 means face occupies 18% of frame width = person is close enough
FACE_CLOSE_THRESHOLD = 0.18


def eye_aspect_ratio(landmarks, eye_indices, w, h):
    pts = [
        (int(landmarks[i].x * w), int(landmarks[i].y * h))
        for i in eye_indices
    ]
    A = np.linalg.norm(np.array(pts[1]) - np.array(pts[5]))
    B = np.linalg.norm(np.array(pts[2]) - np.array(pts[4]))
    C = np.linalg.norm(np.array(pts[0]) - np.array(pts[3]))
    return round((A + B) / (2.0 * C), 4)


def estimate_proximity(landmarks, w, h):
    """
    Estimates proximity based on face width fraction of frame.
    Returns: 'close', 'medium', 'far'
    """
    x_coords            = [lm.x for lm in landmarks]
    face_width_fraction = max(x_coords) - min(x_coords)

    if face_width_fraction >= FACE_CLOSE_THRESHOLD:
        return "close"
    elif face_width_fraction >= FACE_CLOSE_THRESHOLD * 0.6:
        return "medium"
    else:
        return "far"


def analyze_frame(frame):
    """
    Analyzes a single camera frame.
    Returns EAR + impairment status + proximity.
    """
    h, w = frame.shape[:2]
    rgb  = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    with mp.solutions.face_mesh.FaceMesh(
        static_image_mode=True,
        max_num_faces=1,
        min_detection_confidence=0.5
    ) as face_mesh:

        results = face_mesh.process(rgb)

        if not results.multi_face_landmarks:
            return {
                "ear":       None,
                "status":    "no_face",
                "impaired":  False,
                "left_ear":  None,
                "right_ear": None,
                "proximity": "none",
                "is_close":  False,
            }

        landmarks = results.multi_face_landmarks[0].landmark
        left_ear  = eye_aspect_ratio(landmarks, LEFT_EYE,  w, h)
        right_ear = eye_aspect_ratio(landmarks, RIGHT_EYE, w, h)
        avg_ear   = round((left_ear + right_ear) / 2.0, 4)
        proximity = estimate_proximity(landmarks, w, h)

        if avg_ear > 0.25:
            status, impaired = "normal", False
        elif avg_ear > 0.20:
            status, impaired = "drowsy", True
        else:
            status, impaired = "impaired", True

        return {
            "ear":       avg_ear,
            "status":    status,
            "impaired":  impaired,
            "left_ear":  left_ear,
            "right_ear": right_ear,
            "proximity": proximity,
            "is_close":  proximity == "close",
        }


def analyze_blink(ear_history, threshold=0.20):
    blinks = 0
    below  = False
    for ear in ear_history:
        if ear < threshold:
            below = True
        elif below:
            blinks += 1
            below   = False
    return blinks


if __name__ == "__main__":
    print("CV model ready — no training needed.")
    print("Uses MediaPipe Face Mesh + EAR formula.")