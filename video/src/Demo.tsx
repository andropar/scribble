import { AbsoluteFill, interpolate, spring, useCurrentFrame } from "remotion";
import { Stage, fadeIn } from "./Stage";
import { theme } from "./theme";
import { FPS, NARRATION_LENGTH, NARRATION_START, marks, phrases, sec, words } from "./timeline";

export const DEMO_SECONDS = NARRATION_START + NARRATION_LENGTH + 0.7;

export const STAGE = { width: 1380, left: (1920 - 1380) / 2, top: 34 };

export const Keycaps: React.FC<{ keys: string[]; frame: number; at: number; size?: number }> = ({ keys, frame, at, size = 64 }) => (
  <div style={{ display: "flex", gap: size * 0.18 }}>
    {keys.map((k, i) => {
      const press = spring({ frame: frame - at - i * 2, fps: FPS, config: { damping: 14, stiffness: 220 } });
      return (
        <div
          key={k}
          style={{
            width: size * 1.15, height: size * 1.15, borderRadius: size * 0.22, background: "white",
            border: `1.5px solid ${theme.line}`, borderBottomWidth: size * 0.09,
            display: "grid", placeItems: "center", fontFamily: theme.sans, fontWeight: 600,
            fontSize: size * 0.55, color: theme.ink, opacity: press,
            transform: `translateY(${(1 - press) * 20}px) scale(${0.85 + 0.15 * press})`,
            boxShadow: "0 10px 30px rgba(0,0,0,.14)",
          }}
        >
          {k}
        </div>
      );
    })}
  </div>
);

const Hud: React.FC<{ t: number }> = ({ t }) => {
  const frame = Math.round(t * FPS);
  const show = spring({ frame: frame - Math.round(0.75 * FPS), fps: FPS, config: { damping: 16 } });
  const speaking = words.some((w) => t >= w.start - 0.05 && t <= w.end + 0.05);
  const drawn = marks.filter((m) => t >= m.end).length;
  const elapsed = Math.max(0, t - 0.6);
  return (
    <div style={{ position: "absolute", top: 46, left: "50%", transform: `translateX(-50%) translateY(${(1 - show) * -30}px)`, opacity: show }}>
      <div style={{ display: "flex", alignItems: "center", gap: 14, padding: "9px 18px", borderRadius: 999, background: "rgba(255,255,255,.94)", border: "1px solid rgba(0,0,0,.1)", boxShadow: "0 8px 24px rgba(0,0,0,.18)", fontFamily: theme.sans }}>
        <div style={{ width: 11, height: 11, borderRadius: 6, background: theme.red, opacity: 0.6 + 0.4 * Math.sin(t * 6) ** 2 }} />
        <span style={{ fontSize: 17, fontWeight: 600, fontVariantNumeric: "tabular-nums", color: theme.ink }}>
          0:{String(Math.floor(elapsed)).padStart(2, "0")}
        </span>
        <div style={{ display: "flex", gap: 3, alignItems: "flex-end", height: 18 }}>
          {[0, 1, 2, 3, 4].map((i) => {
            const level = speaking ? 0.35 + 0.65 * Math.abs(Math.sin(t * 13 + i * 1.7) * Math.sin(t * 7.3 + i)) : 0.08;
            return <div key={i} style={{ width: 4, height: 18, borderRadius: 2, background: i / 5 < level ? "#34c759" : "rgba(0,0,0,.12)" }} />;
          })}
        </div>
        <div style={{ width: 1, height: 22, background: "rgba(0,0,0,.12)" }} />
        <span style={{ fontSize: 16, color: theme.muted, fontWeight: 500 }}>Snapshot</span>
        {drawn > 0 && <span style={{ fontSize: 16, color: theme.muted }}>· #{drawn}</span>}
      </div>
    </div>
  );
};

const Chip: React.FC<{ id: number; label: string; color: string; age: number }> = ({ id, label, color, age }) => {
  const pop = spring({ frame: Math.round(age * FPS), fps: FPS, config: { damping: 12, stiffness: 200 } });
  return (
    <span
      style={{
        display: "inline-block", margin: "0 10px", padding: "2px 12px", borderRadius: 10, background: color,
        color: "white", fontFamily: theme.mono, fontSize: 30, fontWeight: 600, verticalAlign: "3px",
        transform: `scale(${pop})`, opacity: pop,
      }}
    >
      #{id} {label}
    </span>
  );
};

/**
 * Karaoke caption of the current sentence. Like capture.md, each mark's chip follows the word
 * that was being spoken when drawing started.
 */
const Captions: React.FC<{ t: number }> = ({ t }) => {
  const idx = phrases.reduce((cur, p, i) => (t >= p[0].start - 0.15 ? i : cur), -1);
  if (idx < 0) return null;
  const phrase = phrases[idx];
  const age = t - (phrase[0].start - 0.15);
  const enter = Math.min(1, age / 0.2);
  return (
    <div style={{ position: "absolute", top: STAGE.top + (STAGE.width * 982) / 1512 + 26, width: "100%", textAlign: "center", fontFamily: theme.sans, fontSize: 44, fontWeight: 600, letterSpacing: -0.5, color: theme.ink, opacity: enter, transform: `translateY(${(1 - enter) * 10}px)` }}>
      {phrase.map((w, i) => {
        const next = phrase[i + 1];
        const chip = marks.find((m) => m.start >= w.start && (!next || m.start < next.start));
        return (
          <span key={i}>
            <span style={{ opacity: t >= w.start ? 1 : 0.22 }}>{w.text} </span>
            {chip && t >= chip.start && <Chip id={chip.id} label={chip.label} color={chip.color} age={t - chip.start} />}
          </span>
        );
      })}
    </div>
  );
};

/** `lead` seconds of the live screen come first, before the shortcut is pressed. */
export const Demo: React.FC<{ lead?: number }> = ({ lead = 0 }) => {
  const frame = useCurrentFrame() - sec(lead);
  const t = frame / FPS;
  const keysOut = interpolate(t, [0.55, 0.8], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const flash = interpolate(t, [0.5, 0.58, 0.85], [0, 0.85, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  // HUD and captions leave before the screen slides aside in the Output scene.
  const tail = interpolate(t, [DEMO_SECONDS - 0.5, DEMO_SECONDS - 0.1], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <AbsoluteFill style={{ background: theme.paper, opacity: lead ? 1 : fadeIn(frame, 0, 8) }}>
      <div style={{ position: "absolute", left: STAGE.left, top: STAGE.top }}>
        <Stage t={t} width={STAGE.width} />
        <div style={{ opacity: tail }}>
          <Hud t={t} />
        </div>
        <div style={{ position: "absolute", inset: 0, borderRadius: 18, background: "white", opacity: flash }} />
      </div>
      <AbsoluteFill style={{ opacity: tail }}>
        <Captions t={t} />
      </AbsoluteFill>
      {keysOut > 0 && (
        <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", opacity: keysOut }}>
          <Keycaps keys={["⌃", "⌥", "⌘", "S"]} frame={frame} at={2} size={90} />
        </AbsoluteFill>
      )}
    </AbsoluteFill>
  );
};
