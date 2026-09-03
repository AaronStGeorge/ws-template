#!/usr/bin/env bash
# The bump loop's ticker: the cron substitute for a devcontainer that has
# no crond. Runs `impwatch tick` every five minutes from the workspace
# root, forever. It needs the Daemon up to POST launches to — start both
# detached from the workspace root, daemon first (impd.json is the
# gitignored imp map; copy impd.json.example):
#
#     setsid impd --config impd.json > .imp/impd.out 2>&1 < /dev/null &
#     setsid scripts/ggml-staging-automation/tick-loop.sh > .imp/tick-loop.log 2>&1 < /dev/null &
#
# Daemon first matters: impwatch drops a clearing row when it fires, not
# when the launch is delivered (a flaky daemon must not make a one-shot
# watch fire twice), so a reconcile row firing into a down daemon is a
# lost launch that needs a human re-arm. The standing sensor's row is
# non-clearing and simply re-fires next tick.
# Everything the ticker prints goes to its log; nothing here decides
# anything.
# Health checks belong to the Condition Scripts, whose exit codes land in
# `.imp/watch.log` — the standing sensor checks the codex login itself.

set -u

# Everything runs from the workspace root — cwd is the imp tools' whole
# discovery mechanism — and impwatch comes from the workspace build.
ws="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ws" || exit 1
export PATH="$ws/build/bin:$PATH"

TICK_INTERVAL_SECONDS=300

while true; do
    echo "== tick $(date -Is)"
    impwatch tick
    echo "== tick exit $?"
    sleep "$TICK_INTERVAL_SECONDS"
done
