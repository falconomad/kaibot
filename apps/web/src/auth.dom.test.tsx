// @vitest-environment jsdom
// Sign-in by invite only (stakeholder, 24 Nov 2026; migration 0045). Sign-in, registration and the invite card are
// mounted in jsdom with Supabase Auth and the invite RPCs replaced by recorders, so the tests see what is sent:
// sign-in never asks Supabase to create an account, an unknown email gets the stakeholder's words, registration checks
// the code and the email before any email is sent, and the invite card shows the link, the code and a mailto share.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { all, doing, mount, q, reducedMotionOn, settle, unmount } from "./a11y/dom";
import { SignIn } from "./pages/SignIn";
import { NOT_OPEN, REASON, REFUSED, Register, codeFromUrl } from "./pages/Register";
import { InviteCard, NOT_APPLIED, REVOKE_NOT_APPLIED, mailto } from "./components/InviteCard";
import { Member } from "./pages/Member";
import { ATTEMPT_LIMIT, ATTEMPT_WINDOW_MS, NO_ACCOUNT, isNoAccount, isSignupRefused, takeAttempt } from "./lib/attempts";

const rec = vi.hoisted(() => ({
  otp: vi.fn<(a: { email: string; options: Record<string, unknown> }) => Promise<{ error: { code?: string; message: string } | null }>>(),
  check: vi.fn<(code: string, email?: string) => Promise<unknown>>(),
  create: vi.fn<(email: string) => Promise<unknown>>(),
  list: vi.fn<() => Promise<unknown>>(),
  revoke: vi.fn<(id: number) => Promise<unknown>>(),
  signOut: vi.fn(),
}));
vi.mock("./lib/api", async () => {
  const kit = await import("./pages/testkit-api");
  const never = () => new Promise<never>(() => {});
  const calls: Record<string, unknown> = { inviteCheck: rec.check, inviteCreate: rec.create, inviteList: rec.list, inviteRevoke: rec.revoke };
  return {
    ...kit,
    api: new Proxy({}, { get: (_t, k) => calls[k as string] ?? never }),
    supa: { ...kit.supa, auth: { ...kit.supa.auth, signInWithOtp: rec.otp, signOut: rec.signOut } },
  };
});

const mem = new Map<string, string>();
vi.stubGlobal("localStorage", { getItem: (k: string) => mem.get(k) ?? null, setItem: (k: string, v: string) => { mem.set(k, v); } });

const text = (el: Element | null) => (el?.textContent ?? "").replace(/\s+/g, " ").trim();
const type = (sel: string, value: string) => doing(() => {
  const el = q<HTMLInputElement>(sel)!;
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
});
const submit = async () => { await doing(() => { q<HTMLButtonElement>("button.primary")!.click(); }); await settle(); };
const alert = () => text(q('[role="alert"]'));

beforeEach(() => {
  reducedMotionOn(); mem.clear();
  for (const f of Object.values(rec)) f.mockReset();
});
afterEach(async () => { await unmount(); });

describe("sign-in: registered users only", () => {
  it("an unknown email gets the message, and Supabase is told never to create an account (so no email is sent)", async () => {
    rec.otp.mockResolvedValue({ error: { code: "otp_disabled", message: "Signups not allowed for otp" } });
    await mount(<SignIn />);
    await type("#email", "stranger@example.com");
    await submit();
    expect(rec.otp).toHaveBeenCalledTimes(1);
    const arg = rec.otp.mock.calls[0][0];
    expect(arg.email).toBe("stranger@example.com");
    expect(arg.options.shouldCreateUser).toBe(false);
    expect(alert()).toBe(NO_ACCOUNT);
    expect(alert()).toBe("No account for this email. Ask an existing user for an invite.");
    expect(text(document.body)).not.toContain("Check your email");
    // the form stays usable and offers registration
    expect(q<HTMLButtonElement>("button.primary")!.disabled).toBe(false);
    expect(q<HTMLAnchorElement>('a[href="/register"]')).not.toBeNull();
  });

  it("a registered email gets the link as before", async () => {
    rec.otp.mockResolvedValue({ error: null });
    await mount(<SignIn />);
    await type("#email", "owner@example.com");
    await submit();
    expect(rec.otp.mock.calls[0][0].options).toMatchObject({ shouldCreateUser: false });
    expect(text(document.body)).toContain("We sent a sign-in link to owner@example.com.");
  });

  it("other errors are shown as Supabase words them", async () => {
    rec.otp.mockResolvedValue({ error: { code: "over_email_send_rate_limit", message: "Email rate limit exceeded" } });
    await mount(<SignIn />);
    await type("#email", "owner@example.com");
    await submit();
    expect(alert()).toBe("Email rate limit exceeded");
  });

  it(`after ${ATTEMPT_LIMIT} tries in 15 minutes the browser stops asking`, async () => {
    rec.otp.mockResolvedValue({ error: { code: "otp_disabled", message: "Signups not allowed for otp" } });
    await mount(<SignIn />);
    for (let i = 0; i < ATTEMPT_LIMIT; i++) { await type("#email", `guess${i}@example.com`); await submit(); }
    expect(rec.otp).toHaveBeenCalledTimes(ATTEMPT_LIMIT);
    await type("#email", "one-more@example.com");
    await submit();
    expect(rec.otp).toHaveBeenCalledTimes(ATTEMPT_LIMIT);
    expect(alert()).toMatch(/^Too many tries from this browser\. Try again in \d+ minutes\.$/);
  });
});

describe("registration: /register?code=...", () => {
  const fill = async (email = "friend@example.com") => {
    await type("#name", "Fran");
    await type("#email", email);
  };

  it("takes the code from the link, and it can be typed instead", async () => {
    expect(codeFromUrl("?code=ABCD-1234-ABCD-1234")).toBe("ABCD-1234-ABCD-1234");
    await mount(<Register search="?code=ABCD-1234-ABCD-1234" />);
    expect(q<HTMLInputElement>("#code")!.value).toBe("ABCD-1234-ABCD-1234");
    await mount(<Register search="" />);
    expect(q<HTMLInputElement>("#code")!.value).toBe("");
    expect(q<HTMLButtonElement>("button.primary")!.disabled).toBe(true);
  });

  it("a wrong email is refused before any email is sent", async () => {
    rec.check.mockResolvedValue({ ok: false, reason: "email_mismatch" });
    await mount(<Register search="?code=ABCD-1234-ABCD-1234" />);
    await fill("someone-else@example.com");
    await submit();
    expect(rec.check).toHaveBeenCalledWith("ABCD-1234-ABCD-1234", "someone-else@example.com");
    expect(rec.otp).not.toHaveBeenCalled();
    expect(alert()).toBe(REASON.email_mismatch);
  });

  it.each(["unknown", "revoked", "used", "expired"] as const)("a %s code is refused in words, and nothing is sent", async (reason) => {
    rec.check.mockResolvedValue({ ok: false, reason });
    await mount(<Register search="?code=ABCD-1234-ABCD-1234" />);
    await fill();
    await submit();
    expect(rec.otp).not.toHaveBeenCalled();
    expect(alert()).toBe(REASON[reason]);
  });

  it("a good code and email: Supabase's own email creates the account, with the name and the code", async () => {
    rec.check.mockResolvedValue({ ok: true, reason: null });
    rec.otp.mockResolvedValue({ error: null });
    await mount(<Register search="?code=ABCD-1234-ABCD-1234" />);
    await fill();
    await submit();
    expect(rec.otp).toHaveBeenCalledTimes(1);
    const arg = rec.otp.mock.calls[0][0];
    expect(arg.email).toBe("friend@example.com");
    expect(arg.options).toMatchObject({ shouldCreateUser: true, data: { name: "Fran", invite_code: "ABCD-1234-ABCD-1234" } });
    expect(text(document.body)).toContain("We sent a link to friend@example.com.");
    expect(text(document.body)).toContain("After that, sign in any time with your email");
  });

  it("the database's refusal (0045's trigger) is explained", async () => {
    rec.check.mockResolvedValue({ ok: true, reason: null });
    rec.otp.mockResolvedValue({ error: { code: "unexpected_failure", message: "Database error saving new user" } });
    await mount(<Register search="?code=ABCD-1234-ABCD-1234" />);
    await fill();
    await submit();
    expect(alert()).toBe(REFUSED);
  });

  it("before migration 0045 is applied: a notice, and nothing is sent", async () => {
    rec.check.mockResolvedValue(null);
    await mount(<Register search="?code=ABCD-1234-ABCD-1234" />);
    await fill();
    await submit();
    expect(rec.otp).not.toHaveBeenCalled();
    expect(alert()).toBe(NOT_OPEN);
  });
});

describe("the invite card", () => {
  const made = { email: "friend@example.com", code: "ABCD-1234-ABCD-1234", link: "/register?code=ABCD-1234-ABCD-1234",
    expires_at: "2026-12-01T12:00:00Z", reused: false };

  it("creates an invite and shows the link, the code, copy buttons and a mailto share", async () => {
    rec.list.mockResolvedValue({ cap: 5, taken: 2, invites: [] });
    rec.create.mockResolvedValue(made);
    await mount(<InviteCard />);
    await settle();
    expect(text(document.body)).toContain("3 of 5 places left.");
    expect(text(document.body)).toContain("their sign-in email from kaibo when they register");
    await type("#inv-email", " friend@example.com ");
    await submit();
    expect(rec.create).toHaveBeenCalledWith("friend@example.com");
    expect(q<HTMLInputElement>("#inv-link")!.value).toBe(`${window.location.origin}/register?code=ABCD-1234-ABCD-1234`);
    expect(text(document.body)).toContain("ABCD-1234-ABCD-1234");
    expect(text(document.body)).toContain("they get their sign-in email when they register");
    expect(all("button").map((b) => text(b))).toEqual(expect.arrayContaining(["Copy link", "Copy code"]));
    const share = q<HTMLAnchorElement>('a[href^="mailto:"]')!;
    expect(share.href).toBe(mailto(made, `${window.location.origin}${made.link}`));
    const body = decodeURIComponent(share.href.split("body=")[1]);
    expect(body).toContain("ABCD-1234-ABCD-1234");
    expect(body).toContain("expires in 7 days");
  });

  it("no places left: the create button is off", async () => {
    rec.list.mockResolvedValue({ cap: 5, taken: 5, invites: [] });
    await mount(<InviteCard />);
    await settle();
    await type("#inv-email", "x@example.com");
    expect(q<HTMLButtonElement>("button.primary")!.disabled).toBe(true);
    expect(text(document.body)).toContain("0 of 5 places left.");
  });

  it("the server's refusal is shown", async () => {
    rec.list.mockResolvedValue({ cap: 5, taken: 1, invites: [] });
    rec.create.mockRejectedValue(new Error("this email has an account already"));
    await mount(<InviteCard />);
    await settle();
    await type("#inv-email", "owner@example.com");
    await submit();
    expect(alert()).toBe("this email has an account already");
  });

  it("before migration 0045 is applied: a notice instead of the form", async () => {
    rec.list.mockResolvedValue(null);
    await mount(<InviteCard />);
    await settle();
    expect(text(document.body)).toContain(NOT_APPLIED);
    expect(q("#inv-email")).toBeNull();
  });

  // #635: revoking an open invite (migration 0046's kb_invite_revoke and kb_invite_list's can_revoke)
  const row = (id: number, email: string, status: string, can_revoke?: boolean) => ({ id, email, status, can_revoke,
    created_at: "2026-11-24T12:00:00Z", expires_at: "2026-12-01T12:00:00Z", used_at: null,
    code: status === "open" ? "ABCD-1234-ABCD-1234" : null, mine: true });
  const revokeButtons = () => all("button").filter((b) => text(b) === "Revoke");

  it("Revoke shows only where the list allows it, asks once more, then sends the id and refreshes the list", async () => {
    rec.list.mockResolvedValueOnce({ cap: 5, taken: 3, invites: [row(7, "open@example.com", "open", true),
      row(6, "used@example.com", "used", false), row(5, "old@example.com", "expired", false)] });
    rec.list.mockResolvedValue({ cap: 5, taken: 2, invites: [row(7, "open@example.com", "revoked", false),
      row(6, "used@example.com", "used", false), row(5, "old@example.com", "expired", false)] });
    rec.revoke.mockResolvedValue({ id: 7, email: "open@example.com", status: "revoked", revoked_at: "2026-11-25T09:00:00Z" });
    await mount(<InviteCard />);
    await settle();
    expect(revokeButtons()).toHaveLength(1);
    expect(revokeButtons()[0].getAttribute("aria-label")).toBe("Revoke invite for open@example.com");
    await doing(() => { revokeButtons()[0].click(); });
    expect(rec.revoke).not.toHaveBeenCalled();  // asked first
    const keep = all("button").find((b) => text(b) === "Keep")!;
    await doing(() => { keep.click(); });
    expect(rec.revoke).not.toHaveBeenCalled();
    await doing(() => { revokeButtons()[0].click(); });
    await doing(() => { all("button").find((b) => text(b) === "Yes, revoke")!.click(); });
    await settle();
    expect(rec.revoke).toHaveBeenCalledWith(7);
    expect(text(q('[role="status"]'))).toBe("Invite for open@example.com revoked. Its link and code no longer work.");
    expect(rec.list).toHaveBeenCalledTimes(2);
    expect(revokeButtons()).toHaveLength(0);
    expect(all("tbody tr").map((tr) => text(tr.children[1]))).toEqual(["revoked", "registered", "expired"]);
  });

  it("before migration 0046: no Revoke button (the list sends no can_revoke)", async () => {
    rec.list.mockResolvedValue({ cap: 5, taken: 2, invites: [row(7, "open@example.com", "open")] });
    await mount(<InviteCard />);
    await settle();
    expect(text(document.body)).toContain("open@example.com");
    expect(revokeButtons()).toHaveLength(0);
    expect(all("th").length).toBe(3);
  });

  it("the server's refusal, or the migration missing, is shown and the invite stays", async () => {
    rec.list.mockResolvedValue({ cap: 5, taken: 2, invites: [row(7, "open@example.com", "open", true)] });
    rec.revoke.mockRejectedValueOnce(new Error("no open invite of yours with that id"));
    rec.revoke.mockResolvedValueOnce(null);
    await mount(<InviteCard />);
    await settle();
    await doing(() => { revokeButtons()[0].click(); });
    await doing(() => { all("button").find((b) => text(b) === "Yes, revoke")!.click(); });
    await settle();
    expect(alert()).toBe("no open invite of yours with that id");
    await doing(() => { revokeButtons()[0].click(); });
    await doing(() => { all("button").find((b) => text(b) === "Yes, revoke")!.click(); });
    await settle();
    expect(alert()).toBe(REVOKE_NOT_APPLIED);
    expect(rec.list).toHaveBeenCalledTimes(1);
    expect(revokeButtons()).toHaveLength(1);
  });
});

describe("an invited member's page", () => {
  it("shows their name, the invite card and sign-out; no run, headline or control", async () => {
    rec.list.mockResolvedValue({ cap: 5, taken: 3, invites: [] });
    await mount(<Member name="Fran" />);
    await settle();
    const t = text(document.body);
    expect(t).toContain("Welcome, Fran");
    expect(t).toContain("Invite someone");
    expect(t).not.toMatch(/headline|kill|pause|stop trading|positions/i);
  });

  it("reads nothing but its own invites (the database refuses everything else anyway)", async () => {
    const src = (await import("./pages/Member.tsx?raw")).default as string;
    expect(src).not.toMatch(/\bapi\.|useRunCtx|Headlines|Controls/);
  });
});

describe("lib/attempts", () => {
  it("allows the limit in the window, then says how long to wait", () => {
    const store = new Map<string, string>();
    const s = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); } };
    const t0 = 1_000_000;
    for (let i = 0; i < ATTEMPT_LIMIT; i++) expect(takeAttempt(t0 + i, s).ok).toBe(true);
    const no = takeAttempt(t0 + 10, s);
    expect(no.ok).toBe(false);
    if (!no.ok) expect(no.waitMs).toBe(ATTEMPT_WINDOW_MS - 10);
    expect(takeAttempt(t0 + ATTEMPT_WINDOW_MS + 1, s).ok).toBe(true);
  });

  it("a broken store never blocks", () => {
    const s = { getItem: () => "not json", setItem: () => { throw new Error("full"); } };
    expect(takeAttempt(1, s).ok).toBe(true);
    expect(takeAttempt(1, undefined).ok).toBe(true);
  });

  it("reads Supabase's answers", () => {
    expect(isNoAccount({ code: "otp_disabled", message: "Signups not allowed for otp" })).toBe(true);
    expect(isNoAccount({ message: "Signups not allowed for otp" })).toBe(true);
    expect(isNoAccount({ code: "over_email_send_rate_limit", message: "Email rate limit exceeded" })).toBe(false);
    expect(isSignupRefused({ message: "Database error saving new user" })).toBe(true);
    expect(isSignupRefused({ message: "Email rate limit exceeded" })).toBe(false);
  });
});
