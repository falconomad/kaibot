import { supa } from "../lib/api";
import { NAME } from "../brand/brand";
import { InviteCard } from "../components/InviteCard";

// What an invited user sees (stakeholder, 24 Nov 2026; decision in the PR for migration 0045). The dashboard is the
// owner's paper run and every kb_* read and write is owner-only in the database; an invited account is not the owner,
// so it gets none of it: no run, no positions, no headlines (Alpaca/Benzinga licence: owner only), no settings, no
// kill switch. This page shows only their own account and the invite card. A read-only view of the owner's run is a
// later step that needs its own owner-checked, licence-checked RPCs.

// 0048 (#721, Product 25 Nov 2026): only the owner invites. kb_me's can_invite is then false for a member, and the
// invite card is not shown (before 0048 it is true for a member, and the card shows as it did).
export function Member({ name, canInvite }: { name: string | null; canInvite: boolean }) {
  return (
    <div className="center">
      <div style={{ display: "grid", gap: 16, width: "min(560px, 100%)" }}>
        <section className="card" aria-labelledby="mem-h">
          <h1 id="mem-h" style={{ margin: 0, fontSize: 18 }}>{name ? `Welcome, ${name}` : "Welcome"}</h1>
          <p className="muted">You're registered. {NAME}'s dashboard is the owner's private paper run, and it isn't shared
            with other accounts yet. When it is, it'll appear here; you don't need to do anything.</p>
          <button onClick={() => supa.auth.signOut()}>Sign out</button>
        </section>
        {canInvite && <InviteCard />}
      </div>
    </div>
  );
}
