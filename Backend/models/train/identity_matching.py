"""Pure identity-matching logic for the AlcoDetect gate.

Deliberately dependency-free: no DeepFace, cv2, TensorFlow or SQLAlchemy. Everything
here is a deterministic function of its arguments so it can be unit-tested with plain
numbers and so it never pulls a model into the process.

The design goal is fail-safe. A wrong name is worse than "unknown", so anything
ambiguous, out of range or malformed resolves to unknown rather than to a guess.

The thresholds themselves are NOT defined here. They live once in
``models/train/face_recognition.py`` (which reads them from the environment) and are
passed in by the caller.
"""

import math
from collections import deque

# Distances that cannot be used for a decision at all.
NO_MATCH_NAME = None


def _usable(distance):
    """True when a distance can take part in a decision."""
    if distance is None:
        return False
    try:
        d = float(distance)
    except (TypeError, ValueError):
        return False
    if math.isnan(d) or math.isinf(d):
        return False
    return d >= 0.0


def decide_identity(candidates, threshold, margin):
    """Pick an identity from DeepFace's per-image matches.

    ``candidates`` is a sequence of ``(person, distance)`` pairs — one entry per
    gallery IMAGE, so the same person normally appears several times. Distances are
    aggregated per person by taking that person's MINIMUM distance, because a single
    good photo is enough to recognise someone; the worst photos must not drag the
    person away from the answer.

    Decision order:
        no usable candidate      -> (None, "no_candidates")
        best distance > threshold-> (None, "above_threshold")
        a second DISTINCT person
          within `margin` of the best
                                -> (None, "ambiguous")
        otherwise                -> (best person, "matched", info)

    A gallery holding only ONE person cannot be ambiguous, so the margin rule does not
    apply there and only the threshold decides. That is intentional: with a single
    enrolled person there is nobody to confuse them with, and refusing every frame
    would make the gate unusable.

    Returns ``(name_or_None, reason, info)``. NaN, infinite, negative and None
    distances are ignored; if nothing usable remains the result is unknown.
    """
    try:
        threshold = float(threshold)
        margin = float(margin)
    except (TypeError, ValueError):
        return NO_MATCH_NAME, "no_candidates", {"reason_detail": "invalid_threshold"}

    # aggregate by person -> minimum distance wins
    best_per_person = {}
    for item in candidates or ():
        try:
            person, distance = item
        except (TypeError, ValueError):
            continue
        if person is None or person == "":
            continue
        if not _usable(distance):
            continue
        d = float(distance)
        if person not in best_per_person or d < best_per_person[person]:
            best_per_person[person] = d

    info = {
        "usable_people": len(best_per_person),
        "threshold": threshold,
        "margin": margin,
    }

    if not best_per_person:
        return NO_MATCH_NAME, "no_candidates", info

    ranked = sorted(best_per_person.items(), key=lambda kv: (kv[1], str(kv[0])))
    best_person, best_distance = ranked[0]
    info["best_distance"] = best_distance

    if best_distance > threshold:
        info["closest_person"] = best_person
        return NO_MATCH_NAME, "above_threshold", info

    if len(ranked) > 1:
        second_person, second_distance = ranked[1]
        info["second_distance"] = second_distance
        gap = second_distance - best_distance
        info["margin_gap"] = gap
        if gap < margin:
            # two people are effectively tied — refuse rather than pick one
            return NO_MATCH_NAME, "ambiguous", info

    info["distance"] = best_distance
    return best_person, "matched", info


def confidence_for_distance(distance, threshold):
    """Map a distance to a 0..1 confidence, monotonically decreasing.

    distance 0 -> 1.0, distance == threshold -> 0.0, clamped to [0, 1]. Purely a
    display number; the pass/fail decision is made by `decide_identity`.
    """
    try:
        d = float(distance)
        t = float(threshold)
    except (TypeError, ValueError):
        return 0.0
    if not _usable(d) or t <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - (d / t)))


class IdentitySmoother:
    """Turn noisy per-frame identities into a stable one.

    A name is only reported once the SAME name has won `consecutive` frames in a row.
    Anything else stays unknown:

      * a frame with no usable face (reason ``no_face`` / ``too_small``) is a BREAK.
        One break frame is tolerated as grace; `break_grace` consecutive breaks clear
        the accumulated state entirely, so a face that left and came back has to earn
        its identity again.
      * an unknown frame (no candidate, too far, ambiguous) also breaks the run, but
        does not clear history on its own.
      * switching to a different name always restarts the run at 1.

    Fully deterministic: the caller supplies the frame counter, there is no clock, no
    thread and no I/O inside this class.
    """

    BREAK_REASONS = ("no_face", "too_small")

    def __init__(self, consecutive=3, break_grace=2, window=None):
        consecutive = max(1, int(consecutive))
        break_grace = max(1, int(break_grace))
        # N (the reported window) is defined relative to the consecutive requirement
        self.consecutive = consecutive
        self.break_grace = break_grace
        self.window = int(window) if window else consecutive * 2
        self._history = deque(maxlen=self.window)
        self._run_name = None
        self._run_count = 0
        self._break_count = 0

    def reset(self):
        """Forget everything — used when the stream restarts."""
        self._history.clear()
        self._run_name = None
        self._run_count = 0
        self._break_count = 0

    def update(self, frame_index, name, reason=None):
        """Feed one frame's raw result; get the smoothed one back.

        `frame_index` is the caller's frame counter (injected so the class stays
        clock-free). Returns a dict with `identified`, `name`, `reason`, `run` and
        `run_needed`.
        """
        reason = reason or ""
        is_break = reason in self.BREAK_REASONS

        if is_break:
            # a missing face interrupts the run; enough consecutive misses clear it
            self._run_name = None
            self._run_count = 0
            self._break_count += 1
            self._history.append(None)
            if self._break_count >= self.break_grace:
                self._history.clear()
            return {
                "identified": False, "name": NO_MATCH_NAME, "reason": reason,
                "run": 0, "run_needed": self.consecutive,
                "frame": frame_index,
            }

        # any real frame ends a pending break streak
        self._break_count = 0

        if name is None:
            self._run_name = None
            self._run_count = 0
            self._history.append(None)
            return {
                "identified": False, "name": NO_MATCH_NAME, "reason": reason or "unknown",
                "run": 0, "run_needed": self.consecutive,
                "frame": frame_index,
            }

        if name == self._run_name:
            self._run_count += 1
        else:
            self._run_name = name
            self._run_count = 1
        self._history.append(name)

        stable = self._run_count >= self.consecutive
        # the run must also still be inside the reported window
        recent = list(self._history)[-self.consecutive:]
        in_window = len(recent) == self.consecutive and recent == [name] * self.consecutive
        identified = bool(stable and in_window)

        return {
            "identified": identified,
            "name": name if identified else NO_MATCH_NAME,
            "reason": "matched" if identified else "confirming",
            "run": self._run_count,
            "run_needed": self.consecutive,
            "frame": frame_index,
        }
