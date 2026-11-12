"""M7 · dashboard for a multi-day book (issue #129): Overview, Holdings, one holding, Next close, Evidence and the
empty, loading, halted and error states. Desktop and phone boards.

Run from this folder: python3 multiday.py
Writes ../project/v3/{Overview,Holdings,Holding,NextClose,Evidence}[Mobile].dc.html MultidayStates[Mobile].dc.html and HoldingSettings[Mobile].dc.html.

  m7_data.py      sample data, one place for every number
  m7_ui.py        nav, phone tab bar, CSS, icons, the chart
  m7_overview.py  Overview (Home)
  m7_holdings.py  Holdings and one holding
  m7_next.py      Next close
  m7_evidence.py  Evidence (gates A to D)
  m7_states.py    empty, loading, halted and error states
  m7_settings.py  Holding settings: the owner's tighten-only settings (issue #132)

Check the result with: python3 preview.py --shots shots_m7.json --check
"""
import sys

sys.path.insert(0, '.')
import m7_evidence  # noqa: E402
import m7_holdings  # noqa: E402
import m7_next  # noqa: E402
import m7_overview  # noqa: E402
import m7_settings  # noqa: E402
import m7_states  # noqa: E402

if __name__ == '__main__':
    m7_overview.build()
    m7_holdings.build()
    m7_holdings.build_one()
    m7_next.build()
    m7_evidence.build()
    m7_states.build()
    m7_settings.build()
    print('ok')
