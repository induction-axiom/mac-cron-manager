"""Shared crontab + cronmon state logic for the mac-cron-manager TUI and web dashboard.

Disabled jobs stay in the crontab as `#off# <line>` so they can be switched back
on. Every change to the crontab is preceded by a backup in <STATE>/backups/.
"""

import json
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from cron_descriptor import get_description
from croniter import croniter

HERE = Path(__file__).resolve().parent
CRONMON = HERE / "cronmon"
STATE = Path(os.environ.get("MCM_STATE", Path.home() / ".local/state/mac-cron-manager"))
OFF = "#off# "
HISTORY_LEN = 24
ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*=")
CRONMON_RE = re.compile(r"cronmon\s+(?:--\S*\s+)*([^-\s]\S*)")  # skips options like --unmetered
TCC_HINT = ("macOS blocked file access: add /usr/sbin/cron to System Settings → "
            "Privacy & Security → Full Disk Access.")


@dataclass
class Job:
    line_no: int
    raw: str
    schedule: str
    command: str
    enabled: bool
    name: str
    monitored: bool
    runs: list = field(default_factory=list)
    running_since: float | None = None

    @property
    def dir(self) -> Path:
        return STATE / "jobs" / self.name

    @property
    def last(self):
        return self.runs[-1] if self.runs else None

    @property
    def log_file(self) -> Path:
        return self.dir / ("current.log" if self.running_since else "last.log")

    def warning(self) -> str:
        f = self.log_file
        if self.monitored and f.exists() and "Operation not permitted" in f.read_text(errors="replace"):
            return TCC_HINT
        return ""


# ---------------------------------------------------------------- crontab

def read_crontab() -> list[str]:
    proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return proc.stdout.splitlines() if proc.returncode == 0 else []


def write_crontab(lines: list[str]) -> None:
    backups = STATE / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    old = "\n".join(read_crontab())
    (backups / f"crontab-{datetime.now():%Y%m%d-%H%M%S}").write_text(old + "\n")
    subprocess.run(["crontab", "-"], input="\n".join(lines).rstrip("\n") + "\n", text=True, check=True)


def split_line(raw: str):
    """Return (schedule, command, enabled) for a job line, or None for anything else."""
    line, enabled = raw.strip(), True
    if line.startswith(OFF):
        line, enabled = line[len(OFF):].strip(), False
    if not line or line.startswith("#") or ENV_RE.match(line):
        return None
    if line.startswith("@"):
        parts = line.split(None, 1)
        return (parts[0], parts[1], enabled) if len(parts) == 2 else None
    parts = line.split(None, 5)
    return (" ".join(parts[:5]), parts[5], enabled) if len(parts) == 6 else None


def parse_jobs(lines: list[str]) -> list[Job]:
    jobs = []
    for i, raw in enumerate(lines):
        parsed = split_line(raw)
        if not parsed:
            continue
        schedule, command, enabled = parsed
        m = CRONMON_RE.search(command)
        name = m.group(1) if m else Path(command.split()[0]).name
        jobs.append(Job(i, raw, schedule, command, enabled, name, bool(m)))
    return jobs


def validate(lines: list[str]) -> list[str]:
    """Return a list of problems (empty if the crontab looks fine)."""
    errors = []
    for i, raw in enumerate(lines, 1):
        line = raw.strip()
        if line.startswith(OFF):
            line = line[len(OFF):].strip()
        if not line or line.startswith("#") or ENV_RE.match(line):
            continue
        parsed = split_line(line)
        if not parsed:
            errors.append(f"line {i}: expected 5 schedule fields and a command")
        elif parsed[0] != "@reboot" and not croniter.is_valid(parsed[0]):
            errors.append(f"line {i}: bad schedule '{parsed[0]}'")
    return errors


def load_jobs() -> list[Job]:
    jobs = parse_jobs(read_crontab())
    for job in jobs:
        runs_file = job.dir / "runs.jsonl"
        if runs_file.exists():
            job.runs = [json.loads(l) for l in runs_file.read_text().splitlines()[-HISTORY_LEN:] if l.strip()]
        running = job.dir / "running"
        if running.exists():
            try:
                pid, started = running.read_text().split()
                os.kill(int(pid), 0)
                job.running_since = float(started)
            except (ValueError, ProcessLookupError, PermissionError):
                pass
    return jobs


# ---------------------------------------------------------------- actions

def toggle(lines: list[str], job: Job) -> list[str]:
    lines = list(lines)
    line = lines[job.line_no].strip()
    lines[job.line_no] = line[len(OFF):] if line.startswith(OFF) else OFF + line
    return lines


def wrap(lines: list[str], job: Job) -> list[str]:
    """Route an unmonitored job through cronmon so its runs get recorded."""
    if "%" in job.command:
        raise ValueError("command contains %, wrap it by hand")
    name = re.sub(r"[^A-Za-z0-9_.-]", "-", job.name)
    lines = list(lines)
    wrapped = f"{CRONMON} {name} /bin/sh -c {shlex.quote(job.command)}"
    lines[job.line_no] = lines[job.line_no].replace(job.command, wrapped, 1)
    return lines


def run_now(job: Job) -> None:
    subprocess.Popen(["/bin/sh", "-c", job.command], start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def cron_up() -> bool:
    return subprocess.run(["pgrep", "-x", "cron"], capture_output=True).returncode == 0


# ---------------------------------------------------------------- formatting

def ago(ts: float) -> str:
    s = int(time.time() - ts)
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return f"{s // n}{unit} ago"
    return f"{s}s ago"


def dur(s: float) -> str:
    s = int(s)
    return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


def next_run(job: Job) -> tuple[str, float | None]:
    if not job.enabled:
        return "—", None
    if job.schedule == "@reboot":
        return "at boot", None
    try:
        nxt = croniter(job.schedule, datetime.now()).get_next(datetime)
    except (ValueError, KeyError):
        return "bad schedule", None
    delta = (nxt - datetime.now()).total_seconds()
    when = f"{int(delta // 60)}m" if delta < 3600 else f"{delta / 3600:.1f}h"
    return f"in {when} · {nxt:%a %H:%M}", nxt.timestamp()


def describe(schedule: str) -> str:
    try:
        return get_description(schedule)
    except Exception:
        return schedule
