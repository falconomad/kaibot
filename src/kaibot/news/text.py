"""Headline hygiene and near-duplicate detection (story S33). Pure functions, no I/O.

- `clean`: news text is untrusted (it ends up in an LLM prompt): control/format characters are removed, whitespace
  collapsed and the length capped.
- `canonical`: lowercased, wire/source tags stripped ("UPDATE 2-", "(Reuters)", "- Bloomberg", "[PRN]"...),
  punctuation and whitespace collapsed. `headline_sha1` is the SHA-1 of that string (exact-echo key).
- `minhash`: 64-permutation MinHash over word 3-shingles of the canonical headline. Universal hashing
  h_i(x) = (a_i * x + b_i) mod (2^61 - 1) with seeds fixed by a constant, so signatures are identical across
  processes and can be stored (news_cluster.minhash, bigint[]: every value is < 2^61).
"""

from __future__ import annotations

import hashlib
import random
import re
import unicodedata

NUM_PERM = 64
SHINGLE = 3
JACCARD_MIN = 0.8
_P = (1 << 61) - 1
_rng = random.Random(0x6B616962)  # "kaib": fixed seed -> deterministic permutations, never change it
_A = tuple(_rng.randrange(1, _P) for _ in range(NUM_PERM))
_B = tuple(_rng.randrange(0, _P) for _ in range(NUM_PERM))

_WIRES = r"(?:reuters|bloomberg|ap|associated press|dow jones|benzinga|prnewswire|pr newswire|" \
         r"globe ?newswire|gnw|prn|accesswire|business ?wire|businesswire|newsfile|marketwatch|cnbc|" \
         r"seeking alpha|zacks|motley fool|investing\.com|yahoo finance|the fly|thefly|street ?insider)"
_PREFIX = re.compile(r"^\s*(?:(?:update|corrected|refile|rpt|brief|exclusive|breaking|wrapup|press release|"
                     r"pr|instant view|factbox|analysis)\s*\d*\s*[-:–—|]\s*)+", re.I)
_SUFFIX = re.compile(rf"\s*(?:[-–—|:]\s*{_WIRES}|\(\s*{_WIRES}\s*\)|\[\s*{_WIRES}\s*\])\s*$", re.I)
_LEAD_TAG = re.compile(rf"^\s*(?:\(\s*{_WIRES}\s*\)|\[\s*{_WIRES}\s*\]|{_WIRES}\s*:)\s*", re.I)
_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)
_WS = re.compile(r"\s+")


def clean(text: object, max_len: int = 300) -> str:
    """Untrusted text -> printable single-line string of at most max_len characters."""
    if not isinstance(text, str):
        return ""
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"):  # controls, bidi/format, surrogates, private use
            out.append(" ")
        else:
            out.append(ch)
    s = _WS.sub(" ", "".join(out)).strip()
    return s[:max_len].rstrip()


def canonical(headline: str) -> str:
    s = clean(headline, 500)
    for _ in range(3):  # tags can stack: "UPDATE 1-BRIEF-... - Reuters (Reuters)"
        s2 = _SUFFIX.sub("", _LEAD_TAG.sub("", _PREFIX.sub("", s)))
        if s2 == s:
            break
        s = s2
    s = _PUNCT.sub(" ", s.lower()).replace("_", " ")
    return _WS.sub(" ", s).strip()


def headline_sha1(headline: str) -> str:
    return hashlib.sha1(canonical(headline).encode("utf-8")).hexdigest()


def shingles(canon: str, k: int = SHINGLE) -> set[str]:
    w = canon.split()
    if not w:
        return set()
    if len(w) < k:
        return {" ".join(w)}
    return {" ".join(w[i : i + k]) for i in range(len(w) - k + 1)}


def _h64(s: str) -> int:
    return int.from_bytes(hashlib.sha1(s.encode("utf-8")).digest()[:8], "big") % _P


def minhash(canon: str) -> list[int]:
    """MinHash signature (NUM_PERM ints < 2^61). An empty headline gets all-max (similar to nothing)."""
    xs = [_h64(s) for s in shingles(canon)]
    if not xs:
        return [_P] * NUM_PERM
    return [min((a * x + b) % _P for x in xs) for a, b in zip(_A, _B, strict=True)]


def similarity(a: list[int], b: list[int]) -> float:
    """Estimated Jaccard similarity: the fraction of equal signature slots."""
    if len(a) != NUM_PERM or len(b) != NUM_PERM:
        return 0.0
    return sum(1 for x, y in zip(a, b, strict=True) if x == y and x != _P) / NUM_PERM


def is_echo(a: list[int], b: list[int], threshold: float = JACCARD_MIN) -> bool:
    return similarity(a, b) >= threshold
