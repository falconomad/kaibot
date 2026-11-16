"""Colour maths for the kaibo brand system v1 (issue #265): sRGB <-> OKLCH, WCAG contrast, scales built on lightness."""
from __future__ import annotations

import math


def _lin(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _gam(c):
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def hex_rgb(h):
    return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))


def rgb_hex(rgb):
    return "#" + "".join(f"{min(255, max(0, round(c * 255))):02X}" for c in rgb)


def oklab(h):
    r, g, b = (_lin(c) for c in hex_rgb(h))
    l, m, s = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b, 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b, 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l, m, s = l ** (1 / 3), m ** (1 / 3), s ** (1 / 3)
    return 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s, 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s, 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s


def oklch(h):
    L, a, b = oklab(h)
    return L, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360


def _rgb(L, C, H):
    a, b = C * math.cos(math.radians(H)), C * math.sin(math.radians(H))
    l, m, s = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3, (L - 0.1055613458 * a - 0.0638541728 * b) ** 3, (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s, -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s, -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)


def from_oklch(L, C, H):
    """The sRGB hex for an OKLCH colour; chroma is reduced until it fits the screen's range."""
    for _ in range(60):
        lin = _rgb(L, C, H)
        if all(-0.0005 <= c <= 1.0005 for c in lin):
            break
        C *= 0.97
    return rgb_hex(tuple(_gam(min(1, max(0, c))) for c in _rgb(L, C, H)))


def lum(h):
    r, g, b = (_lin(c) for c in hex_rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def mix(fg, bg, alpha):
    """fg at the given opacity over bg (for scrims and translucent bars)."""
    return rgb_hex(tuple(f * alpha + b * (1 - alpha) for f, b in zip(hex_rgb(fg), hex_rgb(bg))))
