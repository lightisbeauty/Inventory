#!/usr/bin/env python3
"""
inventory_mac.py
Outputs a self-contained HTML software inventory report.
Usage: python3 inventory_mac.py > software_inventory.html
       open software_inventory.html

Runtime dependencies:
  Required:   macOS 10.15+, Python 3.8+
  Optional:   Homebrew   – brew cask/formula sections
              mas         – full App Store inventory with IDs (brew install mas)
              MacPorts    – port section
              Fink        – fink section
              Nix         – nix section
              pip3        – Python packages section
              npm         – Node global packages section
              gem         – Ruby gems section
              cargo       – Rust binaries section
              conda/mamba – Conda environments section
"""
import subprocess, json, re, sys
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# ── subprocess helper ────────────────────────────────────────────────────────
def run(cmd, default=""):
    try:
        if isinstance(cmd, str):
            r = subprocess.run(cmd, capture_output=True, text=True, shell=True)
        else:
            r = subprocess.run(cmd, capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else default
    except Exception:
        return default

def which(binary):
    return bool(run(f"which {binary}"))

def plistbuddy(key, path):
    return run(["/usr/libexec/PlistBuddy", "-c", f"Print {key}", str(path)])

# ── app scanners ─────────────────────────────────────────────────────────────
def app_version(app_path):
    return plistbuddy("CFBundleShortVersionString", Path(app_path) / "Contents" / "Info.plist")

def scan_apps(directory):
    d = Path(directory)
    if not d.is_dir():
        return []
    candidates = sorted(d.glob("*.app")) + sorted(
        p for p in d.glob("*/*.app") if p.parent != d
    )
    seen, unique = set(), []
    for app in candidates:
        app = app.resolve()
        if app.stem not in seen:
            seen.add(app.stem)
            unique.append(app)
    unique.sort(key=lambda x: x.stem.lower())
    with ThreadPoolExecutor(max_workers=16) as ex:
        results = list(ex.map(lambda a: (a.stem, app_version(a)), unique))
    return results

def scan_plists(directory):
    d = Path(directory)
    if not d.is_dir():
        return []
    plists = sorted(d.glob("*.plist"))
    def read(f):
        label = plistbuddy("Label", f)
        return (label or f.name, f.name)
    with ThreadPoolExecutor(max_workers=16) as ex:
        return list(ex.map(read, plists))

# ── package manager data fetchers ────────────────────────────────────────────
def fetch_brew():
    """Returns (cask_map, formula_map) or (None, None) if brew not found."""
    if not which("brew"):
        return None, None
    raw = run(["brew", "info", "--json=v2", "--installed"])
    if not raw:
        return {}, {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}, {}
    casks = {c["token"]: c.get("installed") or c.get("version", "")
             for c in data.get("casks", []) if c.get("token")}
    formulae = {}
    for f in data.get("formulae", []):
        name = f.get("name", "")
        installed = f.get("installed", [])
        ver = installed[0].get("version", "") if installed else ""
        if name:
            formulae[name] = ver
    return casks, formulae

def fetch_mas():
    """
    Returns one of three states:
      ("no_mas", receipts_list)   – mas not installed; MAS apps detected via receipt scan
      ("has_mas", items_list)     – mas installed; full list with IDs and versions
      ("no_mas_no_receipts", [])  – mas not installed and no MAS receipts found
    """
    if which("mas"):
        raw = run(["mas", "list"])
        items = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            tok = line.split(" ", 1)
            rest = tok[1] if len(tok) > 1 else tok[0]
            m = re.match(r"^(.*?)\s*\(([^)]+)\)$", rest)
            items.append((m.group(1).strip(), m.group(2).strip()) if m else (rest, ""))
        return "has_mas", sorted(items, key=lambda x: x[0].lower())
    else:
        receipts = sorted(set(
            r.parent.parent.stem
            for r in Path("/Applications").rglob("_MASReceipt")
        ))
        if receipts:
            return "no_mas", receipts
        return "no_mas_no_receipts", []

def fetch_macports():
    if not which("port"):
        return None
    raw = run(["port", "installed"])
    items = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("The following"):
            continue
        parts = line.split()
        name = parts[0]
        ver  = parts[1].lstrip("@") if len(parts) > 1 else ""
        items.append((name, ver))
    return sorted(items, key=lambda x: x[0].lower())

def fetch_fink():
    if not which("fink"):
        return None
    raw = run(["fink", "list", "--installed"])
    items = []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            items.append((parts[1], parts[2] if len(parts) > 2 else ""))
    return sorted(items, key=lambda x: x[0].lower())

def fetch_nix():
    if not which("nix-env"):
        return None
    raw = run(["nix-env", "-q"])
    items = [(line.strip(), "") for line in raw.splitlines() if line.strip()]
    return sorted(items, key=lambda x: x[0].lower())

def fetch_pip():
    pip = "pip3" if which("pip3") else ("pip" if which("pip") else None)
    if not pip:
        return None
    raw = run([pip, "list", "--format=columns"])
    items = []
    for line in raw.splitlines()[2:]:  # skip header rows
        parts = line.split()
        if len(parts) >= 2:
            items.append((parts[0], parts[1]))
    return sorted(items, key=lambda x: x[0].lower())

def fetch_npm():
    if not which("npm"):
        return None
    raw = run(["npm", "list", "-g", "--depth=0", "--parseable"])
    items = []
    for line in raw.splitlines():
        p = Path(line.strip())
        if p.name and p.name != "lib":
            # format: /path/node_modules/package@version  -- split on last @
            name_ver = p.name
            if "@" in name_ver[1:]:
                idx = name_ver.rfind("@")
                items.append((name_ver[:idx], name_ver[idx+1:]))
            else:
                items.append((name_ver, ""))
    return sorted(set(items), key=lambda x: x[0].lower())

def fetch_gem():
    if not which("gem"):
        return None
    raw = run(["gem", "list", "--local"])
    items = []
    for line in raw.splitlines():
        m = re.match(r"^(\S+)\s+\(([^)]+)\)", line)
        if m:
            items.append((m.group(1), m.group(2).split(",")[0].strip()))
    return sorted(items, key=lambda x: x[0].lower())

def fetch_cargo():
    if not which("cargo"):
        return None
    raw = run(["cargo", "install", "--list"])
    items = []
    for line in raw.splitlines():
        m = re.match(r"^(\S+)\s+v([^\s:]+)", line)
        if m:
            items.append((m.group(1), m.group(2)))
    return sorted(items, key=lambda x: x[0].lower())

def fetch_conda():
    tool = "mamba" if which("mamba") else ("conda" if which("conda") else None)
    if not tool:
        return None
    raw = run([tool, "list", "--json"])
    try:
        data = json.loads(raw)
        items = [(p["name"], p.get("version","")) for p in data]
        return sorted(items, key=lambda x: x[0].lower())
    except Exception:
        return None

# ── HTML helpers ─────────────────────────────────────────────────────────────
def esc(s):
    return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def li_row(name, ver="", extra=""):
    v = f'<span class="ver">{esc(ver)}</span>' if ver else ""
    e = f'<span class="plist-file">{esc(extra)}</span>' if extra else ""
    return f"<li>{esc(name)}{e}{v}</li>\n"

def ul(rows):
    return "<ul>\n" + "".join(rows) + "</ul>\n" if rows else ""

def empty(msg="No items found."):
    return f'<p class="empty">{msg}</p>'

def notice(msg):
    return f'<p class="notice">{msg}</p>'

def section(title, body, count, open_attr=""):
    return (
        f'<details {open_attr}>\n'
        f'  <summary>\n'
        f'    <span class="section-title">{esc(title)}</span>\n'
        f'    <span class="section-count">{count}</span>\n'
        f'  </summary>\n'
        f'  <div class="section-body">{body}</div>\n'
        f'</details>\n'
    )

# ── styles ───────────────────────────────────────────────────────────────────
FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@300;400;500'
    '&family=Syne:wght@400;600;700;800&display=swap" rel="stylesheet">'
)

CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{height:100%}
body{font-family:'Syne',sans-serif;background:#040d1a;color:#f0f6ff;font-size:14px;line-height:1.7;padding:2rem;max-width:960px;margin:0 auto;min-height:100vh}
.page-header{margin-bottom:2rem}
.page-header h1{font-size:22px;font-weight:700;color:#f0f6ff;letter-spacing:-0.01em;margin-bottom:4px}
.page-header .meta{font-family:'DM Mono',monospace;font-size:12px;color:#6a8caa}
.page-header .meta span{color:#3eb8f0}
details{background:#0a1c30;border:1px solid rgba(62,184,240,0.15);margin-bottom:10px;overflow:hidden}
summary{display:flex;align-items:center;gap:12px;padding:12px 16px;cursor:pointer;user-select:none;list-style:none}
summary::-webkit-details-marker{display:none}
summary::before{content:'\\25B8';color:#3eb8f0;font-size:11px;transition:transform .15s;flex-shrink:0}
details[open] summary::before{transform:rotate(90deg)}
.section-title{font-family:'DM Mono',monospace;font-size:11px;font-weight:500;text-transform:uppercase;letter-spacing:0.25em;color:#3eb8f0;flex:1}
.section-count{font-family:'DM Mono',monospace;font-size:11px;color:#6a8caa;background:#071428;border:1px solid rgba(62,184,240,0.15);border-radius:99px;padding:2px 8px}
.section-body{padding:0 16px 14px;border-top:1px solid rgba(62,184,240,0.15)}
ul{list-style:none;padding:0;margin-top:10px}
li{font-family:'DM Mono',monospace;font-size:13px;color:#f0f6ff;padding:5px 0;border-bottom:1px solid rgba(62,184,240,0.07);display:flex;align-items:baseline;gap:8px;word-break:break-all}
li:last-child{border-bottom:none}
li::before{content:'\\2014';color:#6a8caa;flex-shrink:0}
.ver{font-family:'DM Mono',monospace;font-size:11px;color:#3eb8f0;background:#071428;border:1px solid rgba(62,184,240,0.15);padding:1px 6px;margin-left:auto;flex-shrink:0}
.plist-file{font-family:'DM Mono',monospace;font-size:11px;color:#6a8caa;margin-left:4px}
.empty{font-family:'DM Mono',monospace;font-size:12px;color:#6a8caa;padding:8px 0;margin-top:8px}
.notice{margin-top:10px;background:#071428;border:1px solid rgba(240,201,62,0.25);padding:8px 12px;font-family:'DM Mono',monospace;font-size:12px;color:#f0c93e}
.notice code{color:#f0c93e;background:#0a1c30;padding:1px 5px}
.footer{margin-top:2.5rem;font-size:10px;color:#6a8caa;font-family:'DM Mono',monospace;text-align:center}
"""

# ── main build ───────────────────────────────────────────────────────────────
def build():
    HOST     = run("scutil --get ComputerName") or run("hostname")
    GEN_DATE = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Launch all slow tasks concurrently
    with ThreadPoolExecutor(max_workers=12) as ex:
        f_apps      = ex.submit(scan_apps, "/Applications")
        f_uapps     = ex.submit(scan_apps, str(Path.home() / "Applications"))
        f_brew      = ex.submit(fetch_brew)
        f_mas       = ex.submit(fetch_mas)
        f_macports  = ex.submit(fetch_macports)
        f_fink      = ex.submit(fetch_fink)
        f_nix       = ex.submit(fetch_nix)
        f_pip       = ex.submit(fetch_pip)
        f_npm       = ex.submit(fetch_npm)
        f_gem       = ex.submit(fetch_gem)
        f_cargo     = ex.submit(fetch_cargo)
        f_conda     = ex.submit(fetch_conda)
        f_la_user   = ex.submit(scan_plists, Path.home() / "Library" / "LaunchAgents")
        f_la_sys    = ex.submit(scan_plists, "/Library/LaunchAgents")
        f_ld_sys    = ex.submit(scan_plists, "/Library/LaunchDaemons")

        apps        = f_apps.result()
        uapps       = f_uapps.result()
        cask_map, formula_map = f_brew.result()
        mas_state, mas_data   = f_mas.result()
        macports    = f_macports.result()
        fink        = f_fink.result()
        nix         = f_nix.result()
        pip         = f_pip.result()
        npm         = f_npm.result()
        gem         = f_gem.result()
        cargo       = f_cargo.result()
        conda       = f_conda.result()
        la_user     = f_la_user.result()
        la_sys      = f_la_sys.result()
        ld_sys      = f_ld_sys.result()

    parts = [f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Software Inventory — {esc(HOST)}</title>
{FONTS}
<style>{CSS}</style>
</head>
<body>
<div class="page-header">
  <h1>Software Inventory</h1>
  <div class="meta">Host: <span>{esc(HOST)}</span> &nbsp;&middot;&nbsp; Generated: <span>{esc(GEN_DATE)}</span></div>
</div>
"""]

    # /Applications
    rows = [li_row(n, v) for n, v in apps]
    parts.append(section("/Applications", ul(rows) or empty(), len(apps), "open"))

    # ~/Applications
    rows = [li_row(n, v) for n, v in uapps]
    if uapps:
        parts.append(section("~/Applications", ul(rows), len(uapps)))

    # Homebrew Casks — hidden if brew not installed
    if cask_map is not None:
        rows = [li_row(k, v) for k, v in sorted(cask_map.items())]
        parts.append(section("Homebrew Casks", ul(rows) or empty("No casks installed."), len(rows)))

    # Homebrew Formulae — hidden if brew not installed
    if formula_map is not None:
        rows = [li_row(k, v) for k, v in sorted(formula_map.items())]
        parts.append(section("Homebrew Formulae (CLI)", ul(rows) or empty("No formulae installed."), len(rows)))

    # Mac App Store
    if mas_state == "has_mas":
        # mas installed — full list with versions
        rows = [li_row(n, v) for n, v in mas_data]
        parts.append(section("Mac App Store", ul(rows) or empty("No App Store apps found."), len(rows)))
    elif mas_state == "no_mas":
        # mas not installed but receipts found — show apps, explain limitation
        rows = [li_row(n) for n in mas_data]
        body = ul(rows) + notice(
            "App names detected via receipt scan only — versions and App Store IDs unavailable. "
            "Install <code>mas</code> for the full picture: <code>brew install mas</code>"
        )
        parts.append(section("Mac App Store", body, len(mas_data)))
    # no_mas_no_receipts → section omitted entirely

    # MacPorts — hidden if not installed
    if macports is not None:
        rows = [li_row(n, v) for n, v in macports]
        parts.append(section("MacPorts", ul(rows) or empty("No ports installed."), len(rows)))

    # Fink — hidden if not installed
    if fink is not None:
        rows = [li_row(n, v) for n, v in fink]
        parts.append(section("Fink", ul(rows) or empty("No Fink packages installed."), len(rows)))

    # Nix — hidden if not installed
    if nix is not None:
        rows = [li_row(n, v) for n, v in nix]
        parts.append(section("Nix", ul(rows) or empty("No Nix packages installed."), len(rows)))

    # pip — hidden if not installed
    if pip is not None:
        rows = [li_row(n, v) for n, v in pip]
        parts.append(section("Python Packages (pip)", ul(rows) or empty("No pip packages installed."), len(rows)))

    # npm — hidden if not installed
    if npm is not None:
        rows = [li_row(n, v) for n, v in npm]
        parts.append(section("Node Packages (npm global)", ul(rows) or empty("No global npm packages installed."), len(rows)))

    # gem — hidden if not installed
    if gem is not None:
        rows = [li_row(n, v) for n, v in gem]
        parts.append(section("Ruby Gems", ul(rows) or empty("No gems installed."), len(rows)))

    # cargo — hidden if not installed
    if cargo is not None:
        rows = [li_row(n, v) for n, v in cargo]
        parts.append(section("Rust Binaries (cargo)", ul(rows) or empty("No cargo binaries installed."), len(rows)))

    # conda — hidden if not installed
    if conda is not None:
        rows = [li_row(n, v) for n, v in conda]
        parts.append(section("Conda Packages", ul(rows) or empty("No conda packages installed."), len(rows)))

    # Launch Agents — User
    rows = [li_row(label, extra=fname if label != fname else "") for label, fname in la_user]
    if la_user:
        parts.append(section("Launch Agents — User", ul(rows), len(la_user)))

    # Launch Agents — System
    rows = [li_row(label, extra=fname if label != fname else "") for label, fname in la_sys]
    if la_sys:
        parts.append(section("Launch Agents — System", ul(rows), len(la_sys)))

    # Launch Daemons — System
    rows = [li_row(label, extra=fname if label != fname else "") for label, fname in ld_sys]
    if ld_sys:
        parts.append(section("Launch Daemons — System", ul(rows), len(ld_sys)))

    # Login Items
    if which("sfltool"):
        raw = run(["sfltool", "dumpbtm"])
        li_items = [l.strip() for l in raw.splitlines()
                    if l.strip().startswith(("url", "name", "developer"))]
        rows = [f"<li>{esc(x)}</li>\n" for x in li_items]
        body = ul(rows) or empty("No login items detected.")
        if li_items:
            parts.append(section("Login Items", body, len(li_items)))
    # sfltool absent → section omitted entirely

    parts.append(
        '<div class="footer">Light Is Beauty Inc &middot; inventory_mac.py</div>\n'
        '</body>\n</html>\n'
    )
    return "".join(parts)

if __name__ == "__main__":
    print(build())
