# Project Handoff: macOS Software Inventory Tool

## Overview
A Python script that scans a macOS system for all installed software across every
package manager and system mechanism, then outputs a self-contained styled HTML report
with collapsible accordion sections.

## Current State
Single file: `inventory_mac.py`
Usage: `python3 inventory_mac.py > software_inventory.html && open software_inventory.html`
No pip dependencies — stdlib only (subprocess, json, re, datetime, pathlib, concurrent.futures).

## What It Does
Scans and reports on the following sources, **hiding any section entirely if the
corresponding tool is not installed on the system:**

### App Directories
- `/Applications` — with version numbers from Info.plist
- `~/Applications` — same

### Package Managers (section hidden if tool not found)
- **Homebrew** — casks and formulae via single `brew info --json=v2 --installed` call
- **MacPorts** — `port installed`
- **Fink** — `fink list --installed`
- **Nix** — `nix-env -q`
- **pip3** — `pip3 list`
- **npm** — `npm list -g --depth=0`
- **gem** — `gem list --local`
- **cargo** — `cargo install --list`
- **conda/mamba** — `conda list --json`

### Mac App Store (three distinct states)
- `mas` installed → full list: app name, version, App Store ID (cleanest output)
- `mas` not installed but MAS receipts found → app names only + amber notice explaining
  limitation and how to install mas: `brew install mas`
- `mas` not installed and no receipts → section omitted entirely

### System/Background Software
- Launch Agents — User (`~/Library/LaunchAgents`)
- Launch Agents — System (`/Library/LaunchAgents`)
- Launch Daemons — System (`/Library/LaunchDaemons`)
- Login Items — via `sfltool dumpbtm` (macOS 13+ only; section omitted if unavailable)

## Performance
All data sources are fetched concurrently via `ThreadPoolExecutor` (12 top-level workers).
Per-app PlistBuddy reads are also parallelized (16 workers per directory scan).
Key optimization: Homebrew uses a single JSON API call instead of one subprocess per package.

## HTML Output Design
- Inspired by: https://github.com/lightisbeauty/Collision/blob/main/Collision.html
- Black background `#000`, cards `#2a2a2a`, borders `0.5px solid #444`
- Accent color: `#41b6e6` (cyan) for titles, version badges, arrows
- Amber `#a07840` for notices (e.g. mas not installed warning)
- Monospace data, `-apple-system` UI font
- Native HTML `<details>`/`<summary>` accordions — no JS required
- Version numbers displayed as teal pill badges, right-aligned
- Item count shown in collapsed header so user knows what's inside before expanding
- `/Applications` opens expanded by default; all others collapsed
- Self-contained single HTML file, no external dependencies or CDN

## Runtime Dependencies
| Dependency | Required | Notes |
|---|---|---|
| macOS 10.15+ | Yes | PlistBuddy assumed at /usr/libexec/PlistBuddy |
| Python 3.8+ | Yes | f-strings, ThreadPoolExecutor |
| Homebrew | Optional | Cask/formula sections hidden without it |
| mas | Optional | `brew install mas` — best App Store output |
| macOS 13+ | Optional | Login Items via sfltool |
| All other package managers | Optional | Sections hidden if not found |

## What Still Needs To Be Done
1. **README.md** — install instructions, dependency matrix, usage, screenshots
2. **LICENSE** — MIT recommended
3. **CONTRIBUTING.md** — contribution guidelines
4. **Dependency check / user guidance** — script currently silently skips missing tools;
   could optionally print a pre-run summary to stderr listing what was/wasn't found
5. **`--verbose` / `--quiet` flags** — CLI argument parsing not yet implemented
6. **Output path flag** — e.g. `--output ~/Desktop/inventory.html` instead of stdout redirect
7. **GitHub repo structure** — currently just a single .py file
8. **Screenshots** for README
9. **Testing** — no tests exist yet; would need a mock/fixture approach since
   subprocess calls are tightly coupled throughout

## Design Decisions Made
- **No `setup.sh` or auto-install** — decided against auto-installing Homebrew/mas;
  README documentation is sufficient; Homebrew installer is interactive anyway
- **Python over bash** — original bash version had heredoc quoting issues that
  corrupted HTML output; Python avoids this entirely
- **Hide vs empty** — sections are omitted entirely when the tool isn't present,
  rather than showing an empty card
- **Single brew JSON call** — `brew info --json=v2 --installed` replaces N individual
  `brew list --versions <name>` calls; biggest single performance win
