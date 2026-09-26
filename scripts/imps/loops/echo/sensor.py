#!/usr/bin/env python3
"""Sensor: always emit the one standing echo launch.

The echo loop's standing Sensor, and the reference example of the
Sensor contract: stdout is zero or more Launches, one JSON
per line; stderr and exit code are diagnostics only. This one has no
condition to test and emits the same Launch on every Tick, under the
fixed Run Id `echo-standing`. The first Tick launches it; every later Tick
is a rejected duplicate (409), which is the design working: deterministic
Run Ids make re-emission idempotent, never duplicated work.
"""

import json

print(json.dumps({"sigil": "echo", "id": "echo-standing", "args": ["standing-token"]}))
