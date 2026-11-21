// Settings, "On your phone" (M13 4.7, #481): put the dashboard on the home screen. Chrome and Edge make an install
// offer the button can use; Safari has none, so the steps are written out. Once it runs from the home screen the card
// says so.
import { useState, useSyncExternalStore } from "react";
import { NAME } from "../brand/brand";
import { installOffer, installSteps, isStandalone, platform, subscribe } from "../pwa/install";

export function InstallApp() {
  const offer = useSyncExternalStore(subscribe, installOffer, () => null);
  const [asked, setAsked] = useState<"" | "accepted" | "dismissed">("");
  const browser = typeof window !== "undefined";
  const installed = browser && isStandalone();
  const steps = installSteps(browser ? platform(navigator.userAgent, navigator.maxTouchPoints) : "other");
  async function install() {
    if (!offer) return;
    await offer.prompt();
    setAsked((await offer.userChoice).outcome);
  }
  return (
    <section className="card" aria-labelledby="install-h">
      <h2 id="install-h">On your phone</h2>
      {installed ? <p style={{ marginBottom: 0 }}>{NAME} is on your home screen and you are using it from there.</p> : (
        <>
          <p>Put {NAME} on your home screen. It opens full screen, like an app, with nothing to download from a store.</p>
          {offer && <p><button type="button" className="primary full-sm" onClick={() => void install()}>Add {NAME} to this device</button></p>}
          {asked === "accepted" && <p role="status">Added. Look for the {NAME} icon on your home screen.</p>}
          {!offer && asked !== "accepted" && <ol className="sheet-list">{steps.map((s) => <li key={s}>{s}</li>)}</ol>}
          <p className="muted" style={{ fontSize: 13, marginBottom: 0 }}>You sign in once more inside the installed app, with the 6-digit code from the sign-in email.
            The app shows live data only: without a connection it opens and says so, and never shows old prices.</p>
        </>
      )}
    </section>
  );
}
