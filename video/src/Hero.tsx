import { AbsoluteFill, Sequence, interpolate, useCurrentFrame } from "remotion";
import { DEMO_SECONDS, Demo } from "./Demo";
import { HOOK_SECONDS, Hook } from "./Hook";
import { OUTPUT_SECONDS, Output } from "./Output";
import { theme } from "./theme";
import { sec } from "./timeline";

/**
 * The video in the website's hero: the problem, the capture and the paste, without the
 * title cards (no name or slogan, so it survives a rename). It ends on the paste, which is
 * also its poster.
 */
const HOLD = 1.5;
const scenes = [
  { C: Hook, length: sec(HOOK_SECONDS), fade: true },
  { C: Demo, length: sec(DEMO_SECONDS), fade: false },
  { C: Output, length: sec(OUTPUT_SECONDS + HOLD), fade: false },
];
const starts = scenes.map((_, i) => scenes.slice(0, i).reduce((a, s) => a + s.length, 0));
export const HERO_FRAMES = starts[starts.length - 1] + scenes[scenes.length - 1].length;

const FadeOut: React.FC<{ length: number; fade: boolean; children: React.ReactNode }> = ({ length, fade, children }) => {
  const frame = useCurrentFrame();
  const o = !fade ? 1 : interpolate(frame, [length - 8, length], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return <AbsoluteFill style={{ opacity: o }}>{children}</AbsoluteFill>;
};

export const Hero: React.FC = () => (
  <AbsoluteFill style={{ background: theme.paper }}>
    {scenes.map(({ C, length, fade }, i) => (
      <Sequence key={i} from={starts[i]} durationInFrames={length}>
        <FadeOut length={length} fade={fade}>
          <C />
        </FadeOut>
      </Sequence>
    ))}
  </AbsoluteFill>
);
