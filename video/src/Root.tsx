import { Composition } from "remotion";
import { Hero, HERO_FRAMES } from "./Hero";
import { Promo, PROMO_FRAMES } from "./Promo";
import { FPS } from "./timeline";

export const Root: React.FC = () => (
  <>
    <Composition id="Promo" component={Promo} durationInFrames={PROMO_FRAMES} fps={FPS} width={1920} height={1080} />
    <Composition id="Hero" component={Hero} durationInFrames={HERO_FRAMES} fps={FPS} width={1920} height={1080} />
  </>
);
