"""face_uploads.py — pure helpers for face-photo uploads.

Deliberately free of FastAPI, SQLAlchemy and cv2 so the naming and containment rules
can be reasoned about (and tested) on their own. The route layer in
routes/training.py does the I/O.

WHY A SEPARATE NAMESPACE
------------------------
Every image already in data/faces/<class>/ is owned by a TrainingData row through the
name "<prefix>_<row.id>.jpg" (routes/training.py:247). Uploads have no row, so they
must be distinguishable BY NAME or row cleanup can never tell them apart.

The upload name deliberately embeds a literal "up_s" infix:

    <class>_up_s<subject_id>_<digest12>.jpg      e.g. sober_up_s5_1a2b3c4d5e6f.jpg
    <prefix>_<row_id>.jpg                        e.g. sober_42.jpg

Neither can match the other: after "sober_" the capture form requires only digits,
while the upload form requires "up_s". This module asserts that separation in
tests/test_face_uploads.py with realistic names from both families, so a future
change to either pattern cannot silently start colliding.
"""

import hashlib
import os
import re

# ── Upload limits ────────────────────────────────────────────────────────────
UPLOAD_CLASSES = ("sober", "fatigue", "impaired")
MAX_FILES_PER_REQUEST = 20
MAX_FILE_BYTES = 5 * 1024 * 1024          # 5 MB
MAX_SIDE_PX = 1280                         # downscale the LONGEST side, never upscale
JPEG_QUALITY = 92

# The "_up_s" marker that separates this namespace from row-owned files.
UPLOAD_INFIX = "_up_s"      # then the digits of subject_id, then "_" + digest

# Exactly the form produced by upload_filename(). Anchored on both ends.
UPLOAD_PATTERN = re.compile(
    r"^(?P<face_class>sober|fatigue|impaired)"
    r"_up_s(?P<subject_id>\d+)"
    r"_(?P<digest>[0-9a-f]{12})\.jpg$"
)

# The capture-owned form, expressed as a regex ONLY so the collision test can prove
# the two namespaces are disjoint. Nothing in the shipping code uses this.
CAPTURE_PATTERN = re.compile(
    r"^(?:sober|fatigue|drowsy|yawning|impaired|impaired_perfume)_(?P<row_id>\d+)\.jpg$"
)


def upload_digest(data: bytes) -> str:
    """First 12 hex chars of the SHA-1 of the ORIGINAL uploaded bytes."""
    return hashlib.sha1(data).hexdigest()[:12]


def upload_filename(face_class, subject_id, digest12) -> str:
    """Build the stored name. Callers must pass a valid class and a real digest."""
    return f"{face_class}{UPLOAD_INFIX}{int(subject_id)}_{digest12}.jpg"


def parse_upload_filename(name):
    """Return (face_class, subject_id, digest) or None when the name is not ours.

    None is returned for anything that is not exactly our form, which includes every
    capture-owned name, every name in data/faces/registered/, and anything containing
    a path separator.
    """
    if not isinstance(name, str):
        return None
    # Reject separators outright: a name is a bare basename, never a path.
    if "/" in name or "\\" in name or os.sep in name:
        return None
    m = UPLOAD_PATTERN.match(name)
    if not m:
        return None
    return m.group("face_class"), int(m.group("subject_id")), m.group("digest")


def is_upload_name(name) -> bool:
    return parse_upload_filename(name) is not None


def is_capture_name(name) -> bool:
    """True only for the row-owned form. Used to keep the two namespaces apart."""
    if not isinstance(name, str):
        return False
    if "/" in name or "\\" in name or os.sep in name:
        return False
    return bool(CAPTURE_PATTERN.match(name))


def faces_root(base_dir, face_class) -> str:
    """The realpath'd data/faces/<face_class> directory for a base directory."""
    return os.path.realpath(os.path.join(base_dir, "data", "faces", face_class))


def safe_upload_path(base_dir, face_class, filename):
    """Return the absolute path to delete/write, or None.

    Three independent gates, all of which must pass:

      1. the class is one of UPLOAD_CLASSES;
      2. the filename matches UPLOAD_PATTERN **and names that same class**;
      3. the resolved path really is inside <base_dir>/data/faces/<face_class>.

    Gate 3 is what makes "..", an absolute path, an encoded separator, a symlink out of
    the folder, or a name from another class harmless: the result is None and the
    caller does nothing. A path equal to the root itself is also rejected, so this can
    never be used to remove a directory.
    """
    if face_class not in UPLOAD_CLASSES:
        return None
    parsed = parse_upload_filename(filename)
    if parsed is None:
        return None
    parsed_class, _subject_id, _digest = parsed
    if parsed_class != face_class:
        return None

    root = faces_root(base_dir, face_class)
    candidate = os.path.realpath(os.path.join(root, filename))
    try:
        if os.path.commonpath([root, candidate]) != root:
            return None
    except ValueError:
        # Different drives / malformed paths on some platforms.
        return None
    if candidate == root:
        return None
    return candidate