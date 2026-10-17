// FaceUploadPanel — upload face photos for MobileNet training.
//
// Supplement to the gate capture flow, not a replacement: photos taken at the gate are
// what the model will actually meet. Use uploads to add coverage, not to substitute for
// real captures.
//
// The shared token never reaches this component: the backend holds it and adds the
// header itself. Nothing here logs or sends a name anywhere except the subject id the
// operator picks.
import { useEffect, useRef, useState } from "react";
import { API_BASE } from "../api";

const MAX_FILES = 20;
const MAX_MB = 5;

const CLASSES = [
  {
    key: "sober",
    label: "Sober",
    hint: "eyes open, mouth closed, neutral expression",
  },
  {
    key: "fatigue",
    label: "Fatigue",
    hint: "eyes closed or yawning — posing is fine",
  },
  {
    key: "impaired",
    label: "Impaired",
    hint: "ONLY people with a measured BAC of 0.05 or above — never acted",
  },
];

const CLASS_HINT =
  "Photos from the gate camera train a model that works at the gate; phone or studio " +
  "photos differ in angle, light and resolution, use uploads to supplement.";

const REASON_TEXT = {
  too_large: "larger than 5 MB",
  not_an_image: "not a readable image",
  no_face: "no face detected",
  too_small: "face too small in frame",
  write_failed: "could not be stored",
  already_uploaded: "already uploaded",
};

const STATUS_COLOR = {
  accepted: "var(--pass)",
  duplicate: "var(--near)",
  rejected: "var(--over)",
};

export function FaceUploadPanel({ onChanged }) {
  const [faceClass, setFaceClass] = useState("sober");
  const [subjects, setSubjects] = useState([]);
  const [subjectId, setSubjectId] = useState("");
  const [bac, setBac] = useState("");
  const [confirmReal, setConfirmReal] = useState(false);
  const [picked, setPicked] = useState([]);       // { key, file, url }
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState(null);
  const [err, setErr] = useState(null);
  const [listed, setListed] = useState(null);
  const [listOpen, setListOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(null);
  const inputRef = useRef(null);

  // Object URLs must be revoked or the page leaks blobs.
  const urlsRef = useRef([]);
  const makeUrl = (file) => {
    const u = URL.createObjectURL(file);
    urlsRef.current.push(u);
    return u;
  };
  const releaseUrls = () => {
    urlsRef.current.forEach((u) => URL.revokeObjectURL(u));
    urlsRef.current = [];
  };
  useEffect(() => () => releaseUrls(), []);

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/recognition/subjects`)
      .then((r) => (r.ok ? r.json() : []))
      .then((list) => { if (!cancelled) setSubjects(list || []); })
      .catch(() => { if (!cancelled) setSubjects([]); });
    return () => { cancelled = true; };
  }, []);

  const refreshListed = () =>
    fetch(`${API_BASE}/training/uploaded-faces`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setListed)
      .catch(() => setListed(null));

  const pickedForClass = (() => {
    const out = [];
    for (const entry of picked) {
      const over = entry.file.size > MAX_MB * 1024 * 1024;
      out.push({ ...entry, over, name: entry.file.name.slice(0, 80) });
    }
    return out;
  })();

  const impaired = faceClass === "impaired";
  const bacNum = parseFloat(bac);
  const bacOk = !impaired || (bac !== "" && !Number.isNaN(bacNum) && bacNum >= 0.05);
  const canSend =
    picked.length > 0 &&
    picked.length <= MAX_FILES &&
    pickedForClass.every((p) => !p.over) &&
    subjectId !== "" &&
    bacOk &&
    (!impaired || confirmReal) &&
    !sending;

  function onPick(e) {
    const files = Array.from(e.target.files || []);
    const room = MAX_FILES - picked.length;
    setPicked((prev) =>
      [...prev, ...files.slice(0, Math.max(0, room)).map((file) => ({
        key: `${file.name}:${file.size}:${file.lastModified}`,
        file,
        url: makeUrl(file),
      }))]
    );
    if (files.length > room) {
      setErr(`Only ${MAX_FILES} photos at a time.`);
    } else {
      setErr(null);
    }
  }

  function removeAt(i) {
    const target = picked[i];
    if (target) URL.revokeObjectURL(target.url);
    urlsRef.current = urlsRef.current.filter((u) => u !== target?.url);
    setPicked((prev) => prev.filter((_, idx) => idx !== i));
  }

  function clearPicked() {
    releaseUrls();
    setPicked([]);
    setResult(null);
    if (inputRef.current) inputRef.current.value = "";
  }

  async function send() {
    if (!canSend) return;
    setSending(true);
    setErr(null);
    setResult(null);
    const fd = new FormData();
    fd.append("face_class", faceClass);
    fd.append("subject_id", subjectId);
    if (impaired) {
      fd.append("bac", String(bacNum));
      fd.append("confirm_real", "true");
    }
    picked.forEach((p) => fd.append("files", p.file, p.file.name));
    try {
      const r = await fetch(`${API_BASE}/training/upload-faces`, {
        method: "POST",
        body: fd,
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        setErr(j.detail || `Upload refused (HTTP ${r.status}).`);
      } else {
        setResult(j);
        clearPicked();
        refreshListed();
        // the collection checklist and coverage panel live on this page
        if (onChanged) onChanged();
      }
    } catch (e) {
      setErr(`Could not reach the backend: ${e.message}`);
    } finally {
      setSending(false);
    }
  }

  async function removeUploaded(file) {
    setErr(null);
    try {
      const r = await fetch(
        `${API_BASE}/training/uploaded-faces/${file.face_class}/${encodeURIComponent(file.name)}`,
        { method: "DELETE" }
      );
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        setErr(j.detail || `Delete refused (HTTP ${r.status}).`);
      } else {
        setConfirmDelete(null);
        refreshListed();
        if (onChanged) onChanged();
      }
    } catch (e) {
      setErr(`Could not reach the backend: ${e.message}`);
    }
  }

  const current = CLASSES.find((c) => c.key === faceClass) || CLASSES[0];
  const color = (k) => (STATUS_COLOR[k] || "var(--text-muted)");

  return (
    <div className="p-2 rounded-lg border text-xs space-y-2"
         style={{ background: "var(--bg-active)", borderColor: "var(--border-subtle)" }}>
      <div className="flex items-center gap-2">
        <span className="text-[11px] uppercase font-medium"
              style={{ color: "var(--text-muted)" }}>Upload photos</span>
        <span className="text-[10px] px-1.5 py-0.5 rounded"
              style={{ background: "var(--bg-card)", color: "var(--text-muted)" }}>
          supplement, not a replacement
        </span>
      </div>

      {/* class + subject */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        <label className="block">
          <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>Class</span>
          <select
            value={faceClass}
            onChange={(e) => { setFaceClass(e.target.value); setResult(null); }}
            className="w-full mt-0.5 px-1.5 py-1 rounded-[4px] text-xs"
            style={{ background: "var(--bg-card)", color: "var(--text-primary)",
                      border: "1px solid var(--border-subtle)" }}
          >
            {CLASSES.map((c) => (
              <option key={c.key} value={c.key}>{c.label}</option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>Person</span>
          <select
            value={subjectId}
            onChange={(e) => { setSubjectId(e.target.value); setResult(null); }}
            className="w-full mt-0.5 px-1.5 py-1 rounded-[4px] text-xs"
            style={{ background: "var(--bg-card)", color: "var(--text-primary)",
                      border: "1px solid var(--border-subtle)" }}
          >
            <option value="">select a person…</option>
            {subjects.map((s) => (
              <option key={s.id} value={String(s.id)}>{s.name}</option>
            ))}
          </select>
        </label>
      </div>

      <div className="text-[10px]" style={{ color: "var(--text-muted)" }}>
        {current.label}: {current.hint}
      </div>
      <div className="text-[10px] leading-relaxed" style={{ color: "var(--text-muted)" }}>
        {CLASS_HINT}
      </div>

      {/* impaired gate */}
      {impaired && (
        <div className="p-2 rounded-[4px] space-y-1.5"
             style={{ background: "var(--bg-card)", border: "1px solid var(--over)" }}>
          <div className="text-[10px]" style={{ color: "var(--over)" }}>
            Impaired photos are only accepted with a measured BAC of 0.05 or above.
          </div>
          <label className="flex items-center gap-2">
            <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>
              Measured BAC
            </span>
            <input
              type="number" step="0.01" min="0" max="0.40" value={bac}
              onChange={(e) => { setBac(e.target.value); setResult(null); }}
              className="w-20 px-1.5 py-0.5 rounded-[4px] text-xs font-mono"
              style={{ background: "var(--bg-active)", color: "var(--text-primary)",
                        border: "1px solid var(--border-subtle)" }}
            />
          </label>
          <label className="flex items-start gap-2">
            <input type="checkbox" checked={confirmReal}
                   onChange={(e) => { setConfirmReal(e.target.checked); setResult(null); }}
                   style={{ marginTop: 1 }} />
            <span className="text-[10px] leading-relaxed"
                  style={{ color: confirmReal ? "var(--text-secondary)" : "var(--text-muted)" }}>
              these photos were taken while this person's BAC was measured at or above 0.05;
              they are not acted
            </span>
          </label>
          {!bacOk && bac !== "" && (
            <div className="text-[10px]" style={{ color: "var(--over)" }}>
              BAC must be 0.05 or above.
            </div>
          )}
        </div>
      )}

      {/* picker */}
      <div className="space-y-1.5">
        <input ref={inputRef} type="file" multiple accept="image/jpeg,image/png"
               onChange={onPick}
               className="text-[10px]"
               style={{ color: "var(--text-muted)" }} />
        <div className="text-[10px]" style={{ color: "var(--text-muted)" }}>
          {picked.length} selected — max {MAX_FILES} photos, {MAX_MB} MB each.
        </div>

        {pickedForClass.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {pickedForClass.map((p, i) => (
              <div key={p.key}
                   className="relative rounded-[4px] overflow-hidden"
                   style={{ width: 56, height: 56,
                            border: `1px solid ${p.over ? "var(--over)" : "var(--border-subtle)"}` }}>
                <img src={p.url} alt="" style={{ width: "100%", height: "100%",
                                                  objectFit: "cover" }} />
                <button
                  onClick={() => removeAt(i)}
                  title="remove"
                  className="absolute top-0 right-0 w-4 h-4 text-[10px] leading-none"
                  style={{ background: "rgba(0,0,0,0.65)", color: "#fff", borderRadius: "0 0 0 4px" }}
                >×</button>
                {p.over && (
                  <div className="absolute bottom-0 left-0 right-0 text-[7px] text-center"
                       style={{ background: "var(--over)", color: "#fff" }}>too big</div>
                )}
              </div>
            ))}
          </div>
        )}

        <div className="flex items-center gap-2">
          <button
            onClick={send}
            disabled={!canSend}
            className="px-2 py-1 rounded-[4px] border text-[11px] font-medium disabled:opacity-50 disabled:cursor-not-allowed"
            style={{ background: "var(--bg-card)", color: "var(--text-primary)",
                      borderColor: "var(--border-subtle)" }}
          >
            {sending ? "uploading…" : `Upload ${picked.length || ""} photo${picked.length === 1 ? "" : "s"}`}
          </button>
          {picked.length > 0 && (
            <button onClick={clearPicked}
                    className="px-2 py-1 rounded-[4px] text-[10px]"
                    style={{ color: "var(--text-muted)" }}>
              clear
            </button>
          )}
        </div>
      </div>

      {err && (
        <div className="text-[10px]" style={{ color: "var(--over)" }}>{err}</div>
      )}

      {/* per-file result */}
      {result && (
        <div className="space-y-1">
          <div className="text-[10px]" style={{ color: "var(--text-secondary)" }}>
            {result.accepted} accepted · {result.duplicates} duplicate
            {result.duplicates === 1 ? "" : "s"} · {result.rejected} rejected
          </div>
          <div className="space-y-0.5">
            {result.files.map((f, i) => (
              <div key={`${f.name}-${i}`} className="flex items-center gap-2 text-[10px]">
                <span className="font-mono truncate" style={{ maxWidth: 180,
                                                             color: "var(--text-muted)" }}>
                  {f.name}
                </span>
                <span className="font-medium" style={{ color: color(f.status) }}>
                  {f.status}
                </span>
                {f.reason && (
                  <span style={{ color: "var(--text-muted)" }}>
                    {REASON_TEXT[f.reason] || f.reason}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* uploaded list */}
      <div>
        <button onClick={() => { setListOpen((v) => !v); if (!listOpen) refreshListed(); }}
                className="text-[10px] underline"
                style={{ color: "var(--text-muted)" }}>
          {listOpen ? "hide uploaded photos" : "uploaded photos"}
          {listed ? ` (${listed.total})` : ""}
        </button>
        {listOpen && (
          <div className="mt-1.5 space-y-1">
            {!listed && (
              <div className="text-[10px]" style={{ color: "var(--text-muted)" }}>loading…</div>
            )}
            {listed && listed.files.length === 0 && (
              <div className="text-[10px]" style={{ color: "var(--text-muted)" }}>
                nothing uploaded yet
              </div>
            )}
            {listed && listed.files.map((f) => (
              <div key={f.name} className="flex items-center gap-2 text-[10px]">
                <span style={{ color: "var(--text-muted)" }}>{f.face_class}</span>
                <span className="font-mono truncate" style={{ maxWidth: 220,
                                                                 color: "var(--text-secondary)" }}>
                  {f.name}
                </span>
                <span style={{ color: "var(--text-muted)" }}>
                  {(f.bytes / 1024).toFixed(0)} KB
                </span>
                {confirmDelete === f.name ? (
                  <span className="flex items-center gap-1">
                    <button onClick={() => removeUploaded(f)}
                            className="underline"
                            style={{ color: "var(--over)" }}>confirm delete</button>
                    <button onClick={() => setConfirmDelete(null)}
                            style={{ color: "var(--text-muted)" }}>cancel</button>
                  </span>
                ) : (
                  <button onClick={() => setConfirmDelete(f.name)}
                          style={{ color: "var(--text-muted)" }}>delete</button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}