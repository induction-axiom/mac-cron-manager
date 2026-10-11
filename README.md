# mac-cron-manager

A small, local dashboard for the crontab on your Mac: a web page at
`http://127.0.0.1:7878` (plus a terminal version), showing every cron job with
its next run, last result, run history and live output.

cron itself doesn't remember whether a job worked. `cronmon` fills that gap: put
it in front of a job's command and it records start/end time, exit code and the
output, which mac-cron-manager reads.

```
*/30 * * * * /path/to/mac-cron-manager/cronmon photo-sync /path/to/run.sh
```

## Features

- Status at a glance: running / ok / failed / disabled, next run, last run, duration,
  last 24 runs as a coloured history bar, and a favicon that turns red when a job fails.
- Live log of the selected job while it runs.
- Run now, enable/disable (kept in the crontab as `#off# <line>`), wrap an
  unmonitored job in `cronmon`, and edit the crontab in the browser with validation.
- Every crontab change is backed up to `~/.local/state/mac-cron-manager/backups/` first.
- Spots the classic macOS failure (cron lacking Full Disk Access) and tells you how to fix it.
- Keyboard: `j/k` select, `r` run, `space` enable/disable, `w` monitor, `e` edit crontab.

## Install

Requires macOS and Python 3.10+.

```bash
git clone https://github.com/induction-axiom/mac-cron-manager ~/mac-cron-manager
~/mac-cron-manager/install.sh
```

`install.sh` creates a virtualenv and registers a launchd agent (`local.mac-cron-manager`)
that serves the dashboard from login onwards and restarts it if it dies.
Change the port with `MCM_PORT=8080 ./install.sh`; remove it with `./install.sh --uninstall`.

Terminal version: `~/mac-cron-manager/tui`.

## Security

The dashboard can edit your crontab and run commands, so:

- it only listens on `127.0.0.1`;
- it rejects requests whose `Host` isn't `127.0.0.1`/`localhost` (blocks DNS rebinding);
- state-changing requests need an `X-MCM` header and a same-origin `Origin`, so
  other pages open in your browser can't forge them.

Don't expose the port to a network.

## How it fits together

| File | Role |
|---|---|
| `cronmon` | Bash wrapper used in crontab lines; writes `~/.local/state/mac-cron-manager/jobs/<name>/` (`runs.jsonl`, `last.log`, `current.log`, `running`). Skips a run if the previous one is still going. |
| `core.py` | Reads/writes the crontab and job state. |
| `web.py` + `static/index.html` | Web dashboard (Python standard library HTTP server, no build step). |
| `tui.py` | Terminal dashboard ([Textual](https://textual.textualize.io/)). |

## macOS notes

- cron needs **Full Disk Access** (System Settings → Privacy & Security → add `/usr/sbin/cron`)
  for jobs that touch protected folders like `~/Pictures`, `~/Documents` or `~/Desktop`.
- cron skips runs while the Mac is asleep; it doesn't catch up afterwards.

## License

[AGPL-3.0](LICENSE). Copyright (c) 2026 Jong Luo.
