# mac-cron-manager

A dashboard for the scheduled and background jobs on your Mac.

- **cron**: every job with its next run, last result and live output, plus a run timeline you can click or drag through to read any past run's output; run / enable / disable / edit.
- **launchd**: the non-Apple agents and daemons apps have installed: who made each one, what triggers it,
  whether it's running, its log, and leftovers from uninstalled apps (program missing, empty plists).
  Agents can be started or disabled for your user; system daemons are view-only (they run as root).

Calm, readable interface that follows the system light/dark setting.

**Open it:** http://127.0.0.1:7878 once the agent is installed. https://induction-axiom.github.io/mac-cron-manager/
takes you there too, or shows how to install it if your Mac doesn't have it yet.

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
that runs from login onwards and restarts if it dies. Then open http://127.0.0.1:7878.

- Different port: `MCM_PORT=8080 ./install.sh` (the hosted page takes `?agent=http://127.0.0.1:8080`)
- Remove: `./install.sh --uninstall`

## How it works

The **agent** (`web.py`) is a small HTTP server that listens on `127.0.0.1` only and runs
as your user. It reads `crontab -l`, the job records `cronmon` writes and the launchd
folders, and serves the dashboard page together with its JSON API.

Because it can edit the crontab and run commands, only the page it serves itself may use
it: requests need a `Host` of 127.0.0.1/localhost (against DNS rebinding) and no `Origin`
or the agent's own, and POSTs a custom header that cross-site forms can't send.

The hosted page (`site/`, on GitHub Pages) holds no data and has no access to your jobs.
It may only call `GET /api/ping` to see whether an agent is running, then sends the browser
to http://127.0.0.1:7878. Its origin is set with `MCM_HOSTED_ORIGINS` (comma-separated; empty
to turn the ping off).

Every crontab change is backed up to `~/.local/state/mac-cron-manager/backups/` first.
Disabled jobs stay in the crontab as `#off# <line>`.

| File | Role |
|---|---|
| `cronmon` | Wrapper used in crontab lines; writes `~/.local/state/mac-cron-manager/jobs/<name>/` (`runs.jsonl`, `last.log`, `current.log`, `running`, and `logs/<start>.log` for the last `MCM_KEEP_LOGS` runs, default 100). Skips a run if the previous one is still going. |
| `web.py` | The agent (Python standard library HTTP server). |
| `static/index.html` | The dashboard page, served by the agent. |
| `site/index.html` | The hosted landing page: finds the agent and opens it, or explains the install. |
| `core.py` | Reads/writes the crontab and job state. |
| `netcheck` | Tells whether the current network is a phone hotspot / untrusted (used by `cronmon --unmetered`). |
| `launchd.py` | Scans `~/Library/LaunchAgents`, `/Library/LaunchAgents`, `/Library/LaunchDaemons`; checks code signatures. |

## macOS notes

- cron needs **Full Disk Access** (System Settings → Privacy & Security → add `/usr/sbin/cron`)
  for jobs that touch protected folders like `~/Pictures`, `~/Documents` or `~/Desktop`.
  The dashboard flags jobs that failed for this reason.
- cron skips runs while the Mac is asleep; it doesn't catch up afterwards.
- Keyboard: `1`/`2` cron/launchd, `j/k` select, `←/→` past runs, `r` run, `space` enable/disable, `w` monitor, `e` edit crontab.

## License

[AGPL-3.0](LICENSE). Copyright (c) 2026 Jong Luo.
