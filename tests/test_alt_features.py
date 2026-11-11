"""Story S37: alt-data features are point-in-time and return None (never raise) when data is missing."""

from datetime import UTC, date, datetime, timedelta

import pyarrow as pa

from kaibot.features.alt import alt_features, gdelt_tone_z, insider_net_buying, short_volume
from kaibot.ingest.alt import finra, form4, gdelt, store

NOW = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)


def _finra(root, days=22, symbol="ACME"):
    for i in range(days):
        d = date(2026, 10, 1) + timedelta(days=i)
        avail = finra.publication_ts(d)
        short = 600 if i == days - 1 else 400  # the latest day has a 0.6 ratio vs 0.4 before
        t = pa.table({"trade_date": [d], "symbol": [symbol], "short_volume": [short], "short_exempt_volume": [0],
                      "total_volume": [1000], "market": ["N"], "available_ts": pa.array([avail], store.TS),
                      "ingested_ts": pa.array([avail], store.TS)}, schema=finra.SCHEMA)
        store.append_versions(root, finra.SOURCE, d, t, finra.KEY, finra.VALUES)
    return date(2026, 10, 1) + timedelta(days=days - 1)


def test_short_volume_ratio_vs_20d_mean_is_point_in_time(tmp_path):
    last = _finra(tmp_path)
    r = short_volume(tmp_path, "ACME", NOW)
    assert r["trade_date"] == str(last) and abs(r["svr"] - 0.6) < 1e-9 and abs(r["svr_mean20"] - 0.4) < 1e-9
    assert abs(r["svr_rel"] - 1.5) < 1e-9
    # 17:59 ET on the last trade date: that day's file is not published yet
    before = finra.publication_ts(last) - timedelta(minutes=1)
    r2 = short_volume(tmp_path, "ACME", before)
    assert r2["trade_date"] != str(last) and abs(r2["svr"] - 0.4) < 1e-9
    assert short_volume(tmp_path, "OTHER", NOW) is None


def test_short_volume_needs_history(tmp_path):
    _finra(tmp_path, days=5)
    r = short_volume(tmp_path, "ACME", NOW)
    assert r["svr"] is not None and r["svr_mean20"] is None and r["svr_rel"] is None


def _form4(root, rows):
    led, tx = [], []
    for i, (code, shares, price, tdate, avail) in enumerate(rows):
        acc = f"0000000000-26-{i:06d}"
        led.append({"accession": acc, "symbol": "ACME", "issuer_cik": "0000000001", "form": "4",
                    "filing_date": tdate, "status": "ok", "n_rows": 1, "accepted_ts": avail, "available_ts": avail,
                    "ingested_ts": avail})
        tx.append({"accession": acc, "row": 0, "symbol": "ACME", "issuer_cik": "0000000001", "form": "4",
                   "derivative": False, "transaction_date": tdate, "code": code, "shares": shares, "price": price,
                   "filing_date": tdate, "accepted_ts": avail, "available_ts": avail, "ingested_ts": avail})
    d = date(2026, 11, 1)
    store.append_versions(root, form4.LEDGER, d, pa.Table.from_pylist(led, schema=form4.LEDGER_SCHEMA),
                          ["accession"], ["status"])
    store.append_versions(root, form4.SOURCE, d, pa.Table.from_pylist(tx, schema=form4.TX_SCHEMA),
                          form4.TX_KEY, form4.TX_VALUES)


def test_insider_net_buying_last_30_days(tmp_path):
    a = NOW - timedelta(days=3)
    _form4(tmp_path, [("P", 1000, 10.0, date(2026, 11, 5), a), ("S", 400, 12.0, date(2026, 11, 6), a),
                      ("A", 5000, 0.0, date(2026, 11, 6), a),  # grant: not open-market
                      ("P", 9999, 1.0, date(2026, 9, 1), a),  # older than 30 days
                      ("P", 500, 10.0, date(2026, 11, 10), NOW + timedelta(hours=1))])  # not yet available
    r = insider_net_buying(tmp_path, "ACME", NOW)
    assert r == {"net_shares": 600.0, "net_usd": 10000.0 - 4800.0, "n_buys": 1, "n_sells": 1, "days": 30}
    assert insider_net_buying(tmp_path, "NOTCOVERED", NOW) is None
    assert insider_net_buying(tmp_path, "ACME", a - timedelta(seconds=1)) is None  # nothing known yet


def test_gdelt_tone_z(tmp_path):
    day = date(2026, 11, 10)
    start = datetime(2026, 11, 10, 0, 0, tzinfo=UTC)
    n = 40
    vals = [(-2.0 if i % 2 else -1.0) for i in range(n - 1)] + [-6.0]
    buckets = [start + timedelta(minutes=15 * i) for i in range(n)]
    t = pa.table({"query_name": ["tariffs"] * n, "query": ["q"] * n, "mode": ["timelinetone"] * n,
                  "series": ["Average Tone"] * n, "bucket_ts": pa.array(buckets, store.TS),
                  "value": pa.array(vals, pa.float64()), "norm": pa.array([None] * n, pa.float64()),
                  "available_ts": pa.array([b + timedelta(minutes=20) for b in buckets], store.TS),
                  "ingested_ts": pa.array([b + timedelta(minutes=20) for b in buckets], store.TS)}, schema=gdelt.SCHEMA)
    store.append_versions(tmp_path, gdelt.SOURCE, day, t, gdelt.KEY, gdelt.VALUES)
    z = gdelt_tone_z(tmp_path, "tariffs", NOW)
    assert z is not None and z < -5
    just_before_last = buckets[-1] + timedelta(minutes=19)
    assert abs(gdelt_tone_z(tmp_path, "tariffs", just_before_last)) < 2
    assert gdelt_tone_z(tmp_path, "sanctions", NOW) is None


def test_everything_missing_is_none_not_an_error(tmp_path):
    out = alt_features(tmp_path / "nothing-here", "ACME", NOW)
    assert out["short_volume"] is None and out["insider_30d"] is None
    assert set(out["gdelt_tone_z"]) == set(gdelt.QUERIES) and all(v is None for v in out["gdelt_tone_z"].values())
    corrupt = tmp_path / "alt" / finra.SOURCE / "date=2026-11-10"
    corrupt.mkdir(parents=True)
    (corrupt / "part-x.parquet").write_bytes(b"not parquet")
    assert alt_features(tmp_path, "ACME", NOW)["short_volume"] is None
