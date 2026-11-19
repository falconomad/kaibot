"""Deterministic headline tags (story S105, #379). Pure functions: no I/O, no model, no clock.

    tags(headline)      -> the tags that passed the measurement (SHIPPED), as a set of strings
    all_tags(headline)  -> every rule's answer, the ones that did not pass included (measurement and research only)
    with_tags(items)    -> copies of evidence-pack items or news_item rows with a "tags" list added, at read time

A headline is untrusted text. These functions only match fixed patterns on it; nothing written in a headline is
ever interpreted, followed or executed, and the output is always a subset of ALL_TAGS.

The tags are computed when a headline is read. Nothing is stored (no column, no migration), so a change of rules
changes what a reader sees; RULES_VERSION says which rules produced a result.

Nothing may use a tag for an order in this story. The engine, risk, strategies, broker, features and replay do not
import this module (tests/unit/test_news_tags.py). A rule that wants a tag later declares it in its own
pre-registration (decision 15).

Measured on src/kaibot/news/tagset (300 hand-labelled headlines, labelled before these rules were written): see
tagset/README.md for the table. A tag is in SHIPPED only with at least 20 labelled positives and precision of at
least 95% on that sample.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from kaibot.news import text

RULES_VERSION = 1
ALL_TAGS = ("results", "guidance_up", "guidance_down", "analyst_up", "analyst_down", "deal", "offering", "buyback",
            "management_change", "legal_regulatory", "listing")
# The outcome of tagset/measure.py on headlines_v1 (README.md). Change it only together with a new measurement.
SHIPPED: frozenset[str] = frozenset({"analyst_up"})
NOT_SHIPPED: dict[str, str] = {
    "results": "10 positives in the sample: precision not established",
    "guidance_up": "2 positives in the sample: precision not established",
    "guidance_down": "no positive in the sample: precision not established",
    "analyst_down": "11 positives in the sample: precision not established",
    "deal": "12 positives in the sample: precision not established",
    "offering": "4 positives in the sample: precision not established",
    "buyback": "2 positives in the sample: precision not established",
    "management_change": "10 positives in the sample: precision not established",
    "legal_regulatory": "19 positives in the sample: precision not established",
    "listing": "3 positives in the sample: precision not established",
}
MAX_LEN = 500

_F = re.IGNORECASE
_UP = r"(?:rais(?:es|ed|ing)|increas(?:es|ed|ing)|lifts?|lifted|boosts?|boosted|hikes?|hiked|ups)"
_DOWN = r"(?:cuts?|cutting|lower(?:s|ed|ing)|reduc(?:es|ed|ing)|trims?|trimmed|slash(?:es|ed)|decreas(?:es|ed))"
_MONEY = r"[$€£]?\s?(\d[\d,]*(?:\.\d+)?)"

# ---- results
_REPORT = re.compile(r"\b(?:reports?|reported|posts?|posted|announces?|announced|releases?|released|delivers?)\b", _F)
_PERIOD = re.compile(r"\b(?:q[1-4]|(?:first|second|third|fourth|1st|2nd|3rd|4th)[- ]quarter|quarterly|half[- ]year|"
                     r"full[- ]year|fiscal|fy ?\d{2,4}|annual)\b", _F)
_FIN = re.compile(r"\b(?:results|earnings|profit|loss|revenue|sales|eps|trading update)\b", _F)
_NOT_RESULTS = re.compile(
    r"\b(?:to|will|plans? to|set to|scheduled to|expected to) (?:announce|report|release|host|hold|post|webcast|"
    r"discuss)\b|\b(?:date|conference call|earnings call|investor call|webcast|preview|ahead of|following|after|"
    r"production|clinical|trial|phase [1-4i]+|study|topline|top-line|data|survey)\b", _F)
_EPS_LINE = re.compile(r"\bq[1-4]\b.{0,40}\b(?:eps|sales|revenue)\b.{0,60}\b(?:beats?|miss(?:es)?|in-?line|"
                       r"estimate|vs\.?)\b", _F)

# ---- guidance
_GUIDE = r"(?:guidance|outlook|forecast)"
_GUIDE_UP = re.compile(rf"\b{_UP}\b.{{0,80}}\b{_GUIDE}\b|\b{_GUIDE}\b.{{0,20}}\b(?:raised|increased|lifted|boosted)\b",
                       _F)
_GUIDE_DOWN = re.compile(rf"\b{_DOWN}\b.{{0,80}}\b{_GUIDE}\b|\b{_GUIDE}\b.{{0,20}}\b(?:cut|lowered|reduced|slashed|"
                         rf"trimmed)\b", _F)

# ---- analyst
_PT = re.compile(r"\b(?:price targets?|target price|price objective|pt)\b", _F)
_PT_UP = re.compile(rf"\b{_UP}\b", _F)
_PT_DOWN = re.compile(rf"\b{_DOWN}\b", _F)
_TO_FROM = re.compile(rf"\bto {_MONEY}\s+from {_MONEY}", _F)
_FROM_TO = re.compile(rf"\bfrom {_MONEY}\s+to {_MONEY}", _F)
_RATING = re.compile(r"\b(?:buy|outperform|overweight|neutral|hold|sell|underperform|underweight|market perform|"
                     r"sector perform|peer perform|equal[- ]?weight|in[- ]line|accumulate|rating)\b", _F)
_UPGRADE = re.compile(r"\bupgrad(?:es|ed)\b", _F)
_DOWNGRADE = re.compile(r"\bdowngrad(?:es|ed)\b", _F)

# ---- deal
_DEAL = re.compile(
    r"\bto acquire\b|\bacquires\b|\bacquired by\b|\bacquisition of\b|\bto be acquired\b|\bmerger\b|\bto merge\b|"
    r"\bmerges with\b|\btakeover\b|\bbuyout\b|\bagrees? to (?:buy|acquire|sell)\b|\bbusiness combination\b|"
    r"\bdefinitive agreement to (?:acquire|sell|buy|merge)\b|\bto divest\b|\bdivests\b|\bdivestiture\b|"
    r"\bcompletes (?:the )?(?:acquisition|merger|sale)\b|"
    r"\bto buy\b.{1,60}\bfor [$€£]?\s?\d[\d.,]* ?(?:billion|million|bn|mln)\b|"
    r"\b(?:to sell|sells|sale of)\b.{0,60}\b(?:business|unit|division|subsidiary)\b", _F)
_STAKE = re.compile(r"\bstake\b", _F)

# ---- offering
_OFFERING = re.compile(
    r"\b(?:public|secondary|follow-on|registered direct|equity|share|stock|notes|debt|bond) offering\b|"
    r"\boffering of (?:[$€£]|common|senior|shares|notes|convertible)|\bprivate placement\b|\bat-the-market\b|"
    r"\bconvertible (?:senior )?notes\b|\bequity(?: and equity-linked)? financing\b|"
    r"\b(?:bond|debt|notes?) (?:sale|issue|issuance)\b|"
    r"\b(?:prices?|priced|issues?|sells?) [$€£]?\s?\d[\d.,]* ?(?:billion|million|bn|mln)? ?(?:of |in )?"
    r"(?:senior |convertible |unsecured )*(?:notes|bonds)\b|"
    r"\b(?:prices?|priced|files? for|launch(?:es|ed)?|completes?|closes?|plans?|syndicate for|underwrit\w+)\b"
    r".{0,60}\b(?:ipo|initial public offering)\b", _F)

# ---- buyback
_BUYBACK = re.compile(
    r"\bbuy-?backs?\b|\bbuy back\b|\b(?:share|stock) repurchases?\b|\brepurchase (?:program|plan|authorization)\b|"
    r"\bto repurchase\b|\brepurchases? (?:of )?(?:up to )?[$€£]?\s?\d[\d.,]* ?(?:billion|million)? ?(?:of |in )?"
    r"(?:its )?(?:own )?(?:common )?(?:shares|stock)\b", _F)

# ---- management change
_TITLE = (r"(?:ceo|cfo|coo|cto|cio|cao|chief \w+ officer|chief executive|president|chair(?:man|woman|person)?|"
          r"director|board|general manager|regional manager|managing director|vice president|evp|svp|head of|"
          r"general counsel|treasurer)")
_MGMT = re.compile(
    rf"\b(?:appoints?|appointed|names?|named|hires?|hired|elects?|elected|promotes?|promoted|taps)\b.{{0,120}}"
    rf"\b{_TITLE}\b|"
    rf"\b(?:joins?|to join)\b.{{1,60}}\bas (?:its |the |new |a )*{_TITLE}\b|"
    rf"\b{_TITLE}\b.{{0,60}}\b(?:steps? down|resigns?|resigned|retires?|to retire|departs?|to depart|ousted)\b|"
    rf"\b(?:steps? down|resigns?|resigned|retires?|to retire|departs?)\b.{{0,60}}\bas (?:its |the )*{_TITLE}\b|"
    r"\bsuccession\b|\b(?:leadership|management|executive) (?:changes?|transition)\b|\bceo transition\b", _F)

# ---- legal and regulatory
_AGENCY = (r"(?:fda|ema|ftc|doj|sec|fcc|ntsb|stb|cma|epa|faa|nhtsa|cfpb|european commission|justice department|"
           r"surface transportation board|competition and markets authority|attorney general|regulators?)")
_ACTION = (r"(?:approv\w+|clear(?:s|ed|ance)|den(?:y|ies|ied)|reject\w*|block\w*|investigat(?:es|ed|ing|ion)|"
           r"prob(?:es|ed|ing)|su(?:es|ed|ing)|fine[sd]?|charg(?:es|ed)|designat\w+|propos\w+|rul(?:es|ed|ing)|"
           r"orders?|ordered|grants?|granted|petitions?|exempt\w*|authoriz\w+)")
_LEGAL = re.compile(
    r"\blawsuits?\b|\bsu(?:es|ed|ing)\b|\bclass action\b|\bcourt\b|\bjudge\b|\bjury\b|\bverdict\b|\bsettlement\b|"
    r"\bsettles\b|\blitigation\b|\bantitrust\b|\bsubpoena\w*\b|\bindict\w*\b|\bprobe\b|"
    r"\binvestigat(?:es|ed|ing|ion|ions)\b|\bfined\b|\bpatent infringement\b|\binjunction\b|\bruling\b|"
    r"\bconsent decree\b|\bwarning letter\b|\bsecurities law\b|"
    r"\bregulatory (?:approval|clearance|review|probe|scrutiny)\b|"
    r"\breceiv(?:es|ed)\b.{0,40}\b(?:approval|authorization|clearance)\b|"
    rf"\b{_AGENCY}\b.{{0,80}}\b{_ACTION}\b|\b{_ACTION}\b.{{0,80}}\b{_AGENCY}\b", _F)

# ---- listing and index changes
_INDEX = (r"(?:s&p (?:500|400|600|midcap|smallcap)|nasdaq[- ]?100|dow jones industrial|russell \d+|ftse \d+|"
          r"msci \w+ index)")
_LISTING = re.compile(
    rf"\b(?:to join|joins?|added to|to be added to|joining|removed from|dropped from)\b.{{0,30}}\b{_INDEX}|"
    r"\bindex (?:addition|inclusion|deletion)\b|\b(?:begins?|starts?|to begin|to start) trading on\b|"
    r"\bto list on\b|\blists on\b|\blisting on\b|\bdirect listing\b|\buplist\w*\b|"
    r"\bdebuts? on (?:the )?(?:nyse|nasdaq|new york stock exchange)\b|"
    r"\blaunch(?:es|ed)?\b.{0,80}\bdepositary receipts?\b|"
    r"\blaunch(?:es|ed)?\b.{0,80}\betf\b.{0,30}\bon (?:the )?(?:nyse|nasdaq|cboe|tsx)\b|"
    r"\b(?:prices?|priced|files? for|launch(?:es|ed)?|completes?|closes?|syndicate for|underwrit\w+)\b"
    r".{0,60}\b(?:ipo|initial public offering)\b", _F)


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _pt_direction(h: str) -> int:
    """+1 a price target raised, -1 cut, 0 unknown. Two figures ("to $90 from $95") decide before the verb."""
    m = _TO_FROM.search(h)
    new_old = (m.group(1), m.group(2)) if m else None
    if new_old is None:
        m = _FROM_TO.search(h)
        new_old = (m.group(2), m.group(1)) if m else None
    if new_old is not None:
        new, old = _num(new_old[0]), _num(new_old[1])
        return (new > old) - (new < old)
    up, down = bool(_PT_UP.search(h)), bool(_PT_DOWN.search(h))
    return int(up and not down) - int(down and not up)


def _results(h: str) -> bool:
    if _NOT_RESULTS.search(h):
        return False
    if _EPS_LINE.search(h):
        return True
    m = _REPORT.search(h)
    if m is None:
        return False
    tail = h[m.end():]
    return bool(_PERIOD.search(tail) and _FIN.search(tail))


def all_tags(headline: object) -> set[str]:
    """Every rule's answer for one headline, the tags that did not pass the measurement included."""
    h = text.clean(headline, MAX_LEN).replace("’", "'")
    out: set[str] = set()
    if not h:
        return out
    analyst = bool(_PT.search(h))
    if _results(h):
        out.add("results")
    if not analyst:
        if _GUIDE_UP.search(h):
            out.add("guidance_up")
        if _GUIDE_DOWN.search(h):
            out.add("guidance_down")
    direction = _pt_direction(h) if analyst else 0
    if direction > 0 or (_UPGRADE.search(h) and _RATING.search(h)):
        out.add("analyst_up")
    if direction < 0 or (_DOWNGRADE.search(h) and _RATING.search(h)):
        out.add("analyst_down")
    if _DEAL.search(h) and not _STAKE.search(h):
        out.add("deal")
    if _OFFERING.search(h):
        out.add("offering")
    if _BUYBACK.search(h):
        out.add("buyback")
    if _MGMT.search(h):
        out.add("management_change")
    if _LEGAL.search(h):
        out.add("legal_regulatory")
    if _LISTING.search(h):
        out.add("listing")
    return out


def tags(headline: object) -> set[str]:
    """The measured tags of one headline: a subset of SHIPPED. The same headline always gives the same set."""
    return all_tags(headline) & SHIPPED


def with_tags(items: Iterable[dict]) -> list[dict]:
    """Copies of evidence-pack items or news_item rows with "tags" (a sorted list) added from their "headline".

    Read-time only: the input is not changed and nothing is stored. For the evidence pack and for research; never
    for an order.
    """
    return [{**it, "tags": sorted(tags(it.get("headline")))} for it in items if isinstance(it, dict)]
