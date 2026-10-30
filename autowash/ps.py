"""Exécution de scripts PowerShell depuis Python."""
from __future__ import annotations

import base64
import json
import re
import subprocess

CREATE_NO_WINDOW = 0x08000000

_PREFIX = (
    "$ProgressPreference='SilentlyContinue';"
    "$ErrorActionPreference='Stop';"
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8;\n"
)


class PSError(RuntimeError):
    pass


def _clean_error(text: str) -> str:
    if text.startswith("#< CLIXML"):
        text = "".join(re.findall(r'<S S="Error">(.*?)</S>', text, re.S)).replace("_x000D__x000A_", "\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[0] if lines else ""


def quote(value: str) -> str:
    """Chaîne PowerShell littérale (apostrophes échappées)."""
    return "'" + str(value).replace("'", "''") + "'"


def run(script: str, timeout: float | None = 120) -> str:
    encoded = base64.b64encode((_PREFIX + script).encode("utf-16-le")).decode("ascii")
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", encoded],
            capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise PSError("Délai dépassé") from exc
    out = proc.stdout.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        err = _clean_error(proc.stderr.decode("utf-8", errors="replace"))
        raise PSError(err or out or f"PowerShell a échoué (code {proc.returncode})")
    return out


def run_list(script: str, timeout: float | None = 120) -> list[dict]:
    """Exécute le script et renvoie sa sortie convertie en liste d'objets JSON."""
    out = run(f"ConvertTo-Json -Depth 4 -Compress -InputObject @(& {{\n{script}\n}})", timeout)
    if not out:
        return []
    data = json.loads(out)
    return data if isinstance(data, list) else [data]


def run_obj(script: str, timeout: float | None = 120) -> dict | None:
    items = run_list(script, timeout)
    return items[0] if items else None
