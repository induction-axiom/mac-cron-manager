"""mac-cron-manager agent: serves the dashboard and its API on http://127.0.0.1:7878 only.

install.sh registers it as a launchd agent so it starts at login and restarts if it dies.

The agent can edit the crontab and run commands, so only the page it serves itself may
use it. A request is refused unless
  - its Host is 127.0.0.1 / localhost on our port (stops DNS rebinding), and
  - it has no Origin, or our own (browsers always send Origin for other websites' requests).
POSTs must also carry an X-MCM header, which a cross-site form can't add.

The one exception: the hosted page (GitHub Pages) may GET /api/ping. It learns only
that an agent is running, and then sends the browser to http://127.0.0.1:7878.
"""

import json
import os
import socket
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import core
import launchd

VERSION = "0.4.0"
PORT = int(os.environ.get("MCM_PORT", "7878"))
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
LOCAL_ORIGINS = {f"http://{h}" for h in ALLOWED_HOSTS}
HOSTED_ORIGINS = {o.strip().rstrip("/") for o in
                  os.environ.get("MCM_HOSTED_ORIGINS", "https://induction-axiom.github.io").split(",") if o.strip()}
INDEX = core.HERE / "static" / "index.html"
KEEP_IDLE_DAYS = int(os.environ.get("MCM_KEEP_IDLE_DAYS", "2"))  # cronmon's default too


def job_json(job: core.Job) -> dict:
    return {
        "line": job.line_no, "raw": job.raw, "name": job.name,
        "schedule": job.schedule, "desc": core.describe(job.schedule), "command": job.command,
        "enabled": job.enabled, "monitored": job.monitored, "running_since": job.running_since,
        "next": core.next_run(job), "last": job.last,
        "history": [{k: r.get(k) for k in ("start", "status", "reason", "summary")} for r in job.history],
        "warning": job.warning(),
    }


def find_job(name: str) -> core.Job | None:
    return next((j for j in core.load_jobs() if j.name == name), None)


class Handler(BaseHTTPRequestHandler):
    server_version = "mac-cron-manager"

    def log_message(self, *args):
        pass

    @property
    def route(self) -> str:
        return urlparse(self.path).path

    def query(self, key: str) -> str:
        return parse_qs(urlparse(self.path).query).get(key, [""])[0]

    def hosted_ping(self) -> bool:
        return self.headers.get("Origin") in HOSTED_ORIGINS and self.route == "/api/ping"

    def allowed(self) -> bool:
        if self.headers.get("Host") not in ALLOWED_HOSTS:
            return False
        origin = self.headers.get("Origin")
        if origin is None or origin in LOCAL_ORIGINS:
            return self.command != "POST" or self.headers.get("X-MCM") == "1"
        return self.command == "GET" and self.hosted_ping()

    def reply(self, code: int, body=b"", ctype="application/json", extra=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        if data:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Vary", "Origin")
        if self.hosted_ping():
            self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        # Chrome asks before a public site may reach localhost (Private Network Access).
        if self.headers.get("Host") not in ALLOWED_HOSTS or not self.hosted_ping():
            return self.reply(403)
        self.reply(204, extra={"Access-Control-Allow-Methods": "GET", "Access-Control-Max-Age": "600",
                               "Access-Control-Allow-Private-Network": "true"})

    def do_GET(self):
        if not self.allowed():
            return self.reply(403, {"error": "forbidden"})
        path = self.route
        if path == "/api/ping":
            return self.reply(200, {"app": "mac-cron-manager", "version": VERSION})
        if path == "/":
            return self.reply(200, INDEX.read_bytes(), "text/html; charset=utf-8")
        if path == "/api/state":
            return self.reply(200, {
                "host": socket.gethostname().split(".")[0],
                "cron_up": core.cron_up(),
                "jobs": [job_json(j) for j in core.load_jobs()],
            })
        if path in ("/api/log", "/api/runs"):
            job = find_job(self.query("name"))
            if not job:
                return self.reply(404, {"error": "no such job"})
            if path == "/api/runs":
                return self.reply(200, {"runs": [{**r, "log": job.run_log(r["start"]).exists()} for r in job.runs],
                                        "idle_days": KEEP_IDLE_DAYS})
            run = self.query("run")
            f = job.run_log(int(run)) if run.isdigit() else job.log_file
            text = "\n".join(f.read_text(errors="replace").splitlines()[-500:]) if f.exists() else ""
            return self.reply(200, {"file": str(f), "text": text, "exists": f.exists()})
        if path == "/api/crontab":
            return self.reply(200, {"text": "\n".join(core.read_crontab())})
        if path == "/api/launchd":
            return self.reply(200, {"jobs": launchd.scan()})
        if path == "/api/launchd/detail":
            job = launchd.find(self.query("id"))
            if not job:
                return self.reply(404, {"error": "no such job"})
            return self.reply(200, launchd.details(job))
        self.reply(404, {"error": "not found"})

    def do_POST(self):
        if not self.allowed():
            return self.reply(403, {"error": "forbidden"})
        path = self.route
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if path == "/api/crontab":
                lines = body["text"].splitlines()
                errors = core.validate(lines)
                if errors:
                    return self.reply(400, {"error": "\n".join(errors)})
                core.write_crontab(lines)
                return self.reply(200, {"ok": True})

            if path.startswith("/api/launchd/"):
                job = launchd.find(body.get("id", ""))
                action = path.removeprefix("/api/launchd/")
                if not job or action not in job["actions"]:
                    return self.reply(400, {"error": f"can't {action} this job"})
                (launchd.run_now if action == "run" else launchd.toggle)(job)
                return self.reply(200, {"ok": True})

            if path not in ("/api/run", "/api/toggle", "/api/wrap"):
                return self.reply(404, {"error": "not found"})

            # Job actions name the line and its exact text, so a crontab edited
            # elsewhere in the meantime can't make us act on the wrong job.
            lines = core.read_crontab()
            job = next((j for j in core.load_jobs()
                        if j.line_no == body.get("line") and j.raw == body.get("raw")), None)
            if not job:
                return self.reply(409, {"error": "crontab changed, refreshing"})
            if path == "/api/run":
                if job.running_since:
                    return self.reply(409, {"error": f"{job.name} is already running"})
                core.run_now(job)
            elif path == "/api/toggle":
                core.write_crontab(core.toggle(lines, job))
            elif path == "/api/wrap":
                core.write_crontab(core.wrap(lines, job))
            return self.reply(200, {"ok": True})
        except subprocess.CalledProcessError as e:
            return self.reply(500, {"error": (e.stderr or b"").decode(errors="replace").strip() or str(e)})
        except (ValueError, KeyError) as e:
            return self.reply(400, {"error": str(e)})


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"mac-cron-manager agent {VERSION} on http://127.0.0.1:{PORT}", flush=True)
    server.serve_forever()
