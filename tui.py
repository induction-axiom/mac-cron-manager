"""mac-cron-manager TUI: a terminal dashboard for this machine's crontab (see core.py).

The web version is web.py; both read the same crontab and cronmon state.
"""

import socket
import subprocess
import time
from datetime import datetime

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Log, Static

import core
from core import Job, ago, describe, dur, next_run

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def status_cell(job: Job, tick: int) -> Text:
    if job.running_since:
        return Text(SPINNER[tick % len(SPINNER)], style="bold yellow")
    if not job.enabled:
        return Text("○", style="dim")
    if not job.monitored:
        return Text("?", style="dim")
    if not job.last:
        return Text("·", style="dim")
    return Text("●", style={"ok": "bold green", "fail": "bold red"}.get(job.last["status"], "yellow"))


def history_cell(job: Job) -> Text:
    t = Text()
    for r in job.runs:
        t.append("▮", style={"ok": "green", "fail": "red"}.get(r["status"], "yellow"))
    return t


# ---------------------------------------------------------------- app

class MacCronManager(App):
    TITLE = "mac-cron-manager"
    CSS = """
    #top { height: 1; padding: 0 1; background: $boost; }
    DataTable { height: 1fr; }
    /* keep cell colors (status dots, history bars) visible on the selected row */
    DataTable > .datatable--cursor { background: $primary 35%; text-style: bold; }
    #detail { height: auto; padding: 0 1; border-top: solid $primary; }
    Log { height: 2fr; border: round $primary-darken-2; }
    """
    BINDINGS = [
        Binding("r", "run_now", "run now"),
        Binding("space", "toggle", "enable/disable"),
        Binding("w", "wrap", "monitor (wrap in cronmon)"),
        Binding("e", "edit", "edit crontab"),
        Binding("q", "quit", "quit"),
    ]

    def __init__(self):
        super().__init__()
        self.jobs: list[Job] = []
        self.tick = 0
        self.log_sig = None

    def compose(self) -> ComposeResult:
        yield Static(id="top")
        with Vertical():
            yield DataTable(cursor_type="row", zebra_stripes=True, cursor_foreground_priority="renderable")
            yield Static(id="detail")
            yield Log(highlight=False)
        yield Footer()

    def on_mount(self) -> None:
        self.theme = "tokyo-night"
        table = self.query_one(DataTable)
        table.add_columns("", "JOB", "SCHEDULE", "NEXT", "LAST", "TOOK", "HISTORY")
        self.refresh_data()
        self.set_interval(1, self.refresh_data)

    @property
    def selected(self) -> Job | None:
        table = self.query_one(DataTable)
        if not self.jobs or table.cursor_row is None:
            return None
        return self.jobs[min(table.cursor_row, len(self.jobs) - 1)]

    def refresh_data(self) -> None:
        self.tick += 1
        self.jobs = core.load_jobs()
        cron_up = core.cron_up()
        running = sum(1 for j in self.jobs if j.running_since)
        failing = sum(1 for j in self.jobs if j.enabled and j.last and j.last["status"] == "fail")
        top = Text()
        top.append(f" {socket.gethostname().split('.')[0]} ", style="bold reverse")
        top.append("  cron ")
        top.append("● up" if cron_up else "● down", style="green" if cron_up else "red")
        top.append(f"   {len(self.jobs)} jobs   ")
        top.append(f"{running} running   ", style="yellow" if running else "dim")
        top.append(f"{failing} failing", style="bold red" if failing else "dim")
        top.append(f"   {datetime.now():%Y-%m-%d %H:%M:%S}", style="dim")
        self.query_one("#top", Static).update(top)

        table = self.query_one(DataTable)
        row = table.cursor_row or 0
        table.clear()
        for job in self.jobs:
            last, took = Text("never", style="dim"), Text("")
            if job.running_since:
                last = Text("running", style="yellow")
                took = Text(dur(time.time() - job.running_since), style="yellow")
            elif job.last:
                ok = job.last["status"] == "ok"
                last = Text(ago(job.last["end"]) + ("" if ok else f" · exit {job.last['exit']}"),
                            style="" if ok else "red")
                took = Text(dur(job.last["end"] - job.last["start"]))
            elif not job.monitored:
                last = Text("not monitored", style="dim")
            name = Text(job.name, style="bold" if job.enabled else "dim strike")
            table.add_row(status_cell(job, self.tick), name, job.schedule, next_run(job)[0],
                          last, took, history_cell(job))
        if self.jobs:
            table.move_cursor(row=min(row, len(self.jobs) - 1))
        self.update_detail()

    def update_detail(self) -> None:
        job = self.selected
        detail = self.query_one("#detail", Static)
        log = self.query_one(Log)
        if not job:
            detail.update(Text("No cron jobs. Press e to edit the crontab.", style="dim"))
            log.clear()
            self.log_sig = None
            return

        t = Text()
        t.append(f"{job.name}", style="bold")
        t.append(f"  {describe(job.schedule)}" + ("" if job.enabled else "  (disabled)"), style="dim")
        t.append(f"\n$ {job.command}", style="cyan")
        if not job.monitored:
            t.append("\nNot monitored: press w to wrap it in cronmon and get status + logs.", style="yellow")
        log_file = job.log_file
        if job.warning():
            t.append(f"\n⚠ {job.warning()}", style="bold red")
        detail.update(t)

        sig = (str(log_file), log_file.stat().st_mtime_ns if log_file.exists() else 0)
        if sig != self.log_sig:
            self.log_sig = sig
            log.clear()
            if log_file.exists():
                log.write_lines(log_file.read_text(errors="replace").splitlines()[-500:])
            elif job.monitored:
                log.write_line("(no runs yet)")

    def on_data_table_row_highlighted(self, _) -> None:
        self.update_detail()

    # ------------------------------------------------------------ actions

    def action_run_now(self) -> None:
        job = self.selected
        if not job:
            return
        if job.running_since:
            self.notify(f"{job.name} is already running", severity="warning")
            return
        core.run_now(job)
        self.notify(f"started {job.name}" + ("" if job.monitored else " (not monitored, no status)"))

    def action_toggle(self) -> None:
        job = self.selected
        if not job:
            return
        core.write_crontab(core.toggle(core.read_crontab(), job))
        self.notify(f"{job.name} {'disabled' if job.enabled else 'enabled'}")
        self.refresh_data()

    def action_wrap(self) -> None:
        job = self.selected
        if not job or job.monitored:
            return
        try:
            core.write_crontab(core.wrap(core.read_crontab(), job))
        except ValueError as e:
            self.notify(str(e), severity="error")
            return
        self.notify(f"{job.name} is now monitored")
        self.refresh_data()

    def action_edit(self) -> None:
        with self.suspend():
            subprocess.run(["crontab", "-e"])
        self.refresh_data()


if __name__ == "__main__":
    MacCronManager().run()
