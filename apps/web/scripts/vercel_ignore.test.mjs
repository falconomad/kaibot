// Vercel's ignored build step (vercel.json: ignoreCommand -> scripts/vercel_ignore.sh): exit 0 skips the build,
// exit 1 builds. Each case makes a small repository, clones it the way Vercel does (--depth=10) and runs the script
// in the clone's apps/web, which is the project's Root Directory.
import { execFileSync, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));
const script = resolve(here, "vercel_ignore.sh");
const env = { ...process.env, GIT_AUTHOR_NAME: "t", GIT_AUTHOR_EMAIL: "t@example.invalid", GIT_COMMITTER_NAME: "t", GIT_COMMITTER_EMAIL: "t@example.invalid", GIT_CONFIG_GLOBAL: "/dev/null", GIT_CONFIG_SYSTEM: "/dev/null" };
const git = (cwd, ...args) => execFileSync("git", args, { cwd, env, encoding: "utf8" }).trim();

/** A repository with one first commit that has both an engine file and a dashboard file. */
function repo() {
  const dir = mkdtempSync(join(tmpdir(), "kb-ignore-"));
  git(dir, "init", "-q", "-b", "main");
  commit(dir, { "src/engine.py": "1", "apps/web/src/a.ts": "1", "apps/web/vercel.json": "{}", "apps/web/api/marks.ts": "1" });
  return dir;
}
/** Writes the files and commits them; returns the new commit's SHA. */
function commit(dir, files) {
  for (const [path, text] of Object.entries(files)) {
    mkdirSync(dirname(join(dir, path)), { recursive: true });
    writeFileSync(join(dir, path), text);
  }
  git(dir, "add", "-A");
  git(dir, "commit", "-q", "-m", Object.keys(files).join(" "));
  return git(dir, "rev-parse", "HEAD");
}
/** Clones as Vercel does and runs the script in apps/web. `prev` is VERCEL_GIT_PREVIOUS_SHA (undefined: not set). */
function run(dir, prev) {
  const clone = mkdtempSync(join(tmpdir(), "kb-ignore-clone-"));
  git(clone, "clone", "-q", "--depth=10", pathToFileURL(dir).href, ".");
  const e = { ...env };
  delete e.VERCEL_GIT_PREVIOUS_SHA;
  if (prev !== undefined) e.VERCEL_GIT_PREVIOUS_SHA = prev;
  const r = spawnSync("bash", [script], { cwd: join(clone, "apps/web"), env: e, encoding: "utf8" });
  return { code: r.status, out: r.stdout };
}
const SKIP = 0;
const BUILD = 1;

describe("the ignored build step", () => {
  it("skips a commit that changes only engine files", () => {
    const dir = repo();
    const deployed = git(dir, "rev-parse", "HEAD");
    commit(dir, { "src/engine.py": "2", "migrations/0001.sql": "x" });
    expect(run(dir, deployed)).toMatchObject({ code: SKIP });
  });

  it.each([
    ["the app", "apps/web/src/a.ts"],
    ["an API function", "apps/web/api/marks.ts"],
    ["vercel.json", "apps/web/vercel.json"],
    ["a new file", "apps/web/public/new.txt"],
  ])("builds when %s changed", (_what, path) => {
    const dir = repo();
    const deployed = git(dir, "rev-parse", "HEAD");
    commit(dir, { [path]: "changed" });
    expect(run(dir, deployed)).toMatchObject({ code: BUILD });
  });

  it("judges a push of several commits as a whole, not by its last commit", () => {
    const dir = repo();
    const deployed = git(dir, "rev-parse", "HEAD");
    commit(dir, { "apps/web/src/a.ts": "2" });
    commit(dir, { "src/engine.py": "2" });
    commit(dir, { "docs/note.md": "x" });
    expect(run(dir, deployed)).toMatchObject({ code: BUILD });
  });

  it("skips when a dashboard change was undone again", () => {
    const dir = repo();
    const deployed = git(dir, "rev-parse", "HEAD");
    commit(dir, { "apps/web/src/a.ts": "2" });
    commit(dir, { "apps/web/src/a.ts": "1" });
    expect(run(dir, deployed)).toMatchObject({ code: SKIP });
  });

  it("builds a branch's first deployment (no earlier commit to compare with)", () => {
    const dir = repo();
    commit(dir, { "src/engine.py": "2" });
    expect(run(dir, undefined)).toMatchObject({ code: BUILD });
    expect(run(dir, "")).toMatchObject({ code: BUILD });
  });

  it("builds when the last deployed commit is not in the shallow clone", () => {
    const dir = repo();
    const deployed = git(dir, "rev-parse", "HEAD");
    for (let i = 0; i < 12; i++) commit(dir, { "src/engine.py": `e${i}` });
    expect(run(dir, deployed)).toMatchObject({ code: BUILD });
    expect(run(dir, "0123456789abcdef0123456789abcdef01234567")).toMatchObject({ code: BUILD });
    expect(run(dir, "not-a-sha")).toMatchObject({ code: BUILD });
  });

  it("still compares with a commit nine back, the edge of the clone", () => {
    const dir = repo();
    const deployed = git(dir, "rev-parse", "HEAD");
    for (let i = 0; i < 9; i++) commit(dir, { "src/engine.py": `e${i}` });
    expect(run(dir, deployed)).toMatchObject({ code: SKIP });
  });

  it("is what vercel.json runs", () => {
    const config = JSON.parse(readFileSync(resolve(here, "../vercel.json"), "utf8"));
    expect(config.ignoreCommand).toBe("bash scripts/vercel_ignore.sh");
  });

  // git.deploymentEnabled: a branch that matches a `false` pattern gets no deployment at all, so it cannot use up the
  // day's deployments. Every rule here is `team/<name>/**: false`, which is why a prefix check is a faithful reading.
  describe("the branches Vercel does not deploy", () => {
    const rules = JSON.parse(readFileSync(resolve(here, "../vercel.json"), "utf8")).git.deploymentEnabled;
    const off = (branch) => Object.keys(rules).some((pattern) => branch.startsWith(pattern.slice(0, -2)));

    it("are listed only as whole team folders switched off", () => {
      for (const [pattern, on] of Object.entries(rules)) {
        expect(pattern).toMatch(/^team\/[a-z-]+\/\*\*$/);
        expect(on).toBe(false);
      }
    });
    it.each(["main", "team/frontend/health-labels", "team/design/logo", "team/product/status", "fe/night-pot"])("%s is still deployed", (branch) => {
      expect(off(branch)).toBe(false);
    });
    it.each(["team/backend-quant/m9-1.2", "team/quant/x", "team/platform/y", "team/data/383", "team/agents-ai/360", "team/qa-ops/drill", "team/test-eng/z"])("%s is not deployed", (branch) => {
      expect(off(branch)).toBe(true);
    });
  });
});
