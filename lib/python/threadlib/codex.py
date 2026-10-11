"""A small client for Codex's daemon, the local app-server that hosts threads.

Threads live on Codex's daemon rather than in a ``codex exec`` child because
only daemon-hosted threads show a live status in the agent command center,
can be opened while running, and can be reached by ``codex queue``
(README.md, "Why the thread lives on Codex's daemon"). The daemon listens on
``$CODEX_HOME/app-server-control/app-server-control.sock`` and speaks
JSON-RPC 2.0 over WebSocket; ``codex app-server daemon start`` starts it when
it is not running and is a no-op when it is.

The protocol surface used here is the v2 app-server API: ``initialize``,
``thread/start``, ``thread/name/set``, ``turn/start``, and the ``turn/*``,
``item/*`` and ``error`` notifications a turn emits. Verified against
codex-cli 0.162.1.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from websockets.sync.client import ClientConnection, unix_connect

CLIENT_INFO = {"name": "threadlib", "title": "ws-template threadlib", "version": "0.1.0"}

# Codex's daemon prints JSON on stdout for `daemon start`; the socket path is
# in it, but it is also a fixed location under CODEX_HOME, read directly so
# `connect` works without having been the one to start the daemon.
SOCKET_RELATIVE = Path("app-server-control") / "app-server-control.sock"


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def socket_path() -> Path:
    return codex_home() / SOCKET_RELATIVE


def ensure_daemon(log) -> None:
    """Start Codex's daemon if it is not running; idempotent."""
    subprocess.run(
        ["codex", "app-server", "daemon", "start"],
        check=True,
        stdout=log,
        stderr=log,
    )


@dataclass
class TurnResult:
    status: str
    error: str | None
    last_agent_message: str | None


@dataclass
class Client:
    """One connection to Codex's daemon, initialized and ready for requests."""

    connection: ClientConnection
    next_id: int = 1
    # Notifications that arrive while waiting for a response, kept in order
    # so `notifications()` sees everything the daemon sent.
    pending: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def connect(cls) -> "Client":
        path = socket_path()
        if not path.exists():
            raise FileNotFoundError(
                f"Codex's daemon is not running: no socket at {path}"
            )
        # The socket is a symlink into /tmp; websockets wants the real path.
        connection = unix_connect(str(path.resolve()), uri="ws://codex/", legacy=True)
        client = cls(connection)
        client.request("initialize", {"clientInfo": CLIENT_INFO})
        client.notify("initialized", {})
        return client

    def close(self) -> None:
        self.connection.close()

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self.connection.send(json.dumps({"jsonrpc": "2.0", "method": method, "params": params}))

    def request(self, method: str, params: dict[str, Any], timeout: float = 120) -> Any:
        """Send one request and return its ``result``; a JSON-RPC error raises."""
        request_id = self.next_id
        self.next_id += 1
        self.connection.send(
            json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        )
        while True:
            message = json.loads(self.connection.recv(timeout=timeout))
            is_our_response = message.get("id") == request_id
            if not is_our_response:
                self.pending.append(message)
                continue
            if "error" in message:
                raise RuntimeError(f"{method}: {json.dumps(message['error'])}")
            return message.get("result")

    def notifications(self, timeout: float | None = None) -> Iterator[dict[str, Any]]:
        """Yield server notifications as they arrive, queued ones first."""
        while self.pending:
            yield self.pending.pop(0)
        while True:
            yield json.loads(self.connection.recv(timeout=timeout))

    # The thread-level calls the launcher makes.

    def start_thread(self, cwd: Path, settings: dict[str, Any]) -> dict[str, Any]:
        """Create a thread with ``cwd``; returns the daemon's thread document.

        ``settings`` are extra ``thread/start`` params, the door's approval
        policy and sandbox; empty means the user's own Codex defaults.
        """
        result = self.request("thread/start", {"cwd": str(cwd), **settings})
        return result["thread"]

    def set_thread_name(self, thread_id: str, name: str) -> None:
        self.request("thread/name/set", {"threadId": thread_id, "name": name})

    def start_turn(
        self, thread_id: str, prompt: str, output_schema: dict[str, Any] | None = None
    ) -> str:
        """Start a turn with ``prompt`` as its user input; returns the turn id."""
        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt}],
        }
        if output_schema is not None:
            params["outputSchema"] = output_schema
        return self.request("turn/start", params)["turn"]["id"]

    def wait_for_turn(self, thread_id: str, turn_id: str, log) -> TurnResult:
        """Follow a turn's notifications into ``log`` until it completes.

        The log gets what a reader of a Run log needs: each completed item in
        one line (commands with their exit code, messages in full), every
        error, and the completion. Deltas are skipped; the completed item
        carries the same text.
        """
        last_agent_message = None
        for notification in self.notifications():
            method = notification.get("method")
            params = notification.get("params", {})
            is_about_this_thread = params.get("threadId") == thread_id
            if method == "error":
                print(f"codex error: {params['error'].get('message')}", file=log)
            elif method == "item/completed" and is_about_this_thread:
                item = params["item"]
                if item.get("type") == "agentMessage":
                    last_agent_message = item.get("text")
                print(describe_item(item), file=log)
            elif method == "turn/completed" and is_about_this_thread:
                turn = params["turn"]
                is_our_turn = turn["id"] == turn_id
                if is_our_turn:
                    error = turn.get("error")
                    print(f"turn {turn['status']}", file=log)
                    return TurnResult(
                        turn["status"],
                        error.get("message") if error else None,
                        last_agent_message,
                    )


def describe_item(item: dict[str, Any]) -> str:
    """One log line per completed item, enough to follow the transcript."""
    kind = item.get("type")
    if kind in ("userMessage", "agentMessage"):
        text = item.get("text")
        if text is None:
            text = "".join(part.get("text", "") for part in item.get("content", []))
        return f"[{kind}] {text}"
    if kind == "commandExecution":
        return f"[command] {item.get('command')} -> exit {item.get('exitCode')}"
    if kind == "fileChange":
        paths = [change.get("path") for change in item.get("changes", [])]
        return f"[fileChange] {', '.join(str(p) for p in paths)}"
    if kind == "reasoning":
        return "[reasoning]"
    return f"[{kind}] {json.dumps(item)[:300]}"
