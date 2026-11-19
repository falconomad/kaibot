"""The seeded draw behind headlines_v1.jsonl (story S105, #379). Prints the request list; it fetches nothing.

Each request: one minute drawn uniformly from FIRST..LAST (UTC), and SYMBOLS_PER_REQUEST companies drawn from the
S&P 500 members in force on that date (point-in-time, src/kaibot/ingest/sp500/changes.csv). The request asks the
Alpaca news endpoint (`/v1beta1/news`, the one the engine's poller reads) for the first PER_REQUEST headlines at or
after that minute for those symbols (sort=asc, no end). The sample is the first SAMPLE distinct headlines in
request order. README.md has the run of 19 Nov 2026 and why FIRST is 29 Apr 2026.

    python -m kaibot.news.tagset.draw
"""

from __future__ import annotations

import json
import random
from datetime import UTC, date, datetime, timedelta

SEED = 379105
# The endpoint returned nothing older than 29 Apr 2026 on the draw date (README.md, "First attempt").
FIRST, LAST = datetime(2026, 4, 29, tzinfo=UTC), datetime(2026, 11, 18, tzinfo=UTC)
REQUESTS = 37  # 40 allowed for the story, 3 spent on the first attempt
SYMBOLS_PER_REQUEST = 8
PER_REQUEST = 10
SAMPLE = 300
DRAWN_ON = date(2026, 11, 19)


def requests(seed: int = SEED) -> list[dict]:
    from kaibot.ingest.sp500_pit import Membership  # read-only use of the committed membership file

    hist = Membership.load(today=DRAWN_ON)
    rng = random.Random(seed)
    minutes = int((LAST - FIRST).total_seconds() // 60)
    out = []
    for i in range(REQUESTS):
        t = FIRST + timedelta(minutes=rng.randrange(minutes))
        syms = sorted(rng.sample(sorted(hist.members_on(t.date())), SYMBOLS_PER_REQUEST))
        out.append({"n": i + 1, "start": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "symbols": ",".join(syms),
                    "sort": "asc", "limit": PER_REQUEST})
    return out


if __name__ == "__main__":
    for r in requests():
        print(json.dumps(r))
