"""Points de restauration système créés par Autowash, supprimés au bout de 7 jours pour ne pas encombrer le disque."""
from __future__ import annotations

import ctypes
from datetime import datetime, timedelta

from . import ps
from .system import HKLM, is_admin, reg_set_dword

PREFIX = "Autowash"
KEEP_DAYS = 7
MIN_INTERVAL = timedelta(hours=1)  # pas de nouveau point si Autowash en a créé un il y a moins d'une heure
_SR_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\SystemRestore"

LIST_PS = r"""
Get-ComputerRestorePoint | ForEach-Object {
  [pscustomobject]@{ Sequence = [int]$_.SequenceNumber; Description = [string]$_.Description
                     Created = $_.ConvertToDateTime($_.CreationTime).ToString('yyyy-MM-dd HH:mm:ss') }
}
"""


def list_points() -> list[dict]:
    """Points de restauration du système, du plus récent au plus ancien (droits administrateur requis)."""
    points = ps.run_list(LIST_PS)
    for p in points:
        p["Created"] = datetime.strptime(p["Created"], "%Y-%m-%d %H:%M:%S")
        p["Ours"] = p["Description"].startswith(PREFIX)
    return sorted(points, key=lambda p: p["Created"], reverse=True)


def create(reason: str) -> str:
    """Crée un point de restauration avant une modification ; renvoie ce qui s'est passé, en une phrase."""
    if not is_admin():
        return "Point de restauration non créé (droits administrateur requis)."
    ours = [p for p in list_points() if p["Ours"]]
    if ours and datetime.now() - ours[0]["Created"] < MIN_INTERVAL:
        return f"Point de restauration récent conservé ({ours[0]['Created']:%H:%M})."
    # Windows refuse par défaut plus d'un point par 24 h
    reg_set_dword(HKLM, _SR_KEY, "SystemRestorePointCreationFrequency", 0)
    description = ps.quote(f"{PREFIX} - {reason}"[:60])
    try:
        ps.run(f"Checkpoint-Computer -Description {description} -RestorePointType MODIFY_SETTINGS", timeout=600)
    except ps.PSError:
        # protection du système désactivée sur le disque Windows : on l'active puis on réessaie
        ps.run(f'Enable-ComputerRestore -Drive "$env:SystemDrive\\"; '
               f"Checkpoint-Computer -Description {description} -RestorePointType MODIFY_SETTINGS", timeout=600)
    pruned = prune()
    return "Point de restauration créé." + (f" {pruned} ancien(s) supprimé(s)." if pruned else "")


def prune(days: int = KEEP_DAYS) -> int:
    """Supprime les points créés par Autowash depuis plus de `days` jours ; ceux de Windows sont conservés."""
    if not is_admin():
        return 0
    limit = datetime.now() - timedelta(days=days)
    old = [p for p in list_points() if p["Ours"] and p["Created"] < limit]
    remove = ctypes.WinDLL("srclient").SRRemoveRestorePoint
    return sum(remove(p["Sequence"]) == 0 for p in old)
