// People.jsx — ALCOGATE terminal: registered personnel (ID only, no Card Number)
import { useState, useEffect, useMemo } from "react";
import { API_BASE } from "../api";

const MOCK = [
  { id: 1, name: "Juan Dela Cruz", face_id: "2026-0001", group: "Staff", created_at: "2026-05-12T02:00:00", status: "Active", auth: "Authorized", accesses: [{ date: "May 20, 2026 10:38 AM", gate: "GATE 01", res: "Passed" }, { date: "May 19, 2026 08:12 AM", gate: "GATE 01", res: "Passed" }] },
  { id: 2, name: "Jane Dela Cruz", face_id: "2026-0002", group: "Staff", created_at: "2026-05-11T02:00:00", status: "Active", auth: "Authorized", face_id: "2026-0002" },
  { id: 3, name: "Mark Solis", face_id: "2026-0003", group: "Visitor", created_at: "2026-05-10T02:00:00", status: "Active", auth: "Authorized" },
  { id: 4, name: "Ken Alvarez", face_id: "2026-0004", group: "Staff", created_at: "2026-05-09T02:00:00", status: "Active", auth: "Authorized" },
];

export default function People() {
  const [q, setQ] = useState("");
  const [statusF, setStatusF] = useState("All Status");
  const [groupF, setGroupF] = useState("All Groups");
  const [subjects, setSubjects] = useState([]);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [enrolling, setEnrolling] = useState(false);
  const [form, setForm] = useState({ name: "", id_number: "", group: "Staff" });
  const [files, setFiles] = useState(null);
  const [previews, setPreviews] = useState([]);
  const [toast, setToast] = useState("");
  const [showEdit, setShowEdit] = useState(false);
  const [editForm, setEditForm] = useState({ name: "", id_number: "", group: "Staff" });
  const [showDelete, setShowDelete] = useState(null);

  const load = async () => {
    try {
      const r = await fetch(`${API_BASE}/recognition/subjects`);
      if (r.ok) {
        const j = await r.json();
          const mapped = j.map((s) => ({
          id: s.id,
          name: s.name,
          face_id: s.face_id,
          id_number: s.id_number || s.face_id,
          group: s.group || "Staff",
          avatar_path: s.avatar_path,
          created_at: s.created_at,
          status: "Active",
          auth: "Authorized",
        }));
        const use = mapped.length ? mapped : MOCK;
        setSubjects(use);
        setSelected((prev) => use.find((x) => x.id === prev?.id) || use[0]);
      } else {
        setSubjects(MOCK);
        setSelected(MOCK[0]);
      }
    } catch {
      setSubjects(MOCK);
      setSelected(MOCK[0]);
    }
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const filtered = useMemo(() => {
    let d = subjects;
    if (q.trim()) {
      const s = q.toLowerCase();
      d = d.filter((p) => `${p.name} ${p.id_number} ${p.face_id}`.toLowerCase().includes(s));
    }
    if (statusF !== "All Status") d = d.filter((p) => p.status === statusF);
    if (groupF !== "All Groups") d = d.filter((p) => p.group === groupF);
    return d;
  }, [subjects, q, statusF, groupF]);

  useEffect(() => {
    if (filtered.length && !filtered.find((x) => x.id === selected?.id)) setSelected(filtered[0]);
  }, [filtered, selected]);

  const onFiles = (fl) => {
    setFiles(fl);
    if (!fl) { setPreviews([]); return; }
    const urls = Array.from(fl).map((f) => URL.createObjectURL(f));
    setPreviews((prev) => { prev.forEach((u) => URL.revokeObjectURL(u)); return urls; });
  };

  const handleEnroll = async () => {
    if (!form.name.trim()) { setToast("Name required"); setTimeout(() => setToast(""), 2500); return; }
    if (!form.id_number.trim()) { setToast("ID Number required"); setTimeout(() => setToast(""), 2500); return; }
    if (!files?.length) { setToast("At least 1 photo required"); setTimeout(() => setToast(""), 2500); return; }
    setEnrolling(true);
    try {
      const fd = new FormData();
      fd.append("name", form.name.trim());
      fd.append("id_number", form.id_number.trim());
      fd.append("group", form.group);
      for (const f of files) fd.append("files", f);
      const r = await fetch(`${API_BASE}/recognition/enroll`, { method: "POST", body: fd });
      const j = await r.json();
      if (j.error) throw new Error(j.error);
      setToast(`${j.message} — ${j.images} photos`);
      setTimeout(() => setToast(""), 2500);
      setShowAdd(false);
      setForm({ name: "", id_number: "", group: "Staff" });
      setFiles(null);
      previews.forEach((u) => URL.revokeObjectURL(u));
      setPreviews([]);
      load();
    } catch (e) {
      setToast(e.message || "Enroll failed");
      setTimeout(() => setToast(""), 2500);
    }
    setEnrolling(false);
  };

  const openEdit = () => {
    setEditForm({ name: active.name, id_number: active.id_number || active.face_id, group: active.group });
    setShowEdit(true);
  };
  const handleEdit = async () => {
    if (!editForm.name.trim() || !editForm.id_number.trim()) { setToast("Name + ID required"); setTimeout(() => setToast(""), 2500); return; }
    try {
      const r = await fetch(`${API_BASE}/recognition/subjects/${active.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: editForm.name.trim(), id_number: editForm.id_number.trim(), group: editForm.group }),
      });
      const j = await r.json();
      if (j.error) throw new Error(j.error);
      setToast(j.message);
      setTimeout(() => setToast(""), 2500);
      setShowEdit(false);
      load();
    } catch (e) { setToast(e.message || "Update failed"); setTimeout(() => setToast(""), 2500); }
  };
  const handleDelete = async () => {
    if (!showDelete) return;
    try {
      const r = await fetch(`${API_BASE}/recognition/subjects/${showDelete.id}`, { method: "DELETE" });
      const j = await r.json();
      if (j.error) throw new Error(j.error);
      setToast(j.message);
      setTimeout(() => setToast(""), 2500);
      setShowDelete(null);
      load();
    } catch (e) { setToast(e.message || "Delete failed"); setTimeout(() => setToast(""), 2500); }
  };

  const active = selected || filtered[0] || MOCK[0];

  return (
    <div className="p-2 flex flex-col gap-2 max-w-[1200px] mx-auto">
      <div className="flex items-center gap-2">
        <h1 className="text-[11px] font-bold tracking-[0.08em]" style={{ color: "var(--text-primary)" }}>PEOPLE</h1>
        <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>Registered personnel</span>
        <span className="ml-auto text-[10px] font-mono" style={{ color: toast.includes("enrolled") ? "var(--pass)" : "var(--near)" }}>{toast}</span>
      </div>

      <div className="flex gap-1.5 flex-wrap">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by name, ID number…" className="flex-1 min-w-[180px] text-[11px] px-2.5 py-1.5 rounded-[4px] border" style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)", color: "var(--text-primary)" }} />
        <select value={statusF} onChange={(e) => setStatusF(e.target.value)} className="text-[11px] px-2 py-1.5 rounded-[4px] border" style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)", color: "var(--text-secondary)" }}>
          <option>All Status</option>
          <option>Active</option>
          <option>Inactive</option>
        </select>
        <select value={groupF} onChange={(e) => setGroupF(e.target.value)} className="text-[11px] px-2 py-1.5 rounded-[4px] border" style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)", color: "var(--text-secondary)" }}>
          <option>All Groups</option>
          <option>Staff</option>
          <option>Visitor</option>
        </select>
        <button onClick={() => setShowAdd(true)} className="text-[11px] px-3 py-1.5 rounded-[4px] font-medium" style={{ background: "var(--accent)", color: "#fff" }}>+ Add Person</button>
      </div>

      {showAdd && (
        <div className="fixed inset-0 z-40 flex items-center justify-center p-4" style={{ background: "rgba(0,0,0,0.55)" }} onClick={(e) => e.target === e.currentTarget && setShowAdd(false)}>
          <div className="w-full max-w-md rounded-[6px] p-4 flex flex-col gap-3" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-bold tracking-wide" style={{ color: "var(--text-primary)" }}>Add Person</span>
              <button onClick={() => setShowAdd(false)} className="text-[11px] px-2 py-1" style={{ color: "var(--text-muted)" }}>✕</button>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div className="flex flex-col gap-1">
                <label className="text-[10px]" style={{ color: "var(--text-muted)" }}>Full Name *</label>
                <input value={form.name} onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))} placeholder="Juan Dela Cruz" className="text-[11px] px-2.5 py-1.5 rounded-[4px] border" style={{ background: "var(--bg-card-alt)", borderColor: "var(--border-subtle)", color: "var(--text-primary)" }} />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-[10px]" style={{ color: "var(--text-muted)" }}>ID Number *</label>
                <input value={form.id_number} onChange={(e) => setForm((p) => ({ ...p, id_number: e.target.value }))} placeholder="2026-0001" className="text-[11px] px-2.5 py-1.5 rounded-[4px] border font-mono" style={{ background: "var(--bg-card-alt)", borderColor: "var(--border-subtle)", color: "var(--text-primary)" }} />
              </div>
            </div>

            <div className="flex flex-col gap-1">
              <label className="text-[10px]" style={{ color: "var(--text-muted)" }}>Group / Department</label>
              <select value={form.group} onChange={(e) => setForm((p) => ({ ...p, group: e.target.value }))} className="text-[11px] px-2.5 py-1.5 rounded-[4px] border" style={{ background: "var(--bg-card-alt)", borderColor: "var(--border-subtle)", color: "var(--text-primary)" }}>
                <option>Staff</option>
                <option>Visitor</option>
                <option>Security</option>
                <option>Admin</option>
              </select>
            </div>

            <div className="flex flex-col gap-1.5">
              <label className="text-[10px]" style={{ color: "var(--text-muted)" }}>Photos * (1–5, face visible)</label>
              <label className="rounded-[4px] border-dashed border px-3 py-3 text-center cursor-pointer text-[11px] flex flex-col items-center gap-1" style={{ borderColor: "var(--border)", color: "var(--text-muted)", background: "var(--bg-card-alt)" }}>
                <span>Click to select photos</span>
                <span className="text-[10px]">{files ? `${files.length} selected` : "No files"}</span>
                <input type="file" multiple accept="image/*" className="hidden" onChange={(e) => onFiles(e.target.files)} />
              </label>
              {previews.length > 0 && (
                <div className="grid grid-cols-5 gap-1.5">
                  {previews.map((u, i) => (
                    <div key={i} className="aspect-square rounded-[4px] overflow-hidden" style={{ border: "1px solid var(--border-subtle)", background: "#080C10" }}>
                      <img src={u} alt={`preview ${i}`} className="w-full h-full object-cover" />
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="flex gap-2 justify-end">
              <button onClick={() => setShowAdd(false)} className="text-[11px] px-3 py-1.5 rounded-[4px] border" style={{ borderColor: "var(--border-subtle)", color: "var(--text-secondary)" }}>Cancel</button>
              <button onClick={handleEnroll} disabled={enrolling} className="text-[11px] px-4 py-1.5 rounded-[4px] font-medium disabled:opacity-50" style={{ background: "var(--accent)", color: "#fff" }}>{enrolling ? "Enrolling…" : "Save Person"}</button>
            </div>
          </div>
        </div>
      )}

      <div className="grid grid-cols-12 gap-2">
        <div className="col-span-12 lg:col-span-4 rounded-[6px] flex flex-col overflow-hidden" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
          {loading ? (
            <div className="p-3 space-y-2">{[...Array(4)].map((_, i) => <div key={i} className="skeleton h-11" />)}</div>
          ) : (
            <div className="divide-y overflow-auto max-h-[520px]" style={{ borderColor: "var(--border-subtle)" }}>
              {filtered.map((p) => (
                <button key={p.id} data-interactive onClick={() => setSelected(p)} className="w-full text-left flex items-center gap-2.5 px-3 py-2.5 cursor-pointer" style={{ background: active.id === p.id ? "var(--bg-active)" : "transparent", borderLeft: active.id === p.id ? "2px solid var(--pass)" : "2px solid transparent" }}>
                  <div className="w-8 h-8 rounded-full bg-[#080C10] shrink-0 flex items-center justify-center overflow-hidden" style={{ border: "1px solid var(--border-subtle)" }}>
                    {p.avatar_path ? <img src={`${API_BASE}/${p.avatar_path}`} alt={p.name} className="w-full h-full object-cover" onError={(e) => e.currentTarget.style.display = "none"} /> : <span className="text-[9px] font-bold" style={{ color: "var(--text-muted)" }}>{p.name.slice(0, 2).toUpperCase()}</span>}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-[11px] font-medium truncate leading-none" style={{ color: "var(--text-primary)" }}>{p.name}</div>
                    <div className="text-[9px] font-mono truncate leading-none mt-1" style={{ color: "var(--text-muted)" }}>ID {p.id_number || p.face_id}</div>
                  </div>
                  <span className="text-[8px] px-1.5 py-0.5 rounded-full border font-medium shrink-0" style={{ color: "var(--pass)", borderColor: "var(--pass)", background: "var(--pass-bg)" }}>{p.auth}</span>
                </button>
              ))}
              {filtered.length === 0 && <div className="p-6 text-center text-[11px]" style={{ color: "var(--text-muted)" }}>No personnel match</div>}
            </div>
          )}
          <div className="mt-auto px-3 py-2 flex justify-between text-[10px] border-t shrink-0" style={{ borderColor: "var(--border-subtle)", color: "var(--text-muted)", background: "var(--bg-card)" }}>
            <span>Total People</span><span className="font-mono" style={{ color: "var(--text-primary)" }}>{subjects.length}</span>
          </div>
        </div>

        <div className="col-span-12 lg:col-span-8 rounded-[6px] p-3 flex flex-col gap-3" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
          <div className="flex gap-3">
            <div className="w-16 h-16 rounded-[6px] overflow-hidden bg-[#080C10] shrink-0 flex items-center justify-center" style={{ border: "1px solid var(--border-subtle)" }}>
              {active.avatar_path ? <img src={`${API_BASE}/${active.avatar_path}`} alt={active.name} className="w-full h-full object-cover" onError={(e) => e.currentTarget.style.display = "none"} /> : <span className="text-[11px] font-bold" style={{ color: "var(--text-muted)" }}>{active.name.slice(0, 2).toUpperCase()}</span>}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[12px] font-semibold truncate" style={{ color: "var(--text-primary)" }}>{active.name}</span>
                <div className="flex items-center gap-1.5 shrink-0">
                  <button onClick={openEdit} className="text-[10px] px-2 py-1 rounded-[4px] border" style={{ borderColor: "var(--border-subtle)", color: "var(--text-secondary)" }}>Edit</button>
                  <button onClick={() => setShowDelete(active)} className="text-[10px] px-2 py-1 rounded-[4px] border" style={{ borderColor: "var(--over)", color: "var(--over)", background: "var(--over-bg)" }}>Delete</button>
                  <span className="text-[8px] px-1.5 py-0.5 rounded-full font-bold" style={{ background: "var(--pass-bg)", color: "var(--pass)", border: "1px solid var(--pass)" }}>{active.auth}</span>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-1 mt-2 text-[10px]">
                <span style={{ color: "var(--text-muted)" }}>ID Number</span><span className="font-mono truncate" style={{ color: "var(--text-primary)" }}>{active.id_number || active.face_id}</span>
                <span style={{ color: "var(--text-muted)" }}>Group / Department</span><span style={{ color: "var(--text-primary)" }}>{active.group}</span>
                <span style={{ color: "var(--text-muted)" }}>Added On</span><span style={{ color: "var(--text-primary)" }}>{active.created_at ? new Date(active.created_at).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) : "—"}</span>
                <span style={{ color: "var(--text-muted)" }}>Status</span><span style={{ color: "var(--pass)" }}>{active.status}</span>
              </div>
            </div>
          </div>

          {showEdit && (
            <div className="fixed inset-0 z-30 flex items-center justify-center p-4" style={{ background: "rgba(0,0,0,0.55)" }} onClick={(e) => e.target === e.currentTarget && setShowEdit(false)}>
              <div className="w-full max-w-sm rounded-[6px] p-4 flex flex-col gap-3" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
                <span className="text-[11px] font-bold" style={{ color: "var(--text-primary)" }}>Edit Person — {active.name}</span>
                <div className="flex flex-col gap-1">
                  <label className="text-[10px]" style={{ color: "var(--text-muted)" }}>Full Name</label>
                  <input value={editForm.name} onChange={(e) => setEditForm((p) => ({ ...p, name: e.target.value }))} className="text-[11px] px-2.5 py-1.5 rounded-[4px] border" style={{ background: "var(--bg-card-alt)", borderColor: "var(--border-subtle)", color: "var(--text-primary)" }} />
                </div>
                <div className="flex flex-col gap-1">
                  <label className="text-[10px]" style={{ color: "var(--text-muted)" }}>ID Number</label>
                  <input value={editForm.id_number} onChange={(e) => setEditForm((p) => ({ ...p, id_number: e.target.value }))} className="text-[11px] px-2.5 py-1.5 rounded-[4px] border font-mono" style={{ background: "var(--bg-card-alt)", borderColor: "var(--border-subtle)", color: "var(--text-primary)" }} />
                </div>
                <div className="flex flex-col gap-1">
                  <label className="text-[10px]" style={{ color: "var(--text-muted)" }}>Group</label>
                  <select value={editForm.group} onChange={(e) => setEditForm((p) => ({ ...p, group: e.target.value }))} className="text-[11px] px-2.5 py-1.5 rounded-[4px] border" style={{ background: "var(--bg-card-alt)", borderColor: "var(--border-subtle)", color: "var(--text-primary)" }}>
                    <option>Staff</option><option>Visitor</option><option>Security</option><option>Admin</option>
                  </select>
                </div>
                <div className="flex gap-2 justify-end">
                  <button onClick={() => setShowEdit(false)} className="text-[11px] px-3 py-1.5 rounded-[4px] border" style={{ borderColor: "var(--border-subtle)", color: "var(--text-secondary)" }}>Cancel</button>
                  <button onClick={handleEdit} className="text-[11px] px-4 py-1.5 rounded-[4px] font-medium" style={{ background: "var(--accent)", color: "#fff" }}>Save</button>
                </div>
              </div>
            </div>
          )}
          {showDelete && (
            <div className="fixed inset-0 z-30 flex items-center justify-center p-4" style={{ background: "rgba(0,0,0,0.55)" }} onClick={(e) => e.target === e.currentTarget && setShowDelete(null)}>
              <div className="w-full max-w-sm rounded-[6px] p-4 flex flex-col gap-3" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
                <span className="text-[11px] font-bold" style={{ color: "var(--over)" }}>Delete {showDelete.name}?</span>
                <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>ID {showDelete.id_number || showDelete.face_id} will be removed. This cannot be undone.</span>
                <div className="flex gap-2 justify-end">
                  <button onClick={() => setShowDelete(null)} className="text-[11px] px-3 py-1.5 rounded-[4px] border" style={{ borderColor: "var(--border-subtle)", color: "var(--text-secondary)" }}>Cancel</button>
                  <button onClick={handleDelete} className="text-[11px] px-4 py-1.5 rounded-[4px] font-medium" style={{ background: "var(--over)", color: "#fff" }}>Delete</button>
                </div>
              </div>
            </div>
          )}

          <div style={{ borderTop: "1px solid var(--border-subtle)", paddingTop: "10px" }}>
            <div className="flex items-center justify-between mb-2">
              <span className="text-[10px] font-semibold tracking-wide" style={{ color: "var(--text-primary)" }}>Recent Access</span>
              <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>View all</span>
            </div>
            <div className="space-y-1">
              {(active.accesses || []).map((a, i) => (
                <div key={i} className="flex items-center justify-between text-[10px] px-2.5 py-1.5 rounded-[4px]" style={{ background: "var(--bg-card-alt)" }}>
                  <span style={{ color: "var(--text-secondary)" }}>{a.date}</span>
                  <span style={{ color: "var(--text-muted)" }}>{a.gate}</span>
                  <span className="font-medium" style={{ color: a.res === "Flagged" ? "var(--over)" : "var(--pass)" }}>{a.res}</span>
                </div>
              ))}
              {!active.accesses && <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>No access history</span>}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
