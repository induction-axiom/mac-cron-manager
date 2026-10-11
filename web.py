"""mac-cron-manager web: the crontab dashboard in a browser, at http://127.0.0.1:7878

install.sh registers it as a launchd agent so it starts at login and restarts if it dies.

The API can edit the crontab and run commands, so it only listens on localhost
and refuses requests other web pages could forge (see Handler.allowed).
"""

import json
import os
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import core

PORT = int(os.environ.get("MCM_PORT", "7878"))
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
ALLOWED_ORIGINS = {f"http://{h}" for h in ALLOWED_HOSTS}
INDEX = core.HERE / "static" / "index.html"


def job_json(job: core.Job) -> dict:
    next_label, next_ts = core.next_run(job)
    return {
        "line": job.line_no, "raw": job.raw, "name": job.name,
        "schedule": job.schedule, "desc": core.describe(job.schedule), "command": job.command,
        "enabled": job.enabled, "monitored": job.monitored, "running_since": job.running_since,
        "next": next_label, "next_ts": next_ts, "last": job.last,
        "history": [r["status"] for r in job.runs], "warning": job.warning(),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "mac-cron-manager"

    def log_message(self, *args):
        pass

    def allowed(self, mutating: bool) -> bool:
        # Host check stops DNS rebinding (evil.com resolving to 127.0.0.1).
        if self.headers.get("Host") not in ALLOWED_HOSTS:
            return False
        if mutating:
            # The custom header forces a CORS preflight that we never approve,
            # so pages from other origins can't POST here.
            if self.headers.get("X-MCM") != "1":
                return False
            origin = self.headers.get("Origin")
            if origin and origin not in ALLOWED_ORIGINS:
                return False
        return True

    def reply(self, code: int, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.allowed(mutating=False):
            return self.reply(403, {"error": "forbidden"})
        url = urlparse(self.path)
        if url.path == "/":
            return self.reply(200, INDEX.read_bytes(), "text/html; charset=utf-8")
        if url.path == "/api/state":
            return self.reply(200, {
                "host": socket.gethostname().split(".")[0],
                "cron_up": core.cron_up(),
                "now": time.time(),
                "jobs": [job_json(j) for j in core.load_jobs()],
            })
        if url.path == "/api/log":
            name = parse_qs(url.query).get("name", [""])[0]
            job = next((j for j in core.load_jobs() if j.name == name), None)
            if not job:
                return self.reply(404, {"error": "no such job"})
            f = job.log_file
            text = "\n".join(f.read_text(errors="replace").splitlines()[-500:]) if f.exists() else ""
            return self.reply(200, {"file": str(f), "text": text, "exists": f.exists()})
        if url.path == "/api/crontab":
            return self.reply(200, {"text": "\n".join(core.read_crontab())})
        self.reply(404, {"error": "not found"})

    def do_POST(self):
        if not self.allowed(mutating=True):
            return self.reply(403, {"error": "forbidden"})
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
            else:
                return self.reply(404, {"error": "not found"})
            return self.reply(200, {"ok": True})
        except (ValueError, KeyError) as e:
            return self.reply(400, {"error": str(e)})


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"mac-cron-manager on http://127.0.0.1:{PORT}", flush=True)
    server.serve_forever()
