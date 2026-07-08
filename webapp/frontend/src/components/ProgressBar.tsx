interface Props {
  completed: number;
  total: number;
  running: boolean;
}

export function ProgressBar({ completed, total, running }: Props) {
  const pct = total === 0 ? 0 : Math.round((completed / total) * 100);
  const done = completed >= total && total > 0;

  return (
    <div style={{ marginTop: 16 }}>
      <div style={headerStyle}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {running && <Spinner />}
          <span style={{ fontWeight: 500, color: done ? "#16a34a" : "#0f172a" }}>
            {running
              ? "转换中…"
              : done
                ? "完成"
                : "等待开始"}
          </span>
        </div>
        <div style={pctStyle}>
          <span style={{ fontSize: 20, fontWeight: 600, color: done ? "#16a34a" : "#2563eb" }}>
            {pct}%
          </span>
          <span style={{ fontSize: 13, color: "#64748b", marginLeft: 8 }}>
            {completed} / {total}
          </span>
        </div>
      </div>
      <div style={trackStyle}>
        <div
          style={{
            width: `${pct}%`,
            height: "100%",
            background: done
              ? "linear-gradient(90deg, #16a34a, #22c55e)"
              : running
                ? "linear-gradient(90deg, #2563eb, #3b82f6)"
                : "#94a3b8",
            transition: "width 0.3s ease",
            borderRadius: 5,
            position: "relative",
            overflow: "hidden",
          }}
        >
          {running && (
            <div style={shimmerStyle} />
          )}
        </div>
      </div>
    </div>
  );
}

/** 旋转的圆环 spinner — 纯 CSS, 不依赖外部库 */
function Spinner() {
  return (
    <div
      style={{
        width: 16,
        height: 16,
        border: "2px solid #dbeafe",
        borderTopColor: "#2563eb",
        borderRadius: "50%",
        animation: "spin 0.8s linear infinite",
      }}
    />
  );
}

const headerStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  marginBottom: 6,
};

const pctStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "baseline",
};

const trackStyle: React.CSSProperties = {
  height: 12,
  background: "#e2e8f0",
  borderRadius: 6,
  overflow: "hidden",
  position: "relative",
};

const shimmerStyle: React.CSSProperties = {
  position: "absolute",
  inset: 0,
  background:
    "linear-gradient(90deg, transparent, rgba(255,255,255,0.4), transparent)",
  animation: "shimmer 1.5s infinite",
};

// 关键: 把 keyframes 注入到 document head (只注入一次)
let _injected = false;
if (typeof document !== "undefined" && !_injected) {
  _injected = true;
  const style = document.createElement("style");
  style.textContent = `
    @keyframes spin { to { transform: rotate(360deg); } }
    @keyframes shimmer {
      0% { transform: translateX(-100%); }
      100% { transform: translateX(100%); }
    }
  `;
  document.head.appendChild(style);
}
