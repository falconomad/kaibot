import importlib.util
import urllib.request
from pathlib import Path

from alt_helpers import FakeNet

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "collect_alt.py"


def _load():
    spec = importlib.util.spec_from_file_location("collect_alt", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_finra_is_refused_without_a_request(tmp_path, monkeypatch, capsys):
    """#623: FINRA is switched off. Asking for it exits 2 with the reason; nothing is fetched or written."""
    net = FakeNet([])
    monkeypatch.setattr(urllib.request, "urlopen", net)
    env = tmp_path / "secrets.env"
    env.write_text("SEC_USER_AGENT=Kaibot ops@example.com\n")
    cli = _load()
    rc = cli.main(["--env", str(env), "--source", "finra", "--date", "2026-11-09", "--data", str(tmp_path)])
    assert rc == 2 and "#623" in capsys.readouterr().err
    assert net.calls == [] and not (tmp_path / "alt").exists()
    assert "finra" not in cli.RUNNERS  # `--source all` (the nightly job) no longer runs it


def test_cli_form4_without_user_agent_fails_cleanly(tmp_path, monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", FakeNet([]))
    env = tmp_path / "secrets.env"
    env.write_text("# nothing here\n")
    rc = _load().main(["--env", str(env), "--source", "form4", "--data", str(tmp_path)])
    assert rc == 1
