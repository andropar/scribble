import { AbsoluteFill, Img, staticFile } from "remotion";
import { Stage } from "./Stage";
import { theme } from "./theme";

export const COVER_SECONDS = 1.6;

/**
 * Opening card, complete from frame 0: feeds show the first frame as the preview before
 * anyone presses play. Also exported as poster.png for custom thumbnails.
 */
export const Cover: React.FC = () => (
  <AbsoluteFill style={{ background: theme.paper, fontFamily: theme.sans, flexDirection: "row", alignItems: "center", padding: "0 90px", gap: 70 }}>
    <div style={{ width: 640, flexShrink: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 22 }}>
        <Img src={staticFile("icon.png")} style={{ width: 96, height: 96 }} />
        <div style={{ fontSize: 68, fontWeight: 780, letterSpacing: -2.5, color: theme.ink }}>Scribble</div>
      </div>
      <div style={{ marginTop: 34, fontSize: 64, fontWeight: 740, letterSpacing: -2, lineHeight: 1.08, color: theme.ink }}>
        Talk. <span style={{ color: theme.red }}>Draw.</span> <span style={{ color: theme.blue }}>Paste.</span>
      </div>
      <div style={{ marginTop: 26, fontSize: 32, lineHeight: 1.4, color: theme.muted, fontWeight: 500 }}>
        Point at your screen while you talk. Your coding agent gets annotated screenshots and a transcript that cites every mark.
      </div>
    </div>
    <Stage t={100} width={1030} camera={false} cursor={false} />
  </AbsoluteFill>
);
