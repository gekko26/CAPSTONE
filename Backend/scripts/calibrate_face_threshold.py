"""Suggest FACE_MATCH_THRESHOLD / FACE_MATCH_MARGIN from the enrolled gallery.

    python scripts/calibrate_face_threshold.py --help

Reads the enrolled photos under data/faces/registered/, embeds them with the SAME
model, detector backend and distance metric the live app uses (imported from
models.train.face_recognition — never duplicated here), then reports the spread of
same-person and different-person distances and suggests two thresholds.

PRIVACY: this tool never prints a name or a path. People are referred to only as
"person 0", "person 1", ... Nothing is written to disk.

Exit codes:  0 = suggestions printed   2 = not enough data to suggest anything
"""

import argparse
import os
import statistics
import sys

MIN_PEOPLE = 2
# DeepFace's documented default threshold for Facenet + cosine. A suggestion may
# never come out MORE PERMISSIVE than this: the gate should only ever get stricter
# than the library's own default.
LIBRARY_DEFAULT_THRESHOLD = 0.40
MIN_PHOTOS_PER_PERSON = 2


def summarise(values):
    """min / median / p95 / max of a distance list, rounded for display."""
    if not values:
        return None
    ordered = sorted(values)
    return {
        "min": round(ordered[0], 4),
        "median": round(statistics.median(ordered), 4),
        "p95": round(_percentile(ordered, 0.95), 4),
        "max": round(ordered[-1], 4),
    }


def _percentile(ordered, q):
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def suggest_threshold(same_values, diff_values):
    """Suggest a threshold and a margin from the two distance populations.

    The threshold sits between the worst same-person distance and the best
    different-person distance. When those populations OVERLAP no honest threshold
    exists, so we pick the strictly fail-safe side: the threshold that rejects the
    worst genuine match, and a zero margin.
    """
    same = summarise(same_values)
    diff = summarise(diff_values)
    info = {"same": same, "different": diff, "overlap": False}
    if not same or not diff:
        return None, info

    worst_same = same["max"]
    best_diff = diff["min"]

    if best_diff > worst_same:
        # clean separation — put the threshold in the middle of the gap
        threshold = round(min((worst_same + best_diff) / 2.0,
                             LIBRARY_DEFAULT_THRESHOLD), 3)
        margin = round(max(0.02, (best_diff - worst_same) / 4.0), 3)
        info["overlap"] = False
        return (threshold, margin), info

    # populations overlap: a real threshold cannot separate them. Fail safe —
    # reject anything as far as the worst genuine match, and require no margin.
    threshold = round(min(worst_same, 0.40), 3)
    margin = 0.0
    info["overlap"] = True
    return (threshold, margin), info


def not_enough(reason):
    print(f"[!] Not enough data to calibrate: {reason}")
    print("    Need at least "
          f"{MIN_PEOPLE} enrolled people with at least {MIN_PHOTOS_PER_PERSON} photos each.")
    print("    No threshold suggested.")
    return 2


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="calibrate_face_threshold.py",
        description="Suggest FACE_MATCH_THRESHOLD and FACE_MATCH_MARGIN from the "
                    "enrolled face gallery. Prints aggregate numbers and anonymised "
                    "person indices only — never names or paths. Writes nothing.")
    p.add_argument("--registered-dir", default=None,
                   help="gallery directory (default: the app's registered folder)")
    args = p.parse_args(argv)

    # Imported here, not at module scope: importing face_recognition builds the
    # DeepFace models, so `--help` and `--not-enough-data` must never pay for that.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from models.train import face_recognition as _fr
    MODEL_NAME = _fr.MODEL_NAME
    DISTANCE_METRIC = _fr.DISTANCE_METRIC
    DETECTOR_BACKEND = _fr.DETECTOR_BACKEND

    root = args.registered_dir or _fr.REGISTERED
    if not os.path.isdir(root):
        return not_enough(f"the registered folder was not found")

    people = []          # list of (index, [image paths]) — index, never the name
    for entry in sorted(os.listdir(root)):
        person_dir = os.path.join(root, entry)
        if not os.path.isdir(person_dir):
            continue
        imgs = [os.path.join(person_dir, f)
                for f in sorted(os.listdir(person_dir))
                if f.lower().endswith((".jpg", ".jpeg", ".png"))]
        if imgs:
            people.append((len(people), imgs))

    usable = [(i, imgs) for i, imgs in people if len(imgs) >= MIN_PHOTOS_PER_PERSON]
    if len(people) < MIN_PEOPLE:
        return not_enough(f"only {len(people)} person(s) enrolled, need {MIN_PEOPLE}")
    if len(usable) < MIN_PEOPLE:
        return not_enough(
            f"only {len(usable)} person(s) have >= {MIN_PHOTOS_PER_PERSON} photos")

    # imported lazily so --help works even if the ML stack is unavailable
    from deepface import DeepFace

    embeddings = {}
    skipped = 0
    for idx, imgs in usable:
        vectors = []
        for path in imgs:
            try:
                rep = DeepFace.represent(
                    img_path=path, model_name=MODEL_NAME,
                    detector_backend=DETECTOR_BACKEND,
                    distance_metric=DISTANCE_METRIC,
                    enforce_detection=True, silent=True)
            except Exception:
                skipped += 1        # no face found in this photo — counted, never named
                continue
            if rep:
                vectors.append(rep[0]["embedding"])
        if vectors:
            embeddings[idx] = vectors

    if len(embeddings) < MIN_PEOPLE:
        return not_enough("fewer than 2 people yielded a usable embedding")

    def distance(a, b):
        import numpy as np
        a = np.asarray(a)
        b = np.asarray(b)
        if DISTANCE_METRIC == "cosine":
            na, nb = np.linalg.norm(a), np.linalg.norm(b)
            if na == 0 or nb == 0:
                return 1.0
            return float(1.0 - np.dot(a, b) / (na * nb))
        return float(np.linalg.norm(a - b))

    same, diff = [], []
    keys = sorted(embeddings)
    for i in keys:
        for va in embeddings[i]:
            for vb in embeddings[i]:
                if va is not vb:
                    same.append(distance(va, vb))
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            for va in embeddings[keys[i]]:
                for vb in embeddings[keys[j]]:
                    diff.append(distance(va, vb))

    if not diff:
        return not_enough("no different-person pairs could be compared")

    print(f"[i] people used          : {len(keys)} (person 0 .. person {keys[-1]})")
    print(f"[i] photos skipped       : {skipped} (no face detected)")
    print(f"[i] model / metric       : {MODEL_NAME} / {DISTANCE_METRIC}")
    print(f"[i] detector backend     : {DETECTOR_BACKEND}")

    same_s = summarise(same)
    diff_s = {"min": round(min(diff), 4),
              "p5": round(_percentile(sorted(diff), 0.05), 4),
              "median": round(statistics.median(diff), 4)}
    print(f"[i] same-person distances: min={same_s['min']} median={same_s['median']} "
          f"p95={same_s['p95']} max={same_s['max']}")
    print(f"[i] diff-person distances: min={diff_s['min']} p5={diff_s['p5']} "
          f"median={diff_s['median']}")

    result, info = suggest_threshold(same, diff)
    if result is None:
        return not_enough("distance samples were empty")
    threshold, margin = result

    if info["overlap"]:
        print("[!] OVERLAP: the same-person and different-person distance ranges "
              "intersect, so no clean threshold exists.")
        print("[!] Suggesting the fail-safe option: reject anything as far as the "
              "worst genuine match.")
    else:
        print("[i] Clean separation — the threshold is placed midway between the "
              "worst genuine match and the best impostor.")
    print(f"[i] worst genuine match   : {same_s['max']}")
    print(f"[i] closest impostor      : {diff_s['min']}")

    print()
    print(f"Suggested FACE_MATCH_THRESHOLD = {threshold}")
    print(f"Suggested FACE_MATCH_MARGIN    = {margin}")
    print()
    print("Add these to your environment (do NOT write a .env file from this tool):")
    print(f"export FACE_MATCH_THRESHOLD={threshold}")
    print(f"export FACE_MATCH_MARGIN={margin}")
    print()
    print("Review the result with a few real test shots before relying on it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
