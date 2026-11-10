"""SEC EDGAR: current-filings Atom feed (8-K, Form 4) and ticker->CIK map.

Fair access: <=10 req/s and a User-Agent with a contact email (research 02). We budget 5 req/s
(config/sources.yaml `sec_edgar`, enforced by common.http / common.ratelimit for every SEC host).
The feed is untrusted: parsed with common.safexml (no DTD/entities) and malformed entries are skipped.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime

from kaibot.common import safexml

ATOM = "{http://www.w3.org/2005/Atom}"
CURRENT_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={form}&count=100&output=atom"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_CIK = re.compile(r"\((\d{10})\)")
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Filing:
    form: str
    company: str
    cik: str
    accepted: datetime
    url: str
    accession: str


def parse_current_feed(xml_text: str | bytes) -> list[Filing]:
    root = safexml.fromstring(xml_text)
    out: list[Filing] = []
    for e in root.findall(f"{ATOM}entry"):
        title = (e.findtext(f"{ATOM}title") or "").strip()  # "8-K - ACME CORP (0000123456) (Filer)"
        form, _, rest = title.partition(" - ")
        m = _CIK.search(rest)
        link = e.find(f"{ATOM}link")
        eid = e.findtext(f"{ATOM}id") or ""
        updated = e.findtext(f"{ATOM}updated") or ""
        if not m or link is None:
            continue
        try:
            accepted = datetime.fromisoformat(updated)
        except ValueError:
            log.warning("edgar feed: skipping entry with bad <updated> %r", updated[:40])
            continue
        if accepted.tzinfo is None:
            log.warning("edgar feed: skipping entry without a UTC offset %r", updated[:40])
            continue
        out.append(Filing(
            form=form.strip(), company=rest.split(" (")[0].strip(), cik=m.group(1),
            accepted=accepted, url=link.get("href", ""),
            accession=eid.rsplit("=", 1)[-1] if "accession-number=" in eid else eid,
        ))
    return out


def cik_to_ticker(company_tickers_json: dict) -> dict[str, str]:
    return {str(v["cik_str"]).zfill(10): v["ticker"] for v in company_tickers_json.values()}
