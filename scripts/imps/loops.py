#!/usr/bin/env python3
"""Bring a workspace's imp loops up and down: `loops.py up|down|status|tick`.

A *loop* is a set of imps plus the standing Watch Rows that discover work
for them, declared in `imps.json` beside this file (the one checked-in
config; `tools/README.md` and `docs/imp-design.md` are the runtime's
design record). Its `loops` section names each loop's imp map and the
standing rows to arm; its `workspaces` section says which loops a
workspace runs. `up --workspace NAME` starts everything those loops need;
`down` stops it; `status` shows what is running and what the loops have
been doing; `tick` is the ticker itself (the cron substitute — the
devcontainer has no crond).

`up` does three things, each skipped when already done, so rerunning it is
free: start the Daemon with the union of the selected loops' imp maps
(refusing when two loops map one name to different paths), reconcile the
standing rows in `.imp/watches.jsonl` with the config, then start the
ticker. Daemon before ticker matters: impwatch drops a clearing row when
it fires, not when the launch is delivered, so a reconcile row firing
into a down Daemon is a lost launch that needs a human re-arm. Ready
means the socket accepts a connection — the file alone is not enough,
since impd leaves its socket behind on exit and only unlinks it on the
next start.

Reconciling rows: a configured row whose condition script is already
armed with the same arguments is left alone; with different arguments
(the config was edited) the stale row is replaced, since two standing
rows for one script would fire it twice. Rows are matched by argv[0]
made absolute. Clearing rows and scripts not in the config are never
touched. The rows file is edited under the same blocking flock impwatch
takes, so an edit cannot race a Tick's end-of-pass rewrite.

The Daemon's config is never written to disk: `impd` reads `--config` once
at startup, so the merged JSON goes down a pipe as `/dev/fd/N`. `impd`
itself stays ignorant of loops.

`down` stops the ticker, waits for it to finish any in-flight Tick, then
stops the Daemon; the order and the wait exist for the same lost-launch
reason. It never touches Watch Rows — a pending reconcile row armed by a
fix Run must survive a restart. Both processes are tracked by pidfiles
in `.imp/`; liveness is the pid plus a cmdline check, so a recycled pid
is not mistaken for our process.

Everything runs from the workspace root (cwd is the imp tools' whole
discovery mechanism) with `build/bin` on PATH: imps arm rows by invoking
`impwatch` bare, and the Daemon's children inherit this environment.
"""

import argparse
import fcntl
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = Path(__file__).resolve().parent / "imps.json"
IMP_DIR = ROOT / ".imp"
DAEMON_PIDFILE = IMP_DIR / "impd.pid"
TICKER_PIDFILE = IMP_DIR / "tick.pid"
WATCH_ROWS = IMP_DIR / "watches.jsonl"

TICK_INTERVAL_SECONDS = 300
SOCKET_WAIT_SECONDS = 10
TICKER_EXIT_WAIT_SECONDS = 120


def load_config():
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def selected_loops(config, workspace):
    """The loop definitions the workspace runs; a missing name is a
    config gap the human fixes, not a silent empty set."""
    workspace_is_known = workspace in config["workspaces"]
    if not workspace_is_known:
        raise SystemExit(
            f"workspace {workspace!r} not in {CONFIG}; known: "
            f"{', '.join(sorted(config['workspaces']))}"
        )
    return {name: config["loops"][name] for name in config["workspaces"][workspace]}


def merged_imps(loops):
    """Union of the loops' imp maps, refusing a name that two loops map to
    different paths — the Daemon has one namespace, so the conflict would
    otherwise be settled by whichever loop was listed last."""
    imps = {}
    for loop_name, loop in loops.items():
        for imp_name, path in loop["imps"].items():
            already_mapped = imp_name in imps
            conflicts = already_mapped and imps[imp_name] != path
            if conflicts:
                raise SystemExit(
                    f"imp {imp_name!r} maps to both {imps[imp_name]} and "
                    f"{path} (loop {loop_name!r})"
                )
            imps[imp_name] = path
    return imps


def pid_alive(pidfile, cmdline_marker):
    """The pid in the file, if it is alive and still ours (the marker is
    in its cmdline); None otherwise."""
    if not pidfile.exists():
        return None
    try:
        pid = int(pidfile.read_text().strip())
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
    except (ValueError, OSError):
        return None
    is_ours = cmdline_marker in cmdline
    return pid if is_ours else None


def spawn_detached(argv, log_path, pass_fds=()):
    """Start argv in its own session with stdout+stderr appended to
    log_path; the child outlives this process."""
    with open(log_path, "ab") as log:
        return subprocess.Popen(
            argv,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            pass_fds=pass_fds,
        )


def daemon_accepts():
    """Whether something is listening on the Daemon's socket."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
        try:
            probe.connect(str(IMP_DIR / "daemon.sock"))
        except OSError:
            return False
    return True


def start_daemon(imps):
    """Feed the merged imp map to impd over a pipe — no config file on
    disk, per the design: imps.json is the only thing anyone edits."""
    alive = pid_alive(DAEMON_PIDFILE, "impd")
    if alive is not None:
        print(f"impd already running (pid {alive})")
        return
    read_fd, write_fd = os.pipe()
    proc = spawn_detached(
        ["impd", "--config", f"/dev/fd/{read_fd}"],
        IMP_DIR / "impd.out",
        pass_fds=[read_fd],
    )
    os.close(read_fd)
    failure = f"impd did not come up; see {IMP_DIR / 'impd.out'}"
    try:
        with os.fdopen(write_fd, "w") as pipe:
            json.dump({"imps": imps}, pipe)
    except BrokenPipeError:
        # It died before reading — most likely another Daemon holds the
        # single-instance lock (one started by hand, or the old one still
        # exiting after a `down`).
        raise SystemExit(failure)
    # Accepting a connection is the Daemon's "ready"; arming and ticking
    # before that would POST into nothing. The pidfile is written only
    # then, so a Daemon that died on startup never leaves a pidfile
    # naming a dead pid for `status` and `down` to trust.
    deadline = time.monotonic() + SOCKET_WAIT_SECONDS
    while not daemon_accepts():
        daemon_died = proc.poll() is not None
        timed_out = time.monotonic() > deadline
        if daemon_died or timed_out:
            raise SystemExit(failure)
        time.sleep(0.2)
    DAEMON_PIDFILE.write_text(f"{proc.pid}\n")
    print(f"impd started (pid {proc.pid})")


def armed_rows():
    if not WATCH_ROWS.exists():
        return []
    rows = []
    for line in WATCH_ROWS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def drop_rows(argv0s):
    """Remove the standing rows whose script is in argv0s, under the rows
    file's flock (impwatch's own serialization; a Tick holds it for its
    whole pass, so this waits the Tick out rather than racing its
    rewrite). There is no `impwatch disarm`; this is that."""
    with open(WATCH_ROWS, "a+", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.seek(0)
        kept = []
        for line in f.read().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            is_stale_standing = not row.get("clear") and row["argv"][0] in argv0s
            if not is_stale_standing:
                kept.append(line)
        f.seek(0)
        f.truncate()
        f.write("".join(line + "\n" for line in kept))


def reconcile_watches(loops):
    """Bring the standing rows in line with the config: arm what is
    missing, replace what is armed with other arguments. argv[0] is made
    absolute the way imps arm their own rows — nothing shares a cwd with
    anything — and rows are matched on that form."""
    present = {}
    for row in armed_rows():
        if not row.get("clear"):
            present[row["argv"][0]] = row["argv"]
    for loop_name, loop in loops.items():
        for argv in loop["watches"]:
            absolute_argv = [str(ROOT / argv[0])] + list(argv[1:])
            script = absolute_argv[0]
            armed_as_configured = present.get(script) == absolute_argv
            armed_differently = script in present and not armed_as_configured
            if armed_as_configured:
                print(f"{loop_name}: already armed: {' '.join(argv)}")
                continue
            if armed_differently:
                drop_rows({script})
                print(f"{loop_name}: replacing stale row for {argv[0]}")
            subprocess.run(["impwatch", "arm", "--", *absolute_argv], check=True)
            print(f"{loop_name}: armed: {' '.join(argv)}")


def start_ticker():
    alive = pid_alive(TICKER_PIDFILE, "loops.py")
    if alive is not None:
        print(f"ticker already running (pid {alive})")
        return
    proc = spawn_detached(
        [sys.executable, str(Path(__file__).resolve()), "tick"],
        IMP_DIR / "tick-loop.log",
    )
    TICKER_PIDFILE.write_text(f"{proc.pid}\n")
    print(f"ticker started (pid {proc.pid})")


def stop(pidfile, cmdline_marker, label, wait_seconds=0):
    """SIGTERM the process and, when asked, wait for it to be gone —
    the ticker finishes an in-flight Tick before exiting, and the Daemon
    must outlive that Tick."""
    pid = pid_alive(pidfile, cmdline_marker)
    if pid is None:
        print(f"{label} not running")
    else:
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + wait_seconds
        while wait_seconds and pid_alive(pidfile, cmdline_marker) is not None:
            if time.monotonic() > deadline:
                print(f"warning: {label} (pid {pid}) still running after "
                      f"{wait_seconds}s", file=sys.stderr)
                break
            time.sleep(0.5)
        print(f"{label} stopped (pid {pid})")
    pidfile.unlink(missing_ok=True)


def cmd_up(args):
    IMP_DIR.mkdir(mode=0o700, exist_ok=True)
    loops = selected_loops(load_config(), args.workspace)
    start_daemon(merged_imps(loops))
    reconcile_watches(loops)
    start_ticker()


def cmd_down(args):
    stop(TICKER_PIDFILE, "loops.py", "ticker", wait_seconds=TICKER_EXIT_WAIT_SECONDS)
    stop(DAEMON_PIDFILE, "impd", "impd")


def cmd_status(args):
    for pidfile, marker, label in (
        (DAEMON_PIDFILE, "impd", "impd"),
        (TICKER_PIDFILE, "loops.py", "ticker"),
    ):
        pid = pid_alive(pidfile, marker)
        state = f"running (pid {pid})" if pid is not None else "NOT running"
        print(f"{label}: {state}")

    print("\n== runs")
    daemon_is_up = pid_alive(DAEMON_PIDFILE, "impd") is not None
    if daemon_is_up:
        subprocess.run(["impctl", "runs"])
    else:
        print("(daemon down; run state is in-memory only)")

    print("\n== armed watches")
    for row in armed_rows():
        kind = "clearing" if row.get("clear") else "standing"
        print(f"[{kind}] {' '.join(row['argv'])}")

    watch_log = IMP_DIR / "watch.log"
    print(f"\n== tail of {watch_log}")
    if watch_log.exists():
        subprocess.run(["tail", "-n", "20", str(watch_log)])


def cmd_tick(args):
    """The ticker: one `impwatch tick` every five minutes, forever. Every
    line it prints goes to its log; nothing here decides anything —
    health checks belong to the Condition Scripts, whose exit codes land
    in `.imp/watch.log`. SIGTERM is honored between Ticks, never during
    one: a Tick killed mid-POST is the lost-launch case, so the signal
    only sets a flag that is checked once the Tick returns."""
    stopping = False

    def on_term(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, on_term)
    while True:
        print(f"== tick {time.strftime('%Y-%m-%dT%H:%M:%S%z')}", flush=True)
        result = subprocess.run(["impwatch", "tick"])
        print(f"== tick exit {result.returncode}", flush=True)
        if stopping:
            return
        # A SIGTERM arriving here interrupts the sleep via the handler
        # and the loop notices on the next line.
        time.sleep(TICK_INTERVAL_SECONDS)
        if stopping:
            return


def main():
    parser = argparse.ArgumentParser(description="bring a workspace's imp loops up and down")
    sub = parser.add_subparsers(dest="command", required=True)
    up = sub.add_parser("up", help="start the daemon, arm standing watches, start the ticker")
    up.add_argument("--workspace", required=True, help="workspace name in imps.json")
    up.set_defaults(fn=cmd_up)
    sub.add_parser("down", help="stop the ticker and the daemon").set_defaults(fn=cmd_down)
    sub.add_parser("status", help="processes, runs, watches, watch log").set_defaults(fn=cmd_status)
    sub.add_parser("tick", help="run the ticker in the foreground").set_defaults(fn=cmd_tick)
    args = parser.parse_args()

    os.chdir(ROOT)
    os.environ["PATH"] = f"{ROOT / 'build' / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}"
    args.fn(args)


if __name__ == "__main__":
    main()
