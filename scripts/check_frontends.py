#!/usr/bin/env python3
"""Every library verb (agentspace/*.py `def cmd_*`) must be reachable from BOTH
front ends in zookeeper.py: the click commands and the interactive menu.
Exit 1 and name the gaps otherwise. Zero deps, instant."""
import re, sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
cli, menu = (root / "zookeeper.py").read_text().split("# INTERACTIVE MENU", 1)
gaps = []
for f in sorted((root / "agentspace").glob("*.py")):
    for fn in re.findall(r"^def (cmd_\w+)", f.read_text(), re.M):
        pat = re.compile(rf"\b{f.stem}(_mod)?\.{fn}\(")
        for side, text in (("cli", cli), ("menu", menu)):
            if not pat.search(text):
                gaps.append(f"{f.stem}.{fn} missing from {side}")
print("\n".join(gaps) or "front ends in sync")
sys.exit(1 if gaps else 0)
