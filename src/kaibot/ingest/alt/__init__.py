"""Alt-data collectors (story S19): FINRA Reg SHO short volume, SEC Form 4 insider transactions, GDELT tone.

They only build point-in-time history under data/alt/ for the M3 ablation. Trading code (engine, features,
strategies, risk, broker, agents) must never import this package; tests/unit/test_alt_isolation.py enforces it.
"""
