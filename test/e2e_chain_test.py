#!/usr/bin/env python3
"""The end-to-end chain check for the imp tools.

Drives the whole control plane with fixtures: in a temp directory standing
in for a workspace root, start impd on a config mapping `echo` to the echo
Imp, arm a clearing Watch Row whose Condition Script is /bin/echo
carrying a Launch Body with a random string; after one Tick the Run must
reach `succeeded` with the string in its diagnostics log — impwatch ->
Daemon -> Imp, carried all the way through — and the clearing row must
be gone.

Every subprocess runs with cwd = the temp workspace root — cwd is imp's
whole discovery mechanism (one Daemon per workspace), so the check is
self-contained and never touches this repo's own .imp/. Binary locations
arrive as CTest arguments (--bin-dir, --repo-root), never PATH guessing.
Happy-path by MVP decision: any failed step is a plain assert /
CalledProcessError / timeout SystemExit, all nonzero exits.
"""

import argparse
import http.client
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bin-dir", required=True)
    parser.add_argument("--repo-root", required=True)
    opts = parser.parse_args()

    impd = os.path.join(opts.bin_dir, "impd")
    impwatch = os.path.join(opts.bin_dir, "impwatch")
    echo_imp = os.path.join(
        opts.repo_root, "test/echo_imp.py"
    )

    # The temp dir is the workspace root: config, socket, rows file, and run
    # logs all live under it, so the check leaves no trace in the repo and
    # two runs can never see each other. The config maps `echo` to the
    # script's absolute path because relative paths resolve from the
    # workspace root, which here is not the repo.
    with tempfile.TemporaryDirectory() as root:
        config_path = os.path.join(root, "impd.json")
        with open(config_path, "w") as f:
            json.dump({"imps": {"echo": echo_imp}}, f)

        daemon = subprocess.Popen(
            [impd, "--config", "impd.json"], cwd=root
        )
        try:
            # The socket appearing is the daemon's only readiness signal.
            socket_path = os.path.join(root, ".imp", "daemon.sock")
            deadline = time.monotonic() + 5
            while not os.path.exists(socket_path):
                assert time.monotonic() < deadline, "daemon socket never appeared"
                time.sleep(0.05)
            time.sleep(0.1)  # a moment for accept

            # A random token is the tracer: it rides the Launch Body's args
            # through impwatch and the Daemon into the Imp's argv, and
            # must surface in the run's log. /bin/echo as the Condition
            # Script emits its argument (the Launch Body) verbatim — the
            # smallest possible always-fires condition.
            token = secrets.token_hex(8)
            run_id = f"e2e-{token[:8]}"
            body = {"imp": "echo", "id": run_id, "args": [token]}

            subprocess.run(
                [impwatch, "arm", "--clear", "--", "/bin/echo", json.dumps(body)],
                cwd=root,
                check=True,
            )
            subprocess.run([impwatch, "tick"], cwd=root, check=True)

            # Poll to a terminal state, the way every Client observes (the
            # Daemon never pushes). Seeing `failed` is an immediate assert
            # rather than a timeout, so a broken chain reports fast.
            deadline = time.monotonic() + 10
            while True:
                run = get_run(socket_path, run_id)
                if run["state"] == "succeeded":
                    break
                assert run["state"] in ("starting", "running"), f"run: {run}"
                if time.monotonic() >= deadline:
                    sys.exit(f"run never succeeded: {run}")
                time.sleep(0.1)

            # The two closing assertions: the token completed the whole
            # journey into the diagnostics log, and the clearing row is gone
            # (fired-means-cleared, the one-shot watch semantics).
            log_path = os.path.join(root, ".imp", "runs", f"{run_id}.log")
            with open(log_path) as f:
                log = f.read()
            assert token in log, f"token missing from log: {log!r}"

            listing = subprocess.run(
                [impwatch, "list"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            assert listing.stdout == "", f"row did not clear: {listing.stdout!r}"
        finally:
            daemon.terminate()
            daemon.wait()


if __name__ == "__main__":
    main()
