"""M7 · sample data for the multi-day dashboard boards (issue #129).

Everything here is invented: prices, ranks, results and gate verdicts. One place computes every number so the boards
agree with each other (holdings add up to "invested", invested plus cash is the pot, and so on).

Sample moment: Thu 11 Mar 2027, 20:14 Berlin (14:14 New York), market open. Paper pot $5,000 since Mon 4 Jan 2027.
The period is in the future on purpose, so nobody reads the sample as a real track record.
"""
import datetime as dt
import random

START = 5000.00
D0 = dt.date(2027, 1, 4)           # paper start, rule 1
TODAY = dt.date(2027, 3, 11)       # a Thursday
NOW = '20:14'                      # Berlin
PRICES_AT = '20:00'
HOLIDAYS = {dt.date(2027, 1, 1), dt.date(2027, 1, 18), dt.date(2027, 2, 15), dt.date(2027, 3, 26), dt.date(2027, 5, 31)}
UNIVERSE = 503                     # stocks ranked
CUT = 100                          # top fifth of the ranking
LIMIT_INVESTED = 80                # % of the pot, sample; the real limit is an open decision (level limits, S92)
LIMIT_STOCK = 10                   # % of the pot for one stock, sample
DAILY_LIMIT = 1.5                  # % daily loss limit of the Balanced level today
RUN_LIMIT = 8.0                    # % run loss limit of the Balanced level today (open for multi-day books)
TINTS = ['#7FB2FF', '#C9A8FF', '#8FD8E8', '#D0D5DD']   # stock badges: no green, amber or red tint here, those colours carry meaning on these screens
MINUS = '−'


# ------------------------------------------------------------------ calendar and formats

def is_session(d):
    return d.weekday() < 5 and d not in HOLIDAYS


def sessions(a, b):
    """Trading days from a to b, both included."""
    out, d = [], a
    while d <= b:
        if is_session(d):
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def nth_session(a, n):
    """The n-th trading day counting a as the first."""
    d, k = a, 0
    while True:
        if is_session(d):
            k += 1
            if k == n:
                return d
        d += dt.timedelta(days=1)


def day(d, year=False):
    s = f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"
    return s + f' {d.year}' if year else s


def short(d):
    return f"{d.day} {d.strftime('%b')}"


def usd(v, cents=True):
    return ('${:,.2f}' if cents else '${:,.0f}').format(abs(v))


def signed(v, cents=True):
    # the word joiner keeps a line from breaking between the sign and the dollar amount
    return ('+' if v > 0 else MINUS if v < 0 else '') + '\u2060' + usd(v, cents)


def pct(v, digits=2, sign=True):
    s = f'{abs(v):.{digits}f}%'
    return (('+' if v > 0 else MINUS if v < 0 else '') if sign else (MINUS if v < 0 else '')) + s


def arrow(v):
    return '▲' if v >= 0 else '▼'


def delta(v, p=None, digits=2):
    """'▲ +$22.50 · +7.75%' : arrow, sign and words, never colour alone."""
    return f'{arrow(v)} {signed(v)}' + (f' · {pct(p, digits)}' if p is not None else '')


def ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def tint(sym):
    h = 0
    for ch in sym:
        h = (h * 31 + ord(ch)) % 997
    return TINTS[h % len(TINTS)]


GAIN, LOSS, FLAT = '#3DDC97', '#FF6B6B', '#A1A8B3'


def ink(v):
    return GAIN if v > 0 else LOSS if v < 0 else FLAT


# ------------------------------------------------------------------ rules (plain names on screen; engine names in notes)

RULES = {
    'r1': dict(key='trend_momentum', name='Steady risers', short='Steady risers',
               what='Buys the stocks that have climbed most steadily over about four months.',
               rhythm='Checks its list every Wednesday after the close and trades at Thursday’s close.',
               since=dt.date(2027, 1, 4), next_check=dt.date(2027, 3, 17), filter=True),
    'r2': dict(key='mom_12_1_band', name='12-month winners', short='12-month winners',
               what='Buys the stocks that did best over the past year, leaving out the last month.',
               rhythm='Checks its list on the first trading day of each month and trades at the next close.',
               since=dt.date(2027, 2, 1), next_check=dt.date(2027, 4, 1), filter=False),
}

# sym, name, rule, shares, bought, bought at, now, yesterday's close, today's open, 100-day average, rank now, rank when bought,
# typical daily move % when bought, planned action tonight
_H = [
    ('WMT', 'Walmart', 'r1', 3, (1, 7), 96.80, 104.30, 104.05, 103.60, 98.90, 41, 9, 1.5, ('add', 1)),
    ('CSCO', 'Cisco Systems', 'r1', 4, (1, 7), 74.10, 78.40, 79.10, 78.30, 73.60, 27, 16, 1.6, None),
    ('NEM', 'Newmont', 'r1', 3, (1, 21), 79.30, 88.10, 87.40, 87.60, 77.50, 19, 7, 2.0, ('trim', 1)),
    ('XOM', 'Exxon Mobil', 'r1', 2, (1, 7), 119.40, 116.20, 117.00, 116.50, 114.80, 131, 22, 1.5, ('sell', 2)),
    ('TPR', 'Tapestry', 'r1', 2, (3, 4), 109.80, 112.50, 113.40, 112.00, 101.30, 5, 4, 2.2, None),
    ('C', 'Citigroup', 'r1', 2, (1, 14), 97.20, 103.90, 104.60, 103.20, 95.40, 12, 11, 2.0, None),
    ('GM', 'General Motors', 'r1', 3, (1, 28), 64.90, 61.75, 62.75, 61.90, 63.10, 88, 14, 2.2, ('sell', 3)),
    ('GLW', 'Corning', 'r1', 2, (2, 4), 84.60, 89.40, 90.35, 89.30, 81.20, 8, 6, 2.4, None),
    ('APH', 'Amphenol', 'r2', 2, (2, 2), 124.50, 131.90, 133.20, 131.30, None, 14, 12, 1.9, None),
    ('HWM', 'Howmet Aerospace', 'r2', 1, (2, 2), 188.20, 203.40, 204.90, 202.60, None, 22, 19, 2.1, None),
    ('GILD', 'Gilead Sciences', 'r2', 2, (3, 2), 85.90, 88.10, 87.40, 87.60, None, 9, 8, 2.6, None),
    ('VST', 'Vistra', 'r2', 1, (3, 2), 171.30, 164.80, 166.30, 164.10, None, 31, 27, 2.9, None),
]

CASH = 2447.15
SPY_NOW = 5236.50            # $5,000 in SPY since the same start, at today's prices
SPY_PREV = 5256.50           # ... at yesterday's close
SPY_VS_200 = 6.3             # % above its 200-day average


def _stop(entry, move):
    return round(entry * (1 - 8 * move / 100) / 0.05) * 0.05      # sample width: eight typical daily moves


def holdings():
    rows = []
    invested = sum(h[3] * h[6] for h in _H)
    pot = CASH + invested
    for sym, name, rule, sh, (m, d), px_in, now, prev, opn, avg, rank, rank_in, move, plan in _H:
        bought = dt.date(2027, m, d)
        value, cost = sh * now, sh * px_in
        gain, stop = value - cost, _stop(px_in, move)
        r = dict(sym=sym, name=name, rule=rule, rule_name=RULES[rule]['short'], sh=sh, bought=bought, px_in=px_in, now=now,
                 prev=prev, open=opn, avg=avg, rank=rank, rank_in=rank_in, move=move, plan=plan, value=value, cost=cost,
                 gain=gain, gain_pct=gain / cost * 100, share=value / pot * 100, days=(TODAY - bought).days, stop=stop,
                 stop_below=(now - stop) / now * 100, gap=sh * (opn - prev), intraday=sh * (now - opn))
        r['above_avg'] = (now - avg) / avg * 100 if avg else None
        rows.append(r)
    return rows, invested, pot


ROWS, INVESTED, POT = holdings()
assert abs(POT - 5175.40) < 0.005, POT
PREV_POT = POT - sum(r['gap'] + r['intraday'] for r in ROWS)
GAP = sum(r['gap'] for r in ROWS)
INTRADAY = sum(r['intraday'] for r in ROWS)
TODAY_CHG = GAP + INTRADAY
UNREALISED = sum(r['gain'] for r in ROWS)

# sold so far: sym, name, rule, shares, bought, sold, result $, result %, why
SOLD = [
    ('RTX', 'RTX Corporation', 'r1', 2, (1, 21), (3, 4), 31.90, 9.6, 'Left the top fifth of the ranking'),
    ('DIS', 'Walt Disney', 'r1', 2, (2, 4), (3, 4), -9.40, -4.3, 'Closed below its 100-day average'),
    ('BK', 'Bank of New York Mellon', 'r2', 2, (2, 2), (3, 2), 9.10, 4.4, 'Fell out of the top fifth of its ranking'),
    ('KO', 'Coca-Cola', 'r1', 6, (1, 14), (2, 25), 18.30, 4.4, 'Left the top fifth of the ranking'),
    ('JNJ', 'Johnson & Johnson', 'r1', 2, (1, 7), (2, 18), 21.60, 6.1, 'Left the top fifth of the ranking'),
    ('T', 'AT&T', 'r1', 13, (1, 7), (2, 4), -5.85, -1.6, 'Closed below its 100-day average'),
]
REALISED = sum(s[6] for s in SOLD)
DIVIDENDS, FEES = 3.24, 0.04
assert abs(START + REALISED + DIVIDENDS - FEES + UNREALISED - POT) < 0.005, (START + REALISED + DIVIDENDS - FEES + UNREALISED, POT)

# tonight's plan, decided after last night's close. kind, sym, name, rule, shares, about $, headline, why
PLAN_DECIDED = 'Wed 10 Mar, 22:21'
PLAN_SENT = '01:02'
PLAN = [
    ('sell', 'XOM', 'Exxon Mobil', 'r1', 2, 232.40, 'Sell all 2 shares',
     'It left the top fifth of the ranking: now 131st of 503. Steady risers sells below 100th.'),
    ('sell', 'GM', 'General Motors', 'r1', 3, 185.25, 'Sell all 3 shares',
     'It closed below its 100-day average: $62.75 against $63.10.'),
    ('buy', 'UBER', 'Uber Technologies', 'r1', 2, 192.40, 'Buy 2 shares',
     'New near the top of the ranking: 3rd of 503, and above its 100-day average.'),
    ('buy', 'CVS', 'CVS Health', 'r1', 3, 234.60, 'Buy 3 shares',
     'Ranked 11th of 503 and above its 100-day average. There is cash for it after tonight’s sales.'),
    ('add', 'WMT', 'Walmart', 'r1', 1, 104.30, 'Add 1 share: 3 becomes 4',
     'Two-week size check: it moves less in a day than when it was bought, so the same risk allows one more share.'),
    ('trim', 'NEM', 'Newmont', 'r1', 1, 88.10, 'Sell 1 share: 3 becomes 2',
     'Two-week size check: it moves more in a day than when it was bought, so 2 shares now carry the risk 3 did.'),
    ('skip', 'GE', 'GE Aerospace', 'r1', 0, 312.40, 'Not buying',
     'Ranked 2nd of 503, but one share costs more than its slot in this pot ($262).'),
]
AFTER_INVESTED = INVESTED - 232.40 - 185.25 + 192.40 + 234.60 + 104.30 - 88.10
AFTER_PCT = AFTER_INVESTED / POT * 100

# the last close at which something traded (Thu 4 Mar): kind, sym, name, shares, price, result $, result %, days, why
LAST_TRADES_DAY = dt.date(2027, 3, 4)
LAST_TRADES = [
    ('buy', 'TPR', 'Tapestry', 2, 109.80, None, None, None, 'New in the top fifth of the ranking: 4th of 503.'),
    ('sell', 'RTX', 'RTX Corporation', 2, 181.95, 31.90, 9.6, 42, 'Left the top fifth of the ranking.'),
    ('sell', 'DIS', 'Walt Disney', 2, 104.30, -9.40, -4.3, 28, 'Closed below its 100-day average.'),
]


# ------------------------------------------------------------------ pot and S&P 500 lines (same start)

def curve():
    days = sessions(D0, TODAY)                    # 47 trading days, today included
    n = len(days)
    rnd = random.Random(129)
    spy_r, pot_r = [], []
    for i in range(n - 2):                        # day 0 is the start; one return per later close
        base = rnd.gauss(0.0013, 0.0078)
        if 26 <= i <= 31:
            base -= 0.0085                        # a rough week in mid-February
        if 32 <= i <= 37:
            base += 0.0045
        spy_r.append(base)
        held = 0.0 if i < 3 else min(0.55, 0.12 + 0.045 * (i - 3))      # cash at first, then about half invested
        pot_r.append(held * (base + rnd.gauss(0.0006, 0.004)))
    spy_r.append(SPY_NOW / SPY_PREV - 1)          # today so far
    pot_r.append(POT / PREV_POT - 1)

    def path(rets, end_prev, end_now):
        vals = [START]
        for r in rets[:-1]:
            vals.append(vals[-1] * (1 + r))
        # bend the path so yesterday's close lands on the ledger figure
        k = end_prev / vals[-1]
        m = len(vals) - 1
        vals = [v * (1 + (k - 1) * (i / m)) for i, v in enumerate(vals)]
        vals.append(end_now)
        return vals

    spy = path(spy_r, SPY_PREV, SPY_NOW)
    pot = path(pot_r, PREV_POT, POT)
    pot[0:3] = [START, START, START]              # no stocks before the first buys
    return days, [round(v, 2) for v in pot[:n]], [round(v, 2) for v in spy[:n]]


DAYS, POT_LINE, SPY_LINE = curve()
assert len(DAYS) == 47 and POT_LINE[-1] == round(POT, 2) and SPY_LINE[-1] == SPY_NOW


def worst_fall(vals):
    peak, worst = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        worst = max(worst, (peak - v) / peak * 100)
    return worst


# ------------------------------------------------------------------ evidence (gates A to D), all sample

def gate_c(since):
    done = len(sessions(since, TODAY)) - 1                        # today is still open
    day60 = nth_session(since, 60)
    m3 = since + dt.timedelta(days=90)
    while not is_session(m3):
        m3 += dt.timedelta(days=1)
    return done, max(day60, m3)


C1_DONE, C1_END = gate_c(RULES['r1']['since'])
C2_DONE, C2_END = gate_c(RULES['r2']['since'])
SPY_10Y, SPY_FALL = 14.0, 34

if __name__ == '__main__':
    print('pot', round(POT, 2), 'invested', round(INVESTED, 2), f'{INVESTED / POT * 100:.1f}%', 'cash', CASH)
    print('yesterday', round(PREV_POT, 2), 'gap', round(GAP, 2), 'intraday', round(INTRADAY, 2), 'today', round(TODAY_CHG, 2))
    print('unrealised', round(UNREALISED, 2), 'realised', round(REALISED, 2), 'after tonight', round(AFTER_PCT, 1))
    print('gate C', C1_DONE, C1_END, C2_DONE, C2_END)
    print('worst fall pot', round(worst_fall(POT_LINE), 2), 'spy', round(worst_fall(SPY_LINE), 2))
    print('pot line', POT_LINE[:6], '...', POT_LINE[-4:])
    print('spy line', SPY_LINE[:6], '...', SPY_LINE[-4:])
    for r in ROWS:
        print(r['sym'], r['rule'], r['sh'], round(r['value'], 2), f"{r['share']:.1f}%", round(r['gain'], 2), f"{r['gain_pct']:.2f}%",
              r['days'], 'stop', round(r['stop'], 2), f"{r['stop_below']:.1f}%", 'avg', None if r['above_avg'] is None else round(r['above_avg'], 1))
