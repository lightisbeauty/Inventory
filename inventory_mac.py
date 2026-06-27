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
import subprocess, json, re, sys, os
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

_brew_paths = "/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/local/sbin"
os.environ["PATH"] = _brew_paths + ":" + os.environ.get("PATH", "/usr/bin:/bin")

_DBG = "/tmp/inventory_debug.log"
def _dbg(msg):
    try:
        with open(_DBG, "a") as f:
            f.write(f"{datetime.now().strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass

# ── subprocess helper ────────────────────────────────────────────────────────
def run(cmd, default="", timeout=30):
    try:
        if isinstance(cmd, str):
            r = subprocess.run(cmd, capture_output=True, text=True, shell=True, timeout=timeout)
        else:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
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
    _dbg(f"scan_apps start: {directory}")
    d = Path(directory)
    if not d.is_dir():
        _dbg(f"scan_apps skip (no dir): {directory}")
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
    _dbg(f"scan_plists start: {directory}")
    d = Path(directory)
    if not d.is_dir():
        _dbg(f"scan_plists skip (no dir): {directory}")
        return []
    plists = sorted(d.glob("*.plist"))
    def read(f):
        label = plistbuddy("Label", f)
        return (label or f.name, f.name)
    with ThreadPoolExecutor(max_workers=16) as ex:
        return list(ex.map(read, plists))

# ── system info ──────────────────────────────────────────────────────────────
def fetch_system_info():
    _dbg("fetch_system_info start")
    info = {}
    model_id = run("sysctl -n hw.model")
    model_names = {
        "Mac16": "MacBook Pro", "Mac15": "MacBook Air", "Mac14": "Mac",
        "MacBookPro": "MacBook Pro", "MacBookAir": "MacBook Air",
        "Macmini": "Mac mini", "MacPro": "Mac Pro", "iMac": "iMac",
    }
    model = "Mac"
    for prefix, name in model_names.items():
        if model_id.startswith(prefix):
            model = name
            break
    info["model"] = model
    info["chip"] = run("sysctl -n machdep.cpu.brand_string")
    mem_bytes = run("sysctl -n hw.memsize")
    if mem_bytes.isdigit():
        info["memory"] = f"{int(mem_bytes) // (1024**3)} GB"
    serial_raw = run("ioreg -l | grep IOPlatformSerialNumber")
    m = re.search(r'"IOPlatformSerialNumber"\s*=\s*"([^"]+)"', serial_raw)
    info["serial"] = m.group(1) if m else "—"
    info["macos_ver"] = run("sw_vers -productVersion")
    macos_names = {
        "26": "Tahoe", "15": "Sequoia", "14": "Sonoma", "13": "Ventura",
        "12": "Monterey", "11": "Big Sur", "10.15": "Catalina",
    }
    ver = info.get("macos_ver", "")
    major = ver.split(".")[0] if ver else ""
    info["macos_name"] = macos_names.get(major, "macOS")
    display_map = {
        "Mac16,1": "14-inch", "Mac16,2": "14-inch", "Mac16,3": "14-inch",
        "Mac16,4": "14-inch", "Mac16,5": "16-inch", "Mac16,6": "16-inch",
        "Mac16,7": "16-inch", "Mac16,8": "14-inch", "Mac16,10": "15-inch",
        "Mac16,11": "13-inch", "Mac16,15": "13-inch", "Mac16,16": "15-inch",
        "Mac15,3": "14-inch", "Mac15,6": "14-inch", "Mac15,7": "14-inch",
        "Mac15,8": "16-inch", "Mac15,9": "14-inch", "Mac15,10": "14-inch",
        "Mac15,11": "16-inch", "Mac15,12": "13-inch", "Mac15,13": "15-inch",
        "Mac14,2": "13-inch", "Mac14,5": "14-inch", "Mac14,6": "16-inch",
        "Mac14,7": "13-inch", "Mac14,9": "14-inch", "Mac14,10": "16-inch",
        "Mac14,15": "15-inch",
    }
    info["display"] = display_map.get(model_id, "")
    return info

# ── package manager data fetchers ────────────────────────────────────────────
def fetch_brew():
    _dbg("fetch_brew start")
    """Returns (cask_map, formula_map) or (None, None) if brew not found
    or if mas is the only user-installed formula (auto-installed by our tool)."""
    if not which("brew"):
        return None, None
    raw = run(["brew", "info", "--json=v2", "--installed"])
    if not raw:
        return {}, {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}, {}
    casks = []
    for c in data.get("casks", []):
        token = c.get("token", "")
        if token:
            ver = c.get("installed") or c.get("version", "")
            casks.append((token, ver, c.get("homepage", ""), c.get("desc", "")))
    casks.sort(key=lambda x: x[0].lower())
    formulae = []
    for f in data.get("formulae", []):
        name = f.get("name", "")
        installed = f.get("installed", [])
        ver = installed[0].get("version", "") if installed else ""
        if name:
            formulae.append((name, ver, f.get("homepage", ""), f.get("desc", "")))
    formulae.sort(key=lambda x: x[0].lower())
    leaves_raw = run(["brew", "leaves"])
    leaves = {l.strip() for l in leaves_raw.splitlines() if l.strip()}
    if leaves <= {"mas"} and not casks:
        return None, None
    return casks, formulae

def fetch_mas():
    _dbg("fetch_mas start")
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
    _dbg("fetch_macports start")
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
    _dbg("fetch_fink start")
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
    _dbg("fetch_nix start")
    if not which("nix-env"):
        return None
    raw = run(["nix-env", "-q"])
    items = [(line.strip(), "") for line in raw.splitlines() if line.strip()]
    return sorted(items, key=lambda x: x[0].lower())

def fetch_pip():
    _dbg("fetch_pip start")
    pip = "pip3" if which("pip3") else ("pip" if which("pip") else None)
    if not pip:
        return None
    raw = run([pip, "list", "--format=columns"])
    items = []
    for line in raw.splitlines()[2:]:
        parts = line.split()
        if len(parts) >= 2:
            items.append((parts[0], parts[1]))
    if not items:
        return []
    names = [n for n, _ in items]
    meta_raw = run([pip, "show"] + names)
    meta = {}
    current = {}
    for line in meta_raw.splitlines():
        if line.startswith("Name: "):
            if current.get("name"):
                meta[current["name"].lower()] = current
            current = {"name": line[6:].strip()}
        elif line.startswith("Summary: "):
            current["desc"] = line[9:].strip()
        elif line.startswith("Home-page: "):
            val = line[11:].strip()
            current["url"] = "" if val in ("", "UNKNOWN", "None") else val
        elif line == "---":
            if current.get("name"):
                meta[current["name"].lower()] = current
            current = {}
    if current.get("name"):
        meta[current["name"].lower()] = current
    result = []
    for name, ver in items:
        m = meta.get(name.lower(), {})
        result.append((name, ver, m.get("url", ""), m.get("desc", "")))
    return sorted(result, key=lambda x: x[0].lower())

def fetch_npm():
    _dbg("fetch_npm start")
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
    _dbg("fetch_gem start")
    if not which("gem"):
        return None
    raw = run(["gem", "list", "--local"])
    items = []
    for line in raw.splitlines():
        m = re.match(r"^(\S+)\s+\(([^)]+)\)", line)
        if m:
            items.append((m.group(1), m.group(2).split(",")[0].strip()))
    if not items:
        return []
    def gem_meta(name):
        raw = run(["gem", "specification", name, "--yaml"])
        url, desc = "", ""
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("homepage:"):
                url = line.split(":", 1)[1].strip().strip('"').strip("'")
            elif line.startswith("summary:"):
                desc = line.split(":", 1)[1].strip().strip('"').strip("'")
        return (name, url, desc)
    with ThreadPoolExecutor(max_workers=16) as ex:
        meta = {n: (u, d) for n, u, d in ex.map(lambda i: gem_meta(i[0]), items)}
    result = []
    for name, ver in items:
        u, d = meta.get(name, ("", ""))
        result.append((name, ver, u, d))
    return sorted(result, key=lambda x: x[0].lower())

def fetch_cargo():
    _dbg("fetch_cargo start")
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
    _dbg("fetch_conda start")
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
    return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")

def format_repo_url(url):
    if 'github.com/' in url:
        path = url.split('github.com/')[1].strip('/')
        parts = path.split('/')
        return parts[0] + '/' + parts[1] if len(parts) >= 2 else path
    return url.replace('https://', '').replace('http://', '').strip('/')

def li_row(name, ver="", extra="", url="", desc=""):
    title = f' title="{esc(desc)}"' if desc else ""
    v = f'<span class="ver">{esc(ver)}</span>' if ver else ""
    e = f'<span class="plist-file">{esc(extra)}</span>' if extra else ""
    link = ""
    if url:
        display = format_repo_url(url)
        link = f'<a class="repo-link" href="{esc(url)}" target="_blank">{esc(display)}</a>'
    return f'<li><span class="app-name"{title}>{esc(name)}</span>{e}{v}{link}</li>\n'

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
CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{height:100%}
body{font-family:-apple-system,sans-serif;background:#040d1a;color:#fff;font-size:14px;padding:2rem;max-width:960px;margin:0 auto;min-height:100vh}
.page-header{margin-bottom:1rem;display:flex;align-items:flex-end;justify-content:space-between;gap:16px}
.header-left{flex:1}
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
.app-name{min-width:0}
.repo-link{flex:1;text-align:left;font-family:monospace;font-size:10px;color:#7a9ab0;text-decoration:none;white-space:nowrap;padding:0 8px}
.repo-link:hover{color:#41b6e6}
.ver{font-size:11px;color:#41b6e6;background:#0d1f28;border:0.5px solid #1a3a4a;border-radius:4px;padding:1px 6px;flex-shrink:0}
.plist-file{font-size:11px;color:#555;margin-left:4px}
.empty{font-family:monospace;font-size:12px;color:#555;padding:8px 0;margin-top:8px}
.notice{margin-top:10px;background:#1a140d;border:0.5px solid #3a2a1a;border-radius:6px;padding:8px 12px;font-family:monospace;font-size:12px;color:#a07840}
.notice code{color:#c8a060;background:#221a0d;padding:1px 5px;border-radius:3px}
.page-header .sysinfo{font-family:monospace;font-size:12px;color:#888;margin-top:4px}
.page-header .sysinfo span{color:#41b6e6}
.header-spacer{display:none}
.footer{margin-top:2.5rem;font-size:10px;color:#7a9ab0;font-family:monospace;text-align:center}
.export-bar{display:grid;grid-template-columns:1fr 1fr;gap:6px 8px;flex-shrink:0;padding-top:2px}
.export-btn{font-family:monospace;font-size:11px;color:#41b6e6;background:none;border:0.5px solid #444;border-radius:4px;padding:5px 12px;cursor:pointer;text-transform:uppercase;letter-spacing:0.06em;white-space:nowrap}
.export-btn:hover{border-color:#db3eb1}
.snapshot-picker{display:none}
.snapshot-list{margin-bottom:12px}
.snapshot-row{display:flex;align-items:center;justify-content:space-between;padding:10px 0;border-bottom:0.5px solid #222;gap:16px}
.snapshot-info{display:flex;flex-direction:column;gap:3px}
.snapshot-date{font-family:monospace;font-size:12px;color:#ccc}
.snapshot-serial{font-family:monospace;font-size:11px;color:#555}
.snapshot-actions{display:flex;gap:8px;flex-shrink:0}
.snapshot-delete{color:#db3eb1}
.snapshot-footer{display:flex;align-items:center;padding-top:12px;border-top:0.5px solid #222}
.snapshot-toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#1a1a1a;border:0.5px solid #444;border-radius:6px;padding:8px 20px;font-family:monospace;font-size:12px;color:#3ef09e;pointer-events:none;transition:opacity 0.3s;z-index:999}
.diff-view{display:none}
.diff-header{margin-bottom:1.5rem}
.diff-header h2{font-size:18px;font-weight:700;color:#fff;margin-bottom:4px}
.diff-header .diff-meta{font-family:monospace;font-size:11px;color:#888}
.diff-header .diff-meta span{color:#41b6e6}
.diff-summary{display:flex;gap:12px;margin-bottom:1.5rem;flex-wrap:wrap}
.diff-stat{font-family:monospace;font-size:11px;padding:4px 12px;border-radius:4px}
.diff-stat.added{color:#3ef09e;background:rgba(62,240,158,0.08);border:0.5px solid rgba(62,240,158,0.3)}
.diff-stat.removed{color:#db3eb1;background:rgba(219,62,177,0.08);border:0.5px solid rgba(219,62,177,0.3)}
.diff-stat.updated{color:#f0c93e;background:rgba(240,201,62,0.08);border:0.5px solid rgba(240,201,62,0.3)}
.diff-card{background:#2a2a2a;border:0.5px solid #444;border-radius:8px;margin-bottom:10px;overflow:hidden}
.diff-card>summary{list-style:none;display:flex;align-items:center;justify-content:space-between;cursor:pointer;font-size:13px;font-weight:600;text-transform:uppercase;letter-spacing:0.06em;color:#41b6e6;padding:12px 16px}
.diff-card>summary::-webkit-details-marker{display:none}
.diff-card[open]>summary{border-bottom:0.5px solid #333}
.diff-card-count{font-family:monospace;font-size:11px;color:#888;background:#1a1a1a;border:0.5px solid #333;border-radius:99px;padding:2px 8px;font-weight:400;letter-spacing:0;text-transform:none}
.diff-card-body{padding:0 16px 14px}
.diff-group-label{font-family:monospace;font-size:10px;letter-spacing:0.2em;text-transform:uppercase;padding:10px 0 6px;border-bottom:0.5px solid #222}
.diff-group-label.added{color:#3ef09e}
.diff-group-label.removed{color:#db3eb1}
.diff-group-label.updated{color:#f0c93e}
.diff-row{font-family:monospace;font-size:13px;padding:5px 0;border-bottom:0.5px solid #222;display:flex;align-items:baseline;gap:8px}
.diff-row:last-child{border-bottom:none}
.diff-row::before{content:'\2014';color:#444;flex-shrink:0}
.diff-row .name{flex:1}
.diff-row .ver-new{font-size:11px;color:#3ef09e;background:rgba(62,240,158,0.08);border:0.5px solid rgba(62,240,158,0.3);border-radius:4px;padding:1px 6px}
.diff-row .ver-old{font-size:11px;color:#db3eb1;background:rgba(219,62,177,0.08);border:0.5px solid rgba(219,62,177,0.3);border-radius:4px;padding:1px 6px}
.diff-row .ver-arrow{font-size:11px;color:#555;padding:0 4px}
.diff-row .ver-updated{font-size:11px;color:#f0c93e;background:rgba(240,201,62,0.08);border:0.5px solid rgba(240,201,62,0.3);border-radius:4px;padding:1px 6px}
.no-changes{font-family:monospace;font-size:12px;color:#555;text-align:center;padding:40px 0}
@media print{
  body{background:#fff;color:#000;padding:0.5in}
  .export-bar{display:none}
  details{background:#f5f5f5;border:0.5px solid #ccc;break-inside:avoid}
  details[open]{break-inside:auto}
  .section-title{color:#1a6b8a}
  .section-count{background:#e8e8e8;border-color:#ccc;color:#555}
  .section-body{border-top-color:#ccc}
  li{border-bottom-color:#ddd;color:#000}
  li::before{color:#aaa}
  .ver{color:#1a6b8a;background:#e8f4f8;border-color:#b8d8e8}
  .plist-file{color:#888}
  .repo-link{color:#888}
  .notice{background:#fff8e8;border-color:#e8d8a8;color:#7a6020}
  .notice code{background:#f0e8c8;color:#7a6020}
  .footer{color:#aaa}
  .page-header{border-bottom:1px solid #ccc;padding-bottom:12px;margin-bottom:16px}
  .page-header .sysinfo{color:#555}
  .page-header .sysinfo span{color:#1a6b8a}
  .page-header .meta{color:#888}
  .page-header .meta span{color:#1a6b8a}
}
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
        f_sysinfo   = ex.submit(fetch_system_info)
        f_la_user   = ex.submit(scan_plists, Path.home() / "Library" / "LaunchAgents")
        f_la_sys    = ex.submit(scan_plists, "/Library/LaunchAgents")
        f_ld_sys    = ex.submit(scan_plists, "/Library/LaunchDaemons")

        _dbg("waiting: sysinfo");  sysinfo     = f_sysinfo.result()
        _dbg("waiting: apps");     apps        = f_apps.result()
        _dbg("waiting: uapps");    uapps       = f_uapps.result()
        _dbg("waiting: brew");     brew_casks, brew_formulae = f_brew.result()
        _dbg("waiting: mas");      mas_state, mas_data   = f_mas.result()
        _dbg("waiting: macports"); macports    = f_macports.result()
        _dbg("waiting: fink");     fink        = f_fink.result()
        _dbg("waiting: nix");      nix         = f_nix.result()
        _dbg("waiting: pip");      pip         = f_pip.result()
        _dbg("waiting: npm");      npm         = f_npm.result()
        _dbg("waiting: gem");      gem         = f_gem.result()
        _dbg("waiting: cargo");    cargo       = f_cargo.result()
        _dbg("waiting: conda");    conda       = f_conda.result()
        _dbg("waiting: la_user");  la_user     = f_la_user.result()
        _dbg("waiting: la_sys");   la_sys      = f_la_sys.result()
        _dbg("waiting: ld_sys");   ld_sys      = f_ld_sys.result()
        _dbg("all results collected")

    parts = [f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Software inventory — {esc(HOST)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="page-header">
  <div class="header-left">
    <h1><span style="color:#41b6e6">Software</span> inventory</h1>
    <div class="meta">Generated: <span>{esc(GEN_DATE)}</span></div>
    <div class="sysinfo"><span>{esc(sysinfo.get('model','Mac'))}</span>{(' &middot; ' + esc(sysinfo.get('display',''))) if sysinfo.get('display') else ''} &middot; <span>{esc(sysinfo.get('chip','—'))}</span> &middot; <span>{esc(sysinfo.get('memory','—'))}</span> &middot; {esc(sysinfo.get('macos_name','macOS'))} <span>{esc(sysinfo.get('macos_ver',''))}</span> &middot; SN: <span>{esc(sysinfo.get('serial','—'))}</span></div>
  </div>
  <div class="export-bar">
    <button class="export-btn" onclick="saveSnapshot()">Save Snapshot</button>
    <button class="export-btn" onclick="saveHTML()">Export HTML</button>
    <button class="export-btn" onclick="openCompare()">Compare</button>
    <button class="export-btn" onclick="exportPDF()">Export PDF</button>
  </div>
</div>
<div id="report-sections">
"""]

    # /Applications
    rows = [li_row(n, v) for n, v in apps]
    parts.append(section("/Applications", ul(rows) or empty(), len(apps)))

    # ~/Applications
    rows = [li_row(n, v) for n, v in uapps]
    if uapps:
        parts.append(section("~/Applications", ul(rows), len(uapps)))

    # Homebrew Casks — hidden if brew not installed
    if brew_casks is not None:
        rows = [li_row(n, v, url=u, desc=d) for n, v, u, d in brew_casks]
        parts.append(section("Homebrew Casks", ul(rows) or empty("No casks installed."), len(rows)))

    # Homebrew Formulae — hidden if brew not installed
    if brew_formulae is not None:
        rows = [li_row(n, v, url=u, desc=d) for n, v, u, d in brew_formulae]
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
        rows = [li_row(n, v, url=u, desc=d) for n, v, u, d in pip]
        parts.append(section("Python Packages (pip)", ul(rows) or empty("No pip packages installed."), len(rows)))

    # npm — hidden if not installed
    if npm is not None:
        rows = [li_row(n, v) for n, v in npm]
        parts.append(section("Node Packages (npm global)", ul(rows) or empty("No global npm packages installed."), len(rows)))

    # gem — hidden if not installed
    if gem is not None:
        rows = [li_row(n, v, url=u, desc=d) for n, v, u, d in gem]
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

    # Login Items — omitted; all available methods require user authorization

    parts.append(
        '</div>\n'
        '<div id="diff-view" class="diff-view"></div>\n'
        '<div id="snapshot-picker" class="snapshot-picker diff-view"></div>\n'
        '<div class="footer">inventory v26062605 &middot; by: @lightisbeauty</div>\n'
        '<script>\n'
        'var isNative=!!(window.webkit&&window.webkit.messageHandlers&&window.webkit.messageHandlers.nativeExport);\n'
        'function exportPDF(){\n'
        '  if(isNative){\n'
        '    window.webkit.messageHandlers.nativeExport.postMessage({action:"pdf"});\n'
        '  }else{\n'
        '    var saved=[];\n'
        '    document.querySelectorAll("details").forEach(function(d){saved.push(d.open);d.open=true});\n'
        '    setTimeout(function(){\n'
        '      window.print();\n'
        '      document.querySelectorAll("details").forEach(function(d,i){d.open=saved[i]});\n'
        '    },100);\n'
        '  }\n'
        '}\n'
        'function saveHTML(){\n'
        '  var el=document.querySelector(".export-bar");el.style.display="none";\n'
        '  var html=document.documentElement.outerHTML;\n'
        '  el.style.display="";\n'
        '  if(isNative){\n'
        '    window.webkit.messageHandlers.nativeExport.postMessage({action:"html",html:html});\n'
        '  }else{\n'
        '    var b=new Blob([html],{type:"text/html"});\n'
        '    var a=document.createElement("a");a.href=URL.createObjectURL(b);\n'
        '    a.download="software_inventory.html";a.click();\n'
        '    URL.revokeObjectURL(a.href);\n'
        '  }\n'
        '}\n'
        'function saveSnapshot(){\n'
        '  if(!isNative)return;\n'
        '  var el=document.querySelector(".export-bar");el.style.display="none";\n'
        '  var html=document.documentElement.outerHTML;\n'
        '  el.style.display="";\n'
        '  window.webkit.messageHandlers.nativeExport.postMessage({action:"saveSnapshot",html:html});\n'
        '}\n'
        'function showSnapshotSaved(){\n'
        '  var t=document.createElement("div");t.className="snapshot-toast";t.textContent="Snapshot saved";\n'
        '  document.body.appendChild(t);\n'
        '  setTimeout(function(){t.style.opacity="0";setTimeout(function(){t.remove();},300);},1800);\n'
        '}\n'
        'function openCompare(){\n'
        '  if(isNative){\n'
        '    window.webkit.messageHandlers.nativeExport.postMessage({action:"openSnapshots"});\n'
        '  }else{\n'
        '    var inp=document.createElement("input");inp.type="file";inp.accept=".html";\n'
        '    inp.onchange=function(){var r=new FileReader();r.onload=function(e){receiveCompareData(e.target.result,""+inp.files[0].name);};r.readAsText(inp.files[0]);};\n'
        '    inp.click();\n'
        '  }\n'
        '}\n'
        'function parseSnapshotName(f){\n'
        '  var base=f.replace(/\\.html$/i,""),p=base.split("_");\n'
        '  if(p.length<4)return{display:f,serial:""};\n'
        '  var ds=p[1],ts=p[2],serial=p.slice(3).join("_");\n'
        '  var yr="20"+ds.slice(0,2),mo=parseInt(ds.slice(2,4))-1,dy=parseInt(ds.slice(4,6));\n'
        '  var hr=parseInt(ts.slice(0,2)),mn=ts.slice(2,4);\n'
        '  var months=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];\n'
        '  var ampm=hr>=12?"PM":"AM",hr12=hr%12||12;\n'
        '  return{display:months[mo]+" "+dy+", "+yr+" \\u00b7 "+hr12+":"+mn+" "+ampm,serial:serial};\n'
        '}\n'
        'function receiveSnapshotList(snapshots){\n'
        '  document.getElementById("report-sections").style.display="none";\n'
        '  var picker=document.getElementById("snapshot-picker");\n'
        '  picker.style.display="block";\n'
        '  var rows="";\n'
        '  if(!snapshots.length){\n'
        '    rows=\'<div class="no-changes">No snapshots yet — use Save Snapshot to create one.</div>\';\n'
        '  }else{\n'
        '    snapshots.forEach(function(s){\n'
        '      var info=parseSnapshotName(s.filename);\n'
        '      rows+=\'<div class="snapshot-row"><span class="snapshot-info"><span class="snapshot-date">\'+info.display+\'</span><span class="snapshot-serial">\'+info.serial+\'</span></span>\'\n'
        '        +\'<span class="snapshot-actions"><button class="export-btn" onclick="loadSnapshot(\\\'\'+s.filename+\'\\\')">\'\n'
        '        +\'Compare</button><button class="export-btn snapshot-delete" onclick="deleteSnapshot(\\\'\'+s.filename+\'\\\')">Delete</button></span></div>\';\n'
        '    });\n'
        '  }\n'
        '  picker.innerHTML=\'<div class="diff-header"><h2>Snapshots</h2><div class="diff-meta">Select a snapshot to compare against the current scan</div></div>\'\n'
        '    +\'<div class="snapshot-list">\'+rows+\'</div>\'\n'
        '    +\'<div class="snapshot-footer"><button class="export-btn" onclick="compareFromFile()">Compare from file\\u2026</button>\'\n'
        '    +\'<button class="export-btn" style="margin-left:auto" onclick="closeSnapshots()">Cancel</button></div>\';\n'
        '}\n'
        'function deleteSnapshot(filename){\n'
        '  window.webkit.messageHandlers.nativeExport.postMessage({action:"deleteSnapshot",filename:filename});\n'
        '}\n'
        'function loadSnapshot(filename){\n'
        '  window.webkit.messageHandlers.nativeExport.postMessage({action:"loadSnapshot",filename:filename});\n'
        '}\n'
        'function compareFromFile(){\n'
        '  window.webkit.messageHandlers.nativeExport.postMessage({action:"compareFromFile"});\n'
        '}\n'
        'function closeSnapshots(){\n'
        '  document.getElementById("snapshot-picker").style.display="none";\n'
        '  document.getElementById("report-sections").style.display="";\n'
        '}\n'
        'function parseReport(doc){\n'
        '  var sections={};\n'
        '  doc.querySelectorAll("details").forEach(function(d){\n'
        '    var t=d.querySelector(".section-title");if(!t)return;\n'
        '    var name=t.textContent.trim();var items={};\n'
        '    d.querySelectorAll("li").forEach(function(li){\n'
        '      var n=li.querySelector(".app-name");var v=li.querySelector(".ver");\n'
        '      if(n)items[n.textContent.trim()]=v?v.textContent.trim():"";\n'
        '    });\n'
        '    sections[name]=items;\n'
        '  });\n'
        '  var sysEl=doc.querySelector(".sysinfo");\n'
        '  if(sysEl){\n'
        '    var sys={};\n'
        '    sysEl.textContent.split("\\u00b7").map(function(s){return s.trim();}).filter(Boolean).forEach(function(p){\n'
        '      if(p.match(/MacBook|Mac mini|Mac Pro|iMac/i))sys["Model"]=p;\n'
        '      else if(p.match(/inch/i))sys["Display"]=p;\n'
        '      else if(p.match(/Apple M|Intel/i))sys["Chip"]=p;\n'
        '      else if(p.match(/\\d+\\s*GB/) && !p.match(/SN/i))sys["Memory"]=p;\n'
        '      else if(p.match(/SN:/i))sys["Serial"]=p.replace(/SN:\\s*/,"");\n'
        '      else if(p.match(/\\d+\\.\\d+/))sys["macOS"]=p;\n'
        '    });\n'
        '    if(Object.keys(sys).length)sections["System"]=sys;\n'
        '  }\n'
        '  return sections;\n'
        '}\n'
        'function receiveCompareData(html,filename){\n'
        '  var parser=new DOMParser();\n'
        '  var prevDoc=parser.parseFromString(html,"text/html");\n'
        '  var prev=parseReport(prevDoc);\n'
        '  var curr=parseReport(document);\n'
        '  var allSections=Array.from(new Set(Object.keys(curr).concat(Object.keys(prev)))).sort(function(a,b){\n'
        '    if(a==="System")return -1;if(b==="System")return 1;return a.localeCompare(b);\n'
        '  });\n'
        '  var totalAdded=0,totalRemoved=0,totalUpdated=0;\n'
        '  var cards="";\n'
        '  allSections.forEach(function(sec){\n'
        '    var c=curr[sec]||{};var p=prev[sec]||{};\n'
        '    var added=[],removed=[],updated=[];\n'
        '    Object.keys(c).forEach(function(n){\n'
        '      if(!(n in p))added.push({n:n,v:c[n]});\n'
        '      else if(c[n]!==p[n]&&c[n]&&p[n])updated.push({n:n,ov:p[n],nv:c[n]});\n'
        '    });\n'
        '    Object.keys(p).forEach(function(n){if(!(n in c))removed.push({n:n,v:p[n]});});\n'
        '    if(!added.length&&!removed.length&&!updated.length)return;\n'
        '    totalAdded+=added.length;totalRemoved+=removed.length;totalUpdated+=updated.length;\n'
        '    var rows="";\n'
        '    if(added.length){rows+=\'<div class="diff-group-label added">Added (\'+added.length+\')</div>\';\n'
        '      added.forEach(function(i){rows+=\'<div class="diff-row"><span class="name">\'+i.n+\'</span><span class="ver-new">\'+i.v+\'</span></div>\';});}\n'
        '    if(removed.length){rows+=\'<div class="diff-group-label removed">Removed (\'+removed.length+\')</div>\';\n'
        '      removed.forEach(function(i){rows+=\'<div class="diff-row"><span class="name">\'+i.n+\'</span><span class="ver-old">\'+i.v+\'</span></div>\';});}\n'
        '    if(updated.length){rows+=\'<div class="diff-group-label updated">Updated (\'+updated.length+\')</div>\';\n'
        '      updated.forEach(function(i){rows+=\'<div class="diff-row"><span class="name">\'+i.n+\'</span><span class="ver-old">\'+i.ov+\'</span><span class="ver-arrow">→</span><span class="ver-updated">\'+i.nv+\'</span></div>\';});}\n'
        '    var total=added.length+removed.length+updated.length;\n'
        '    cards+=\'<details class="diff-card"><summary>\'+sec+\'<span class="diff-card-count">\'+total+\'</span></summary><div class="diff-card-body">\'+rows+\'</div></details>\';\n'
        '  });\n'
        '  var report=document.getElementById("report-sections");\n'
        '  var diff=document.getElementById("diff-view");\n'
        '  report.style.display="none";\n'
        '  diff.style.display="block";\n'
        '  diff.innerHTML=\'<div class="diff-header"><h2>Changes</h2><div class="diff-meta">Comparing current scan vs. <span>\'+filename+\'</span></div></div>\'\n'
        '    +\'<div class="diff-summary"><span class="diff-stat added">+ \'+totalAdded+\' added</span><span class="diff-stat removed">— \'+totalRemoved+\' removed</span><span class="diff-stat updated">↑ \'+totalUpdated+\' updated</span><button class="export-btn" style="margin-left:auto" onclick="closeCompare()">Back</button></div>\'\n'
        '    +(cards||\'<div class="no-changes">No changes found between the two reports.</div>\');\n'
        '}\n'
        'function closeCompare(){\n'
        '  document.getElementById("report-sections").style.display="";\n'
        '  document.getElementById("diff-view").style.display="none";\n'
        '  document.getElementById("snapshot-picker").style.display="none";\n'
        '}\n'
        '</script>\n'
        '</body>\n</html>\n'
    )
    _dbg("build() HTML assembled")
    html = "".join(parts)
    _dbg(f"build() done, html size: {len(html)} bytes")
    return html

if __name__ == "__main__":
    _dbg("main: calling build()")
    html = build()
    _dbg("main: writing output")
    print(html)
    _dbg("main: done")
