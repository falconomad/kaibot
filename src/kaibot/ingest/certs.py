"""Root certificates for the Data team's command-line scripts (standard library only, safe to import anywhere).

python.org's macOS Python ships without root certificates, so every HTTPS call fails before it reaches the
server ("http 0"). scripts/run_engine.sh and scripts/fetch_history.sh export SSL_CERT_FILE from certifi in the
shell; scripts started directly with Python call `ensure_ca_bundle()` first instead. Not used by the engine.
"""

from __future__ import annotations

import os

CERT_HINT = ("http 0 everywhere usually means missing root certificates: "
             "pip install certifi, or run with .venv/bin/python")


def ensure_ca_bundle() -> str | None:
    """Point SSL_CERT_FILE at certifi's bundle when it is not set. Call before the first network request.

    Returns the path it set, or None when it changed nothing (already set, or certifi is not installed)."""
    if os.environ.get("SSL_CERT_FILE"):
        return None
    try:
        import certifi
    except ImportError:
        return None
    path = certifi.where()
    os.environ["SSL_CERT_FILE"] = path
    return path
