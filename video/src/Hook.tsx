import { AbsoluteFill, Easing, interpolate, useCurrentFrame } from "remotion";
import { fadeIn } from "./Stage";
import { theme } from "./theme";
import { FPS } from "./timeline";

export const HOOK_SECONDS = 6.8;

const RAMBLE =
  "ok so on the runs page, the big number in the first card (no, the top left one, under “best val accuracy”) says 312% which can't be right, and the legend thing on the chart, kind of bottom right, is covering the lines, and the export button is";

export const Hook: React.FC = () => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const typed = RAMBLE.slice(0, Math.floor(Math.max(0, t - 0.35) * 70));
  const out = interpolate(t, [3.6, 4.0], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const title = interpolate(t, [3.9, 4.4], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.cubic) });
  const circle = interpolate(t, [4.6, 5.4], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.inOut(Easing.cubic) });

  return (
    <AbsoluteFill style={{ background: theme.paper, fontFamily: theme.sans, alignItems: "center", justifyContent: "center", opacity: fadeIn(frame, 0, 8) }}>
      {out > 0 && (
        <div style={{ opacity: out, transform: `translateY(${(1 - out) * -30}px)`, width: 1240 }}>
          <div style={{ fontSize: 30, fontWeight: 600, color: theme.muted, marginBottom: 22 }}>Describing a UI bug to your coding agent:</div>
          <div style={{ background: "white", borderRadius: 22, border: `1.5px solid ${theme.line}`, boxShadow: "0 20px 60px rgba(0,0,0,.08)", padding: "30px 36px", minHeight: 250, fontSize: 36, lineHeight: 1.45, color: theme.ink }}>
            {typed}
            <span style={{ display: "inline-block", width: 3, height: 40, marginLeft: 2, verticalAlign: -6, background: theme.ink, opacity: Math.floor(t * 4) % 2 }} />
          </div>
        </div>
      )}
      {title > 0 && (
        <div style={{ position: "absolute", opacity: title, transform: `translateY(${(1 - title) * 24}px)`, fontSize: 104, fontWeight: 760, letterSpacing: -3, color: theme.ink }}>
          Or just{" "}
          <span style={{ position: "relative", display: "inline-block" }}>
            point
            <svg width={340} height={190} viewBox="0 0 340 190" style={{ position: "absolute", left: -24, top: -26, overflow: "visible", transform: "scaleX(0.9)", transformOrigin: "0 0" }}>
              <path
                d="M300,48 C250,8 90,4 30,50 C-8,82 20,150 120,166 C220,180 330,150 322,80 C316,40 260,22 200,22"
                fill="none" stroke={theme.red} strokeWidth={9} strokeLinecap="round" pathLength={1}
                strokeDasharray={`${circle} 1`} opacity={circle > 0 ? 1 : 0}
              />
            </svg>
          </span>{" "}
          at it and say it.
        </div>
      )}
    </AbsoluteFill>
  );
};
