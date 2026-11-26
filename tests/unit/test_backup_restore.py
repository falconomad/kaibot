"""Audit 2 ticket 7.5 (#715): the nightly backup includes the research stores and the runner's done.json, writes an
encrypted off-box copy when one is set up (never a plain one), and the restore drill rebuilds the engine and the
runner state from it in a temporary folder and checks them.

No network and no real secret: the off-box folder is a temp folder, the age key is made here (a stand-in `age`
when the binary is not installed, so the test never skips), openssl is the system's."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from kaibot.engine.journal import Journal, JournalStore

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"

FAKE_AGE = r"""#!/bin/bash
# stand-in for age: -R recipients -o out in | -d -i identity -o out in; "encrypts" by prefixing a header
set -e
if [ "$1" = -d ]; then id="$3"; out="$5"; in="$6"; grep -q AGE-SECRET-KEY "$id"; tail -c +9 "$in" > "$out"
else r="$2"; out="$4"; in="$5"; grep -q '^age1' "$r"; { printf 'FAKEAGE\n'; cat "$in"; } > "$out"; fi
"""


def sh(args, cwd, env, check=False):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("KAIBOT_", "HEALTHCHECKS_"))}
    e.update(env)
    r = subprocess.run(args, cwd=cwd, env=e, capture_output=True, text=True, timeout=120)
    if check and r.returncode != 0:
        raise AssertionError(r.stdout + r.stderr)
    return r


@pytest.fixture
def mac(tmp_path):
    """A checkout with an engine journal, the research stores and a runner checkout with done.json."""
    home = tmp_path / "home"
    r = home / "Projects" / "kaibot"
    (r / "scripts").mkdir(parents=True)
    for n in ("backup_local.sh", "restore_drill.sh"):
        shutil.copy(SCRIPTS / n, r / "scripts")
    live = r / "data" / "live"
    (live / "history").mkdir(parents=True)
    (live / "history" / "SPY.parquet").write_text("cache")
    (live / "engine_holder_id").write_text("holder-1\n")
    st = JournalStore(Journal(live / "journal.sqlite"), live=True)
    run = st.insert("run", {"amount_usd": 5000, "status": "running", "level": "balanced"})
    st.insert("signal", {"run_id": run["id"], "symbol": "A", "mode": "live"})
    st.journal.close()
    d = r / "data"
    (d / "fundamentals" / "facts").mkdir(parents=True)
    pq.write_table(pa.table({"cik": ["1", "2", "3"], "value": [1.0, 2.0, 3.0]}),
                   d / "fundamentals" / "facts" / "cik=0000000001.parquet")
    (d / "fundamentals" / "build.json").write_text("{}")
    (d / "fundamentals" / "raw" / "companyfacts").mkdir(parents=True)
    (d / "fundamentals" / "raw" / "companyfacts" / "CIK1.json.gz").write_text("big download cache")
    (d / "listings").mkdir()
    (d / "listings" / "ipos.parquet").write_text("x")
    (d / "reports" / "gate").mkdir(parents=True)
    (d / "reports" / "gate" / "r.md").write_text("report")
    (d / "alarm").mkdir()
    (d / "alarm" / "top10.csv").write_text("month,rank\n")
    (d / "daily").mkdir()
    (d / "daily" / "SPY.parquet").write_text("downloaded again")
    (d / "trials.jsonl").write_text('{"id": "a", "n_trials": 3}\n{"id": "b"}\n')
    runner = home / "Projects" / "kaibot-runner" / "data" / "runner"
    runner.mkdir(parents=True)
    (runner / "done.json").write_text(json.dumps({"job-1": {"ok": True}, "job-2": {"ok": False}}))
    (runner / "healthchecks_url").write_text("https://hc-ping.com/secret\n")
    secrets = home / "Projects" / "kaibot-secrets"
    secrets.mkdir()
    (secrets / ".env").write_text("ALPACA_API_SECRET=never-in-a-backup\n")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    if not shutil.which("age"):
        (bin_ / "age").write_text(FAKE_AGE)
        (bin_ / "age").chmod(0o755)
    env = {"HOME": str(home), "KAIBOT_LOG_DIR": str(tmp_path / "svc"),
           "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}"}
    return r, env, home


def names(archive: Path) -> list[str]:
    with tarfile.open(archive) as t:
        return t.getnames()


def age_key(home: Path) -> Path:
    """An age identity outside the checkout; its public key in the secrets folder."""
    ident = home.parent / "usb" / "kaibot-backup.agekey"
    ident.parent.mkdir()
    if shutil.which("age-keygen"):
        subprocess.run(["age-keygen", "-o", str(ident)], check=True, capture_output=True)
        pub = subprocess.run(["age-keygen", "-y", str(ident)], check=True, capture_output=True, text=True).stdout
    else:
        ident.write_text("AGE-SECRET-KEY-1FAKE\n")
        pub = "age1fakepublickey\n"
    (home / "Projects" / "kaibot-secrets" / "backup_age_recipients.txt").write_text(pub)
    return ident


def drill(r, env, *args):
    """restore_drill.py from this repo (its code and the clone source), the paths as restore_drill.sh gives them."""
    home = Path(env["HOME"])
    return sh([sys.executable, str(SCRIPTS / "restore_drill.py"), "--backup-dir",
               str(home / "Projects" / "kaibot-backups"), *args], r, env)


# ---------------------------------------------------------------- what the archive holds


def test_the_archive_has_the_research_stores_done_json_and_a_manifest_and_no_secret(mac):
    r, env, home = mac
    x = sh(["bash", "scripts/backup_local.sh"], r, env, check=True)
    assert "off-box copy: not set up" in x.stdout
    (arch,) = (home / "Projects" / "kaibot-backups").glob("kaibot-*.tar.gz")
    got = names(arch)
    for want in ("kaibot/data/live/journal.sqlite", "kaibot/data/live/engine_holder_id",
                 "kaibot/data/fundamentals/facts/cik=0000000001.parquet", "kaibot/data/fundamentals/build.json",
                 "kaibot/data/listings/ipos.parquet", "kaibot/data/reports/gate/r.md", "kaibot/data/alarm/top10.csv",
                 "kaibot/data/trials.jsonl", "kaibot/runner/data/runner/done.json", "kaibot/MANIFEST.sha256"):
        assert want in got, want
    assert not any(s in n for n in got for s in ("history", "fundamentals/raw", "data/daily", "healthchecks_url",
                                                 ".env", "kaibot-secrets"))
    with tarfile.open(arch) as t:
        man = t.extractfile("kaibot/MANIFEST.sha256").read().decode()
        files = {m.name.removeprefix("kaibot/") for m in t.getmembers() if m.isfile()}
    listed = {ln.split(None, 1)[1] for ln in man.splitlines()}
    assert listed == files - {"MANIFEST.sha256"}


def test_setup_prints_the_commands_and_changes_nothing(mac):
    r, env, home = mac
    x = sh(["bash", "scripts/backup_local.sh", "--setup"], r, env, check=True)
    for s in ("age-keygen -o", "backup_age_recipients.txt", "KAIBOT_BACKUP_OFFBOX_DIR=", "com~apple~CloudDocs",
              "/Volumes/", "restore_drill.sh --offbox --identity", "Secrets"):
        assert s in x.stdout, s
    assert not (home / "Projects" / "kaibot-backups").exists()


# ---------------------------------------------------------------- the off-box copy


def test_the_off_box_copy_is_age_encrypted_and_the_drill_restores_it(mac, tmp_path):
    r, env, home = mac
    ident = age_key(home)
    off = tmp_path / "icloud" / "kaibot backups"  # a space, as iCloud Drive's path has
    (home / "Projects" / "kaibot-secrets" / ".env").write_text(f'KAIBOT_BACKUP_OFFBOX_DIR="{off}"\n')
    x = sh(["bash", "scripts/backup_local.sh"], r, env, check=True)
    (enc,) = off.glob("kaibot-*.tar.gz.age")
    assert "off-box copy:" in x.stdout and b"holder-1" not in enc.read_bytes()
    assert not list(off.glob("*.tar.gz"))  # never a plain copy
    y = drill(r, env, "--offbox", "--offbox-dir", str(off), "--identity", str(ident), "--no-code")
    assert y.returncode == 0, y.stdout + y.stderr
    assert "PASSED" in y.stdout and "decrypted .age copy" in y.stdout


def test_the_openssl_copy_without_age_and_the_drill_restores_it(mac, tmp_path):
    r, env, home = mac
    pf = home / "Projects" / "kaibot-secrets" / "backup.pass"
    pf.write_text("correct horse battery staple\n")
    off = tmp_path / "disk"
    env = {**env, "KAIBOT_BACKUP_OFFBOX_DIR": str(off)}
    sh(["bash", "scripts/backup_local.sh"], r, env, check=True)
    (enc,) = off.glob("kaibot-*.tar.gz.enc")
    y = drill(r, env, "--offbox", "--offbox-dir", str(off), "--passfile", str(pf), "--no-code")
    assert y.returncode == 0 and "decrypted .enc copy" in y.stdout, y.stdout + y.stderr
    pf.write_text("a wrong passphrase\n")
    assert drill(r, env, "--offbox", "--offbox-dir", str(off), "--passfile", str(pf), "--no-code").returncode == 1


def test_no_key_means_no_off_box_copy_and_a_failure_the_daily_job_reports(mac, tmp_path):
    r, env, home = mac
    off = tmp_path / "icloud"
    x = sh(["bash", "scripts/backup_local.sh"], r, {**env, "KAIBOT_BACKUP_OFFBOX_DIR": str(off)})
    assert x.returncode == 1 and "nothing is copied in plain" in x.stderr
    assert not off.exists() or not any(off.iterdir())
    assert list((home / "Projects" / "kaibot-backups").glob("kaibot-*.tar.gz"))  # the local archive is kept


@pytest.mark.parametrize("where", ["checkout", "runner"])
def test_a_key_inside_a_checkout_is_refused(mac, tmp_path, where):
    r, env, home = mac
    inside = r if where == "checkout" else home / "Projects" / "kaibot-runner"
    pf = inside / "backup.pass"
    pf.write_text("pw\n")
    x = sh(["bash", "scripts/backup_local.sh"], r,
           {**env, "KAIBOT_BACKUP_OFFBOX_DIR": str(tmp_path / "off"), "KAIBOT_BACKUP_PASSFILE": str(pf)})
    assert x.returncode == 1 and "inside the checkout" in x.stderr
    assert not (tmp_path / "off").exists()


def test_an_unreachable_off_box_folder_fails_loudly(mac, tmp_path):
    r, env, home = mac
    (home / "Projects" / "kaibot-secrets" / "backup.pass").write_text("pw\n")
    blocker = tmp_path / "not-a-disk"
    blocker.write_text("a file where the folder should be")
    x = sh(["bash", "scripts/backup_local.sh"], r, {**env, "KAIBOT_BACKUP_OFFBOX_DIR": str(blocker / "kaibot")})
    assert x.returncode == 1 and "cannot make" in x.stderr


# ---------------------------------------------------------------- the restore drill


def test_the_drill_rebuilds_the_engine_at_its_release_and_the_runner_state(mac):
    r, env, home = mac
    sh(["bash", "scripts/backup_local.sh"], r, env, check=True)
    into = home / "restore"
    y = drill(r, env, "--into", str(into), "--keep")
    assert y.returncode == 0, y.stdout + y.stderr
    out = y.stdout
    for s in ("PASSED", "manifest:", "every sha256 matches", "engine code: checked out HEAD", "engine holder id",
              "journal: verified and folded", "run 1", "signal 1", "trials.jsonl: 2 rows",
              "fundamentals: 1 parquet files, 3 rows", "runner: done.json restored", "2 done job ids"):
        assert s in out, s
    assert (into / "kaibot" / "src" / "kaibot" / "engine" / "journal.py").is_file()  # the code, cloned
    assert (into / "kaibot" / "data" / "live" / "journal.sqlite").is_file()
    assert json.loads((into / "kaibot-runner" / "data" / "runner" / "done.json").read_text())["job-1"]["ok"]
    # the live install is untouched: no file appeared in the checkout's data/
    assert sorted(p.name for p in (r / "data").iterdir()) == ["alarm", "daily", "fundamentals", "listings", "live",
                                                              "reports", "trials.jsonl"]


def test_the_drill_finds_a_changed_file_and_a_damaged_journal(mac, tmp_path):
    r, env, home = mac
    sh(["bash", "scripts/backup_local.sh"], r, env, check=True)
    (arch,) = (home / "Projects" / "kaibot-backups").glob("kaibot-*.tar.gz")
    work = tmp_path / "edit"
    with tarfile.open(arch) as t:
        t.extractall(work, filter="data")
    db = sqlite3.connect(work / "kaibot" / "data" / "live" / "journal.sqlite")
    db.execute("drop trigger entry_no_update")  # a damaged disk, not SQL
    db.execute("update entry set body = replace(body, 'balanced', 'hunter') where seq = (select max(seq) - 1 "
               "from entry)")
    db.commit()
    db.close()
    (work / "kaibot" / "runner" / "data" / "runner" / "done.json").write_text("[not json")
    bad = tmp_path / "kaibot-bad.tar.gz"
    with tarfile.open(bad, "w:gz") as t:
        t.add(work / "kaibot", arcname="kaibot")
    y = drill(r, env, "--archive", str(bad), "--no-code")
    assert y.returncode == 1, y.stdout
    assert "FAIL  manifest:" in y.stdout and "2 changed" in y.stdout
    assert "FAIL  journal: JournalCorrupt" in y.stdout
    assert "FAIL  runner: done.json does not parse" in y.stdout


def test_the_drill_refuses_an_archive_that_writes_outside_its_folder(mac, tmp_path):
    r, env, home = mac
    evil = tmp_path / "kaibot-evil.tar.gz"
    (tmp_path / "x").write_text("x")
    with tarfile.open(evil, "w:gz") as t:
        t.add(tmp_path / "x", arcname="../escaped")
    y = drill(r, env, "--archive", str(evil), "--no-code")
    assert y.returncode == 1 and "refused archive member" in y.stdout
    assert not (tmp_path / "escaped").exists()


def test_the_wrapper_passes_the_folders_from_the_env_file(mac, tmp_path):
    r, env, home = mac
    (r / "scripts" / "restore_drill.py").symlink_to(SCRIPTS / "restore_drill.py")
    y = sh(["bash", "scripts/restore_drill.sh", "--no-code"], r,
           {**env, "KAIBOT_BACKUP_DIR": str(tmp_path / "empty")})
    assert y.returncode == 2 and f"no backup found in {tmp_path / 'empty'}" in y.stderr
