# File: Backend/models/train/cv_model.py
import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import os
import threading

LEFT_EYE  = [362, 385, 387, 263, 373, 380]
RIGHT_EYE = [33,  160, 158, 133, 153, 144]

# Mouth landmarks: corners (61, 291), outer top/bottom (0, 17), inner top/bottom (13, 14)
MOUTH = [61, 291, 0, 17, 13, 14]

FACE_CLOSE_THRESHOLD = 0.18

# Overlay style constants (BGR)
FONT       = cv2.FONT_HERSHEY_SIMPLEX
LINE       = cv2.LINE_AA
COLOR_OK   = (80, 220, 100)    # green
COLOR_WARN = (60, 200, 255)    # amber
COLOR_BAD  = (60, 60, 230)     # red
COLOR_TEXT = (245, 245, 245)   # near-white
COLOR_IDLE = (180, 180, 180)   # grey

# Thresholds — tune these empirically against your own footage
EAR_NORMAL_THRESHOLD = 0.25
EAR_DROWSY_THRESHOLD = 0.20
MAR_YAWN_THRESHOLD   = 0.55
HEAD_DOWN_PITCH_DEG  = 15.0   # positive pitch ~ chin dropping (nodding off)

_MODEL_PATH = os.path.join(
    os.path.dirname(__file__),
    "../../face_landmarker.task"
)
_landmarker = None
_landmarker_lock = threading.Lock()


def _get_landmarker():
    global _landmarker
    if _landmarker is None:
        with _landmarker_lock:
            if _landmarker is None:
                options = vision.FaceLandmarkerOptions(
                    base_options=python.BaseOptions(model_asset_path=_MODEL_PATH),
                    running_mode=vision.RunningMode.IMAGE,
                    num_faces=1,
                    min_face_detection_confidence=0.5,
                    min_face_presence_confidence=0.5,
                    min_tracking_confidence=0.5,
                    output_facial_transformation_matrixes=True,  # needed for head pose
                )
                _landmarker = vision.FaceLandmarker.create_from_options(options)
    return _landmarker


def eye_aspect_ratio(landmarks, eye_indices, w, h):
    pts = [
        (int(landmarks[i].x * w), int(landmarks[i].y * h))
        for i in eye_indices
    ]
    A = np.linalg.norm(np.array(pts[1]) - np.array(pts[5]))
    B = np.linalg.norm(np.array(pts[2]) - np.array(pts[4]))
    C = np.linalg.norm(np.array(pts[0]) - np.array(pts[3]))
    return round((A + B) / (2.0 * C), 4)


def mouth_aspect_ratio(landmarks, mouth_indices, w, h):
    """
    MAR = average(outer vertical, inner vertical) / horizontal (mouth corner distance)
    mouth_indices order: [corner_left, corner_right, outer_top, outer_bottom, inner_top, inner_bottom]
    """
    def pt(i):
        return np.array([landmarks[i].x * w, landmarks[i].y * h])

    corner_l, corner_r, outer_top, outer_bot, inner_top, inner_bot = mouth_indices

    vertical_outer = np.linalg.norm(pt(outer_top) - pt(outer_bot))
    vertical_inner = np.linalg.norm(pt(inner_top) - pt(inner_bot))
    horizontal      = np.linalg.norm(pt(corner_l) - pt(corner_r))

    if horizontal == 0:
        return 0.0

    return round(((vertical_outer + vertical_inner) / 2.0) / horizontal, 4)


def rotation_matrix_to_euler(matrix):
    """
    Decomposes a 4x4 (or 3x3) rotation matrix into pitch/yaw/roll in degrees.
    pitch: nodding (up/down)
    yaw:   turning (left/right)
    roll:  tilting (side to side)
    """
    R = np.array(matrix)[:3, :3]
    sy = np.sqrt(R[0, 0] ** 2 + R[1, 0] ** 2)
    singular = sy < 1e-6

    if not singular:
        pitch = np.degrees(np.arctan2(R[2, 1], R[2, 2]))
        yaw   = np.degrees(np.arctan2(-R[2, 0], sy))
        roll  = np.degrees(np.arctan2(R[1, 0], R[0, 0]))
    else:
        pitch = np.degrees(np.arctan2(-R[1, 2], R[1, 1]))
        yaw   = np.degrees(np.arctan2(-R[2, 0], sy))
        roll  = 0.0

    return round(float(pitch), 2), round(float(yaw), 2), round(float(roll), 2)


def estimate_proximity(landmarks, w, h):
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
    Returns EAR + MAR + head pose + impairment status + proximity.
    """
    h, w = frame.shape[:2]
    rgb  = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

    landmarker = _get_landmarker()
    results  = landmarker.detect(mp_image)

    if not results.face_landmarks:
        return {
            "ear":        None,
            "mar":        None,
            "pitch":      None,
            "yaw":        None,
            "roll":       None,
            "status":     "no_face",
            "impaired":   False,
            "left_ear":   None,
            "right_ear":  None,
            "yawning":    False,
            "head_down":  False,
            "proximity":  "none",
            "is_close":   False,
            "face_bbox":     None,
            "eye_points_l":  [],
            "eye_points_r":  [],
            "mouth_points":  [],
        }

    landmarks = results.face_landmarks[0]

    left_ear  = eye_aspect_ratio(landmarks, LEFT_EYE,  w, h)
    right_ear = eye_aspect_ratio(landmarks, RIGHT_EYE, w, h)
    avg_ear   = round((left_ear + right_ear) / 2.0, 4)

    mar = mouth_aspect_ratio(landmarks, MOUTH, w, h)
    yawning = mar > MAR_YAWN_THRESHOLD

    pitch = yaw = roll = None
    head_down = False
    if results.facial_transformation_matrixes:
        pitch, yaw, roll = rotation_matrix_to_euler(
            results.facial_transformation_matrixes[0]
        )
        head_down = pitch > HEAD_DOWN_PITCH_DEG

    proximity = estimate_proximity(landmarks, w, h)

    def px(i):
        return (int(landmarks[i].x * w), int(landmarks[i].y * h))

    xs = [lm.x for lm in landmarks]
    ys = [lm.y for lm in landmarks]
    pad = int(min(w, h) * 0.04)
    face_bbox = (
        max(int(min(xs) * w) - pad, 0),
        max(int(min(ys) * h) - pad, 0),
        min(int(max(xs) * w) + pad, w - 1),
        min(int(max(ys) * h) + pad, h - 1),
    )

    # Fusion of EAR + MAR + head pose into a single rule-based status.
    # This stays a lightweight pre-trigger heuristic — it does NOT touch
    # the trained fusion RF, which still only consumes sensor + MobileNetV2 outputs.
    if avg_ear > EAR_NORMAL_THRESHOLD and not yawning and not head_down:
        status, impaired = "normal", False
    elif avg_ear > EAR_DROWSY_THRESHOLD or yawning or head_down:
        status, impaired = "drowsy", True
    else:
        status, impaired = "impaired", True

    return {
        "ear":        avg_ear,
        "mar":        mar,
        "pitch":      pitch,
        "yaw":        yaw,
        "roll":       roll,
        "status":     status,
        "impaired":   impaired,
        "left_ear":   left_ear,
        "right_ear":  right_ear,
        "yawning":    yawning,
        "head_down":  head_down,
        "proximity":  proximity,
        "is_close":   proximity == "close",
        "face_bbox":     face_bbox,
        "eye_points_l":  [px(i) for i in LEFT_EYE],
        "eye_points_r":  [px(i) for i in RIGHT_EYE],
        "mouth_points":  [px(i) for i in MOUTH],
    }


def draw_overlay(frame, result):
    """
    Draws a production-ready HUD on the frame for live visualization:
    face box, eye/mouth landmarks, EAR/MAR/head-pose readouts, status,
    proximity and a head-pose direction arrow.
    Tolerates missing/partial result keys (e.g. cached or minimal dicts).
    Returns the modified frame (drawn in place, also returned for chaining).
    """
    if frame is None:
        return frame

    h, w = frame.shape[:2]

    def get(key, default=None):
        return result.get(key, default) if isinstance(result, dict) else default

    status    = get("status", "no_face") or "no_face"
    ear       = get("ear")
    left_ear  = get("left_ear")
    right_ear = get("right_ear")
    mar       = get("mar")
    pitch     = get("pitch")
    yaw       = get("yaw")
    roll      = get("roll")
    yawning   = bool(get("yawning", False))
    proximity = get("proximity", "none")

    color_status = {
        "normal":   COLOR_OK,
        "drowsy":   COLOR_WARN,
        "impaired": COLOR_BAD,
    }.get(status, COLOR_IDLE)

    # ── Face box + status label ────────────────────────────────
    bbox = get("face_bbox")
    if bbox:
        x1, y1, x2, y2 = bbox
        cv2.rectangle(frame, (x1, y1), (x2, y2), color_status, 2)

        label = status.upper()
        (tw, th), _ = cv2.getTextSize(
            label, FONT, 0.6, 2
        )
        ly = max(y1 - 10, th + 6)
        cv2.rectangle(frame, (x1, ly - th - 8), (x1 + tw + 8, ly + 4), color_status, -1)
        cv2.putText(frame, label, (x1 + 4, ly - 2), FONT, 0.6, (15, 15, 15), 2, LINE)

        # ── Head-pose direction arrow (top-right of face box) ──
        if pitch is not None and yaw is not None:
            cx, cy = x2 + 30, y1 + 30
            dx = np.sin(np.radians(np.clip(yaw,  -90, 90)))
            dy = np.sin(np.radians(np.clip(pitch, -90, 90)))
            scale = min(28.0, 28.0 * np.hypot(dx, dy)) if np.hypot(dx, dy) > 0.05 else 0
            tip = (int(cx + dx * scale), int(cy + dy * scale))
            cv2.circle(frame, (cx, cy), 3, color_status, -1)
            if scale:
                cv2.arrowedLine(frame, (cx, cy), tip, color_status, 2, LINE, tipLength=0.35)
            cv2.putText(frame, f"P{int(pitch)} Y{int(yaw)} R{int(roll or 0)}",
                        (cx - 40, cy + 48), FONT, 0.42, COLOR_TEXT, 1, LINE)

    # ── Landmarks ──────────────────────────────────────────────
    eye_color = (
        COLOR_BAD
        if (ear is not None and ear < EAR_DROWSY_THRESHOLD)
        else (COLOR_WARN if (ear is not None and ear < EAR_NORMAL_THRESHOLD) else COLOR_OK)
    )
    for pts in (get("eye_points_l"), get("eye_points_r")):
        for p in pts or []:
            cv2.circle(frame, tuple(p), 2, eye_color, -1)
    mouth_color = COLOR_BAD if yawning else (160, 160, 255)
    for p in get("mouth_points") or []:
        cv2.circle(frame, tuple(p), 2, mouth_color, -1)

    # ── HUD panel ──────────────────────────────────────────────
    ear_txt = f"{ear:.2f}" if ear is not None else "--"
    lines = [
        ("ALCODETECT", color_status),
        (f"EAR: {ear_txt} (L: {left_ear if left_ear is not None else '--'}"
         f" | R: {right_ear if right_ear is not None else '--'})",
         COLOR_BAD if (ear is not None and ear < EAR_DROWSY_THRESHOLD) else COLOR_TEXT),
        (f"MAR: {mar:.2f}{'  [YAWN]' if yawning else ''}" if mar is not None else "MAR: --",
         COLOR_BAD if yawning else COLOR_TEXT),
        (f"POSE: P{pitch if pitch is not None else '--'}"
         f" Y{yaw if yaw is not None else '--'}"
         f" R{roll if roll is not None else '--'}", COLOR_TEXT),
        (f"DIST: {proximity.upper()}", COLOR_TEXT),
        (f"STATUS: {status.upper()}", color_status),
    ]

    pad, line_h, font_s = 10, 22, 0.52
    panel_w, panel_h = 265, pad * 2 + line_h * len(lines)
    overlay_img = frame.copy()
    cv2.rectangle(overlay_img, (0, 0), (panel_w, panel_h), (25, 25, 25), -1)
    cv2.addWeighted(overlay_img, 0.65, frame, 0.35, 0, frame)
    for i, (txt, col) in enumerate(lines):
        cv2.putText(frame, txt, (pad, pad + line_h * i + 14), FONT, font_s, col, 1, LINE)

    # ── No-face banner ─────────────────────────────────────────
    if status == "no_face":
        msg = "NO FACE DETECTED"
        (tw, th), _ = cv2.getTextSize(msg, FONT, 0.9, 2)
        bx, by = (w - tw) // 2 - 12, h - 60
        cv2.rectangle(frame, (bx, by), (bx + tw + 24, by + th + 18), (40, 40, 40), -1)
        cv2.putText(frame, msg, (bx + 12, by + th + 6), FONT, 0.9, COLOR_WARN, 2, LINE)

    return frame


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
    print("Uses MediaPipe Face Landmarker (Tasks API) + EAR + MAR + head pose.")
