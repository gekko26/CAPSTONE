// File: Frontend/src/components/ui/primitives.jsx
// Shared UI primitives — the single source of truth for cards,
// stat tiles, status badges and buttons across all pages.

/* ── Card ────────────────────────────────────────────────
   Standard container: white surface, subtle border, xl radius.
   hoverable adds a lift effect for clickable cards.        */
export function Card({ children, className = "", style = {}, hoverable = false, onClick }) {
  return (
    <div
      onClick={onClick}
      className={`rounded-[6px] p-3 transition-colors ${onClick ? "cursor-pointer " : ""}${className}`}
      style={{
        background: "var(--bg-card)",
        border: "1px solid var(--border-subtle)",
        boxShadow: "none",
        ...style,
      }}
      onMouseEnter={hoverable ? (e) => {
        e.currentTarget.style.borderColor = "var(--border)";
      } : undefined}
      onMouseLeave={hoverable ? (e) => {
        e.currentTarget.style.borderColor = "var(--border-subtle)";
      } : undefined}
    >
      {children}
    </div>
  );
}

/* ── StatTile ────────────────────────────────────────────
   Label on top, big number, optional sub-line.            */
export function StatTile({ label, value, sub, valueColor, className = "", style = {} }) {
  return (
    <div
      className={`rounded-[6px] px-3 py-2.5 ${className}`}
      style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)", ...style }}
    >
      <p className="text-[10px] tracking-wide uppercase mb-1" style={{ color: "var(--text-muted)" }}>{label}</p>
      <p className="text-[16px] font-bold tabular-nums leading-tight" style={{ color: valueColor ?? "var(--text-primary)" }}>
        {value ?? "—"}
      </p>
      {sub && <p className="text-[10px] mt-1" style={{ color: "var(--text-muted)" }}>{sub}</p>}
    </div>
  );
}

/* ── StatusBadge ─────────────────────────────────────────
   One color logic for every status word in the app.
   tone: "pass" | "near" | "over" | "neutral" | auto       */
const TONES = {
  pass:    { bg: "var(--pass-bg)",    text: "var(--pass-text)",    border: "transparent" },
  near:    { bg: "var(--near-bg)",    text: "var(--near-text)",    border: "transparent" },
  over:    { bg: "var(--over-bg)",    text: "var(--over-text)",    border: "transparent" },
  neutral: { bg: "var(--bg-card-alt)", text: "var(--text-secondary)", border: "transparent" },
};

function toneForLabel(label = "") {
  const l = label.toLowerCase();
  if (l.includes("over") || l.includes("denied") || l.includes("breach") || l.includes("impaired")) return "over";
  if (l.includes("near") || l.includes("warn") || l.includes("drowsy")) return "near";
  if (l.includes("sanitizer") || l.includes("perfume") || l.includes("filtered")) return "neutral";
  return "pass";
}

export function StatusBadge({ label, tone, size = "sm", children }) {
  const t = TONES[tone ?? toneForLabel(typeof label === "string" ? label : "")] ?? TONES.neutral;
  const pad = size === "lg" ? "px-2.5 py-0.5 text-[10px]" : "px-1.5 py-0.5 text-[10px]";
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-[4px] font-semibold tracking-wide uppercase ${pad}`}
      style={{ background: t.bg, color: t.text, border: `1px solid ${t.border}` }}
    >
      {children ?? label}
    </span>
  );
}

/* ── Button ──────────────────────────────────────────────
   variant: "primary" | "secondary" | "ghost" | "danger"
   size:     "sm" | "md"                                    */
export function Button({ variant = "primary", size = "md", children, className = "", style = {}, ...rest }) {
  const base = "inline-flex items-center justify-center gap-1.5 rounded-[4px] font-medium transition-colors disabled:opacity-50 disabled:pointer-events-none";

  const sizes = { sm: "text-[11px] px-2.5 py-1", md: "text-[11px] px-3 py-1.5" };

  const variants = {
    primary: {
      background: "var(--accent)", color: "#fff", border: "1px solid transparent",
      hoverBg: "var(--accent-hover)",
    },
    secondary: {
      background: "var(--bg-card)", color: "var(--text-primary)", border: "1px solid var(--border)",
      hoverBg: "var(--bg-card-alt)",
    },
    ghost: {
      background: "transparent", color: "var(--text-secondary)", border: "1px solid transparent",
      hoverBg: "var(--bg-card-alt)",
    },
    danger: {
      background: "var(--over)", color: "#fff", border: "1px solid transparent",
      hoverBg: "var(--over-text)",
    },
  };

  const v = variants[variant];

  return (
    <button
      className={`${base} ${sizes[size]} ${className}`}
      style={{ background: v.background, color: v.color, border: v.border, cursor: "pointer", ...style }}
      onMouseEnter={(e) => { e.currentTarget.style.background = v.hoverBg; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = v.background; }}
      {...rest}
    >
      {children}
    </button>
  );
}

/* ── Skeleton ────────────────────────────────────────────
   Loading placeholder (uses .skeleton shimmer in css).     */
export function Skeleton({ className = "" }) {
  return <div className={`skeleton ${className}`} />;
}

/* ── EmptyState ────────────────────────────────────────── */
export function EmptyState({ icon: Icon, title, sub }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-8 text-center">
      {Icon && <Icon size={28} strokeWidth={1.5} style={{ color: "var(--text-muted)" }} />}
      <p className="text-sm font-medium" style={{ color: "var(--text-secondary)" }}>{title}</p>
      {sub && <p className="text-xs" style={{ color: "var(--text-muted)" }}>{sub}</p>}
    </div>
  );
}
