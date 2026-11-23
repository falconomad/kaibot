"""The preloaded "core" cut of Outfit (frontend performance pass, 23 Nov 2026). Dev-only; run after a font update.

    python3 -m pip install fonttools brotli   # once, in any environment
    python3 apps/web/scripts/subset_fonts.py            # writes public/fonts/outfit-core-<w>-normal.woff2
    python3 apps/web/scripts/subset_fonts.py --check    # fails if the core files or tokens.css are out of date

index.html preloads the four core files: the characters the dashboard writes (basic Latin, the Latin-1 punctuation
and signs, the general punctuation block, the minus sign and the up/down arrows), about 9 kB a weight instead of 14.
The full Latin files stay as they were and are declared for the rest of their range only (accented letters, ligature
marks, the euro sign...), so the browser fetches one only when a page shows such a character, as with Google Fonts'
own subsets. The two cuts are drawn from the same file, so every character looks exactly as before.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

from fontTools import subset
from fontTools.ttLib import TTFont

WEB = pathlib.Path(__file__).resolve().parents[1]
FONTS = WEB / "public" / "fonts"
TOKENS = WEB / "src" / "tokens.css"
WEIGHTS = (400, 500, 600, 700)
# Fontsource's latin subset, as tokens.css declared it before the split.
LATIN = ("U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,"
         "U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD")
# What the core cut carries: everything up to U+00BF but the C0 controls and DEL, the multiplication and division
# signs, the general punctuation block (dashes, quotes, the ellipsis, the narrow no-break space Intl puts before
# "AM"), the up/down arrows, the minus sign and the division slash.
CORE = "U+0020-007E,U+00A0-00BF,U+00D7,U+00F7,U+2000-206F,U+2191,U+2193,U+2212,U+2215"


def parse(ranges: str) -> set[int]:
    out: set[int] = set()
    for part in ranges.split(","):
        a, _, b = part.strip()[2:].partition("-")
        out.update(range(int(a, 16), int(b or a, 16) + 1))
    return out


def fmt(cps: set[int]) -> str:
    s, out = sorted(cps), []
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[j] + 1:
            j += 1
        out.append(f"U+{s[i]:04X}" + (f"-{s[j]:04X}" if j > i else ""))
        i = j + 1
    return ",".join(out)


REST = fmt(parse(LATIN) - parse(CORE))
CORE_RANGE = fmt(parse(CORE) & parse(LATIN))


def face(w: int) -> str:
    """The two @font-face rules of one weight in tokens.css: the full file for the rest of its range, then the core."""
    return (f'@font-face {{ font-family: "Outfit"; font-style: normal; font-weight: {w}; font-display: swap;\n'
            f'  src: url("/fonts/outfit-latin-{w}-normal.woff2") format("woff2"); unicode-range: {REST}; }}\n'
            f'@font-face {{ font-family: "Outfit"; font-style: normal; font-weight: {w}; font-display: swap;\n'
            f'  src: url("/fonts/outfit-core-{w}-normal.woff2") format("woff2"); unicode-range: {CORE_RANGE}; }}\n')


def cut(w: int) -> bytes:
    src = FONTS / f"outfit-latin-{w}-normal.woff2"
    font = TTFont(src)
    opts = subset.Options()
    opts.flavor = "woff2"
    opts.layout_features = ["*"]
    opts.name_IDs = ["*"]
    opts.notdef_outline = True
    sub = subset.Subsetter(opts)
    sub.populate(unicodes=sorted(parse(CORE) & set(font.getBestCmap())))
    sub.subset(font)
    out = FONTS / f".outfit-core-{w}.tmp"
    font.flavor = "woff2"
    font.save(out)
    data = out.read_bytes()
    out.unlink()
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    css = TOKENS.read_text()
    want = "".join(face(w) for w in WEIGHTS)
    run = re.compile(r'(?:@font-face \{ font-family: "Outfit"; [^}]*\}\n)+')
    bad = []
    for w in WEIGHTS:
        f = FONTS / f"outfit-core-{w}-normal.woff2"
        have = TTFont(f).getBestCmap() if f.exists() else {}
        full = set(TTFont(FONTS / f"outfit-latin-{w}-normal.woff2").getBestCmap())
        if set(have) != parse(CORE) & full:
            if a.check:
                bad.append(f"{f.name}: its characters are not the core set")
            else:
                f.write_bytes(cut(w))
                full_size = (FONTS / f"outfit-latin-{w}-normal.woff2").stat().st_size
                print(f"{f.name}: {f.stat().st_size} bytes (full latin file {full_size})")
    if want not in css:
        if a.check:
            bad.append("tokens.css: the Outfit @font-face rules are not the ones this script writes")
        else:
            TOKENS.write_text(run.sub(lambda _m: want, css, count=1))
            print("tokens.css: the Outfit rules rewritten")
    for b in bad:
        print(b, file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
