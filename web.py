"""mac-cron-manager agent: the local API behind the dashboard, on http://127.0.0.1:7878

install.sh registers it as a launchd agent so it starts at login and restarts if it dies.

The same dashboard page can be opened two ways:
  - locally, served by this agent at http://127.0.0.1:7878
  - from the hosted page (GitHub Pages), which calls this agent across origins

Because the agent can touch the crontab and run commands, what a caller may do
depends on where the request comes from, enforced here rather than in the page:

  caller                        read cron + launchd   run / enable cron jobs   launchd actions, edit crontab, wrap
  local page (127.0.0.1)               yes                    yes                         yes
  hosted page (REMOTE_ORIGINS)         yes                    yes                         no
  any other website                    no                     no                          no

So even a tampered hosted page can only run or switch cron jobs already in the crontab:
it can't add commands, and can't touch launchd jobs (e.g. switch off security software).
"""

import json
import os
import socket
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import core
import launchd

VERSION = "0.3.0"
PORT = int(os.environ.get("MCM_PORT", "7878"))
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
LOCAL_ORIGINS = {f"http://{h}" for h in ALLOWED_HOSTS}
REMOTE_ORIGINS = {o.strip().rstrip("/") for o in
                  os.environ.get("MCM_REMOTE_ORIGINS", "https://induction-axiom.github.io").split(",") if o.strip()}
INDEX = core.HERE / "static" / "index.html"

# Which endpoints each kind of caller may use.
REMOTE_ROUTES = {("GET", "/api/state"), ("GET", "/api/log"), ("GET", "/api/runs"), ("POST", "/api/run"), ("POST", "/api/toggle"),
                 ("GET", "/api/launchd"), ("GET", "/api/launchd/detail")}
LOCAL_ROUTES = REMOTE_ROUTES | {("GET", "/"), ("GET", "/api/crontab"), ("POST", "/api/crontab"), ("POST", "/api/wrap"),
                                ("POST", "/api/launchd/run"), ("POST", "/api/launchd/toggle")}


def job_json(job: core.Job) -> dict:
    next_label, next_ts = core.next_run(job)
    return {
        "line": job.line_no, "raw": job.raw, "name": job.name,
        "schedule": job.schedule, "desc": core.describe(job.schedule), "command": job.command,
        "enabled": job.enabled, "monitored": job.monitored, "running_since": job.running_since,
        "next": next_label, "next_ts": next_ts, "last": job.last,
        "history": [{"start": r["start"], "status": r["status"], "reason": r.get("reason", "")} for r in job.runs],
        "warning": job.warning(),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "mac-cron-manager"

    def log_message(self, *args):
        pass

    @property
    def origin(self):
        return self.headers.get("Origin")

    def caller(self):
        """'local', 'remote' or None (refused)."""
        # Host check stops DNS rebinding (evil.com resolving to 127.0.0.1).
        if self.headers.get("Host") not in ALLOWED_HOSTS:
            return None
        # Browsers always send Origin on cross-origin requests; same-origin GETs
        # from the local page (and the address bar) don't send one.
        if self.origin is None or self.origin in LOCAL_ORIGINS:
            return "local"
        if self.origin in REMOTE_ORIGINS:
            return "remote"
        return None

    def routes(self, caller):
        return LOCAL_ROUTES if caller == "local" else REMOTE_ROUTES if caller == "remote" else set()

    def reply(self, code: int, body=b"", ctype="application/json", extra=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        if data:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Vary", "Origin")
        if self.caller() == "remote":
            self.send_header("Access-Control-Allow-Origin", self.origin)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def check(self, method):
        """Return the caller kind if this request may proceed, else send 403 and return None."""
        path = urlparse(self.path).path
        caller = self.caller()
        if (method, path) not in self.routes(caller):
            msg = "not allowed from the hosted page; use http://127.0.0.1:%d" % PORT if caller == "remote" else "forbidden"
            self.reply(403, {"error": msg})
            return None
        # A custom header can't be sent cross-origin without a preflight we approve,
        # so other websites can't fire POSTs at us blind.
        if method == "POST" and self.headers.get("X-MCM") != "1":
            self.reply(403, {"error": "forbidden"})
            return None
        return caller

    def do_OPTIONS(self):
        # CORS preflight from the hosted page.
        path = urlparse(self.path).path
        method = self.headers.get("Access-Control-Request-Method", "")
        if self.caller() != "remote" or (method, path) not in REMOTE_ROUTES:
            return self.reply(403)
        extra = {
            "Access-Control-Allow-Methods": "GET, POST",
            "Access-Control-Allow-Headers": "Content-Type, X-MCM",
            "Access-Control-Max-Age": "600",
        }
        # Chrome's Private/Local Network Access: a public site asking for localhost.
        if self.headers.get("Access-Control-Request-Private-Network") == "true":
            extra["Access-Control-Allow-Private-Network"] = "true"
        self.reply(204, extra=extra)

    def do_GET(self):
        caller = self.check("GET")
        if not caller:
            return
        url = urlparse(self.path)
        if url.path == "/":
            return self.reply(200, INDEX.read_bytes(), "text/html; charset=utf-8")
        if url.path == "/api/state":
            allowed = sorted(p.removeprefix("/api/") for m, p in self.routes(caller) if m == "POST")
            return self.reply(200, {
                "version": VERSION,
                "caller": caller,
                "allowed": allowed,
                "host": socket.gethostname().split(".")[0],
                "cron_up": core.cron_up(),
                "now": time.time(),
                "local_url": f"http://127.0.0.1:{PORT}",
                "jobs": [job_json(j) for j in core.load_jobs()],
            })
        if url.path == "/api/log":
            name = parse_qs(url.query).get("name", [""])[0]
            job = next((j for j in core.load_jobs() if j.name == name), None)
            if not job:
                return self.reply(404, {"error": "no such job"})
            run = parse_qs(url.query).get("run", [""])[0]
            f = job.run_log(int(run)) if run.isdigit() else job.log_file
            text = "\n".join(f.read_text(errors="replace").splitlines()[-500:]) if f.exists() else ""
            return self.reply(200, {"file": str(f), "text": text, "exists": f.exists()})
        if url.path == "/api/runs":
            name = parse_qs(url.query).get("name", [""])[0]
            job = next((j for j in core.load_jobs() if j.name == name), None)
            if not job:
                return self.reply(404, {"error": "no such job"})
            runs = [{**r, "log": job.run_log(r["start"]).exists()} for r in job.all_runs()]
            return self.reply(200, {"runs": runs})
        if url.path == "/api/crontab":
            return self.reply(200, {"text": "\n".join(core.read_crontab())})
        if url.path == "/api/launchd":
            return self.reply(200, {"jobs": launchd.scan()})
        if url.path == "/api/launchd/detail":
            job = launchd.find(parse_qs(url.query).get("id", [""])[0])
            if not job:
                return self.reply(404, {"error": "no such job"})
            return self.reply(200, launchd.details(job))

    def do_POST(self):
        if not self.check("POST"):
            return
        path = urlparse(self.path).path
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
    print(f"hosted pages allowed: {', '.join(sorted(REMOTE_ORIGINS)) or 'none'}", flush=True)
    server.serve_forever()
