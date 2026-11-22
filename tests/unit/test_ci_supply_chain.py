"""M12 ticket 3.6 (#468): the supply-chain rules of the build, held by a test so that a later workflow cannot
quietly drop them. `zizmor` and `pip-audit` run in the `supply-chain` workflow; this file needs no network.

- actions are pinned by commit (40 hex digits), with the tag in a comment;
- every workflow sets `permissions` at the top, read-only; every write permission is listed here by name;
- no workflow uses a secret or names a production key (Alpaca, Supabase service role, Anthropic);
- a checkout keeps no credentials; a `pull_request_target` workflow never checks out or runs anything;
- everything installed is installed from a lock: hash-pinned requirements, `npm ci`, a base image by digest.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
# the only write permissions any job may hold: (workflow file, job) -> {scope: "write"}
WRITE_ALLOWED = {("labeler.yml", "label"): {"pull-requests": "write"},
                 ("stateful.yml", "weekly-random-seeds"): {"issues": "write"},  # opens the issue with the seed
                 ("dst.yml", "nightly-soak"): {"issues": "write"},  # the same, for the simulation's failing seed
                 ("replay.yml", "report"): {"issues": "write"},  # the same, for a nightly replay's difference
                 ("fuzz.yml", "target"): {"issues": "write"},  # the same, for a fuzzing finding
                 ("mutation.yml", "report"): {"issues": "write"}}  # rewrites the mutation tracking issue
PINNED = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}\s+#\s*v?\d[\w.]*\s*$")
PRODUCTION_KEYS = re.compile(r"APCA|ALPACA|SERVICE_ROLE|SERVICE_KEY|ANTHROPIC_API_KEY|SUPABASE_ACCESS|VERCEL_TOKEN",
                             re.I)


def _load(path: Path) -> dict:
    doc = yaml.safe_load(path.read_text())
    doc["on"] = doc.pop(True, doc.get("on"))  # YAML reads the key `on` as the boolean true
    return doc


def _steps(doc: dict):
    for job, spec in doc["jobs"].items():
        for step in spec.get("steps") or []:
            yield job, step


def test_there_are_workflows_to_check():
    assert {p.name for p in WORKFLOWS} >= {"ci.yml", "labeler.yml", "supply-chain.yml"}


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_actions_are_pinned_by_commit_with_the_tag_in_a_comment(path):
    uses = [line.split("uses:", 1)[1].strip() for line in path.read_text().splitlines()
            if re.match(r"^\s*(- )?uses:", line)]
    loose = [u for u in uses if not u.startswith("./") and not PINNED.match(u)]
    assert not loose, f"{path.name}: not pinned by commit sha (and tag comment): {loose}"
    parsed = [s["uses"] for _, s in _steps(_load(path)) if "uses" in s]
    assert len(parsed) == len(uses), "every `uses:` of the file was seen"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_the_token_is_read_only_by_default(path):
    doc = _load(path)
    assert "permissions" in doc, f"{path.name}: no top-level permissions (the token would get the repository default)"
    assert doc["permissions"] in ({}, {"contents": "read"}), f"{path.name}: top-level {doc['permissions']}"
    for job, spec in doc["jobs"].items():
        perms = spec.get("permissions")
        if perms is None:
            continue
        assert isinstance(perms, dict), f"{path.name}:{job}: permissions must be listed, not `{perms}`"
        writes = {k: v for k, v in perms.items() if v != "read" and v != "none"}
        assert writes == WRITE_ALLOWED.get((path.name, job), {}), f"{path.name}:{job}: write permissions {writes}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_secret_and_no_production_key_is_used(path):
    text = "\n".join(line.split("#", 1)[0] for line in path.read_text().splitlines())  # comments may name them
    secrets = set(re.findall(r"secrets\.([A-Za-z0-9_]+)", text)) - {"GITHUB_TOKEN"}
    assert not secrets, f"{path.name}: secrets in CI: {sorted(secrets)}"
    assert "secrets: inherit" not in text
    assert not PRODUCTION_KEYS.search(text), f"{path.name}: names a production key: {PRODUCTION_KEYS.search(text)[0]}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_checkouts_keep_no_credentials_and_fork_triggers_run_nothing(path):
    doc = _load(path)
    on = doc["on"]
    triggers = set(on if isinstance(on, list | dict) else [on])
    for job, step in _steps(doc):
        if str(step.get("uses", "")).startswith("actions/checkout@"):
            assert (step.get("with") or {}).get("persist-credentials") is False, \
                f"{path.name}:{job}: checkout must set persist-credentials: false"
            assert not triggers & {"pull_request_target", "workflow_run"}, \
                f"{path.name}:{job}: a checkout under a trigger that holds the base repository's token"
        if triggers & {"pull_request_target", "workflow_run"}:
            assert "run" not in step, f"{path.name}:{job}: runs a command under {sorted(triggers)}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_installs_come_from_a_lock(path):
    for job, step in _steps(_load(path)):
        run = str(step.get("run", ""))
        assert not re.search(r"\buvx\b|\bpipx\b|\bnpm (install|i)\b|curl[^\n]*\|\s*(ba)?sh", run), \
            f"{path.name}:{job}: installs something that is not in a lock: {run[:120]}"
        for line in run.splitlines():
            if re.search(r"\bpip install\b", line):
                assert "--require-hashes" in line, f"{path.name}:{job}: pip install without --require-hashes"
            if re.search(r"\buv sync\b", line):
                assert "--locked" in line or "--frozen" in line, f"{path.name}:{job}: uv sync without --locked"
            if "uv run --project" in line:
                assert "--locked" in line, f"{path.name}:{job}: uv run --project without --locked"


@pytest.mark.parametrize("name", ["requirements-engine.txt", "requirements-research.txt"])
def test_every_requirement_is_hash_pinned(name):
    text = (ROOT / name).read_text()
    blocks = [b for b in re.split(r"\n(?=\S)", text) if b.strip() and not b.lstrip().startswith("#")]
    assert len(blocks) > 10
    for b in blocks:
        head = b.splitlines()[0]
        assert re.match(r"^[A-Za-z0-9_.\[\]-]+==[^\s;]+", head), f"{name}: not an exact pin: {head}"
        assert "--hash=sha256:" in b, f"{name}: no hash for {head}"


def test_the_image_and_the_scripts_install_only_with_hashes():
    docker = (ROOT / "Dockerfile").read_text()
    froms = re.findall(r"^FROM\s+(\S+)", docker, flags=re.M)
    assert froms and all(re.search(r"@sha256:[0-9a-f]{64}$", f) for f in froms), f"base image not by digest: {froms}"
    files = [ROOT / "Dockerfile", *sorted((ROOT / "scripts").glob("*.sh"))]
    for path in files:
        for line in path.read_text().splitlines():
            if re.search(r"\bpip3? install\b", line.split("#", 1)[0]) and "echo" not in line:
                assert "--require-hashes" in line, f"{path.name}: pip install without --require-hashes: {line.strip()}"


def test_the_audit_tools_are_locked_with_hashes():
    lock = (ROOT / ".github" / "audit" / "uv.lock").read_text()
    packages = re.findall(r'^\[\[package\]\]\nname = "([^"]+)"\nversion = "([^"]+)"', lock, flags=re.M)
    names = {n for n, _ in packages}
    assert {"pip-audit", "zizmor"} <= names
    body = lock.split("[[package]]")[1:]
    unhashed = [re.search(r'name = "([^"]+)"', b)[1] for b in body
                if 'source = { virtual = "." }' not in b and "hash = \"sha256:" not in b]
    assert not unhashed, f"audit tools without a hash: {unhashed}"
