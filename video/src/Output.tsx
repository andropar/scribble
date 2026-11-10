import { AbsoluteFill, Easing, interpolate, spring, useCurrentFrame } from "remotion";
import { Keycaps, STAGE } from "./Demo";
import { Stage } from "./Stage";
import { theme } from "./theme";
import { FPS, marks } from "./timeline";

export const OUTPUT_SECONDS = 6.0;

type Seg = { text: string; chip?: number; tone?: "head" | "dim" | "time" | "file" };
// An excerpt of the demo capture.md (the real file is in docs/demo/example-capture.md).
const LINES: Seg[][] = [
  [{ text: "# Screen capture · Snapshot · 0:19", tone: "head" }],
  [{ text: "Numbered marks (#1, #2, …) are the user's own", tone: "dim" }],
  [{ text: "annotations, cited where they were drawn.", tone: "dim" }],
  [{ text: "- frame 1 · 1568×1018 · " }, { text: "frame_01.png", tone: "file" }],
  [],
  [{ text: "## Transcript", tone: "head" }],
  [{ text: "[00:00 · frame 1]", tone: "time" }, { text: " This best" }],
  [{ text: "[#1 circled (301,290)-(450,340)]", chip: 1 }, { text: " accuracy" }],
  [{ text: "says 312%. That has to be a units bug." }],
  [{ text: "The legend " }, { text: "[#2 circled (1241,634)-(1461,784)]", chip: 2 }],
  [{ text: "here sits right on top of the curves." }],
  [{ text: "And this export " }, { text: "[#3 box around (290,908)-…]", chip: 3 }],
  [{ text: "button is floating down here. It should go" }],
  [{ text: "[#4 arrow from (446,910) pointing at (809,230)]", chip: 4 }],
  [{ text: "up next to the filters." }],
];
const LINE_START = 0.9;
const LINE_EVERY = 0.2;
const lineTime = (i: number) => LINE_START + i * LINE_EVERY;
const chipTime = (id: number) => lineTime(LINES.findIndex((l) => l.some((s) => s.chip === id)));

const tone = (s: Seg): React.CSSProperties => {
  if (s.chip) {
    const m = marks[s.chip - 1];
    return { background: m.color === theme.red ? "rgba(255,59,48,.42)" : "rgba(10,132,255,.48)", color: "white", borderRadius: 5, padding: "1px 4px" };
  }
  switch (s.tone) {
    case "head": return { color: "white", fontWeight: 700 };
    case "dim": return { color: "#8b8b94" };
    case "time": return { color: "#ffd479" };
    case "file": return { color: "#7ee0b5" };
    default: return {};
  }
};

export const Output: React.FC = () => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const move = interpolate(t, [0, 0.8], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.inOut(Easing.cubic) });
  const width = STAGE.width + (1000 - STAGE.width) * move;
  const left = STAGE.left + (70 - STAGE.left) * move;
  const top = STAGE.top + (230 - STAGE.top) * move;
  const panel = spring({ frame: frame - 12, fps: FPS, config: { damping: 18 } });
  const head = interpolate(t, [0.5, 0.9], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const pulses: Record<number, number> = {};
  for (const m of marks) {
    const p = (t - chipTime(m.id)) / 0.7;
    if (p > 0 && p < 1) pulses[m.id] = p;
  }

  return (
    <AbsoluteFill style={{ background: theme.paper, fontFamily: theme.sans }}>
      <div style={{ position: "absolute", left: 70, top: 92, display: "flex", alignItems: "center", gap: 26, opacity: head }}>
        <Keycaps keys={["⌘", "V"]} frame={frame} at={14} size={54} />
        <div style={{ fontSize: 54, fontWeight: 740, letterSpacing: -1.5, color: theme.ink }}>Paste into any agent.</div>
      </div>
      <div style={{ position: "absolute", left, top }}>
        <Stage t={100} width={width} camera={false} cursor={false} pulses={pulses} />
      </div>
      <div style={{ position: "absolute", left: 70, top: 230 + 649 + 34, fontSize: 32, color: theme.muted, fontWeight: 500, opacity: head }}>
        Each mark is cited at the moment you drew it, in image pixels.
      </div>
      <div style={{ position: "absolute", left: 1110, top: 80, width: 750, opacity: panel, transform: `translateX(${(1 - panel) * 120}px)` }}>
        <div style={{ background: theme.dark, borderRadius: 16, overflow: "hidden", boxShadow: "0 18px 50px rgba(0,0,0,.18)" }}>
          <div style={{ height: 44, background: "#1c1c1f", display: "flex", alignItems: "center", gap: 9, padding: "0 18px" }}>
            {["#ff5f57", "#febc2e", "#28c840"].map((c) => <div key={c} style={{ width: 13, height: 13, borderRadius: 7, background: c }} />)}
            <span style={{ marginLeft: 12, fontSize: 17, color: "#a1a1aa" }}>capture.md</span>
          </div>
          <div style={{ padding: "22px 26px 26px", fontFamily: theme.mono, fontSize: 21.5, lineHeight: 1.62, color: "#e4e4e7", minHeight: 860 }}>
            {LINES.map((line, i) => {
              const a = interpolate(t, [lineTime(i), lineTime(i) + 0.18], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
              return (
                <div key={i} style={{ minHeight: "1.62em", opacity: a, transform: `translateY(${(1 - a) * 8}px)`, whiteSpace: "pre" }}>
                  {line.map((s, j) => <span key={j} style={tone(s)}>{s.text}</span>)}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </AbsoluteFill>
  );
};
