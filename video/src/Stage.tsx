import { Easing, Img, interpolate, spring, staticFile } from "remotion";
import { CANVAS, FPS, Mark, Pt, bounds, marks } from "./timeline";

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

/** Drawing progress of a mark at demo time `t` (0…1). */
export const progress = (m: Mark, t: number) =>
  clamp01((t - m.start) / (m.end - m.start));

/** Where the user's pointer is while drawing mark `m` with progress `p` (a drag). */
const tip = (m: Mark, p: number): Pt => {
  const [a, b] = m.points;
  return { x: a.x + (b.x - a.x) * p, y: a.y + (b.y - a.y) * p };
};

const REST: Pt = { x: 760, y: 560 };
const ENTER: Pt = { x: 1190, y: 250 };

/** Cursor position: follows the stroke while drawing, glides to the next mark before it. */
export const cursorAt = (t: number): Pt => {
  // Before the shortcut (a lead-in, see Steps) the pointer drifts to its resting place.
  if (t < 0) {
    const e = Easing.inOut(Easing.cubic)(clamp01((t + 1.5) / 1.1));
    return { x: ENTER.x + (REST.x - ENTER.x) * e, y: ENTER.y + (REST.y - ENTER.y) * e };
  }
  let from = REST;
  for (const m of marks) {
    const move = 0.65;
    if (t < m.start - move) return from;
    if (t < m.start) {
      const e = Easing.inOut(Easing.cubic)(clamp01((t - (m.start - move)) / move));
      const to = tip(m, 0);
      return { x: from.x + (to.x - from.x) * e, y: from.y + (to.y - from.y) * e };
    }
    if (t <= m.end) return tip(m, progress(m, t));
    from = tip(m, 1);
  }
  return from;
};

type Shot = { k: number; cx: number; cy: number };
const FULL: Shot = { k: 1, cx: CANVAS.w / 2, cy: CANVAS.h / 2 };

/** Camera: eases in on each mark while it's drawn and talked about. */
const cameraAt = (t: number): Shot => {
  const center = (m: Mark) => {
    const b = bounds(m);
    return { cx: (b.minX + b.maxX) / 2, cy: (b.minY + b.maxY) / 2 };
  };
  const [m1, m2, m3, m4] = marks;
  const keys: { t: number; shot: Shot }[] = [
    { t: 0, shot: FULL },
    { t: m1.start - 0.9, shot: FULL },
    { t: m1.start - 0.1, shot: { k: 1.45, ...center(m1) } },
    { t: m2.start - 0.9, shot: { k: 1.45, ...center(m1) } },
    { t: m2.start - 0.1, shot: { k: 1.4, ...center(m2) } },
    { t: m3.start - 0.9, shot: { k: 1.4, ...center(m2) } },
    { t: m3.start - 0.1, shot: { k: 1.3, ...center(m3) } },
    { t: m4.start - 0.6, shot: { k: 1.3, ...center(m3) } },
    { t: m4.start + 0.3, shot: FULL },
  ];
  if (t <= keys[0].t) return keys[0].shot;
  for (let i = 1; i < keys.length; i++) {
    if (t <= keys[i].t) {
      const a = keys[i - 1], b = keys[i];
      const e = Easing.inOut(Easing.cubic)(clamp01((t - a.t) / (b.t - a.t)));
      return { k: a.shot.k + (b.shot.k - a.shot.k) * e, cx: a.shot.cx + (b.shot.cx - a.shot.cx) * e, cy: a.shot.cy + (b.shot.cy - a.shot.cy) * e };
    }
  }
  return keys[keys.length - 1].shot;
};

const MarkShape: React.FC<{ m: Mark; t: number; pulse: number }> = ({ m, t, pulse }) => {
  const p = progress(m, t);
  if (p <= 0) return null;
  const [a, b] = m.points;
  let d: string;
  if (m.tool === "ellipse") {
    // Grows with the drag, like the app's ellipse tool.
    const c = tip(m, p);
    const rx = Math.max(0.5, Math.abs(c.x - a.x) / 2), ry = Math.max(0.5, Math.abs(c.y - a.y) / 2);
    const cx = (a.x + c.x) / 2, cy = (a.y + c.y) / 2;
    d = `M${cx - rx},${cy} a${rx},${ry} 0 1 0 ${2 * rx},0 a${rx},${ry} 0 1 0 ${-2 * rx},0`;
  } else if (m.tool === "rectangle") {
    const c = tip(m, p);
    d = `M${a.x},${a.y} H${c.x} V${c.y} H${a.x} Z`;
  } else {
    const c = tip(m, p);
    const ang = Math.atan2(b.y - a.y, b.x - a.x), len = 18, spread = 0.5;
    const h1 = { x: c.x - len * Math.cos(ang - spread), y: c.y - len * Math.sin(ang - spread) };
    const h2 = { x: c.x - len * Math.cos(ang + spread), y: c.y - len * Math.sin(ang + spread) };
    d = `M${a.x},${a.y} L${c.x},${c.y} M${h1.x},${h1.y} L${c.x},${c.y} L${h2.x},${h2.y}`;
  }
  const bb = bounds(m);
  const badge = m.tool === "arrow" ? { x: a.x + 16, y: a.y + 14 } : { x: bb.minX - 14, y: bb.minY - 14 };
  const pop = spring({ frame: Math.round((t - m.end) * FPS), fps: FPS, config: { damping: 11, stiffness: 180 } });
  return (
    <g>
      {pulse > 0 && (
        <path d={d} fill="none" stroke={m.color} strokeOpacity={0.35 * (1 - pulse)} strokeWidth={4 + 26 * pulse} strokeLinecap="round" strokeLinejoin="round" />
      )}
      <path d={d} fill="none" stroke="white" strokeOpacity={0.9} strokeWidth={8} strokeLinecap="round" strokeLinejoin="round" />
      <path d={d} fill="none" stroke={m.color} strokeWidth={4.5} strokeLinecap="round" strokeLinejoin="round" />
      {t >= m.end && (
        <g transform={`translate(${badge.x} ${badge.y}) scale(${pop})`}>
          <circle r={13} fill={m.color} stroke="white" strokeWidth={3} />
          <text textAnchor="middle" dy={5} fontSize={15} fontWeight={700} fill="white" fontFamily="system-ui">
            {m.id}
          </text>
        </g>
      )}
    </g>
  );
};

const Cursor: React.FC<{ at: Pt }> = ({ at }) => (
  <g transform={`translate(${at.x} ${at.y}) scale(1.15)`}>
    <path d="M0,0 L0,20 L5,15.5 L8.5,23.5 L12,22 L8.6,14.2 L15,14.2 Z" fill="black" stroke="white" strokeWidth={1.6} strokeLinejoin="round" />
  </g>
);

/**
 * The mockup screen with marks, in a viewport of `width`. `t` is demo time; `pulses` maps
 * mark id → 0…1 progress of a highlight ring (Output scene).
 */
export const Stage: React.FC<{
  t: number;
  width: number;
  camera?: boolean;
  cursor?: boolean;
  pulses?: Record<number, number>;
}> = ({ t, width, camera = true, cursor = true, pulses = {} }) => {
  const height = (width * CANVAS.h) / CANVAS.w;
  const shot = camera ? cameraAt(t) : FULL;
  const s = (width / CANVAS.w) * shot.k;
  const tx = Math.min(0, Math.max(width - CANVAS.w * s, width / 2 - shot.cx * s));
  const ty = Math.min(0, Math.max(height - CANVAS.h * s, height / 2 - shot.cy * s));
  return (
    <div style={{ width, height, overflow: "hidden", borderRadius: 18, position: "relative", boxShadow: "0 18px 50px rgba(0,0,0,.12), 0 0 0 1px rgba(0,0,0,.08)" }}>
      <div style={{ position: "absolute", width: CANVAS.w, height: CANVAS.h, transformOrigin: "0 0", transform: `translate(${tx}px, ${ty}px) scale(${s})` }}>
        <Img src={staticFile("screen.png")} style={{ width: CANVAS.w, height: CANVAS.h, display: "block" }} />
        <svg width={CANVAS.w} height={CANVAS.h} viewBox={`0 0 ${CANVAS.w} ${CANVAS.h}`} style={{ position: "absolute", inset: 0, filter: "drop-shadow(0 2px 3px rgba(0,0,0,.25))" }}>
          {marks.map((m) => (
            <MarkShape key={m.id} m={m} t={t} pulse={pulses[m.id] ?? 0} />
          ))}
          {cursor && <Cursor at={cursorAt(t)} />}
        </svg>
      </div>
    </div>
  );
};

export const fadeIn = (frame: number, start: number, length = 10) =>
  interpolate(frame, [start, start + length], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
