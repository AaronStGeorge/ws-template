#!/usr/bin/env python3
"""The echo Imp: write argv to stderr and exit 0.

The smallest Imp honoring the process contract (argv in, diagnostics on
stderr, outcome in the exit code), so the chain check can watch a value
carried through the whole system: Sensor -> Tick -> Daemon ->
Imp -> Run log. Keep it free of filesystem, network, and subprocess work.
"""

import sys

print(" ".join(sys.argv[1:]), file=sys.stderr)
