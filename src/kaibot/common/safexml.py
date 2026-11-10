"""Parse untrusted XML (EDGAR Atom feed, Form 4) without entity-expansion or external-entity risks.

Why not defusedxml: a stdlib pre-pass gives the same protection for our inputs without a new dependency.
Neither EDGAR feed nor ownership XML ever carries a DTD, so ANY document type or entity declaration is refused
before ElementTree sees the bytes: no "billion laughs" / quadratic blow-up (entities must be declared in a DTD to
expand) and no external entities (ElementTree never fetches them anyway). The pre-pass runs pyexpat itself, so
encoding tricks (UTF-16, odd spacing) cannot hide a declaration from a text search.
"""

from __future__ import annotations

import pyexpat
import xml.etree.ElementTree as ET


class UnsafeXML(ValueError):
    pass


def _refuse(*_a) -> None:
    raise UnsafeXML("DTD / entity declarations are not allowed")


def fromstring(data: bytes | str, max_bytes: int = 8 * 1024 * 1024) -> ET.Element:
    raw = data  # str is parsed as text (declared encoding ignored), bytes honour the XML declaration
    if len(raw) > max_bytes:
        raise UnsafeXML(f"XML larger than {max_bytes} bytes")
    p = pyexpat.ParserCreate()
    p.StartDoctypeDeclHandler = _refuse
    p.EntityDeclHandler = _refuse
    p.UnparsedEntityDeclHandler = _refuse
    p.ExternalEntityRefHandler = _refuse
    try:
        p.Parse(raw, True)
    except pyexpat.ExpatError as e:
        raise UnsafeXML(f"malformed XML: {e}") from None
    return ET.fromstring(raw)
