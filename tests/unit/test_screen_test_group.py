"""S66 test-group screen (offline, fake Alpaca): metrics, PASS/FAIL reasons, the seed writer, and the guard that
nothing in the live engine or the dashboard reads `test_groups`."""

from __future__ import annotations

import importlib.util
import math
import types
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from kaibot.common.http import HttpError
from kaibot.ingest import test_group as tg

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 10, 22, 0, tzinfo=UTC)  # 17:00 ET, a Tuesday: that day's daily bar is complete
F = tg.Filters()


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def daily(n: int, seed: int, price: float = 100.0, vol: float = 5e6, beta: float | None = None,
          mkt=None) -> list[dict]:
    """n business-day bars ending 10 Nov 2026. With `beta`, returns are beta x the market's plus small noise."""
    days = pd.bdate_range(end="2026-11-10", periods=n)
    rng = np.random.default_rng(seed)
    if beta is None:
        r = rng.normal(0, 0.01, n)
    else:
        mc = np.array([b["c"] for b in mkt[-n:]])
        r = np.r_[0.0, beta * (mc[1:] / mc[:-1] - 1)]
    c = price * np.cumprod(1 + r)
    c *= price / c[-1]  # last close = price
    return [{"t": f"{d.date()}T05:00:00Z", "o": x, "h": x, "l": x, "c": float(x), "v": vol}
            for d, x in zip(days, c, strict=True)]


SPY = daily(400, 0, price=600.0, vol=5e7)


class FakeAlpaca:
    """Answers /bars (1Day, 1Min) and /quotes from dicts; records every request."""

    def __init__(self, dailies: dict, spread_bp: dict | None = None, quotes_status: int | None = None, minutes=None):
        self.dailies, self.spread_bp, self.quotes_status = dailies, spread_bp or {}, quotes_status
        self.minutes = minutes or {}
        self.calls: list[tuple[str, dict]] = []

    def get(self, path: str, params: dict) -> dict:
        self.calls.append((path, params))
        sym = path.split("/")[3]
        if path.endswith("/quotes"):
            if self.quotes_status:
                raise HttpError(self.quotes_status, "https://data.alpaca.markets" + path)
            bp = self.spread_bp.get(sym)
            if bp is None:
                return {"quotes": []}
            return {"quotes": [{"bp": 100 * (1 - bp / 2e4), "ap": 100 * (1 + bp / 2e4)}, {"bp": 0, "ap": 0}]}
        if params["timeframe"] == "1Min":
            return {"bars": self.minutes.get(sym, [])}
        if sym not in self.dailies:
            raise HttpError(404, "https://data.alpaca.markets" + path)
        return {"bars": self.dailies[sym]}


def one(api, sym, f=F):
    return tg.screen(api, [sym], f, now=NOW)[0]


def test_beta_recovers_a_known_slope_and_needs_61_sessions():
    assert tg.beta_vs(daily(300, 1, beta=1.8, mkt=SPY), SPY) == pytest.approx(1.8, abs=1e-6)
    assert tg.beta_vs(daily(60, 1, beta=1.8, mkt=SPY), SPY) is None


def test_pass_row_has_every_metric_and_only_old_sip_data():
    api = FakeAlpaca({"SPY": SPY, "AAA": daily(300, 1, price=80.0, vol=2e6, beta=2.0, mkt=SPY)}, {"AAA": 3.0})
    s = one(api, "AAA")
    assert s.passed and s.reasons == [] and s.notes == []
    assert s.beta == pytest.approx(2.0, abs=1e-6) and s.price == pytest.approx(80.0)
    assert s.spread_bp == pytest.approx(3.0, abs=1e-6) and s.spread_src == "nbbo"
    assert s.sessions == 300 and s.first_date == pd.bdate_range(end="2026-11-10", periods=300)[0].date()
    expect_adv = np.mean([b["c"] * b["v"] for b in api.dailies["AAA"][-20:]])
    assert s.adv_usd == pytest.approx(expect_adv)
    limit = NOW - timedelta(minutes=15)
    for _, p in api.calls:
        assert p["feed"] == "sip" and datetime.fromisoformat(p["end"]) <= limit
    assert sum(path.endswith("/quotes") for path, _ in api.calls) == 25  # 5 sessions x 5 times


@pytest.mark.parametrize(("kw", "spread", "reason"), [
    ({"price": 4.0, "vol": 5e7}, 3.0, "price $4.00 < $5"),
    ({"price": 80.0, "vol": 1e5}, 3.0, "ADV $8M < $50M"),
    ({"price": 80.0, "vol": 2e6}, 9.0, "spread 9.0 bp > 5 bp"),
    ({"price": 80.0, "vol": 2e6}, None, "spread unknown"),
])
def test_each_filter_fails_with_its_reason(kw, spread, reason):
    api = FakeAlpaca({"SPY": SPY, "AAA": daily(300, 1, **kw)}, {"AAA": spread})
    s = one(api, "AAA")
    assert not s.passed and s.reasons == [reason]


def test_recent_ipo_fails_on_history_and_short_history_is_flagged():
    api = FakeAlpaca({"SPY": SPY, "IPO": daily(40, 1, vol=2e6), "YNG": daily(200, 2, vol=2e6, beta=1.5, mkt=SPY)},
                     {"IPO": 2.0, "YNG": 2.0})
    ipo, yng = tg.screen(api, ["IPO", "YNG"], F, now=NOW)
    assert ipo.reasons == ["history 40 sessions < 126"] and ipo.beta is None
    assert yng.passed and yng.notes == ["short history (200 sessions)"]


def test_unknown_ticker_fails_without_stopping_the_run():
    api = FakeAlpaca({"SPY": SPY, "AAA": daily(300, 1, vol=2e6)}, {"AAA": 2.0})
    bad, good = tg.screen(api, ["NOPE", "AAA"], F, now=NOW)
    assert bad.reasons == ["no data (HTTP 404)"] and good.passed


def test_optional_min_beta_and_low_beta_note():
    api = FakeAlpaca({"SPY": SPY, "LOW": daily(300, 1, vol=2e6, beta=0.5, mkt=SPY)}, {"LOW": 2.0})
    assert one(api, "LOW").notes == ["beta below 1"]
    assert one(api, "LOW", tg.Filters(min_beta=1.2)).reasons == ["beta 0.50 < 1.2"]


def test_todays_unfinished_daily_bar_is_dropped():
    api = FakeAlpaca({"SPY": SPY})
    mid_session = datetime(2026, 11, 10, 18, 0, tzinfo=UTC)  # 13:00 ET
    assert tg._bar_date(tg.fetch_daily(api, "SPY", tg.sip_end(mid_session))[-1]) == date(2026, 11, 9)
    assert tg._bar_date(tg.fetch_daily(api, "SPY", tg.sip_end(NOW))[-1]) == date(2026, 11, 10)
    with pytest.raises(ValueError, match="15 min"):
        tg.fetch_daily(api, "SPY", datetime.now(UTC))


def test_roll_fallback_when_quotes_are_not_served():
    # a pure bid-ask bounce of +-2 bp around 100 (spread 4 bp): Roll recovers about 4 bp
    ts = pd.Timestamp("2026-11-10 09:30", tz=tg.ET).tz_convert("UTC") + pd.to_timedelta(np.arange(390), unit="m")
    rng = np.random.default_rng(3)
    mins = [{"t": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "c": 100 * (1 + s * 2e-4)} for t, s in
            zip(ts, rng.choice([-1, 1], 390), strict=True)]
    api = FakeAlpaca({"SPY": SPY, "AAA": daily(300, 1, vol=2e6)}, quotes_status=403, minutes={"AAA": mins})
    s = one(api, "AAA")
    assert s.spread_src == "roll" and s.spread_bp == pytest.approx(4.0, abs=0.8)
    assert "spread is a Roll estimate, not NBBO" in s.notes
    runs = [100 * math.exp(0.001 * (i if i < 30 else 60 - i)) for i in range(60)]  # up then down: positive autocov
    assert tg.roll_spread_bp(runs) is None and tg.roll_spread_bp(runs[:10]) is None
    with pytest.raises(HttpError):  # anything but "not served" is a real error
        one(FakeAlpaca({"SPY": SPY, "AAA": daily(300, 1)}, quotes_status=500), "AAA")


def test_filters_come_from_profiles_yaml():
    f = tg.filters_from_profiles(ROOT / "config" / "profiles.yaml", min_sessions=None, min_beta=None)
    assert (f.min_adv_usd, f.max_spread_bp, f.min_price_usd, f.min_sessions, f.min_beta) == (50e6, 5, 5, 126, None)


# ---------- the seed writer ----------
@pytest.fixture
def seed(tmp_path):
    p = tmp_path / "config" / "universe_seed.yaml"
    p.parent.mkdir()
    p.write_text((ROOT / "config" / "universe_seed.yaml").read_text())
    return p


def test_write_appends_a_block_and_leaves_the_rest_byte_identical(seed):
    before = seed.read_text()
    assert "test_groups" not in before, "the committed seed gets its block only from the screen on the Mac"
    tg.write_test_group(seed, "high_beta_growth", ["NBIS", "CRWV"], "High-beta growth (test)", date(2026, 11, 12), F)
    after = seed.read_text()
    assert after.startswith(before.rstrip("\n") + "\n")
    cfg, old = yaml.safe_load(after), yaml.safe_load(before)
    assert {k: cfg[k] for k in old} == old
    g = cfg["test_groups"]["high_beta_growth"]
    assert g["symbols"] == ["NBIS", "CRWV"] and g["screened_on"] == "2026-11-12" and "ADV>=$50M" in g["filters"]
    # a second run replaces the group instead of stacking blocks; other groups survive
    tg.write_test_group(seed, "other", ["FSLY"], "Other", date(2026, 11, 13), F)
    tg.write_test_group(seed, "high_beta_growth", ["NBIS"], "High-beta growth (test)", date(2026, 11, 14), F)
    text = seed.read_text()
    cfg = yaml.safe_load(text)
    assert text.count("test_groups:") == 1 and text.count(tg.GROUP_HEADER) == 1
    assert cfg["test_groups"]["high_beta_growth"]["symbols"] == ["NBIS"]
    assert cfg["test_groups"]["other"]["symbols"] == ["FSLY"]
    assert {k: cfg[k] for k in old} == old


def test_write_refuses_live_symbols_and_bad_input(seed):
    before = seed.read_text()
    for syms in (["NVDA"], ["SPY"], ["XLK"], [], ["nbis"], ["A B"], ["X]\nsectors: {}"]):
        with pytest.raises(ValueError):
            tg.write_test_group(seed, "high_beta_growth", syms, "x", date(2026, 11, 12), F)
    with pytest.raises(ValueError):
        tg.write_test_group(seed, "Bad Name", ["NBIS"], "x", date(2026, 11, 12), F)
    assert seed.read_text() == before


# ---------- the script ----------
def run_script(seed, capsys, *extra, spread=None):
    mod = load_script("screen_test_group")
    api = FakeAlpaca({"SPY": SPY, "NBIS": daily(300, 1, vol=2e6, beta=2.0, mkt=SPY),
                      "CRWV": daily(300, 2, vol=2e6, beta=2.5, mkt=SPY), "FSLY": daily(300, 3, price=9.0, vol=1e6)},
                     spread or {"NBIS": 3.0, "CRWV": 4.0, "FSLY": 8.0})
    rc = mod.main(["--symbols", "NBIS,CRWV,FSLY,NVDA", "--seed", str(seed), *extra], api=api)
    return rc, capsys.readouterr().out


def test_script_is_a_dry_run_by_default(seed, capsys, monkeypatch):
    monkeypatch.setattr(tg, "sip_end", lambda now=None: NOW - tg.SIP_DELAY)
    before = seed.read_text()
    rc, out = run_script(seed, capsys)
    assert rc == 0 and seed.read_text() == before
    assert "skipping names already in the live universe: NVDA" in out
    assert "2 of 3 pass" in out and "Dry run: nothing written" in out
    rows = {ln.split()[0]: ln for ln in out.splitlines() if ln[:4] in ("NBIS", "CRWV", "FSLY")}
    assert rows["NBIS"].rstrip().endswith("PASS") and "2.00" in rows["NBIS"] and "nbbo" in rows["NBIS"]
    assert "FAIL: ADV $9M < $50M; spread 8.0 bp > 5 bp" in rows["FSLY"]


def test_script_write_puts_only_passing_names_in_the_seed(seed, capsys, monkeypatch):
    monkeypatch.setattr(tg, "sip_end", lambda now=None: NOW - tg.SIP_DELAY)
    rc, out = run_script(seed, capsys, "--write")
    cfg = yaml.safe_load(seed.read_text())
    assert rc == 0 and cfg["test_groups"]["high_beta_growth"]["symbols"] == ["NBIS", "CRWV"]
    assert cfg["test_groups"]["high_beta_growth"]["label"] == "High-beta growth (test)"
    # nothing passes -> nothing written
    seed.write_text((ROOT / "config" / "universe_seed.yaml").read_text())
    rc, out = run_script(seed, capsys, "--write", spread={"NBIS": 9.0, "CRWV": 9.0, "FSLY": 9.0})
    assert rc == 1 and "nothing written" in out and "test_groups" not in seed.read_text()


def test_script_without_an_env_file_stops_before_any_request(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("KAIBOT_ENV_FILE", raising=False)
    mod = load_script("screen_test_group")
    assert mod.main(["--env", str(tmp_path / "missing.env")]) == 2
    assert "No .env found" in capsys.readouterr().out
    env = tmp_path / ".env"
    env.write_text("ALPACA_API_KEY=abc123secret\n")
    assert mod.main(["--env", str(env)]) == 2
    assert "abc123secret" not in capsys.readouterr().out


def test_env_default_follows_kaibot_env_file(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("KAIBOT_ENV_FILE", str(tmp_path / "from-env-var.env"))
    assert load_script("screen_test_group").main([]) == 2
    assert "from-env-var.env" in capsys.readouterr().out


# ---------- the candidates file ----------
def test_candidates_file_is_marked_unscreened_and_carries_no_metrics():
    path = ROOT / "config" / "test_group_candidates.yaml"
    cfg = yaml.safe_load(path.read_text())
    assert cfg["status"] == "candidates_not_yet_screened" and "NOT YET SCREENED" in path.read_text().splitlines()[0]
    label, syms = load_script("screen_test_group").load_candidates(path, "high_beta_growth")
    assert 15 <= len(syms) <= 25 and syms[:3] == ["NBIS", "CRWV", "FSLY"] and label
    seed = yaml.safe_load((ROOT / "config" / "universe_seed.yaml").read_text())
    live = {x for v in seed["sectors"].values() for x in v} | set(seed["sectors"]) | {seed["market"]}
    assert not live & set(syms), "candidates must be outside the live universe"
    for c in cfg["groups"]["high_beta_growth"]["candidates"]:
        assert set(c) == {"symbol", "reason"} and tg._SYMBOL.match(c["symbol"]) and len(c["reason"]) > 10


# ---------- guard rail: the live engine and the dashboard do not read test_groups ----------
def test_engine_universe_loader_ignores_test_groups(seed, monkeypatch):
    from kaibot.engine import core

    plain = core.Engine._universe_and_themes(types.SimpleNamespace())
    tg.write_test_group(seed, "high_beta_growth", ["NBIS", "CRWV", "FSLY"], "High-beta growth (test)",
                        date(2026, 11, 12), F)
    monkeypatch.setattr(core, "ROOT", seed.parents[1])
    uni, themes = core.Engine._universe_and_themes(types.SimpleNamespace())
    assert (uni, themes) == plain and len(uni) == 59
    assert not {"NBIS", "CRWV", "FSLY"} & (uni | {s for v in themes.values() for s in v})


def test_fetch_history_default_symbols_ignore_test_groups(seed):
    fh = load_script("fetch_history")
    old = fh.universe_symbols(yaml.safe_load(seed.read_text()))
    tg.write_test_group(seed, "high_beta_growth", ["NBIS", "CRWV"], "t", date(2026, 11, 12), F)
    cfg = yaml.safe_load(seed.read_text())
    assert fh.universe_symbols(cfg) == old and len(old) == 1 + 11 + 59
    assert fh.universe_symbols(cfg, "high_beta_growth") == ["SPY", "NBIS", "CRWV"]
    with pytest.raises(SystemExit):
        fh.universe_symbols(cfg, "missing")


def test_only_the_screen_and_the_downloader_mention_test_groups():
    allowed = {"src/kaibot/ingest/test_group.py", "scripts/screen_test_group.py", "scripts/fetch_history.py"}
    hits = set()
    where = (("src", ("*.py",)), ("scripts", ("*.py", "*.sh")), ("apps/web/src", ("*.ts", "*.tsx", "*.json")))
    for base, pats in where:
        for pat in pats:
            for p in (ROOT / base).rglob(pat):
                if "test_groups" in p.read_text(errors="ignore"):
                    hits.add(p.relative_to(ROOT).as_posix())
    assert hits <= allowed, f"test_groups must stay research-only; also read by: {sorted(hits - allowed)}"
