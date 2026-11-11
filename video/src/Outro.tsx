import { AbsoluteFill, Img, interpolate, spring, staticFile, useCurrentFrame } from "remotion";
import { theme } from "./theme";
import { FPS } from "./timeline";

export const OUTRO_SECONDS = 4.0;

const FACTS = ["Snapshot, Walkthrough & Dictation", "On-device Whisper", "Multi-display", "Open source · MIT"];

export const Outro: React.FC = () => {
  const frame = useCurrentFrame();
  const icon = spring({ frame, fps: FPS, config: { damping: 12, stiffness: 160 } });
  const at = (s: number) => interpolate(frame / FPS, [s, s + 0.35], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const words: [string, string][] = [["Talk.", theme.ink], ["Draw.", theme.red], ["Paste.", theme.blue]];
  return (
    <AbsoluteFill style={{ background: theme.paper, fontFamily: theme.sans, alignItems: "center", justifyContent: "center" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 34, transform: `scale(${0.9 + 0.1 * icon})`, opacity: icon }}>
        <Img src={staticFile("icon.png")} style={{ width: 168, height: 168 }} />
        <div style={{ fontSize: 120, fontWeight: 780, letterSpacing: -4, color: theme.ink }}>Scribble</div>
      </div>
      <div style={{ display: "flex", gap: 26, marginTop: 30, fontSize: 64, fontWeight: 700, letterSpacing: -1.5 }}>
        {words.map(([w, c], i) => (
          <span key={w} style={{ color: c, opacity: at(0.35 + i * 0.22), transform: `translateY(${(1 - at(0.35 + i * 0.22)) * 16}px)` }}>{w}</span>
        ))}
      </div>
      <div style={{ display: "flex", gap: 14, marginTop: 52, opacity: at(1.2) }}>
        {FACTS.map((f) => (
          <div key={f} style={{ padding: "10px 20px", borderRadius: 999, border: `1.5px solid ${theme.line}`, background: "white", fontSize: 25, color: theme.ink, fontWeight: 500 }}>{f}</div>
        ))}
      </div>
      <div style={{ marginTop: 44, fontFamily: theme.mono, fontSize: 32, color: theme.muted, opacity: at(1.5) }}>github.com/andropar/scribble</div>
    </AbsoluteFill>
  );
};
