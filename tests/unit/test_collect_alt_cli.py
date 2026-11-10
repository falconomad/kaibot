import importlib.util
import urllib.request
from pathlib import Path

from alt_helpers import FIX, FakeClock, FakeNet

from kaibot.common import ratelimit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "collect_alt.py"


def _load():
    spec = importlib.util.spec_from_file_location("collect_alt", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_finra_one_date(tmp_path, monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(ratelimit, "REGISTRY", ratelimit.Registry(clock=clock))
    net = FakeNet([("CNMSshvol20261109.txt", (FIX / "finra_cnms_20261109.txt").read_bytes())])
    monkeypatch.setattr(urllib.request, "urlopen", net)
    env = tmp_path / "secrets.env"
    env.write_text("SEC_USER_AGENT=Kaibot ops@example.com\n")
    cli = _load()
    rc = cli.main(["--env", str(env), "--source", "finra", "--date", "2026-11-09", "--data", str(tmp_path)])
    assert rc == 0
    assert list((tmp_path / "alt" / "finra_regsho" / "date=2026-11-09").glob("part-*.parquet"))
    assert clock.sleeps == []  # one request: the 1 s gap never kicked in


def test_cli_form4_without_user_agent_fails_cleanly(tmp_path, monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", FakeNet([]))
    env = tmp_path / "secrets.env"
    env.write_text("# nothing here\n")
    rc = _load().main(["--env", str(env), "--source", "form4", "--data", str(tmp_path)])
    assert rc == 1
