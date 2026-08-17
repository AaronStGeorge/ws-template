#!/usr/bin/env python3
import re
import shutil
import subprocess
import sys
from pathlib import Path

ticket = re.sub(r"[^a-z0-9]+", "-", " ".join(sys.argv[1:]).lower()).strip("-")
ws = Path(__file__).resolve().parent.parent
wsdir = ws / ".workspaces" / ticket
wsdir.mkdir(parents=True)

for src in sorted(p for p in (ws / "sources").iterdir() if p.is_dir()):
    subprocess.run(["git", "worktree", "add", "-b", ticket, str(wsdir / src.name), "main"], cwd=src, check=True)

shared_skills_target = Path("..", "..", "..", ".agents", "skills")
for name in (".agents", ".claude"):
    config_dir = wsdir / name
    config_dir.mkdir()
    (config_dir / "skills").symlink_to(shared_skills_target, target_is_directory=True)

(wsdir / "docs").symlink_to(Path("..", "..", "docs"), target_is_directory=True)

(wsdir / "AGENTS.md").symlink_to(Path("..", "..", "AGENTS.md"))

shutil.copy2(ws / "sources" / "build.py", wsdir / "build.py")

print(wsdir)
