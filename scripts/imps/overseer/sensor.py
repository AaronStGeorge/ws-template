#!/usr/bin/env python3
"""Sensor: emit one overseer launch per weekday slot, for one overseen
Sigil.

scripts/imps/README.md#overseeing-a-loop defines how loops opt in. This file
owns scheduling; imp.py documents the check and its limits. A standing Watch
passes the overseen Sigil, slot, and Codex settings:

    sensor.py --sigil S --at HH:MM --tz ZONE --model M --effort E

`--at`/`--tz` are the Sensor's. `--sigil`/`--model`/`--effort` pass
through to the imp as the Run's args, plus `--run-id` so the imp can name
the thread it opens after its own Run.

Each Tick on a weekday (Monday to Friday in the zone) once the slot is
past, it emits `{"sigil": "overseer", "id":
"oversee-<S>-<YYYY-MM-DD>-<HHMM>", ...}`. The `HHMM` is the slot's
configured time from `--at`, never the Tick's time: every Tick after the
slot emits the same id, so a past slot re-emitted on every later Tick
that day is a rejected duplicate (409), and that is what makes it run
once. Weekends emit nothing.

Deliberately no firing window: a slot missed while the container was
down runs late, and a Daemon restart mid-day (which forgets its Run Ids)
re-runs the slot. Both are accepted over the complexity of guarding them.

The overseen Sigil can be at most 40 characters. A Run Id is capped at
64 and the rest of this Id takes 24. There is no check for it here: the
Daemon owns the Run Id rule, and a Launch it rejects is a line in the
watch log on every Tick.

Stdout is sacred: Launches only, one JSON per line.
"""

import argparse
import json
import sys
from datetime import datetime, time
from zoneinfo import ZoneInfo

# The Sigil the overseer itself runs under, as distinct from the one it
# oversees. Sigil names are global in the Daemon, so this one name serves
# every loop, and each loop's Manifest inscribes it at the same path.
OVERSEER_SIGIL = "overseer"


def parse_slot(text):
    hour, minute = text.strip().split(":")
    return time(int(hour), int(minute))


def main():
    # Direct invocation also accepts human input; invalid slots or zones
    # fail here before a Launch is emitted.
    parser = argparse.ArgumentParser()
    parser.add_argument("--sigil", required=True, help="the Sigil to oversee")
    parser.add_argument("--at", required=True, help="HH:MM")
    parser.add_argument("--tz", required=True, help="IANA zone, e.g. America/Boise")
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", required=True)
    args = parser.parse_args()

    now = datetime.now(ZoneInfo(args.tz))
    slot = parse_slot(args.at)
    is_weekday = now.weekday() < 5
    if not is_weekday:
        print(f"oversee {args.sigil}: weekend in {args.tz}, no slot", file=sys.stderr)
        return
    slot_is_due = now.time() >= slot
    if not slot_is_due:
        print(
            f"oversee {args.sigil}: slot {slot:%H:%M} {args.tz} not yet",
            file=sys.stderr,
        )
        return
    run_id = f"oversee-{args.sigil}-{now:%Y-%m-%d}-{slot:%H%M}"
    launch_body = {
        "sigil": OVERSEER_SIGIL,
        "id": run_id,
        "args": [
            "--sigil", args.sigil,
            "--model", args.model,
            "--effort", args.effort,
            "--run-id", run_id,
        ],
    }
    print(json.dumps(launch_body))


if __name__ == "__main__":
    main()
