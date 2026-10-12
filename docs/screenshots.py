"""Regenerate the README images from made-up data, without touching this Mac's crontab or launchd.

    .venv/bin/python docs/screenshots.py

Serves static/index.html with a fake agent API on 127.0.0.1:7979, then has headless Chrome
capture the cron and launchd tabs, light and dark, into docs/images/.
"""

import json
import random
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import core  # noqa: E402  (next-run labels, computed the same way as the real agent)

OUT = ROOT / "docs" / "images"
PORT = 7979
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
NOW = int(time.time())
HOME = "/Users/alex"
STATE = f"{HOME}/.local/state/mac-cron-manager/jobs"
random.seed(7)


# ---------------------------------------------------------------- made-up cron jobs

def make_runs(every: int, count: int, took: tuple[int, int], fail_at=(), skip_at=()) -> list[dict]:
    runs = []
    for i in range(count):
        start = NOW - (count - i) * every + random.randint(0, 3)
        if i in skip_at:
            runs.append({"start": start, "end": start, "exit": None, "status": "skipped", "reason": "iPhone Personal Hotspot"})
            continue
        end = start + random.randint(*took)
        status = "fail" if i in fail_at else "ok"
        runs.append({"start": start, "end": end, "exit": 1 if status == "fail" else 0, "status": status})
    return runs


CRON = [
    ("photo-backup", "*/30 * * * *", "Every 30 minutes",
     f"{HOME}/mac-cron-manager/cronmon --unmetered photo-backup {HOME}/jobs/photo-backup.sh",
     make_runs(1800, 46, (4, 190), fail_at={12}, skip_at={20, 21, 22})),
    ("notes-export", "0 * * * *", "At 0 minutes past the hour",
     f"{HOME}/mac-cron-manager/cronmon notes-export {HOME}/jobs/notes-export.sh",
     make_runs(3600, 24, (2, 9), fail_at={23})),
    ("brew-upgrade", "0 9 * * 1", "At 09:00, only on Monday",
     f"{HOME}/mac-cron-manager/cronmon brew-upgrade /opt/homebrew/bin/brew upgrade",
     make_runs(7 * 86400, 6, (40, 300))),
    ("cleanup-downloads", "15 3 * * *", "At 03:15", f"{HOME}/bin/cleanup-downloads.sh", []),
]

LOGS = {
    "photo-backup": """[{t}] 1,284 media files in {home}/Pictures
[{t}] syncing with backup@nas.local:/volume1/photos
[{t}] uploaded 37 files, 412.6 MB
[{t}] verified 37 files on the NAS
[{t}] cleanup: 52 verified files older than 30 days can be removed""",
    "notes-export": """[{t}] exporting 312 notes to {home}/Documents/notes-export
[{t}] error: {home}/Documents/notes-export: Operation timed out
[{t}] export failed after 1 attempt""",
}


def cron_json(i, name, schedule, desc, command, runs):
    monitored = "cronmon" in command
    return {
        "line": i, "raw": f"{schedule} {command}", "name": name, "schedule": schedule, "desc": desc,
        "command": command, "enabled": True, "monitored": monitored, "running_since": None,
        "next": core.next_run(core.Job(i, "", schedule, command, True, name, monitored)),
        "last": runs[-1] if runs else None,
        "history": [{"start": r["start"], "status": r["status"], "reason": r.get("reason", "")} for r in runs[-24:]],
        "warning": "",
    }


def log_for(name: str, run: str) -> dict:
    runs = next(r for n, *_, r in CRON if n == name)
    start = int(run) if run.isdigit() else next((r["start"] for r in reversed(runs) if r["status"] != "skipped"), NOW)
    if any(r["start"] == start and r["status"] == "skipped" for r in runs):
        return {"file": "", "text": "", "exists": False}
    t = time.strftime("%H:%M:%S", time.localtime(start))
    text = LOGS.get(name, "[{t}] done").format(t=t, home=HOME)
    return {"file": f"{STATE}/{name}/logs/{start}.log", "text": text, "exists": True}


# ---------------------------------------------------------------- made-up launchd jobs

def agent(scope, label, developer, trigger, program, **kw):
    kind = "daemon" if scope == "system" else "agent"
    folder = {"user": f"{HOME}/Library/LaunchAgents", "global": "/Library/LaunchAgents", "system": "/Library/LaunchDaemons"}[scope]
    job = {"id": f"{scope}:{label}", "kind": kind, "scope": scope, "plist": f"{folder}/{label}.plist", "label": label,
           "readable": True, "program": program, "command": program or "", "developer": developer, "trigger": trigger,
           "next": None, "pid": None, "last_exit": None, "loaded": False if kind == "agent" else None,
           "disabled": False, "warning": "", "note": "", "actions": ["toggle"] if kind == "agent" else [], "logs": []}
    job.update(kw)
    return job


LAUNCHD = [
    agent("user", "com.lwouis.alt-tab-macos", "missing", "at login", "/Applications/AltTab.app/Contents/MacOS/AltTab",
          warning="program not found (/Applications/AltTab.app/Contents/MacOS/AltTab): probably left over from an uninstalled app"),
    agent("user", "com.google.GoogleUpdater.wake", "Google LLC", "every 1h",
          f"{HOME}/Library/Application Support/Google/GoogleUpdater/Current/GoogleUpdater.app/Contents/MacOS/GoogleUpdater"),
    agent("user", "local.mac-cron-manager", "you", "at login · kept alive", f"{HOME}/mac-cron-manager/.venv/bin/python",
          command=f"{HOME}/mac-cron-manager/.venv/bin/python {HOME}/mac-cron-manager/web.py",
          pid=48213, loaded=True, actions=[], logs=[f"{HOME}/.local/state/mac-cron-manager/web.log"]),
    agent("user", "com.valvesoftware.steamclean", "Valve Corporation", "at login · on file change",
          f"{HOME}/Library/Application Support/Steam/SteamApps/steamclean", loaded=True, last_exit=0, actions=["toggle", "run"]),
    agent("global", "com.logi.ghub", "missing", "at login", "/Applications/lghub.app/Contents/MacOS/lghub_system_tray",
          warning="program not found (/Applications/lghub.app/Contents/MacOS/lghub_system_tray): probably left over from an uninstalled app"),
    agent("global", "com.google.keystone.agent", "?", "manual", None,
          note="empty plist: this job does nothing (its app has switched it off)"),
    agent("system", "us.zoom.ZoomDaemon", "Zoom Video Communications, Inc.", "on demand",
          "/Library/PrivilegedHelperTools/us.zoom.ZoomDaemon"),
    agent("system", "com.docker.vmnetd", "Docker Inc", "on demand", "/Library/PrivilegedHelperTools/com.docker.vmnetd"),
]


# ---------------------------------------------------------------- fake agent

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/":
            html = (ROOT / "static" / "index.html").read_text()
            # Pin the theme and the starting tab, which a real page takes from the system and from the last visit.
            html = html.replace("@media (prefers-color-scheme: dark)", "@media all" if q.get("theme") == "dark" else "@media not all")
            html = html.replace("<script>", f"<script>localStorage.setItem('mcm.tab', '{q.get('tab', 'cron')}');\n", 1)
            return self.reply(html.encode(), "text/html; charset=utf-8")
        if url.path == "/api/state":
            return self.reply({"host": "MacBook-Pro", "cron_up": True,
                               "jobs": [cron_json(i, *job) for i, job in enumerate(CRON)]})
        if url.path == "/api/runs":
            runs = next(r for n, *_, r in CRON if n == q["name"])
            return self.reply({"runs": [{**r, "log": r["status"] != "skipped"} for r in runs]})
        if url.path == "/api/log":
            return self.reply(log_for(q["name"], q.get("run", "")))
        if url.path == "/api/launchd":
            return self.reply({"jobs": LAUNCHD})
        if url.path == "/api/launchd/detail":
            job = next(j for j in LAUNCHD if j["id"] == q["id"])
            log = "mac-cron-manager agent 0.4.0 on http://127.0.0.1:7878\n" if job["logs"] else ""
            return self.reply({"runs": 1 if job["loaded"] else None, "log": log, "log_files": job["logs"]})
        self.send_error(404)


def capture(tab: str, theme: str) -> None:
    out = OUT / f"{tab}-{theme}.png"
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run([CHROME, "--headless=new", f"--user-data-dir={profile}", "--hide-scrollbars",
                        "--window-size=1280,780", "--force-device-scale-factor=2", "--virtual-time-budget=4000",
                        f"--screenshot={out}", f"http://127.0.0.1:{PORT}/?tab={tab}&theme={theme}"],
                       check=True, capture_output=True)
    print("wrote", out.relative_to(ROOT))


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for tab in ("cron", "launchd"):
        for theme in ("light", "dark"):
            capture(tab, theme)
    server.shutdown()
