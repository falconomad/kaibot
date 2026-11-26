// Settings, "Theme" (#720; lib/theme.ts): System, Dark or Light.
import { RadioGroup } from "./ConfigControls";
import { THEME_CHOICES, setTheme, useThemeChoice, type ThemeChoice } from "../lib/theme";

const LABEL: Record<ThemeChoice, string> = { system: "System", dark: "Dark", light: "Light" };

/** "Theme": a radio group (arrow keys move and choose). System, the default, follows the device as it changes. */
export function ThemeCard() {
  const choice = useThemeChoice();
  return (
    <section className="card" aria-labelledby="theme-h" style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 16 }}>
      <div style={{ flex: "1 1 260px" }}>
        <h2 id="theme-h" style={{ marginBottom: 4 }}>Theme</h2>
        <span id="theme-d" className="muted" style={{ fontSize: 14 }}>System follows your device&apos;s light or dark setting. Kept on this device.</span>
      </div>
      <RadioGroup labelledBy="theme-h" className="seg" value={choice} onChange={setTheme}
        options={THEME_CHOICES.map((c) => ({ value: c, label: LABEL[c] }))} />
    </section>
  );
}
