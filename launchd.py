"""Read-mostly view of the non-Apple launchd jobs on this Mac.

Covers the three places third parties and the user put jobs (Apple's own under
/System are left out):
  ~/Library/LaunchAgents   agents for this user (yours, or installed by apps)
  /Library/LaunchAgents    agents installed by apps for every user
  /Library/LaunchDaemons   system daemons, running as root: view only

Agents run in this user's gui domain, so they can be started, and disabled or
enabled for this user, without admin rights. Daemons need root for any of that;
as a normal user launchd won't even report their state, so whether one is
running is inferred from the process list.
"""

import os
import plistlib
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from croniter import croniter

UID = os.getuid()
GUI = f"gui/{UID}"
DIRS = [  # folder, kind, scope (also the id prefix)
    (Path.home() / "Library/LaunchAgents", "agent", "user"),
    (Path("/Library/LaunchAgents"), "agent", "global"),
    (Path("/Library/LaunchDaemons"), "daemon", "system"),
]
SELF_LABEL = os.environ.get("MCM_LABEL", "local.mac-cron-manager")

_signer_cache: dict = {}


def _run(*cmd) -> str:
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def _loaded_agents() -> dict:
    """label -> (pid or None, last exit status) for jobs loaded in this user's domain."""
    out = {}
    for line in _run("launchctl", "list").splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) == 3:
            pid, status, label = parts
            out[label] = (int(pid) if pid.isdigit() else None, int(status) if status.lstrip("-").isdigit() else None)
    return out


def _overrides(domain: str) -> dict:
    """label -> True if disabled via `launchctl disable`."""
    return {m.group(1): m.group(2) == "disabled"
            for m in re.finditer(r'"([^"]+)" => (disabled|enabled)', _run("launchctl", "print-disabled", domain))}


def _processes() -> dict:
    """executable path -> pid, for spotting running daemons."""
    procs = {}
    for line in _run("ps", "-axww", "-o", "pid=,comm=").splitlines():
        pid, _, comm = line.strip().partition(" ")
        procs.setdefault(comm.strip(), int(pid))
    return procs


def signer(program: str | None) -> str:
    """Who signed the program: a developer name, 'Apple', 'unsigned' or 'missing'."""
    if not program or not os.path.exists(program):
        return "missing"
    key = (program, os.stat(program).st_mtime_ns)
    if key not in _signer_cache:
        info = subprocess.run(["codesign", "-dv", "--verbose=2", program], capture_output=True, text=True).stderr
        m = re.search(r"Authority=(.+)", info)
        if not m:
            name = "unsigned"
        elif "Developer ID" not in m.group(1) and ("Apple" in m.group(1) or "Software Signing" in m.group(1)):
            name = "Apple"
        else:
            name = re.sub(r"^Developer ID Application: |\s*\([A-Z0-9]+\)$", "", m.group(1)).strip()
        _signer_cache[key] = name
    return _signer_cache[key]


def _calendar_to_cron(entries) -> list[str]:
    entries = entries if isinstance(entries, list) else [entries]
    keys = ("Minute", "Hour", "Day", "Month", "Weekday")
    return [" ".join(str(e.get(k, "*")) for k in keys) for e in entries if isinstance(e, dict)]


def _every(seconds: int) -> str:
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds % n == 0 and seconds >= n:
            return f"every {seconds // n}{unit}"
    return f"every {seconds}s"


def triggers(plist: dict, kind: str) -> tuple[str, str | None, float | None]:
    """(summary, label for next run, next run timestamp)."""
    parts, nxt = [], None
    crons = _calendar_to_cron(plist["StartCalendarInterval"]) if "StartCalendarInterval" in plist else []
    if crons:
        parts.append("calendar " + ", ".join(crons))
        now = datetime.now()
        times = [croniter(c, now).get_next(datetime) for c in crons if croniter.is_valid(c)]
        if times:
            nxt = min(times)
    if "StartInterval" in plist:
        parts.append(_every(int(plist["StartInterval"])))
    if plist.get("RunAtLoad"):
        parts.append("at login" if kind == "agent" else "at boot")
    if plist.get("KeepAlive"):
        parts.append("kept alive")
    if plist.get("WatchPaths") or plist.get("QueueDirectories"):
        parts.append("on file change")
    if plist.get("MachServices") or plist.get("Sockets"):
        parts.append("on demand")
    summary = " · ".join(parts) or "manual"
    if nxt is None:
        return summary, None, None
    delta = (nxt - datetime.now()).total_seconds()
    when = f"{int(delta // 60)}m" if delta < 3600 else f"{delta / 3600:.1f}h"
    return summary, f"in {when} · {nxt:%a %H:%M}", nxt.timestamp()


def scan() -> list[dict]:
    loaded = _loaded_agents()
    disabled = {"agent": _overrides(GUI), "daemon": _overrides("system")}
    procs = None
    jobs = []
    for folder, kind, scope in DIRS:
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.plist")):
            job = {"id": f"{scope}:{path.stem}", "kind": kind, "scope": scope, "plist": str(path),
                   "label": path.stem, "readable": True}
            try:
                with open(path, "rb") as f:
                    data = plistlib.loads(f.read().lstrip())  # some vendors ship a leading newline; launchd accepts it
            except PermissionError:
                job.update(readable=False, program=None, developer="?", trigger="(plist not readable as your user)",
                           next=None, next_ts=None, pid=None, last_exit=None, loaded=None,
                           disabled=disabled[kind].get(path.stem, False), warning="", note="", actions=[], logs=[])
                jobs.append(job)
                continue
            except Exception as e:
                data = {"Label": path.stem}
                job["warning"] = f"can't parse plist: {e}"
            label = data.get("Label", path.stem)
            args = data.get("ProgramArguments") or []
            program = data.get("Program") or (args[0] if args else None)
            trigger, next_label, next_ts = triggers(data, kind)

            if kind == "agent":
                pid, last_exit = loaded.get(label, (None, None))
                is_loaded = label in loaded
            else:
                procs = procs if procs is not None else _processes()
                pid, last_exit, is_loaded = procs.get(program), None, None
            is_disabled = disabled[kind].get(label, bool(data.get("Disabled", False)))

            # `open [-g] /Applications/X.app` launches an app: credit the app, not /usr/bin/open.
            target = program
            if program == "/usr/bin/open":
                target = next((a for a in args[1:] if a.endswith(".app")), program)
            developer = signer(target) if program else "?"
            warning, note = job.get("warning", ""), ""
            if not data or data.keys() <= {"Label"}:
                note = "empty plist: this job does nothing (its app has switched it off)"
            elif developer == "missing" and program:
                warning = f"program not found ({target}): probably left over from an uninstalled app"
            elif last_exit not in (None, 0) and not pid:
                warning = f"last run exited with {last_exit}" + (" and it is set to restart: may be crash-looping"
                                                                   if data.get("KeepAlive") else "")

            actions = []
            if kind == "agent" and label != SELF_LABEL:
                actions = ["toggle"] + (["run"] if is_loaded and not is_disabled else [])

            logs = sorted({p for p in (data.get("StandardOutPath"), data.get("StandardErrorPath")) if p})
            jobs.append({
                **job, "label": label, "program": program,
                "command": " ".join(args) if args else (program or ""),
                "developer": "you" if developer == "unsigned" and scope == "user" else developer,
                "trigger": trigger, "next": next_label, "next_ts": next_ts,
                "pid": pid, "last_exit": last_exit, "loaded": is_loaded, "disabled": is_disabled,
                "warning": warning, "note": note, "actions": actions, "logs": logs,
            })
    return jobs


def find(job_id: str) -> dict | None:
    return next((j for j in scan() if j["id"] == job_id), None)


def details(job: dict) -> dict:
    """Run count and log tail for one job (too slow to gather for every job on each poll)."""
    runs = None
    if job["kind"] == "agent" and job["loaded"]:
        m = re.search(r"^\s+runs = (\d+)", _run("launchctl", "print", f"{GUI}/{job['label']}"), re.M)
        runs = int(m.group(1)) if m else None
    text = ""
    for log in job["logs"]:
        try:
            lines = Path(log).read_text(errors="replace").splitlines()[-300:]
            text += (f"==> {log} <==\n" if len(job["logs"]) > 1 else "") + "\n".join(lines) + "\n"
        except OSError as e:
            text += f"(can't read {log}: {e.strerror})\n"
    return {"runs": runs, "log": text, "log_files": job["logs"]}


def run_now(job: dict) -> None:
    subprocess.run(["launchctl", "kickstart", f"{GUI}/{job['label']}"], check=True, capture_output=True)


def toggle(job: dict) -> None:
    """Disable (and stop) or enable (and load) an agent for this user. Survives reboots."""
    target = f"{GUI}/{job['label']}"
    if job["disabled"]:
        subprocess.run(["launchctl", "enable", target], check=True, capture_output=True)
        subprocess.run(["launchctl", "bootstrap", GUI, job["plist"]], capture_output=True)  # may already be loaded
    else:
        subprocess.run(["launchctl", "disable", target], check=True, capture_output=True)
        subprocess.run(["launchctl", "bootout", target], capture_output=True)  # may not be loaded
    time.sleep(0.3)
