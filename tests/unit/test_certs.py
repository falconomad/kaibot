"""Root-certificate helper for the Data scripts: certifi fills SSL_CERT_FILE only when it is unset (no network)."""

from __future__ import annotations

import builtins
import os
import sys
import types
from pathlib import Path

import pytest

from kaibot.ingest import certs

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = "/fake/certifi/cacert.pem"


@pytest.fixture
def no_cert_env(monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", "x")  # registers the original value, so teardown restores it
    monkeypatch.delenv("SSL_CERT_FILE")


@pytest.fixture
def fake_certifi(monkeypatch):
    monkeypatch.setitem(sys.modules, "certifi", types.SimpleNamespace(where=lambda: BUNDLE))


def test_sets_ssl_cert_file_from_certifi_when_unset(no_cert_env, fake_certifi):
    assert certs.ensure_ca_bundle() == BUNDLE
    assert os.environ["SSL_CERT_FILE"] == BUNDLE


def test_keeps_an_existing_ssl_cert_file(monkeypatch, fake_certifi):
    monkeypatch.setenv("SSL_CERT_FILE", "/my/own/roots.pem")
    assert certs.ensure_ca_bundle() is None
    assert os.environ["SSL_CERT_FILE"] == "/my/own/roots.pem"


def test_does_nothing_when_certifi_is_missing(no_cert_env, monkeypatch):
    monkeypatch.delitem(sys.modules, "certifi", raising=False)
    real_import = builtins.__import__

    def no_certifi(name, *a, **kw):
        if name == "certifi":
            raise ImportError("No module named 'certifi'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_certifi)
    assert certs.ensure_ca_bundle() is None
    assert "SSL_CERT_FILE" not in os.environ


def test_helper_is_stdlib_only_and_not_used_by_the_engine():
    src = (ROOT / "src" / "kaibot" / "ingest" / "certs.py").read_text()
    assert "import os" in src and "pyarrow" not in src and "yaml" not in src
    for p in (ROOT / "src" / "kaibot" / "engine").rglob("*.py"):
        assert "ingest.certs" not in p.read_text(), p
