# mac-cron-manager

A dashboard for the cron jobs on your Mac: every job with its next run, last
result, run history and live output, plus run / enable / disable buttons.

**Open it:** https://induction-axiom.github.io/mac-cron-manager/ — or http://127.0.0.1:7878 once the agent is installed.

cron itself doesn't remember whether a job worked. `cronmon` fills that gap: put
it in front of a job's command and it records start/end time, exit code and output.

```
*/30 * * * * ~/mac-cron-manager/cronmon photo-sync /path/to/run.sh
```

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
| status, history, logs | ✅ | ✅ |
| run now, enable / disable existing jobs | ✅ | ✅ |
| edit the crontab, wrap a job in `cronmon` | ❌ | ✅ |

The limits are enforced by the agent, not the page: even a tampered hosted page can
only run or switch jobs that are already in your crontab, never add a command. Any
other website is refused outright (Origin allow-list, `Host` check against DNS
rebinding, and a custom header that forces a CORS preflight). Allowed hosted origins
are set with `MCM_REMOTE_ORIGINS` (comma-separated; empty to disable the hosted page).

Every crontab change is backed up to `~/.local/state/mac-cron-manager/backups/` first.
Disabled jobs stay in the crontab as `#off# <line>`.

| File | Role |
|---|---|
| `cronmon` | Wrapper used in crontab lines; writes `~/.local/state/mac-cron-manager/jobs/<name>/` (`runs.jsonl`, `last.log`, `current.log`, `running`). Skips a run if the previous one is still going. |
| `web.py` | The agent (Python standard library HTTP server). |
| `static/index.html` | The dashboard page, served by the agent and published to GitHub Pages. |
| `core.py` | Reads/writes the crontab and job state. |
| `tui.py` | Terminal dashboard ([Textual](https://textual.textualize.io/)). |

## macOS notes

- cron needs **Full Disk Access** (System Settings → Privacy & Security → add `/usr/sbin/cron`)
  for jobs that touch protected folders like `~/Pictures`, `~/Documents` or `~/Desktop`.
  The dashboard flags jobs that failed for this reason.
- cron skips runs while the Mac is asleep; it doesn't catch up afterwards.
- Keyboard: `j/k` select, `r` run, `space` enable/disable, `w` monitor, `e` edit crontab.

## License

[AGPL-3.0](LICENSE). Copyright (c) 2026 Jong Luo.
