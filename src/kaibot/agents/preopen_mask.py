"""The masked arm of the shadow pre-open review (M12 3.9, #471).

A model that recognises a company can answer from what it remembers about it rather than from the headline in
front of it (Lopez-Lira, Tang and Zhu: anonymised earnings calls named more than 85% of the time for large
technology firms; docs/research/engine_audit/report.md). The shadow therefore sends each session's buys twice: as
they are (the raw arm, the one a veto would use) and masked, with the ticker and the company's name replaced by an
alias (STOCK_A, STOCK_B, ...) in the candidate's id, symbol and every headline. The pre-registration judges the
raw arm and reads the masked arm beside it: a veto whose value disappears once names are hidden is labelled
identity-driven (docs/prereg/preopen_negative_news_veto.md).

Names come from the committed ticker -> issuer list (`ingest/sp500/ivv_names.csv`, IVV holdings), with legal
suffixes dropped; a ticker that is not in it is masked by ticker only (the record says so). Masking errs towards
hiding too much (a one-word name is masked wherever it appears as a word). It changes only what is shown, never the
features (scores, ranks, earnings dates) and never the plan.
"""

from __future__ import annotations

import csv
import dataclasses
import re
import string
from collections.abc import Iterable, Mapping
from functools import lru_cache
from pathlib import Path

from kaibot.agents.preopen import ReviewCandidate, candidate_id

MASK_VERSION = "mask-v1"
NAMES_CSV = Path(__file__).resolve().parents[1] / "ingest" / "sp500" / "ivv_names.csv"
_DROP = {"INC", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "PLC", "NV", "THE", "HOLDINGS", "HOLDING", "GROUP",
         "CLASS", "CL", "SA", "LP", "LLC", "TRUST", "INCORPORATED", "LIMITED", "AG", "SE", "NEW", "DE", "A", "B",
         "C", "AND", "COS", "INTL", "INTERNATIONAL"}
# first words too common to mask alone ("General" Motors, "American" Airlines): only the whole name is masked
_GENERIC = {"GENERAL", "AMERICAN", "FIRST", "UNITED", "NATIONAL", "INTERNATIONAL", "GLOBAL", "DIGITAL", "SOUTHERN",
            "NORTHERN", "EASTERN", "WESTERN", "PUBLIC", "CONSOLIDATED", "ADVANCED", "APPLIED", "ROYAL", "STATE",
            "CENTRAL", "PACIFIC", "ATLANTIC", "TECHNOLOGIES", "SYSTEMS", "ENERGY", "HEALTH", "BANK", "CAPITAL",
            "REALTY", "PROPERTIES", "DATA", "LIFE", "WASTE", "BEST", "UNIVERSAL", "MARATHON", "DOMINION", "CITIZENS"}


def name_words(name: str) -> list[str]:
    w = re.sub(r"[^A-Za-z0-9& ]", " ", name).replace("&", " ").split()
    return [x for x in w if x.upper() not in _DROP]


@lru_cache(maxsize=1)
def _names_from_csv() -> dict[str, tuple[str, ...]]:
    out: dict[str, list[str]] = {}
    try:
        with NAMES_CSV.open() as f:
            rows = [r for r in csv.reader(line for line in f if not line.startswith("#"))]
    except OSError:
        return {}
    for r in rows[1:]:
        if len(r) >= 2 and r[0] and r[1]:
            out.setdefault(r[0].strip().upper(), []).append(r[1].strip())
    return {k: tuple(dict.fromkeys(v)) for k, v in out.items()}


def names_for(symbol: str, extra: Mapping[str, Iterable[str]] | None = None) -> tuple[str, ...]:
    got = list(_names_from_csv().get(symbol.upper(), ()))
    if extra and symbol in extra:
        got += list(extra[symbol])
    return tuple(dict.fromkeys(got))


# between the words of a name: spaces, a hyphen or an ampersand ("Coca-Cola", "AT&T", "Brown-Forman"; QA on #581)
_JOIN = r"(?:\s*[-&]\s*|\s+)"


def _patterns(symbol: str, names: Iterable[str]) -> list[re.Pattern]:
    pats: list[re.Pattern] = []  # names first, then the ticker, so "GE Aerospace" goes whole (QA on #581)
    names_pats: list[re.Pattern] = []
    # a share class may be written BRK.B, BRK-B or BRK/B
    sym = re.escape(symbol.upper()).replace(r"\.", r"[./-]")
    if len(symbol) >= 2:  # QA on #581: two-letter tickers ("GE beats estimates") too, case-sensitive, as words
        pats.append(re.compile(rf"\$?\b{sym}\b"))
    else:  # one letter: only where it is plainly the ticker
        pats.append(re.compile(rf"\b{sym}\b(?=\s+(?:shares|stock)\b)"))
    pats.append(re.compile(rf"\${sym}\b|\({sym}\)|(?:NYSE|NASDAQ|Nasdaq|AMEX|NYSE American)\s*:\s*{sym}\b"))
    phrases = set()
    for n in names:
        w = name_words(n)
        if not w:
            continue
        phrases.add(_JOIN.join(re.escape(x) for x in w))
        if len(w[0]) >= 4 and w[0].upper() not in _GENERIC:
            phrases.add(re.escape(w[0]))
    for ph in sorted(phrases, key=len, reverse=True):
        names_pats.append(re.compile(rf"\b{ph}(?:'s|’s)?\b", re.IGNORECASE))
    return names_pats + pats


def mask_text(text: str, symbol: str, alias: str, names: Iterable[str] = ()) -> str:
    for p in _patterns(symbol, names):
        text = p.sub(alias, text)
    return text


def alias_for(i: int) -> str:
    letters = string.ascii_uppercase
    return "STOCK_" + (letters[i] if i < 26 else letters[i // 26 - 1] + letters[i % 26])


@dataclasses.dataclass
class Masker:
    """`transform` for `preopen.review`: masks the candidates and remembers the aliases to map verdicts back."""

    extra_names: Mapping[str, Iterable[str]] | None = None
    aliases: dict[str, str] = dataclasses.field(default_factory=dict)  # alias -> real symbol
    by_name: dict[str, bool] = dataclasses.field(default_factory=dict)  # real symbol -> a name was known

    def __call__(self, cands: list[ReviewCandidate]) -> list[ReviewCandidate]:
        out = []
        syms = list(dict.fromkeys(c.symbol for c in cands))
        alias = {s: alias_for(i) for i, s in enumerate(syms)}
        self.aliases = {a: s for s, a in alias.items()}
        for c in cands:
            names = names_for(c.symbol, self.extra_names)
            self.by_name[c.symbol] = bool(names)
            a = alias[c.symbol]
            ev = []
            for e in c.evidence:
                e = dict(e)
                for k in ("headline",):
                    if isinstance(e.get(k), str):
                        for s in syms:  # a headline may name another candidate too
                            e[k] = mask_text(e[k], s, alias[s], names_for(s, self.extra_names))
                ev.append(e)
            reasons = tuple(mask_text(r, c.symbol, a, names) for r in c.reasons)
            out.append(dataclasses.replace(c, candidate_id=candidate_id((c.strategy, a)), symbol=a,
                                           evidence=tuple(ev), reasons=reasons))
        return out

    def real_id(self, cid: str) -> str:
        strat, _, sym = str(cid).partition(":")
        return candidate_id((strat, self.aliases.get(sym, sym)))

    def unmask_answer(self, answer: dict | None) -> dict | None:
        if not isinstance(answer, dict):
            return answer
        vs = [{**v, "candidate_id": self.real_id(v.get("candidate_id", ""))} for v in answer.get("verdicts") or []]
        return {**answer, "verdicts": vs}

    def unmask_verdicts(self, verdicts: Mapping[str, str]) -> dict[str, str]:
        return {self.real_id(k): v for k, v in verdicts.items()}
