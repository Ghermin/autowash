"""Rapport en mode texte (python main.py --report)."""
from __future__ import annotations

import sys
import time

from . import MODULES
from .models import Severity

_MARKS = {Severity.CRITICAL: "[CRITIQUE]", Severity.WARNING: "[ATTENTION]", Severity.INFO: "[INFO]", Severity.OK: "[OK]"}


def report() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    worst = Severity.OK
    for module in MODULES:
        start = time.time()
        findings = module.scan()
        print(f"\n=== {module.NAME} ({time.time() - start:.1f} s) ===")
        for f in findings:
            worst = max(worst, f.severity)
            print(f"{_MARKS[f.severity]:<12} {f.title}")
            for line in f.detail.splitlines():
                print(f"{'':<13}{line}")
            if f.actions:
                print(f"{'':<13}-> " + " | ".join(a.label for a in f.actions))
    return 1 if worst >= Severity.WARNING else 0
