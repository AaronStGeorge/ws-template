#!/usr/bin/env python3
"""The end-to-end chain check for the imp tools.

Walks the whole lifecycle against the echo loop in a temp directory
standing in for a workspace root: `impctl up --manifest` starts the Daemon
and applies the echo Manifest; a one-shot Watch armed by hand carries a
random token in a Launch; one `tick` must drive both the one-shot and
the standing Run to `succeeded` with their tokens in the Run logs (Tick ->
launch -> Imp -> log, carried all the way through); the one-shot must be
gone and the standing Watch kept; a second `tick` and a second `up` must
change nothing; Sigil replace and erase must leave Runs untouched; `down`
must leave no Daemon and no socket.

Every subprocess runs with cwd = the temp workspace root: cwd is imp's
whole discovery mechanism (one Daemon per workspace), so the check is
self-contained and never touches this repo's own .imp/. The echo loop is
copied in at the same workspace-relative path its Manifest names, which is
what lets the Manifest be applied verbatim. Binary locations arrive as
CTest arguments (--bin-dir, --repo-root), never PATH guessing.

Happy-path by decision: any failed step is a plain assert /
CalledProcessError / timeout SystemExit, all nonzero exits. The Daemon is
SIGTERMed in a finally as a backstop, so a failure mid-check never leaves
a stray impd behind; on the happy path `down` has already stopped it. The
finally encloses `up` itself, because `up` spawns the Daemon and then
applies the Manifest, and a failure in the apply step would otherwise
leave the Daemon alive in a temp dir about to be deleted. The pid comes
from the lock file, which impd writes it into right after taking the lock.
"""

import argparse
import http.client
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

ECHO_LOOP = "scripts/imps/loops/echo"
MANIFEST = f"{ECHO_LOOP}/imps.json"


class UnixHTTPConnection(http.client.HTTPConnection):
    """http.client over the Daemon's unix socket; the host is a placeholder."""

    def __init__(self, socket_path):
        super().__init__("impd")
        self._socket_path = socket_path

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self._socket_path)
        self.sock = sock


def get_run(socket_path, run_id):
    conn = UnixHTTPConnection(socket_path)
    try:
        conn.request("GET", f"/v1/runs/{run_id}")
        resp = conn.getresponse()
        assert resp.status == 200, f"GET /v1/runs/{run_id}: {resp.status}"
        return json.loads(resp.read())
    finally:
        conn.close()


def socket_accepts(socket_path):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
        try:
            probe.connect(socket_path)
        except OSError:
            return False
    return True


def wait_for_state(socket_path, run_id, wanted, timeout=10):
    """Poll to a terminal state, the way every Client observes (the Daemon
    never pushes). Seeing `failed` is an immediate assert rather than a
    timeout, so a broken chain reports fast."""
    deadline = time.monotonic() + timeout
    while True:
        run = get_run(socket_path, run_id)
        if run["state"] == wanted:
            return run
        assert run["state"] in ("starting", "running"), f"run: {run}"
        if time.monotonic() >= deadline:
            sys.exit(f"run {run_id} never reached {wanted}: {run}")
        time.sleep(0.1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bin-dir", required=True)
    parser.add_argument("--repo-root", required=True)
    opts = parser.parse_args()
    impctl = os.path.join(opts.bin_dir, "impctl")

    with tempfile.TemporaryDirectory() as root:
        socket_path = os.path.join(root, ".imp", "daemon.sock")
        lock_path = os.path.join(root, ".imp", "daemon.lock")

        def ctl(*args):
            return subprocess.run(
                [impctl, *args], cwd=root, check=True,
                stdout=subprocess.PIPE, text=True,
            ).stdout

        def ctl_json(*args):
            return [json.loads(line) for line in ctl(*args).splitlines() if line.strip()]

        def read_log(run_id):
            with open(os.path.join(root, ".imp", "runs", f"{run_id}.log")) as f:
                return f.read()

        def read_pid():
            with open(lock_path) as f:
                return int(f.read().strip())

        def kill_daemon_if_alive():
            """The backstop: SIGTERM whatever pid the lock file names, if it
            is still alive. On the happy path `down` has already stopped it
            and there is nothing to do; if `up` failed before writing the
            lock file, there is nothing to kill either."""
            try:
                pid = read_pid()
            except (OSError, ValueError):
                return
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

        # 1. The temp dir is the workspace root; the echo loop sits at the
        # workspace-relative path its Manifest names.
        shutil.copytree(os.path.join(opts.repo_root, ECHO_LOOP), os.path.join(root, ECHO_LOOP))

        try:
            # 2. up: spawns the Daemon, waits for ready, applies the Manifest.
            up_out = ctl("up", "--manifest", MANIFEST)
            assert "impd ready in" in up_out, up_out
            pid = read_pid()
            sigils = ctl_json("sigils")
            assert [s["name"] for s in sigils] == ["echo"], sigils
            watches = ctl_json("watches")
            assert len(watches) == 1, watches
            assert watches[0]["once"] is False, watches
            standing_id = watches[0]["id"]

            # 3. A random token is the tracer: it rides the Launch's
            # args through the Tick and the Daemon into the Imp's argv, and
            # must surface in the Run's log. /bin/echo as the Sensor
            # emits its argument (the Launch) verbatim, the
            # smallest possible always-fires condition. Watching the same
            # argv twice must return the same Watch, not a second one.
            token = secrets.token_hex(8)
            run_id = f"e2e-{token[:8]}"
            body = json.dumps({"sigil": "echo", "id": run_id, "args": [token]})
            first = ctl_json("watch", "--once", "--", "/bin/echo", body)[0]
            second = ctl_json("watch", "--once", "--", "/bin/echo", body)[0]
            assert first["id"] == second["id"], (first, second)
            assert first["once"] is True, first
            assert len(ctl_json("watches")) == 2, ctl("watches")

            # 4. One Tick launches both: the one-shot's token Run and the
            # standing echo Run.
            ctl("tick")
            token_run = wait_for_state(socket_path, run_id, "succeeded")
            standing_run = wait_for_state(socket_path, "echo-standing", "succeeded")
            assert token in read_log(run_id), read_log(run_id)
            assert "standing-token" in read_log("echo-standing"), read_log("echo-standing")
            for run in (token_run, standing_run):
                assert run["sigil"] == "echo", run
                assert run["path"].endswith("echo/imp.py"), run

            # 5. Fired-means-dropped for the one-shot; the standing Watch
            # is never dropped by the Daemon.
            watches = ctl_json("watches")
            assert [w["id"] for w in watches] == [standing_id], watches

            # 6. A second Tick re-emits the standing launch, which is a
            # rejected duplicate: still exactly two Runs.
            ctl("tick")
            assert len(ctl_json("runs")) == 2, ctl("runs")

            # 7. A second up is free: same Daemon, nothing added.
            up_again = ctl("up", "--manifest", MANIFEST)
            assert "impd already running" in up_again, up_again
            assert "unchanged echo" in up_again, up_again
            assert "(existing)" in up_again, up_again
            assert read_pid() == pid
            assert len(ctl_json("sigils")) == 1
            assert len(ctl_json("watches")) == 1
            assert len(ctl_json("runs")) == 2

            # 8. Sigil changes never touch a Run: replace, replace back,
            # erase, and the two Runs still carry their original path.
            echo_path = sigils[0]["path"]
            moved = ctl_json("inscribe", "echo", "/bin/true")[0]
            assert moved["outcome"] == "replaced" and moved["previous"] == echo_path, moved
            restored = ctl_json("inscribe", "echo", echo_path)[0]
            assert restored["outcome"] == "replaced" and restored["previous"] == "/bin/true", restored
            ctl("erase", "echo")
            assert ctl_json("sigils") == [], ctl("sigils")
            runs = ctl_json("runs")
            assert len(runs) == 2, runs
            assert all(r["path"] == echo_path for r in runs), runs
            recreated = ctl_json("inscribe", "echo", echo_path)[0]
            assert recreated["outcome"] == "created", recreated

            # 9. down: the Daemon and its socket are gone; the lock file
            # stays behind with a pid that means nothing now the lock is free.
            down_out = ctl("down")
            assert f"impd stopped (pid {pid})" in down_out, down_out
            assert not socket_accepts(socket_path), "socket still accepts after down"
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                pass
            else:
                sys.exit(f"impd pid {pid} still alive after down")
        finally:
            kill_daemon_if_alive()


if __name__ == "__main__":
    main()
