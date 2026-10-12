# mac-cron-manager

A dashboard for the scheduled and background jobs on your Mac.

- **cron**: every job with its next run, last result and live output, plus a run timeline you can click or drag through to read any past run's output; run / enable / disable / edit.
- **launchd**: the non-Apple agents and daemons apps have installed: who made each one, what triggers it,
  whether it's running, its log, and leftovers from uninstalled apps (program missing, empty plists).
  Agents can be started or disabled for your user; system daemons are view-only (they run as root).

Black-and-white interface that follows the system light/dark setting.

**Open it:** https://induction-axiom.github.io/mac-cron-manager/ — or http://127.0.0.1:7878 once the agent is installed.

cron itself doesn't remember whether a job worked. `cronmon` fills that gap: put
it in front of a job's command and it records start/end time, exit code and output.

```
*/30 * * * * ~/mac-cron-manager/cronmon photo-sync /path/to/run.sh
```

## Skip phone hotspots

Big transfers on a phone's data plan are rarely what you want. Add `--unmetered`:

```
*/30 * * * * ~/mac-cron-manager/cronmon --unmetered photo-sync /path/to/run.sh
```

The run is skipped on a phone hotspot, and if the Mac switches to one while the job is
running, the whole job (including e.g. an rsync it started) is stopped within ~20 s.
Both show up as a grey "skipped" with the reason. `netcheck` does the detection:
iPhone Personal Hotspot (172.20.10.x), Android hotspots (DHCP marks them
`ANDROID_METERED`), iPhone USB / Bluetooth tethering. macOS hides the Wi-Fi name from
background jobs, so to go further and run only on networks you trust, trust them by
their router: `netcheck --trust` while on the network (`netcheck --list` shows them).

## Install

Requires macOS and Python 3.10+.

```bash
git clone https://github.com/induction-axiom/mac-cron-manager ~/mac-cron-manager
~/mac-cron-manager/install.sh
```

`install.sh` creates a virtualenv and registers a launchd agent (`local.mac-cron-manager`)
that runs from login onwards and restarts if it dies. Then open the hosted page; the first
time, Chrome asks whether the site may access apps on your device — choose **Allow**.

- Different port: `MCM_PORT=8080 ./install.sh` (then open the hosted page with `?agent=http://127.0.0.1:8080`)
- Remove: `./install.sh --uninstall`
- Terminal version: `~/mac-cron-manager/tui`

## How it works

The page is only an interface. All access to your Mac goes through the **agent**
(`web.py`), a small HTTP server that listens on `127.0.0.1` only, runs as your user,
reads `crontab -l` and the job records `cronmon` writes, and returns JSON.
The same page can be loaded two ways:

| | hosted page (GitHub Pages) | local page (`http://127.0.0.1:7878`) |
|---|---|---|
| cron + launchd status, history, logs | ✅ | ✅ |
| run now, enable / disable existing **cron** jobs | ✅ | ✅ |
| edit the crontab, wrap a job in `cronmon` | ❌ | ✅ |
| start / disable **launchd** agents | ❌ | ✅ |

The limits are enforced by the agent, not the page: even a tampered hosted page can
only run or switch cron jobs that are already in your crontab. It can't add a command,
and it can't touch launchd jobs (so it can't, say, switch off security software). Any
other website is refused outright (Origin allow-list, `Host` check against DNS
rebinding, and a custom header that forces a CORS preflight). Allowed hosted origins
are set with `MCM_REMOTE_ORIGINS` (comma-separated; empty to disable the hosted page).

Every crontab change is backed up to `~/.local/state/mac-cron-manager/backups/` first.
Disabled jobs stay in the crontab as `#off# <line>`.

| File | Role |
|---|---|
| `cronmon` | Wrapper used in crontab lines; writes `~/.local/state/mac-cron-manager/jobs/<name>/` (`runs.jsonl`, `last.log`, `current.log`, `running`, and `logs/<start>.log` for the last `MCM_KEEP_LOGS` runs, default 100). Skips a run if the previous one is still going. |
| `web.py` | The agent (Python standard library HTTP server). |
| `static/index.html` | The dashboard page, served by the agent and published to GitHub Pages. |
| `core.py` | Reads/writes the crontab and job state. |
| `netcheck` | Tells whether the current network is a phone hotspot / untrusted (used by `cronmon --unmetered`). |
| `launchd.py` | Scans `~/Library/LaunchAgents`, `/Library/LaunchAgents`, `/Library/LaunchDaemons`; checks code signatures. |
| `tui.py` | Terminal dashboard ([Textual](https://textual.textualize.io/)). |

## macOS notes

- cron needs **Full Disk Access** (System Settings → Privacy & Security → add `/usr/sbin/cron`)
  for jobs that touch protected folders like `~/Pictures`, `~/Documents` or `~/Desktop`.
  The dashboard flags jobs that failed for this reason.
- cron skips runs while the Mac is asleep; it doesn't catch up afterwards.
- Keyboard: `1`/`2` cron/launchd, `j/k` select, `r` run, `space` enable/disable, `w` monitor, `e` edit crontab.

## License

[AGPL-3.0](LICENSE). Copyright (c) 2026 Jong Luo.
