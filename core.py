"""Crontab + cronmon state for the mac-cron-manager agent (web.py).

Disabled jobs stay in the crontab as `#off# <line>` so they can be switched back
on. Every change to the crontab is preceded by a backup in <STATE>/backups/.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
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
    def last(self) -> dict | None:
        return self.runs[-1] if self.runs else None

    @property
    def log_file(self) -> Path:
        return self.dir / ("current.log" if self.running_since else "last.log")

    def run_log(self, start: int) -> Path:
        """Output of the run that started at `start` (kept for the last MCM_KEEP_LOGS runs)."""
        return self.dir / "logs" / f"{int(start)}.log"

    @property
    def history(self) -> list:
        return self.runs[-HISTORY_LEN:]

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


def unwrap(raw: str) -> tuple[str, bool]:
    """(line without the #off# marker, enabled)."""
    line = raw.strip()
    return (line[len(OFF):].strip(), False) if line.startswith(OFF) else (line, True)


def is_job(line: str) -> bool:
    """False for blank lines, comments and VAR=value lines."""
    return bool(line) and not line.startswith("#") and not ENV_RE.match(line)


def split_line(raw: str):
    """Return (schedule, command, enabled) for a well-formed job line, or None for anything else."""
    line, enabled = unwrap(raw)
    if not is_job(line):
        return None
    n = 1 if line.startswith("@") else 5  # @reboot / @daily … or five time fields
    parts = line.split(None, n)
    return (" ".join(parts[:n]), parts[n], enabled) if len(parts) == n + 1 else None


def cronmon_name(command: str) -> str | None:
    """The job name in `.../cronmon [--when CHECK] NAME COMMAND...`, or None if cronmon isn't used."""
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    for i, word in enumerate(words):
        if Path(word).name == "cronmon":
            rest = words[i + 1:]
            while rest and rest[0].startswith("--"):
                rest = rest[2:] if rest[0] == "--when" else rest[1:]
            return rest[0] if rest else None
    return None


def parse_jobs(lines: list[str]) -> list[Job]:
    jobs = []
    for i, raw in enumerate(lines):
        parsed = split_line(raw)
        if not parsed:
            continue
        schedule, command, enabled = parsed
        name = cronmon_name(command)
        jobs.append(Job(i, raw, schedule, command, enabled, name or Path(command.split()[0]).name, bool(name)))
    return jobs


def validate(lines: list[str]) -> list[str]:
    """Return a list of problems (empty if the crontab looks fine)."""
    errors = []
    for i, raw in enumerate(lines, 1):
        if not is_job(unwrap(raw)[0]):
            continue
        parsed = split_line(raw)
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
            job.runs = [json.loads(l) for l in runs_file.read_text().splitlines() if l.strip()]
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
    line, enabled = unwrap(lines[job.line_no])
    lines[job.line_no] = OFF + line if enabled else line
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

def until(when: datetime) -> str:
    """'in 25m · Sun 23:30'"""
    delta = (when - datetime.now()).total_seconds()
    left = f"{int(delta // 60)}m" if delta < 3600 else f"{delta / 3600:.1f}h" if delta < 2 * 86400 else f"{delta / 86400:.1f}d"
    return f"in {left} · {when:%a %H:%M}"


def next_run(job: Job) -> str:
    if not job.enabled:
        return "—"
    if job.schedule == "@reboot":
        return "at boot"
    try:
        return until(croniter(job.schedule, datetime.now()).get_next(datetime))
    except (ValueError, KeyError):
        return "bad schedule"


def describe(schedule: str) -> str:
    try:
        return get_description(schedule)
    except Exception:
        return schedule
