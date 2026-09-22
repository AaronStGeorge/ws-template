#!/usr/bin/env python3
"""Condition script: emit one ggml-bump overseer launch per daily slot.

Paired with the imp.py beside it; `scripts/imps/README.md` is the record
of the pair. Armed as a standing row by `loops.py up` with the schedule
and the check's codex knobs:

    condition.py --at HH:MM[,HH:MM...] --tz ZONE --model M --effort E

`--at`/`--tz` are the sensor's; `--model`/`--effort` pass through to the
imp as the Run's args, plus `--run-id` so the imp can name the thread it
opens after its own Run.

Each Tick, for every slot already past in the zone's local today, it
emits `{"imp": "overseer", "id": "oversee-ggml-bump-<YYYY-MM-DD>-<HHMM>", ...}`.
The `HHMM` is the slot's configured time from `--at`, never the Tick's
time: every Tick after 09:00 emits the same `...-0900` id, so a past
slot re-emitted on every later Tick that day is a rejected duplicate
(409), and that is what makes it run once. The slot time is in the id
so a loop with several slots gets one Run per slot rather than the
later ones being rejected as duplicates of the first. Deliberately no
firing window: a slot missed while the container was down runs late,
and a Daemon restart mid-day (which forgets its Run Ids) re-runs the
slot — both accepted over the complexity of guarding them.

Stdout is sacred: Launch Bodies only, one JSON per line.
"""

import argparse
import json
import sys
from datetime import datetime, time
from zoneinfo import ZoneInfo

LOOP = "ggml-bump"


def parse_slots(text):
    slots = []
    for item in text.split(","):
        hour, minute = item.strip().split(":")
        slots.append(time(int(hour), int(minute)))
    return slots


def main():
    # argv is the provenance boundary — a human types the arm line into
    # imps.json — so a bad slot or zone crashes visibly on every Tick.
    parser = argparse.ArgumentParser()
    parser.add_argument("--at", required=True, help="HH:MM, comma-separated")
    parser.add_argument("--tz", required=True, help="IANA zone, e.g. America/Boise")
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", required=True)
    args = parser.parse_args()

    now = datetime.now(ZoneInfo(args.tz))
    for slot in parse_slots(args.at):
        slot_is_due = now.time() >= slot
        if not slot_is_due:
            print(f"{LOOP}: slot {slot:%H:%M} {args.tz} not yet", file=sys.stderr)
            continue
        run_id = f"oversee-{LOOP}-{now:%Y-%m-%d}-{slot:%H%M}"
        launch_body = {
            "imp": "overseer",
            "id": run_id,
            "args": [
                "--model", args.model,
                "--effort", args.effort,
                "--run-id", run_id,
            ],
        }
        print(json.dumps(launch_body))


if __name__ == "__main__":
    main()
