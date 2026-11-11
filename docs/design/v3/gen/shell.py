"""Shared shell for v3 artboards: helmet (fonts, motion, responsive rules), top bar, phone tab bar."""
from pathlib import Path

_HERE = Path(__file__).resolve().parent
CSS = (_HERE / 'kb_motion.css').read_text()       # motion, responsive and reduced-motion rules
TAB = (_HERE / 'kb_tabbar.html').read_text()      # phone tab bar
MORE = (_HERE / 'kb_more.css').read_text()        # baseline-rise charts, ring, skeleton shimmer (opt-in: shell.CSS += shell.MORE)
NAV = [("Home", "Home.dc.html"), ("Trades", "Trades.dc.html"), ("Report", "Report.dc.html"),
       ("Engine", "Engine.dc.html"), ("Run &amp; settings", "Settings.dc.html")]


def head(title, active, h):
    links = "\n".join(
        f'      <a href="{href}" aria-current="page" style="color: #F4F5F7; background: #1B1E24; border-radius: 999px; padding: 8px 14px">{n}</a>'
        if n.startswith(active) else f'      <a href="{href}" style="padding: 8px 14px">{n}</a>' for n, href in NAV)
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Kaibot · {title}</title>
<script src="./support.js"></script>
</head>
<body>
<x-dc>
<helmet>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
body{{margin:0;background:#0A0B0D}}
a{{color:#7FB2FF}}a:hover{{color:#B3D1FF}}
.num{{font-variant-numeric:tabular-nums}}
.nav a{{color:#A1A8B3;text-decoration:none}}.nav a:hover{{color:#F4F5F7}}
{CSS}
button:focus-visible,input:focus-visible,a:focus-visible,select:focus-visible{{outline:2px solid #4F95F0;outline-offset:2px}}
</style>
</helmet>
<div style="min-height: {h}px; background: #0A0B0D; color: #F4F5F7; font-family: Inter, system-ui, sans-serif">
  <header class="bar" style="display: flex; flex-wrap: wrap; align-items: center; gap: 24px; padding: 16px 40px; border-bottom: 1px solid #1B1E24">
    <a href="Home.dc.html" style="display: flex; align-items: center; gap: 10px; color: #F4F5F7; text-decoration: none">
      <svg width="26" height="26" viewBox="0 0 28 28" aria-hidden="true"><rect x="1" y="1" width="26" height="26" rx="8" fill="#F4F5F7"></rect><path d="M8 18 L12.5 12.5 L15.5 15.5 L20 9.5" stroke="#0A0B0D" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"></path></svg>
      <span style="font-weight: 700; font-size: 18px; letter-spacing: -0.02em">kaibot</span>
    </a>
    <nav class="nav topnav" aria-label="Main" style="display: flex; flex-wrap: wrap; gap: 4px; font-size: 15px; font-weight: 500">
{links}
    </nav>
    <div style="flex: 1"></div>
    <span style="display: flex; align-items: center; gap: 8px; font-size: 13px; color: #A1A8B3; background: #131519; border: 1px solid #262A31; border-radius: 999px; padding: 7px 12px">
      <span class="live-dot" aria-hidden="true" style="width: 8px; height: 8px; background: #3DDC97"></span>
      <span style="color: #F4F5F7; font-weight: 500">Running</span><span class="hide-sm"> · Mac mini · 6 s ago</span>
    </span>
    <span style="font-size: 11px; font-weight: 600; letter-spacing: 0.08em; color: #0A0B0D; background: #F5B64C; border-radius: 999px; padding: 4px 9px">PAPER · HUNTER</span>
  </header>
'''


def tail(active, script, h, props=None):
    tabs = {"Home": "tabHome", "Trades": "tabTrades", "Report": "tabReport", "Engine": "tabEngine", "Run": "tabRun"}
    tb = TAB
    for k, v in tabs.items():
        tb = tb.replace("{{" + v + "}}", "#F4F5F7" if active.startswith(k) else "#7E8693")
    return f'''{tb}</div>
</x-dc>
<script type="text/x-dc" data-dc-script data-props='{{"$preview":{{"width":1440,"height":{h}}}}}'>
{script}
</script>
</body>
</html>
'''

CARD = 'background: #131519; border: 1px solid #262A31; border-radius: 24px'
