"""Where a proposed test runs (M12 3.8, #470): a throw-away copy, no credentials, no network, no filesystem.

The generating step (`core.propose`) holds the API key and never executes the model's code. This step executes it
and holds nothing:

- a fresh temporary directory with a copy of `src/kaibot` and the YAML files of `config/` (no `.env`, no `data/`,
  no journal, no git checkout), plus the one test file; a surviving mutant's diff is applied to that copy only;
- an isolated filesystem. Linux: `unshare -rmn` (new user, mount and network namespaces), `pivot_root` into a
  fresh tmpfs with the old root unmounted, and every capability dropped (`setpriv`); the tmpfs holds only
  `/usr` and the Python installation (read-only binds), the copy at `/work`
  (read-only), a writable `/tmp` and four device nodes. The checkout, `$HOME`, `/etc`, every `.env` and the rest
  of the machine do not exist inside. macOS: `sandbox-exec` with a profile that denies the network, every write
  outside the run's scratch folder, and every read under `$HOME`, under the checkout and of any `.env`, then
  allows reads of the copy, the scratch folder and Python again (later rules win; not run in CI, which is Linux).
  Where neither works the run is refused (`SandboxUnavailable`), never done without it;
- an EMPTY environment: PATH, HOME=TMPDIR=/tmp (macOS: the scratch folder), a fixed hash seed. No key variable;
- `python -I`, a CPU and memory limit, a wall-clock timeout, and a bootstrap that, BEFORE any line of the test runs,
  resolves every name the test imports and every attribute chain it reads off an imported name to the module that
  really defines it (`guard.import_spec`) and refuses the run (outcome "refused") when that module is not allowed
  (QA on #470: `from kaibot.evaluation.trials import Path` is pathlib's Path, not Kaibot code). After the test
  module is imported, its globals are checked the same way. Python sockets are disabled too (belt and braces).

The output is returned to the caller, which scrubs it and keeps it out of committed files (`core.triage`). The copy
is deleted afterwards whatever happened.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import site
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from kaibot.agents.testwriter import guard

ROOT = Path(__file__).resolve().parents[4]
TIMEOUT_S = 180
CPU_S = 150
MEM_BYTES = 2 * 1024**3
REFUSED_EXIT = 7

# Linux, inside the namespaces: $1 new root, $2 the copy, then read-only binds up to "--", then the command
LINUX_INNER = r"""
set -eu
NR=$1; W=$2; shift 2
mount -t tmpfs -o size=512m,mode=755 tmpfs "$NR"
mkdir -p "$NR/usr" "$NR/work" "$NR/tmp" "$NR/dev"
chmod 1777 "$NR/tmp"
mount --bind /usr "$NR/usr"
mount -o remount,bind,ro "$NR/usr"
while [ "$1" != "--" ]; do
  mkdir -p "$NR$1"
  mount --bind "$1" "$NR$1"
  mount -o remount,bind,ro "$NR$1"
  shift
done
shift
mount --bind "$W" "$NR/work"
mount -o remount,bind,ro "$NR/work"
for l in bin lib lib32 lib64 sbin; do
  if [ -L "/$l" ]; then ln -s "$(readlink "/$l")" "$NR/$l"; fi
done
for d in null zero urandom random; do
  touch "$NR/dev/$d"
  mount --bind "/dev/$d" "$NR/dev/$d"
done
mkdir "$NR/.old"
cd "$NR"
pivot_root . .old
cd /
# umount(8) needs /proc, which does not exist in here: the interpreter detaches the old root with umount2(MNT_DETACH)
"$1" -I -S -c 'import ctypes, sys; sys.exit(ctypes.CDLL(None, use_errno=True).umount2(b"/.old", 2) != 0)'
rmdir /.old
exec /usr/bin/setpriv --no-new-privs --inh-caps=-all --ambient-caps=-all --bounding-set=-all \
  /usr/bin/env -i PATH=/usr/bin:/bin HOME=/tmp TMPDIR=/tmp PYTHONHASHSEED=0 \
  PYTHONDONTWRITEBYTECODE=1 LANG=C.UTF-8 "$@"
"""
# QA on #470 (second round): a plain `chroot` kept the host's root in the mount namespace, and the test ran as root of
# its user namespace with every capability. `os.chroot` into a subfolder, `fchdir` back and `chdir("..")` reached the
# real filesystem (a planted .env was read and the checkout written); `mount -o remount,rw` could undo the read-only
# binds. Now `pivot_root` makes the tmpfs the namespace's root and the old root is unmounted, so nothing of the host is
# left to climb back to, and `setpriv` drops every capability (no chroot, no mount, no remount) and sets
# no_new_privs before Python starts. A nested user namespace gets its capabilities back, but only over this tree,
# whose binds are then locked read-only.

# Inside the sandbox. argv: src dir, test file, rootdir, scratch dir, the import spec (JSON)
BOOTSTRAP = r"""
import importlib, importlib.util, json, os, socket, sys, types
def _no_net(*a, **k):
    raise OSError("network is disabled in the test sandbox")
socket.socket.connect = _no_net
socket.socket.connect_ex = _no_net
socket.create_connection = _no_net
src, test, rootdir, scratch, spec = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], json.loads(sys.argv[5])
os.chdir(scratch)
sys.path.insert(0, src)
KAIBOT, ROOTS = spec["kaibot"], set(spec["roots"])
DATA = (int, float, complex, str, bytes, bool, type(None), tuple, list, dict, set, frozenset)

def mod_ok(name):
    if not name:
        return False
    top = name.split(".")[0]
    if top == "kaibot":
        return any(name == k or name.startswith(k + ".") or k.startswith(name + ".") for k in KAIBOT)
    return top in ROOTS or (top.startswith("_") and top.lstrip("_") in ROOTS)

def origin(v):
    if isinstance(v, types.ModuleType):
        return v.__name__
    if isinstance(v, DATA):
        return "data"
    if isinstance(v, type) or callable(v):
        return getattr(v, "__module__", None)
    return type(v).__module__

def refuse(why):
    print("REFUSED: " + why)
    sys.stdout.flush()
    os._exit(7)

def check(label, v):
    if spec.get("unchecked"):
        return
    o = origin(v)
    if o == "data":
        return
    if o in ("builtins", None) or not mod_ok(o):
        refuse(f"{label} comes from {o!r}, which a proposed test may not use")

bound = {}
for imp in spec["imports"]:
    if not mod_ok(imp["module"]) and not spec.get("unchecked"):
        refuse(f"import of {imp['module']}")
    m = importlib.import_module(imp["module"])
    if imp.get("names") is None:
        check(imp["module"], m)
        bound[imp["bind"]] = m if imp.get("aliased") else importlib.import_module(imp["module"].split(".")[0])
        continue
    for name, alias in imp["names"]:
        try:
            v = getattr(m, name)
        except AttributeError:
            v = importlib.import_module(imp["module"] + "." + name)
        check(f"{imp['module']}.{name}", v)
        bound[alias] = v
for chain in spec["chains"]:
    if chain[0] not in bound:
        continue
    v = bound[chain[0]]
    for i, attr in enumerate(chain[1:], 1):
        try:
            v = getattr(v, attr)
        except AttributeError:
            break
        check(".".join(chain[: i + 1]), v)

ts = importlib.util.spec_from_file_location("test_candidate", test)
tm = importlib.util.module_from_spec(ts)
ts.loader.exec_module(tm)
for k, v in vars(tm).items():
    if k.startswith("__") or (callable(v) and getattr(v, "__module__", None) == "test_candidate"):
        continue
    check(f"global {k}", v)

import pytest
sys.exit(pytest.main([test, "-q", "-x", "--no-header", "-p", "no:cacheprovider", "-p", "no:randomly",
                      "-o", "addopts=", "--rootdir", rootdir]))
"""


class SandboxUnavailable(RuntimeError):
    """No way to isolate the network and the filesystem here: proposed tests are not run on this machine."""


@dataclass(frozen=True)
class RunResult:
    outcome: str  # passed | failed | refused | error | timeout | mutant_not_applied
    exit_code: int | None
    output: str  # the last lines of the output (raw: the caller scrubs it before keeping it anywhere)


def python_dirs() -> list[str]:
    """Where the interpreter and its packages live outside /usr (bound read-only into the Linux sandbox)."""
    dirs = {os.path.realpath(p) for p in (sys.prefix, sys.base_prefix, sys.exec_prefix, *site.getsitepackages(),
                                          os.path.dirname(sys.executable))}
    out: list[str] = []
    for d in sorted(dirs, key=len):
        if d == "/" or not os.path.isdir(d) or d == "/usr" or d.startswith("/usr/"):
            continue
        if not any(d == o or d.startswith(o + "/") for o in out):
            out.append(d)
    return out


def mac_profile(work: str, scratch: str, home: str, root: str, pydirs: list[str]) -> str:
    """The sandbox-exec profile (later rules win): no network; no writes but to scratch; no reads under $HOME, under
    the checkout or of any .env; then reads of the copy, the scratch folder and Python are allowed again."""
    def q(p):
        return json.dumps(os.path.realpath(p))

    allow_read = " ".join(f"(subpath {q(p)})" for p in [work, scratch, *pydirs])
    return "\n".join([
        "(version 1)", "(allow default)", "(deny network*)", "(deny file-write*)",
        f'(allow file-write* (subpath {q(scratch)}) (literal "/dev/null"))',
        f'(deny file-read* (subpath {q(home)}) (subpath {q(root)}) (regex #"/\\.env($|\\.)"))',
        f"(allow file-read* {allow_read})",
    ])


def _linux_ok() -> bool:
    if not (sys.platform.startswith("linux") and all(shutil.which(t) for t in ("unshare", "pivot_root", "setpriv"))):
        return False
    probe = tempfile.mkdtemp(prefix="kb-cand-probe-")
    try:
        r = subprocess.run(["unshare", "-rmn", "--fork", "sh", "-c", f'mount -t tmpfs tmpfs "{probe}"'],
                           capture_output=True, timeout=10)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        shutil.rmtree(probe, ignore_errors=True)


def isolation(platform: str | None = None) -> str:
    """"linux" or "darwin": the isolation this machine supports, or SandboxUnavailable."""
    platform = platform or sys.platform
    if platform.startswith("linux") and _linux_ok():
        return "linux"
    if platform == "darwin" and shutil.which("sandbox-exec"):
        return "darwin"
    raise SandboxUnavailable(f"no network and filesystem isolation on {platform}: needs unshare with mount "
                             "namespaces, pivot_root and setpriv (Linux) or sandbox-exec (macOS)")


def _limits():  # pragma: no cover - runs in the child
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (CPU_S, CPU_S))
    with contextlib.suppress(ValueError, OSError):  # macOS does not support RLIMIT_AS
        resource.setrlimit(resource.RLIMIT_AS, (MEM_BYTES, MEM_BYTES))
    os.setsid()


def _copy_tree(work: Path, root: Path) -> None:
    shutil.copytree(root / "src" / "kaibot", work / "src" / "kaibot",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".env", ".env.*"))
    (work / "config").mkdir()
    for f in sorted((root / "config").glob("*.yaml")):
        shutil.copy2(f, work / "config" / f.name)


def _apply(work: Path, diff: str) -> bool:
    """Apply a mutant's unified diff to the copy (paths `src/...`, with or without a/ b/ prefixes)."""
    patch = work.parent / "mutant.diff"
    patch.write_text(diff if diff.endswith("\n") else diff + "\n")
    for p in ("-p0", "-p1"):
        r = subprocess.run(["git", "apply", p, str(patch)], cwd=work, capture_output=True, text=True,
                           env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
        if r.returncode == 0:
            return True
    return False


def run_test(code: str, *, module: str | None, mutant_diff: str | None = None, root: Path = ROOT,
             timeout_s: float = TIMEOUT_S, mode: str | None = None, resolve_imports: bool = True) -> RunResult:
    """Run one proposed test of `module` (the module under test) in a fresh, isolated copy. `mode` defaults to
    `isolation()` (raises SandboxUnavailable). `resolve_imports=False` only in the sandbox's own tests: they prove
    the filesystem and network boundary holds even for code whose imports were never checked."""
    mode = isolation() if mode is None else mode
    base = Path(tempfile.mkdtemp(prefix="kb-cand-"))
    work, scratch, newroot = base / "work", base / "scratch", base / "root"
    try:
        for d in (work, scratch, newroot):
            d.mkdir()
        _copy_tree(work, root)
        if mutant_diff is not None and not _apply(work, mutant_diff):
            return RunResult("mutant_not_applied", None, "the mutant's diff does not apply to this checkout")
        (work / "test_candidate.py").write_text(code)
        try:
            spec = guard.import_spec(code, module, work)
            if not resolve_imports:
                spec = {**spec, "unchecked": True}
        except SyntaxError as e:
            return RunResult("refused", None, f"does not parse: {e}"[:300])
        if mode == "linux":
            args = ["/work/src", "/work/test_candidate.py", "/work", "/tmp", json.dumps(spec)]
            cmd = ["unshare", "-rmn", "--fork", "sh", "-c", LINUX_INNER, "sandbox", str(newroot), str(work),
                   *python_dirs(), "--", sys.executable, "-I", "-c", BOOTSTRAP, *args]
            env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"}
        elif mode == "darwin":
            prof = mac_profile(str(work), str(scratch), os.path.expanduser("~"), str(root), python_dirs())
            args = [str(work / "src"), str(work / "test_candidate.py"), str(work), str(scratch), json.dumps(spec)]
            cmd = ["sandbox-exec", "-p", prof, sys.executable, "-I", "-c", BOOTSTRAP, *args]
            env = {"PATH": "/usr/bin:/bin", "HOME": str(scratch), "TMPDIR": str(scratch), "PYTHONHASHSEED": "0",
                   "PYTHONDONTWRITEBYTECODE": "1", "LANG": "C.UTF-8"}
        else:
            raise SandboxUnavailable(f"unknown sandbox mode {mode!r}")
        try:
            p = subprocess.run(cmd, cwd=scratch, env=env, capture_output=True, text=True, timeout=timeout_s,
                               preexec_fn=_limits)
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            return RunResult("timeout", None, out[-2000:])
        tail = (p.stdout + p.stderr)[-2000:]
        outcome = {0: "passed", 1: "failed", REFUSED_EXIT: "refused"}.get(p.returncode, "error")
        return RunResult(outcome, p.returncode, tail)
    finally:
        shutil.rmtree(base, ignore_errors=True)
