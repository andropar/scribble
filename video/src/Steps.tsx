import { AbsoluteFill, Sequence } from "remotion";
import { DEMO_SECONDS, Demo } from "./Demo";
import { Output } from "./Output";
import { theme } from "./theme";
import { sec } from "./timeline";

/**
 * The capture as one loop for the website's "How it works": the shortcut, drawing while
 * talking, and the paste. STEP_STARTS are the seconds at which each of the three begins;
 * site/app.js has the same numbers.
 */
const LEAD = 1.7;
const OUTPUT_SECONDS = 7.2;
export const STEP_STARTS = [0, LEAD + 1.25, LEAD + DEMO_SECONDS];
export const STEPS_FRAMES = sec(LEAD + DEMO_SECONDS + OUTPUT_SECONDS);

export const Steps: React.FC = () => (
  <AbsoluteFill style={{ background: theme.paper }}>
    <Sequence durationInFrames={sec(LEAD + DEMO_SECONDS)}>
      <Demo lead={LEAD} />
    </Sequence>
    <Sequence from={sec(LEAD + DEMO_SECONDS)}>
      <Output />
    </Sequence>
  </AbsoluteFill>
);
