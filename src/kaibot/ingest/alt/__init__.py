"""Alt-data collectors (story S19): SEC Form 4 insider transactions, GDELT tone, listings. FINRA Reg SHO is
switched off (#623): finra.py only refuses.

They only build point-in-time history under data/alt/ for the M3 ablation. Trading code (engine, features,
strategies, risk, broker, agents) must never import this package; tests/unit/test_alt_isolation.py enforces it.
"""
