"""Santé du PC : disques, espace libre, mémoire, plantages, pilotes, batterie."""
from __future__ import annotations

import ctypes
import shutil
import string
import subprocess

from . import ps
from .cleanup import CLEAN_TEMP, STORAGE_SENSE
from .models import Action, Finding, Severity, run_checks
from .system import CREATE_NO_WINDOW, human_size, launch

NAME = "Santé"
DESCRIPTION = "État des disques, espace libre, mémoire, plantages récents, pilotes en erreur et batterie."


def _repair_system() -> str:
    subprocess.run(["DISM", "/Online", "/Cleanup-Image", "/RestoreHealth"], capture_output=True,
                   creationflags=CREATE_NO_WINDOW)
    proc = subprocess.run(["sfc", "/scannow"], capture_output=True, creationflags=CREATE_NO_WINDOW)
    return (f"Réparation terminée (SFC code {proc.returncode}). "
            r"Détails : C:\Windows\Logs\CBS\CBS.log. Redémarrez le PC si des fichiers ont été réparés.")


TOOLS = [
    Action("Réparer les fichiers système", _repair_system, admin=True,
           confirm="Lance DISM puis SFC pour réparer les fichiers de Windows. Cela prend 15 à 30 minutes. Continuer ?"),
]


# --- Disques ----------------------------------------------------------------

def _fixed_drives() -> list[str]:
    k32 = ctypes.windll.kernel32
    mask = k32.GetLogicalDrives()
    roots = [f"{letter}:\\" for i, letter in enumerate(string.ascii_uppercase) if mask & (1 << i)]
    return [r for r in roots if k32.GetDriveTypeW(r) == 3]  # DRIVE_FIXED


def free_space() -> list[Finding]:
    """Espace disque"""
    findings = []
    for root in _fixed_drives():
        total, _, free = shutil.disk_usage(root)
        if total < 1024**3:
            continue  # petite partition (récupération, EFI…)
        pct = free / total * 100
        text = f"{root[:2]} : {human_size(free)} libres sur {human_size(total)} ({pct:.0f} %)"
        if pct < 5 or (total > 64 * 1024**3 and free < 5 * 1024**3):
            findings.append(Finding(f"Disque {root[:2]} presque plein", Severity.CRITICAL,
                                    text + "\nWindows ralentit et les mises à jour peuvent échouer.",
                                    [CLEAN_TEMP, STORAGE_SENSE]))
        elif pct < 15:
            findings.append(Finding(f"Peu d'espace libre sur {root[:2]}", Severity.WARNING, text,
                                    [CLEAN_TEMP, STORAGE_SENSE]))
        else:
            findings.append(Finding(f"Espace disque {root[:2]} correct", Severity.OK, text))
    return findings


DISKS_PS = r"""
Get-PhysicalDisk | ForEach-Object {
  $r = $null
  try { $r = $_ | Get-StorageReliabilityCounter } catch {}
  [pscustomobject]@{
    Name = $_.FriendlyName
    Media = [string]$_.MediaType
    Health = [string]$_.HealthStatus
    Wear = $(if ($r) { $r.Wear } else { $null })
    Temp = $(if ($r) { $r.Temperature } else { $null })
  }
}
"""


def disks() -> list[Finding]:
    """État des disques"""
    findings = []
    for d in ps.run_list(DISKS_PS):
        name, wear, temp = d["Name"], d["Wear"], d["Temp"]
        info = ", ".join(x for x in (d["Media"] if d["Media"] != "Unspecified" else "",
                                     f"usure {wear} %" if wear else "", f"{temp} °C" if temp else "") if x)
        if d["Health"] != "Healthy":
            findings.append(Finding(f"Disque {name} : état « {d['Health']} »", Severity.CRITICAL,
                                    "Le disque signale une défaillance. Sauvegardez vos données immédiatement "
                                    "et prévoyez son remplacement."))
        elif wear and wear >= 90:
            findings.append(Finding(f"Disque {name} usé à {wear} %", Severity.CRITICAL,
                                    "Le SSD arrive en fin de vie : sauvegardez vos données et prévoyez son remplacement."))
        elif wear and wear >= 70:
            findings.append(Finding(f"Disque {name} usé à {wear} %", Severity.WARNING,
                                    "Pensez à sauvegarder régulièrement."))
        elif temp and temp >= 60:
            findings.append(Finding(f"Disque {name} chaud ({temp} °C)", Severity.WARNING,
                                    "Vérifiez la ventilation du PC."))
        else:
            findings.append(Finding(f"Disque {name} en bonne santé", Severity.OK, info))
    return findings


# --- Système ----------------------------------------------------------------

class _MemoryStatus(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def memory_and_uptime() -> list[Finding]:
    """Mémoire et redémarrage"""
    mem = _MemoryStatus()
    mem.dwLength = ctypes.sizeof(mem)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
    text = f"{mem.dwMemoryLoad} % utilisés sur {human_size(mem.ullTotalPhys)}"
    findings = []
    if mem.dwMemoryLoad >= 90:
        findings.append(Finding("Mémoire vive saturée", Severity.WARNING,
                                text + "\nFermez des applications ou vérifiez le Gestionnaire des tâches.",
                                [Action("Gestionnaire des tâches", lambda: launch("taskmgr"), auto=False, batch=False)]))
    else:
        findings.append(Finding("Mémoire vive", Severity.OK, text))

    days = ctypes.windll.kernel32.GetTickCount64() / 86_400_000
    if days >= 7:
        findings.append(Finding(f"PC allumé depuis {days:.0f} jours", Severity.INFO,
                                "Un redémarrage applique les mises à jour et libère la mémoire."))
    return findings


CRASHES_PS = r"""
$since = (Get-Date).AddDays(-14)
$crit = @(); $bsod = @()
try { $crit = @(Get-WinEvent -FilterHashtable @{LogName='System'; Level=1; StartTime=$since} -ErrorAction Stop) } catch {}
try { $bsod = @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WER-SystemErrorReporting'; Id=1001; StartTime=$since} -ErrorAction Stop) } catch {}
[pscustomobject]@{
  Critical = $crit.Count
  Bsod = $bsod.Count
  Last = $(if ($crit.Count) { $crit[0].TimeCreated.ToString('dd/MM/yyyy HH:mm') } else { '' })
}
"""


def crashes() -> list[Finding]:
    """Plantages récents"""
    c = ps.run_obj(CRASHES_PS)
    total = c["Critical"] + c["Bsod"]
    if not total:
        return [Finding("Aucun plantage système sur 14 jours", Severity.OK)]
    detail = (f"{c['Critical']} arrêt(s) inattendu(s) / erreur(s) critique(s) et {c['Bsod']} écran(s) bleu(s) "
              f"sur 14 jours (dernier : {c['Last'] or '?'}).\n"
              "Causes fréquentes : pilote défectueux, surchauffe, alimentation ou RAM instable.")
    viewer = Action("Observateur d'événements", lambda: launch("eventvwr.msc"), auto=False, batch=False)
    return [Finding(f"{total} plantage(s) système récent(s)", Severity.WARNING if total >= 3 else Severity.INFO,
                    detail, [viewer])]


def devices() -> list[Finding]:
    """Périphériques et pilotes"""
    broken = ps.run_list("Get-PnpDevice -PresentOnly -Status ERROR -ErrorAction SilentlyContinue "
                         "| Select-Object FriendlyName, Class")
    if not broken:
        return [Finding("Tous les périphériques fonctionnent", Severity.OK)]
    names = "\n".join(f"{d['FriendlyName'] or 'Inconnu'} ({d['Class'] or '?'})" for d in broken)
    return [Finding(f"{len(broken)} périphérique(s) en erreur", Severity.WARNING,
                    names + "\nPilote manquant ou défectueux : mettez-le à jour depuis le site du fabricant.",
                    [Action("Gestionnaire de périphériques", lambda: launch("mmc", "devmgmt.msc"), auto=False, batch=False)])]


BATTERY_PS = r"""
try {
  $d = @(Get-CimInstance -Namespace root/wmi -ClassName BatteryStaticData)[0].DesignedCapacity
  $f = @(Get-CimInstance -Namespace root/wmi -ClassName BatteryFullChargedCapacity)[0].FullChargedCapacity
  if ($d -and $f) { [pscustomobject]@{ Design = [int64]$d; Full = [int64]$f } }
} catch {}
"""


def battery() -> list[Finding]:
    """Batterie"""
    b = ps.run_obj(BATTERY_PS)
    if not b:
        return []  # PC fixe ou information indisponible
    pct = min(100, round(b["Full"] / b["Design"] * 100))
    text = f"Capacité actuelle : {pct} % de la capacité d'origine."
    if pct < 50:
        return [Finding(f"Batterie usée ({pct} %)", Severity.WARNING, text + " Prévoyez son remplacement.")]
    if pct < 70:
        return [Finding(f"Batterie fatiguée ({pct} %)", Severity.INFO, text)]
    return [Finding(f"Batterie en bon état ({pct} %)", Severity.OK)]


def startup_load() -> list[Finding]:
    """Programmes au démarrage"""
    count = int(ps.run("@(Get-CimInstance Win32_StartupCommand).Count") or 0)
    if count > 12:
        return [Finding(f"{count} programmes lancés au démarrage", Severity.INFO,
                        "Désactivez ceux dont vous n'avez pas besoin pour accélérer le démarrage.",
                        [Action("Gérer le démarrage", lambda: launch("taskmgr", "/0", "/startup"), auto=False, batch=False)])]
    return [Finding(f"{count} programmes au démarrage", Severity.OK)]


def scan() -> list[Finding]:
    return run_checks([disks, free_space, memory_and_uptime, crashes, devices, battery, startup_load], NAME)
