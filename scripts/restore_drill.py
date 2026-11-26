"""Restore drill (audit 2 ticket 7.5, #715): rebuild the engine and the research runner's state from a backup in a
temporary folder, and check them. Read-only towards the live install: it never writes to ~/Projects/kaibot's data/,
never starts an engine, a watchdog or the runner, and never talks to the broker or Supabase.

    ./scripts/restore_drill.sh                         # the newest local archive (~/Projects/kaibot-backups)
    ./scripts/restore_drill.sh --offbox --identity /Volumes/USB/kaibot-backup.agekey   # the newest off-box copy
    ./scripts/restore_drill.sh --archive <file> [--identity F | --passfile F] [--keep] [--into DIR] [--no-code]

Steps (docs/ops/backup_restore.md, "Restore drill"):
1. Decrypt (an off-box `.age` copy with the age identity, an `.enc` copy with the openssl passphrase file) into the
   temporary folder, and unpack.
2. Every file against MANIFEST.sha256 (written by backup_local.sh): none missing, none changed, none extra.
3. The engine: a fresh clone of this checkout at the backup's pinned release (data/RELEASE, else HEAD) in
   <tmp>/kaibot, the restored data/ laid into it; then the journal is opened with the engine's own `Journal`
   (structure and every entry's checksum) and folded with `JournalStore` as at a start (tables and row counts,
   journal id, last entry). The engine holder id must be there.
4. The research stores: trials.jsonl read as the gate reads it (rows, configurations, unreadable lines); the
   fundamentals parquet row count; listings, reports and alarm file counts.
5. The runner: <tmp>/kaibot-runner/data/runner/done.json parses and lists its job ids.
Exit 0 when every check passed, 1 when one failed (each failure is printed), 2 for a usage error. The temporary
folder is deleted unless --keep. The drill result is printed as one block to paste into the drill record.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

HOME = Path.home()
SECRETS = HOME / "Projects" / "kaibot-secrets"


class Drill:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.failures: list[str] = []

    def ok(self, what: str) -> None:
        self.lines.append(f"ok    {what}")

    def fail(self, what: str) -> None:
        self.lines.append(f"FAIL  {what}")
        self.failures.append(what)

    def note(self, what: str) -> None:
        self.lines.append(f"      {what}")


def newest(folder: Path, patterns: tuple[str, ...]) -> Path | None:
    found = [p for pat in patterns for p in folder.glob(pat) if p.is_file()]
    return max(found, key=lambda p: (p.stat().st_mtime, p.name)) if found else None


def decrypt(src: Path, out: Path, identity: Path | None, passfile: Path | None) -> None:
    if src.name.endswith(".age"):
        if identity is None:
            raise SystemExit(f"{src.name} is age-encrypted: give --identity <the age key file>")
        cmd = ["age", "-d", "-i", str(identity), "-o", str(out), str(src)]
    elif src.name.endswith(".enc"):
        pf = passfile or SECRETS / "backup.pass"
        cmd = ["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-md", "sha256",
               "-pass", f"file:{pf}", "-in", str(src), "-out", str(out)]
    else:
        shutil.copyfile(src, out)
        return
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"could not decrypt {src.name}: {r.stderr.strip()[-300:]}")


def unpack(archive: Path, into: Path) -> Path:
    with tarfile.open(archive) as t:
        for m in t.getmembers():  # an archive of ours has only kaibot/... files and folders
            if not (m.isfile() or m.isdir()) or m.name.startswith(("/", "..")) or "/../" in f"/{m.name}/":
                raise RuntimeError(f"refused archive member {m.name!r}")
        if hasattr(tarfile, "data_filter"):
            t.extractall(into, filter="data")
        else:
            t.extractall(into)
    top = into / "kaibot"
    if not top.is_dir():
        raise RuntimeError("the archive has no kaibot/ folder")
    return top


def check_manifest(top: Path, d: Drill) -> None:
    man = top / "MANIFEST.sha256"
    if not man.is_file():
        d.fail("MANIFEST.sha256 missing (an archive from before #715 has none: run backup_local.sh again)")
        return
    want: dict[str, str] = {}
    for line in man.read_text().splitlines():
        if line.strip():
            h, name = line.split(None, 1)
            want[name.strip().lstrip("*")] = h
    have = {str(p.relative_to(top)) for p in top.rglob("*") if p.is_file() and p.name != "MANIFEST.sha256"}
    missing, extra = sorted(set(want) - have), sorted(have - set(want))
    bad = [n for n in sorted(set(want) & have)
           if hashlib.sha256((top / n).read_bytes()).hexdigest() != want[n]]
    if missing or extra or bad:
        d.fail(f"manifest: {len(missing)} missing, {len(bad)} changed, {len(extra)} not listed "
               f"(first: {(missing + bad + extra)[:3]})")
    else:
        d.ok(f"manifest: {len(want)} files, every sha256 matches")


def rebuild_code(top: Path, work: Path, d: Drill) -> Path:
    """A clone of this checkout at the backup's release, with the restored data/ in it."""
    rel = (top / "data" / "RELEASE").read_text().strip() if (top / "data" / "RELEASE").is_file() else ""
    dest = work / "kaibot"
    r = subprocess.run(["git", "clone", "-q", "--no-checkout", str(ROOT), str(dest)], capture_output=True, text=True)
    if r.returncode != 0:
        d.fail(f"engine code: git clone of {ROOT} failed: {r.stderr.strip()[-200:]}")
        dest.mkdir(parents=True, exist_ok=True)
    else:
        ref = rel or "HEAD"
        r = subprocess.run(["git", "-C", str(dest), "checkout", "-q", "--detach", ref], capture_output=True,
                           text=True)
        if r.returncode != 0:
            d.fail(f"engine code: the pinned release {ref!r} is not in this checkout ({r.stderr.strip()[-160:]})")
        else:
            sha = subprocess.run(["git", "-C", str(dest), "rev-parse", "--short", "HEAD"], capture_output=True,
                                 text=True).stdout.strip()
            d.ok(f"engine code: checked out {('release ' + rel) if rel else 'HEAD (no release pinned)'} at {sha}")
    shutil.copytree(top / "data", dest / "data", dirs_exist_ok=True)
    return dest


def check_engine(data: Path, d: Drill) -> None:
    live = data / "live"
    holder = live / "engine_holder_id"
    if holder.is_file() and holder.read_text().strip():
        d.ok("engine holder id present")
    else:
        d.fail("engine holder id (data/live/engine_holder_id) missing: a restored engine would take a new identity")
    for db in sorted(live.glob("*.sqlite")) if live.is_dir() else []:
        if db.name != "journal.sqlite":
            d.note(f"{db.name}: present ({db.stat().st_size} bytes)")
    journal = live / "journal.sqlite"
    if not journal.is_file():
        d.fail("journal (data/live/journal.sqlite) missing")
        return
    try:
        from kaibot.engine.journal import Journal, JournalStore

        j = Journal(journal)  # structure and every checksum, as at an engine start (exit code 4 if not)
        store = JournalStore(j, remote=None, live=True)
        tables = {t: len(rows) for t, rows in store.tables.items() if rows}
        last = j.db.execute("select coalesce(max(seq), 0), max(ts) from entry").fetchone()
        j.close()
    except Exception as e:  # noqa: BLE001 - every failure is the drill's finding
        d.fail(f"journal: {type(e).__name__}: {e}")
        return
    d.ok(f"journal: verified and folded; id {j.journal_id[:12]}, epoch {j.epoch}, {last[0]} entries, "
         f"last {last[1] or 'none'}")
    d.note("tables: " + (", ".join(f"{t} {n}" for t, n in sorted(tables.items())) or "none"))


def check_research(data: Path, d: Drill) -> None:
    from kaibot.evaluation import trials

    tf = data / "trials.jsonl"
    if tf.is_file():
        rows, bad = trials.read_jsonl(tf)
        msg = f"trials.jsonl: {len(rows)} rows, {trials.count_configurations(rows, bad)} configurations"
        (d.fail if bad else d.ok)(msg + (f", {bad} unreadable lines" if bad else ""))
    else:
        d.fail("trials.jsonl missing: the gate's trial count would restart from Supabase's rows only")
    facts = sorted((data / "fundamentals" / "facts").glob("*.parquet"))
    if facts:
        try:
            import pyarrow.parquet as pq

            n = sum(pq.ParquetFile(f).metadata.num_rows for f in facts)
            d.ok(f"fundamentals: {len(facts)} parquet files, {n} rows")
        except Exception as e:  # noqa: BLE001
            d.fail(f"fundamentals: a parquet file does not read: {e}")
    elif (data / "fundamentals").is_dir():
        d.note("fundamentals: no facts/*.parquet (older store layout); files: "
               f"{sum(1 for p in (data / 'fundamentals').rglob('*') if p.is_file())}")
    else:
        d.fail("fundamentals store missing")
    for name in ("listings", "reports", "alarm"):
        p = data / name
        n = sum(1 for f in p.rglob("*") if f.is_file()) if p.is_dir() else 0
        (d.ok if n else d.fail)(f"{name}: {n} files")


def check_runner(top: Path, work: Path, d: Drill) -> None:
    src = top / "runner" / "data" / "runner" / "done.json"
    if not src.is_file():
        d.fail("runner: done.json missing (every queued job would run again)")
        return
    dest = work / "kaibot-runner" / "data" / "runner"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest / "done.json")
    try:
        done = json.loads((dest / "done.json").read_text())
    except ValueError as e:
        d.fail(f"runner: done.json does not parse: {e}")
        return
    if not isinstance(done, dict):
        d.fail(f"runner: done.json is a {type(done).__name__}, the runner reads a mapping of job ids")
        return
    d.ok(f"runner: done.json restored to {dest}, {len(done)} done job ids")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--archive", type=Path, help="a backup file (.tar.gz, .tar.gz.age or .tar.gz.enc)")
    ap.add_argument("--offbox", action="store_true", help="the newest copy in KAIBOT_BACKUP_OFFBOX_DIR")
    ap.add_argument("--backup-dir", type=Path, help="the local archives (default ~/Projects/kaibot-backups)")
    ap.add_argument("--offbox-dir", type=Path, default=None, help="the off-box folder")
    ap.add_argument("--identity", type=Path, help="the age identity (key) file, for an .age copy")
    ap.add_argument("--passfile", type=Path, help="the openssl passphrase file, for an .enc copy")
    ap.add_argument("--into", type=Path, help="restore here (an empty or new folder) instead of a temporary one")
    ap.add_argument("--keep", action="store_true", help="keep the restored folder")
    ap.add_argument("--no-code", action="store_true", help="skip the clone of the engine's code")
    a = ap.parse_args(argv)

    src = a.archive
    if src is None:
        if a.offbox:
            folder = a.offbox_dir
            if folder is None:
                print("--offbox needs --offbox-dir (restore_drill.sh passes KAIBOT_BACKUP_OFFBOX_DIR)",
                      file=sys.stderr)
                return 2
            src = newest(folder, ("kaibot-*.tar.gz.age", "kaibot-*.tar.gz.enc"))
        else:
            folder = a.backup_dir or HOME / "Projects" / "kaibot-backups"
            src = newest(folder, ("kaibot-*.tar.gz",))
        if src is None:
            print(f"no backup found in {folder}", file=sys.stderr)
            return 2
    if not src.is_file():
        print(f"no such file: {src}", file=sys.stderr)
        return 2
    if a.into is not None and a.into.exists() and any(a.into.iterdir()):
        print(f"--into {a.into} is not empty", file=sys.stderr)
        return 2

    work = a.into or Path(tempfile.mkdtemp(prefix="kaibot-restore-"))
    work.mkdir(parents=True, exist_ok=True)
    d = Drill()
    age_h = (time.time() - src.stat().st_mtime) / 3600
    d.note(f"backup: {src} ({src.stat().st_size} bytes, written {age_h:.1f} h ago)")
    if age_h > 36:
        d.fail(f"the newest backup is {age_h:.0f} hours old (the daily job writes one every night)")
    try:
        plain = work / "backup.tar.gz"
        decrypt(src, plain, a.identity, a.passfile)
        if src.name.endswith((".age", ".enc")):
            d.ok(f"decrypted {src.suffix} copy")
        top = unpack(plain, work / "unpacked")
        plain.unlink()
        check_manifest(top, d)
        data = top / "data" if a.no_code else rebuild_code(top, work, d) / "data"
        check_engine(data, d)
        check_research(data, d)
        check_runner(top, work, d)
    except (RuntimeError, tarfile.TarError, OSError) as e:
        d.fail(str(e))
    finally:
        if not a.keep:
            shutil.rmtree(work, ignore_errors=True)
    verdict = "PASSED" if not d.failures else f"FAILED ({len(d.failures)})"
    print(f"restore drill {time.strftime('%Y-%m-%d %H:%M %Z')}: {verdict}")
    print("\n".join(d.lines))
    print(f"restored into {work}" if a.keep else "temporary folder deleted")
    return 0 if not d.failures else 1


if __name__ == "__main__":
    sys.exit(main())
