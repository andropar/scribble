import { Composition } from "remotion";
import { Promo, PROMO_FRAMES } from "./Promo";
import { Steps, STEPS_FRAMES } from "./Steps";
import { FPS } from "./timeline";

export const Root: React.FC = () => (
  <>
    <Composition id="Promo" component={Promo} durationInFrames={PROMO_FRAMES} fps={FPS} width={1920} height={1080} />
    <Composition id="Steps" component={Steps} durationInFrames={STEPS_FRAMES} fps={FPS} width={1920} height={1080} />
  </>
);
