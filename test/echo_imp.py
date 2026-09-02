#!/usr/bin/env python3
"""The test backend: write argv to stderr and exit 0.

Exists so the automated end-to-end check can watch a value carried through
the whole chain (watcher -> Daemon -> Imp -> Run log). Keep it free of
filesystem, network, and subprocess work.
"""

import sys

print(" ".join(sys.argv[1:]), file=sys.stderr)
