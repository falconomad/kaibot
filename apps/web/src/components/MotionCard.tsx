// Settings, "Pause animations" (audit, WCAG 2.2.2; lib/motion.ts).
import { setAnimationsPaused, useAnimationsPaused } from "../lib/motion";

/** "Pause animations" (lib/motion.ts, WCAG 2.2.2): stops the logo's looping idle motion. Off by default. */
export function MotionCard() {
  const paused = useAnimationsPaused();
  return (
    <section className="card" aria-labelledby="motion-h" style={{ display: "flex", alignItems: "center", gap: 16 }}>
      <div style={{ flex: "1 1 auto" }}>
        <h2 id="motion-h" style={{ marginBottom: 4 }}>Pause animations</h2>
        <span id="motion-d" className="muted" style={{ fontSize: 14 }}>Stops the logo&apos;s repeating motion on this device. It also stays still when your system asks for reduced motion.</span>
      </div>
      <button type="button" role="switch" aria-checked={paused} aria-labelledby="motion-h" aria-describedby="motion-d" className="kb-tog"
        onClick={() => setAnimationsPaused(!paused)}>
        <span aria-hidden="true" />
      </button>
    </section>
  );
}
