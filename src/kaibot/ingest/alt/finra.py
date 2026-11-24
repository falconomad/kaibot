"""FINRA Reg SHO daily short-sale volume (finra_regsho): SWITCHED OFF, stakeholder decision on #623 (24 Nov 2026).

The collector downloaded the CNMS files from cdn.finra.org. That falls under the finra.org Terms of Use, which
forbid creating a database of data from the FINRA website and using it with predictive analytics. The stakeholder
chose option (b): switch it off and remove it from research inputs. This module is kept only so that an old
schedule or import fails loudly with the reason instead of silently collecting again. `collect` always raises,
and kaibot.ingest.alt.store refuses to read or write data/alt/finra_regsho (store.DISABLED).

Old local files must not be used; the owner deletes them on the Mac (docs/data/finra_regsho.md).
Re-enabling needs a new stakeholder decision (e.g. FINRA Query API credentials under their own terms).
"""

from __future__ import annotations

from kaibot.ingest.alt.store import DISABLED, SourceDisabled

SOURCE = "finra_regsho"
DECISION = "#623"
REASON = DISABLED[SOURCE]

__all__ = ["DECISION", "REASON", "SOURCE", "SourceDisabled", "collect"]


def collect(*_args, **_kwargs) -> int:
    """Refuses to run: the FINRA collector is switched off (#623). Makes no network call and writes nothing."""
    raise SourceDisabled(REASON)
