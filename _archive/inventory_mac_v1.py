#!/usr/bin/env python3
"""
inventory_mac.py
Outputs a self-contained HTML software inventory report.
Usage: python3 inventory_mac.py > software_inventory.html
       open software_inventory.html
"""
import subprocess, os, sys
from datetime import datetime
from pathlib import Path

def run(cmd, default=""):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, shell=isinstance(cmd,str))
        return r.stdout.strip() if r.returncode == 0 else default
    except Exception:
        return default

def plistbuddy(key, path):
    return run(["/usr/libexec/PlistBuddy", "-c", f"Print {key}", str(path)])

def app_version(app_path):
    return plistbuddy("CFBundleShortVersionString", Path(app_path)/"Contents"/"Info.plist")

def scan_apps(directory):
    d = Path(directory)
    if not d.is_dir():
        return []
    items = []
    for app in sorted(d.glob("*.app")) + sorted((d/"..").glob("*/*.app")):
        app = app.resolve()
        if app.parent.resolve() == d.resolve() or app.parent.parent.resolve() == d.resolve():
            name = app.stem
            ver  = app_version(app)
            items.append((name, ver))
    # deduplicate preserving order
    seen = set()
    out = []
    for item in sorted(items, key=lambda x: x[0].lower()):
        if item[0] not in seen:
            seen.add(item[0])
            out.append(item)
    return out

def scan_plists(directory):
    d = Path(directory)
    if not d.is_dir():
        return []
    items = []
    for f in sorted(d.glob("*.plist")):
        label = plistbuddy("Label", f)
        items.append((label or f.name, f.name))
    return items

def brew_list(flag):
    if not run("which brew"):
        return None
    raw = run(["brew", "list", flag])
    return raw.splitlines() if raw else []

def brew_version(name):
    out = run(["brew", "list", "--versions", name])
    parts = out.split()
    return parts[1] if len(parts) > 1 else ""

HOST     = run("scutil --get ComputerName") or run("hostname")
GEN_DATE = datetime.now().strftime("%Y-%m-%d %H:%M")

# ── HTML builders ────────────────────────────────────────────────────────────
def esc(s):
    return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def li(name, ver="", extra=""):
    v = f'<span class="ver">{esc(ver)}</span>' if ver else ""
    e = f'<span class="plist-file">{esc(extra)}</span>' if extra else ""
    return f"<li>{esc(name)}{e}{v}</li>\n"

def section(title, items_html, count, open_attr=""):
    return f"""<details {open_attr}>
  <summary>
    <span class="section-title">{title}</span>
    <span class="section-count">{count}</span>
  </summary>
  <div class="section-body">
    {items_html}
  </div>
</details>\n"""

def empty(msg="No items found."):
    return f'<p class="empty">{msg}</p>'

def ul(items):
    return "<ul>\n" + "".join(items) + "</ul>\n" if items else ""

# ── CSS / HTML template ───────────────────────────────────────────────────────
CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{height:100%}
body{font-family:-apple-system,sans-serif;background:#000;color:#fff;font-size:14px;padding:2rem;max-width:960px;margin:0 auto;min-height:100vh}
.page-header{margin-bottom:2rem}
.page-header h1{font-size:22px;font-weight:700;color:#fff;letter-spacing:-0.01em;margin-bottom:4px}
.page-header .meta{font-family:monospace;font-size:12px;color:#888}
.page-header .meta span{color:#41b6e6}
details{background:#2a2a2a;border:0.5px solid #444;border-radius:8px;margin-bottom:10px;overflow:hidden}
summary{display:flex;align-items:center;gap:12px;padding:12px 16px;cursor:pointer;user-select:none;list-style:none}
summary::-webkit-details-marker{display:none}
summary::before{content:'\\25B8';color:#41b6e6;font-size:11px;transition:transform .15s;flex-shrink:0}
details[open] summary::before{transform:rotate(90deg)}
.section-title{font-size:13px;font-weight:600;text-transform:uppercase;letter-spacing:0.06em;color:#41b6e6;flex:1}
.section-count{font-family:monospace;font-size:11px;color:#888;background:#1a1a1a;border:0.5px solid #333;border-radius:99px;padding:2px 8px}
.section-body{padding:0 16px 14px;border-top:0.5px solid #333}
ul{list-style:none;padding:0;margin-top:10px}
li{font-family:monospace;font-size:13px;color:#fff;padding:5px 0;border-bottom:0.5px solid #333;display:flex;align-items:baseline;gap:8px;word-break:break-all}
li:last-child{border-bottom:none}
li::before{content:'\\2014';color:#444;flex-shrink:0}
.ver{font-size:11px;color:#41b6e6;background:#0d1f28;border:0.5px solid #1a3a4a;border-radius:4px;padding:1px 6px;margin-left:auto;flex-shrink:0}
.plist-file{font-size:11px;color:#555;margin-left:4px}
.empty{font-family:monospace;font-size:12px;color:#555;padding:8px 0;margin-top:8px}
.tip{margin-top:10px;background:#0d1a0d;border:0.5px solid #1a3a1a;border-radius:6px;padding:8px 12px;font-family:monospace;font-size:12px;color:#4a9a4a}
.footer{margin-top:2.5rem;font-size:10px;color:#555;font-family:monospace;text-align:center}
"""

def build():
    parts = []
    parts.append(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Software Inventory</title>
<style>{CSS}</style>
</head>
<body>
<div class="page-header">
  <h1>Software Inventory</h1>
  <div class="meta">Host: <span>{esc(HOST)}</span> &nbsp;&middot;&nbsp; Generated: <span>{esc(GEN_DATE)}</span></div>
</div>
""")

    # /Applications
    apps = scan_apps("/Applications")
    rows = [li(n, v) for n,v in apps]
    parts.append(section("/Applications", ul(rows) or empty(), len(apps), "open"))

    # ~/Applications
    uapps = scan_apps(str(Path.home()/"Applications"))
    rows = [li(n, v) for n,v in uapps]
    parts.append(section("~/Applications", ul(rows) or empty("No apps found in ~/Applications."), len(uapps)))

    # Homebrew casks
    casks = brew_list("--cask")
    if casks is None:
        cask_body = empty("Homebrew not found.")
        cask_count = 0
    else:
        rows = [li(c, brew_version(c)) for c in sorted(casks)]
        cask_count = len(rows)
        cask_body = ul(rows) or empty("No casks installed.")
    parts.append(section("Homebrew Casks", cask_body, cask_count))

    # Homebrew formulae
    formulae = brew_list("--formula")
    if formulae is None:
        form_body = empty("Homebrew not found.")
        form_count = 0
    else:
        rows = [li(f, brew_version(f)) for f in sorted(formulae)]
        form_count = len(rows)
        form_body = ul(rows) or empty("No formulae installed.")
    parts.append(section("Homebrew Formulae (CLI)", form_body, form_count))

    # Mac App Store
    mas_path = run("which mas")
    if mas_path:
        raw = run(["mas", "list"])
        mas_items = []
        for line in raw.splitlines():
            line = line.strip()
            if not line: continue
            tok = line.split(" ", 1)
            rest = tok[1] if len(tok) > 1 else tok[0]
            import re
            m = re.match(r"^(.*?)\s*\(([^)]+)\)$", rest)
            if m:
                mas_items.append((m.group(1).strip(), m.group(2).strip()))
            else:
                mas_items.append((rest, ""))
        mas_items.sort(key=lambda x: x[0].lower())
        rows = [li(n, v) for n,v in mas_items]
        mas_body = ul(rows) or empty("No App Store apps found.")
        mas_tip = ""
    else:
        receipts = list(Path("/Applications").rglob("_MASReceipt"))
        mas_items = sorted(set(r.parent.parent.stem for r in receipts))
        rows = [li(n) for n in mas_items]
        mas_body = (ul(rows) or empty()) + '<p class="tip">Install mas for full MAS inventory with IDs: brew install mas</p>'
    parts.append(section("Mac App Store", mas_body, len(mas_items)))

    # User Launch Agents
    la_user = scan_plists(Path.home()/"Library"/"LaunchAgents")
    rows = [li(label, extra=fname if label != fname else "") for label,fname in la_user]
    parts.append(section("Launch Agents — User", ul(rows) or empty("No user launch agents found."), len(la_user)))

    # System Launch Agents
    la_sys = scan_plists("/Library/LaunchAgents")
    rows = [li(label, extra=fname if label != fname else "") for label,fname in la_sys]
    parts.append(section("Launch Agents — System", ul(rows) or empty("No system launch agents found."), len(la_sys)))

    # System Launch Daemons
    ld_sys = scan_plists("/Library/LaunchDaemons")
    rows = [li(label, extra=fname if label != fname else "") for label,fname in ld_sys]
    parts.append(section("Launch Daemons — System", ul(rows) or empty("No system launch daemons found."), len(ld_sys)))

    # Login Items
    sfltool = run("which sfltool")
    if sfltool:
        raw = run(["sfltool", "dumpbtm"])
        li_items = [l.strip() for l in raw.splitlines() if l.strip().startswith(("url", "name", "developer"))]
        rows = [f"<li>{esc(x)}</li>\n" for x in li_items]
        li_body = ul(rows) or empty("No login items detected via sfltool.")
        li_count = len(li_items)
    else:
        li_body = empty("sfltool not available — requires macOS 13+. Check System Settings › General › Login Items manually.")
        li_count = 0
    parts.append(section("Login Items", li_body, li_count))

    parts.append('<div class="footer">Light Is Beauty Inc &middot; inventory_mac.py</div>\n</body>\n</html>\n')
    return "".join(parts)

print(build())
