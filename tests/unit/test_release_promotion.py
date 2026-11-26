"""M20 9.3 (#727): deploys follow `main`. The engine image is built from a release tag, carries its build in every
heartbeat, and refuses to start a build its host did not promote.

- `scripts/build_image.sh <tag>` builds from the tag's tree (`git archive`), never the working directory, and only a
  release (annotated, on main: `release.sh --verify`, the pin's own rule). The image carries `KAIBOT_CODE_SHA`
  ("<tag>@<short>") and `KAIBOT_BUILD_COMMIT` (the full commit).
- `scripts/release.sh <tag>` writes the host's promotion, `data/live/promoted` ("<tag> <full commit>"); `main` and
  `--clear` remove it.
- `scripts/engine_entry.sh` (the image's CMD): with `KAIBOT_REQUIRE_PROMOTED=1` an image whose commit is not the
  promoted one does not start (exit 6, nothing run).
- The deploy and roll-back drill, played on the scripts: promote and build rel-1, promote rel-2 (the old image is
  refused), build rel-2, roll back to rel-1 (the new image is refused, the old one starts again).
- `engine/__main__.make_beat`: every heartbeat names the build, also a "starting" or "error" beat.

No docker here: a fake `docker` on PATH records the build and lists the context it was given.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
G = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}
OUTSIDE = {"KAIBOT_FAKE_ET": "6 1200"}  # a Saturday: promotions are allowed


def sh(args, cwd, env=None, check=False, stdin=None):
    e = {k: v for k, v in os.environ.items() if not k.startswith("KAIBOT_")}
    e.update(env or {})
    r = subprocess.run(args, cwd=cwd, env=e, capture_output=True, text=True, timeout=60, input=stdin)
    if check:
        assert r.returncode == 0, r.stdout + r.stderr
    return r


def git(r, *args):
    return sh(["git", *args], r, G, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A repo with the real scripts and two CI-style releases on main, a fake docker and a fake python on PATH."""
    r = tmp_path / "kaibot"
    (r / "scripts").mkdir(parents=True)
    for n in ("release.sh", "build_image.sh", "engine_entry.sh"):
        shutil.copy(SCRIPTS / n, r / "scripts" / n)
    (r / ".gitignore").write_text("data/\n")
    (r / "Dockerfile").write_text("FROM scratch\n")
    (r / "file.txt").write_text("one\n")
    git(r, "init", "-q", "-b", "main")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "one")
    git(r, "tag", "-a", "rel-20261201-1", "-m", "release 1")
    (r / "file.txt").write_text("two\n")
    git(r, "commit", "-qam", "two")
    git(r, "tag", "-a", "rel-20261201-2", "-m", "release 2")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    # docker build reads the context tar from stdin ("-"): list its files, so the test can see what was built
    (bin_ / "docker").write_text(f"""#!/bin/sh
echo "$@" >> {tmp_path}/docker.calls
case "$1" in
  build) tar -tf - | sort > {tmp_path}/context.txt; echo sha256:built ;;
  image) echo sha256:abc123 ;;
esac
exit 0
""")
    (bin_ / "python").write_text(f'#!/bin/sh\necho "$@" > {tmp_path}/python.ran\nexit 0\n')
    for f in bin_.iterdir():
        f.chmod(0o755)
    env = {"PATH": f"{bin_}:{os.environ['PATH']}", **G}
    return r, env, tmp_path


def commit_of(r, ref):
    return git(r, "rev-parse", f"{ref}^{{commit}}")


def entry(r, env, tmp, commit=None, label="x", args=(), **kw):
    """The image's start as the container runs it: the build's env, the host's data folder at /data."""
    e = {**env, "KAIBOT_DATA_DIR": str(r / "data" / "live"), "KAIBOT_CODE_SHA": label, **kw}
    if commit is not None:
        e["KAIBOT_BUILD_COMMIT"] = commit
    (tmp / "python.ran").unlink(missing_ok=True)
    return sh(["sh", "scripts/engine_entry.sh", *args], r, e)


# ---------------------------------------------------------------- the promotion record
def test_pinning_a_tag_writes_the_promotion_and_main_or_clear_removes_it(repo):
    r, env, _ = repo
    sh(["bash", "scripts/release.sh", "rel-20261201-1"], r, {**env, **OUTSIDE}, check=True)
    assert (r / "data/live/promoted").read_text().split() == ["rel-20261201-1", commit_of(r, "rel-20261201-1")]
    sh(["bash", "scripts/release.sh", "--latest"], r, {**env, **OUTSIDE}, check=True)
    assert (r / "data/live/promoted").read_text().split()[0] == "rel-20261201-2"
    shown = sh(["bash", "scripts/release.sh", "--show"], r, env, check=True).stdout
    assert f"promoted:    rel-20261201-2 {commit_of(r, 'rel-20261201-2')}" in shown
    sh(["bash", "scripts/release.sh", "main"], r, {**env, **OUTSIDE}, check=True)
    assert not (r / "data/live/promoted").exists()
    sh(["bash", "scripts/release.sh", "rel-20261201-1"], r, {**env, **OUTSIDE}, check=True)
    sh(["bash", "scripts/release.sh", "--clear"], r, env, check=True)
    assert not (r / "data/live/promoted").exists()


def test_a_refused_pin_leaves_the_promotion_as_it_was(repo):
    r, env, _ = repo
    sh(["bash", "scripts/release.sh", "rel-20261201-1"], r, {**env, **OUTSIDE}, check=True)
    git(r, "tag", "light")
    x = sh(["bash", "scripts/release.sh", "light"], r, {**env, **OUTSIDE})
    assert x.returncode == 1 and (r / "data/live/promoted").read_text().split()[0] == "rel-20261201-1"


def test_verify_says_whether_a_tag_is_a_release(repo):
    r, env, _ = repo
    sh(["bash", "scripts/release.sh", "--verify", "rel-20261201-1"], r, env, check=True)
    git(r, "tag", "light")
    x = sh(["bash", "scripts/release.sh", "--verify", "light"], r, env)
    assert x.returncode == 1 and "lightweight tag" in x.stdout
    x = sh(["bash", "scripts/release.sh", "--verify", "nope"], r, env)
    assert x.returncode == 1 and "no tag 'nope'" in x.stdout


# ---------------------------------------------------------------- the image is built from the tag
def test_the_image_is_built_from_the_tags_tree_with_its_build_named(repo):
    r, env, tmp = repo
    (r / "file.txt").write_text("an edit nobody committed\n")
    (r / "untracked.py").write_text("print('not in any release')\n")
    x = sh(["bash", "scripts/build_image.sh", "rel-20261201-1"], r, env, check=True)
    assert x.stdout.strip() == "sha256:abc123"  # the image id, last and alone on stdout
    build = [c for c in (tmp / "docker.calls").read_text().splitlines() if c.startswith("build")][-1]
    c1 = commit_of(r, "rel-20261201-1")
    assert f"--build-arg CODE_SHA=rel-20261201-1@{c1[:7]}" in build
    assert f"--build-arg BUILD_COMMIT={c1}" in build
    assert f"org.opencontainers.image.revision={c1}" in build
    assert "-t kaibot-engine:rel-20261201-1" in build and "-t kaibot-engine:latest" in build and build.endswith(" -")
    context = (tmp / "context.txt").read_text().split()
    assert "untracked.py" not in context and "file.txt" in context
    shown = sh(["git", "show", "rel-20261201-1:file.txt"], r, check=True).stdout
    assert shown == "one\n"  # (the archive is the tag's tree, not the edited working copy)


@pytest.mark.parametrize("make", ["lightweight", "off_main", "missing"])
def test_only_a_release_is_built(repo, make):
    r, env, tmp = repo
    if make == "lightweight":
        git(r, "tag", "rel-20261201-9")
    elif make == "off_main":
        git(r, "checkout", "-q", "-b", "side")
        (r / "side.txt").write_text("unreviewed\n")
        git(r, "add", "-A")
        git(r, "commit", "-qm", "side")
        git(r, "tag", "-a", "rel-20261201-9", "-m", "x")
        git(r, "checkout", "-q", "main")
    x = sh(["bash", "scripts/build_image.sh", "rel-20261201-9"], r, env)
    assert x.returncode == 1 and "Refused" in x.stderr and "No image was built" in x.stderr
    assert not (tmp / "docker.calls").exists()


def test_latest_builds_the_newest_release_and_test_builds_head_as_unreleased(repo):
    r, env, tmp = repo
    sh(["bash", "scripts/build_image.sh", "--latest"], r, env, check=True)
    assert "-t kaibot-engine:rel-20261201-2" in (tmp / "docker.calls").read_text()
    sh(["bash", "scripts/build_image.sh", "--test"], r, env, check=True)
    build = (tmp / "docker.calls").read_text().splitlines()[-2]
    assert f"CODE_SHA=unreleased@{commit_of(r, 'HEAD')[:7]}" in build and "kaibot-engine:test" in build


# ---------------------------------------------------------------- the image's start
def test_without_the_requirement_every_build_starts_as_before(repo):
    r, env, tmp = repo
    x = entry(r, env, tmp, commit="unknown", args=("--check",))
    assert x.returncode == 0 and (tmp / "python.ran").read_text().split() == ["-m", "kaibot.engine", "--check"]
    assert "promotion not required" in x.stderr


@pytest.mark.parametrize("case", ["no_record", "other_commit", "no_build_commit", "unknown_build_commit"])
def test_an_unpromoted_build_is_refused_with_exit_6_and_nothing_starts(repo, case):
    r, env, tmp = repo
    c1, c2 = commit_of(r, "rel-20261201-1"), commit_of(r, "rel-20261201-2")
    if case != "no_record":
        sh(["bash", "scripts/release.sh", "rel-20261201-2"], r, {**env, **OUTSIDE}, check=True)
    commit = {"no_record": c2, "other_commit": c1, "no_build_commit": None, "unknown_build_commit": "unknown"}[case]
    x = entry(r, env, tmp, commit=commit, KAIBOT_REQUIRE_PROMOTED="1")
    assert x.returncode == 6 and "REFUSED:" in x.stderr and "Nothing was started" in x.stderr
    assert not (tmp / "python.ran").exists()
    if case == "other_commit":
        assert f"the host promoted rel-20261201-2 (commit {c2})" in x.stderr


def test_the_promoted_build_starts_and_verify_only_starts_nothing(repo):
    r, env, tmp = repo
    sh(["bash", "scripts/release.sh", "rel-20261201-2"], r, {**env, **OUTSIDE}, check=True)
    c2 = commit_of(r, "rel-20261201-2")
    x = entry(r, env, tmp, commit=c2, label=f"rel-20261201-2@{c2[:7]}", KAIBOT_REQUIRE_PROMOTED="1")
    assert x.returncode == 0 and (tmp / "python.ran").read_text().split() == ["-m", "kaibot.engine"]
    assert "is rel-20261201-2, the release this host promoted" in x.stderr
    x = entry(r, env, tmp, commit=c2, args=("--verify-only",), KAIBOT_REQUIRE_PROMOTED="1")
    assert x.returncode == 0 and not (tmp / "python.ran").exists()


def test_the_read_only_check_runs_on_any_build(repo):
    r, env, tmp = repo
    x = entry(r, env, tmp, commit="unknown", args=("--check",), KAIBOT_REQUIRE_PROMOTED="1")
    assert x.returncode == 0 and (tmp / "python.ran").read_text().split() == ["-m", "kaibot.engine", "--check"]


def test_the_requirement_is_read_from_the_env_file_and_a_bad_value_is_refused(repo, tmp_path):
    r, env, tmp = repo
    c1 = commit_of(r, "rel-20261201-1")
    env_file = tmp_path / "secrets.env"
    env_file.write_text("ALPACA_API_KEY=x\nexport KAIBOT_REQUIRE_PROMOTED=\"1\"  # the VM\n")
    x = entry(r, env, tmp, commit=c1, KAIBOT_ENV_FILE=str(env_file))
    assert x.returncode == 6 and "nothing is promoted on this host" in x.stderr
    x = entry(r, env, tmp, commit=c1, KAIBOT_ENV_FILE=str(env_file), KAIBOT_REQUIRE_PROMOTED="0")
    assert x.returncode == 0  # the process environment wins, as for every switch the scripts read
    env_file.write_text("KAIBOT_REQUIRE_PROMOTED=yes\n")
    x = entry(r, env, tmp, commit=c1, KAIBOT_ENV_FILE=str(env_file))
    assert x.returncode == 6 and "is not 0 or 1" in x.stderr  # a typo never switches the check off


# ---------------------------------------------------------------- the deploy and roll-back drill
def test_deploy_and_rollback_drill(repo):
    """The ticket's check, on the scripts: each step is what docs/ops/cloud_move.md tells the owner to run."""
    r, env, tmp = repo
    req = {"KAIBOT_REQUIRE_PROMOTED": "1"}
    c1, c2 = commit_of(r, "rel-20261201-1"), commit_of(r, "rel-20261201-2")

    def build(tag):
        sh(["bash", "scripts/build_image.sh", tag], r, env, check=True)
        b = [c for c in (tmp / "docker.calls").read_text().splitlines() if c.startswith("build")][-1]
        return b.split("BUILD_COMMIT=")[1].split()[0]

    def promote(tag):
        sh(["bash", "scripts/release.sh", tag], r, {**env, **OUTSIDE}, check=True)

    img1 = build("rel-20261201-1")  # 1. deploy rel-1
    assert img1 == c1 and entry(r, env, tmp, commit=img1, **req).returncode == 6  # built but not promoted
    promote("rel-20261201-1")
    assert entry(r, env, tmp, commit=img1, **req).returncode == 0
    promote("rel-20261201-2")  # 2. deploy rel-2: until its image is built, the running build is refused at a restart
    assert entry(r, env, tmp, commit=img1, **req).returncode == 6
    img2 = build("rel-20261201-2")
    assert img2 == c2 and entry(r, env, tmp, commit=img2, **req).returncode == 0
    promote("rel-20261201-1")  # 3. roll back: the newer image no longer starts, the older one does again
    assert entry(r, env, tmp, commit=img2, **req).returncode == 6
    assert entry(r, env, tmp, commit=img1, **req).returncode == 0
    history = (r / "data/release_history.log").read_text().splitlines()
    assert [ln.split()[1] for ln in history] == ["rel-20261201-1", "rel-20261201-2", "rel-20261201-1"]


# ---------------------------------------------------------------- the image's files
def test_the_dockerfile_bakes_the_build_and_starts_through_the_entry():
    text = (ROOT / "Dockerfile").read_text()
    assert "ARG BUILD_COMMIT=unknown" in text and "KAIBOT_BUILD_COMMIT=${BUILD_COMMIT}" in text
    assert "COPY scripts/engine_entry.sh ./scripts/engine_entry.sh" in text
    assert text.rstrip().splitlines()[-1] == 'CMD ["sh", "/app/scripts/engine_entry.sh"]'
    run_engine = (SCRIPTS / "run_engine.sh").read_text()
    assert "kaibot-engine sh /app/scripts/engine_entry.sh" in run_engine
    assert '--build-arg BUILD_COMMIT="$(git rev-parse HEAD' in run_engine


# ---------------------------------------------------------------- the build in every heartbeat
class _Store:
    def __init__(self):
        self.beats = []

    def heartbeat(self, process, detail):
        self.beats.append(detail)


class _Lease:
    degraded = None

    def renew(self):
        return True

    def status(self):
        return "ok"


class _Eng:
    process = "engine"
    code = "rel-20261201-2@abc1234"

    def __init__(self):
        self.store = _Store()

    def trading_detail(self):
        return {"trading_state": "active"}


@pytest.mark.parametrize("status", [{"state": "starting"}, {"state": "error", "error": "boom"},
                                    {"state": "trading", "code_sha": "rel-20261201-2@abc1234"}])
def test_every_heartbeat_names_the_build(status):
    from kaibot.engine import __main__ as engine_main

    eng = _Eng()
    engine_main.make_beat(eng, _Lease(), "kaibot-engine-vm", {"now": False})(status)
    assert eng.store.beats[-1]["code_sha"] == "rel-20261201-2@abc1234"
    assert eng.store.beats[-1]["state"] == status["state"] and eng.store.beats[-1]["host"] == "kaibot-engine-vm"
