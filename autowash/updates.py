"""Mises à jour : Windows Update, logiciels (winget), redémarrage en attente, mise à jour automatique."""
from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from typing import Callable

from . import journal, ps, restore
from .malware import update_signatures
from .models import Action, Finding, Severity, run_checks
from .system import CREATE_NO_WINDOW, HKLM, app_command, data_dir, human_size, notify, open_target, reg_exists

NAME = "Mises à jour"
DESCRIPTION = "Correctifs Windows, logiciels obsolètes (navigateurs, runtimes…) et mise à jour automatique des urgences."

BATCH_LABEL = "Mettre à jour la sélection"
TASK_NAME = "Autowash AutoUpdate"

# Logiciels exposés à Internet ou aux fichiers reçus : leurs mises à jour corrigent souvent des failles.
URGENT_PACKAGES = (
    "google.chrome", "mozilla.firefox", "mozilla.thunderbird", "microsoft.edge", "brave.brave", "opera.",
    "vivaldi.", "7zip.", "rarlab.winrar", "videolan.vlc", "adobe.acrobat", "oracle.javaruntime", "oracle.jdk",
    "eclipseadoptium.", "microsoft.openjdk","zoom.zoom", "discord.discord", "microsoft.teams", "notepad++.", "putty.", "winscp.",
    "git.git", "openjs.nodejs", "python.python", "microsoft.dotnet", "microsoft.vcredist", "microsoft.powershell",
    "keepassxcteam.", "dominikreichl.keepass", "bitwarden.", "telegram.", "slacktechnologies.", "foxit.",
    "wiresharkfoundation.", "timkosse.filezilla", "teamviewer.", "anydesksoftwaregmbh.", "shininglight.openssl",
)


# --- Windows Update ---------------------------------------------------------

_SECURITY = "0fa1201d-4330-4fa8-8ae9-b877473b6441"
_CRITICAL = "e6cf1350-c01b-414d-a61f-263d14d133b4"
_DEFINITIONS = "e0789628-ce08-4437-be74-2495b842f43b"
_ROLLUPS = "28bc880e-0592-4cbf-8f95-c79b17911d5f"
_UPGRADES = "3689bdc8-b205-4af4-8d4b-a63924c5e9d5"

WU_SEARCH_PS = r"""
$s = New-Object -ComObject Microsoft.Update.Session
$r = $s.CreateUpdateSearcher().Search("IsInstalled=0 and IsHidden=0")
foreach ($u in $r.Updates) {
  [pscustomobject]@{
    Id = $u.Identity.UpdateID
    Title = $u.Title
    Severity = [string]$u.MsrcSeverity
    Categories = @($u.Categories | ForEach-Object { $_.CategoryID })
    Size = [int64]$u.MaxDownloadSize
    Driver = ([int]$u.Type -eq 2)
  }
}
"""

WU_INSTALL_PS = r"""
$ids = __IDS__
$s = New-Object -ComObject Microsoft.Update.Session
$r = $s.CreateUpdateSearcher().Search("IsInstalled=0 and IsHidden=0")
$col = New-Object -ComObject Microsoft.Update.UpdateColl
foreach ($u in $r.Updates) {
  if ($ids -contains $u.Identity.UpdateID) { if (-not $u.EulaAccepted) { $u.AcceptEula() }; [void]$col.Add($u) }
}
if ($col.Count -eq 0) { 'Aucune mise à jour à installer (déjà installées ?).' }
else {
  $d = $s.CreateUpdateDownloader(); $d.Updates = $col; [void]$d.Download()
  $i = $s.CreateUpdateInstaller(); $i.Updates = $col; $res = $i.Install()
  $state = @{2 = 'réussie'; 3 = 'réussie avec des erreurs'; 4 = 'échouée'; 5 = 'annulée'}[[int]$res.ResultCode]
  $msg = "$($col.Count) mise(s) à jour Windows : installation $state."
  if ($res.RebootRequired) { $msg += ' Redémarrage nécessaire.' }
  $msg
}
"""


_WU_LOCK = threading.Lock()
_WU_CACHE: tuple[float, list[dict]] = (0.0, [])


def search_windows_updates() -> list[dict]:
    """Mises à jour Windows en attente (logiciels et pilotes). Une seule recherche à la fois, gardée 2 minutes :
    les pages Mises à jour et Pilotes analysent en parallèle et la recherche peut prendre une minute."""
    global _WU_CACHE
    with _WU_LOCK:
        if time.time() - _WU_CACHE[0] > 120:
            _WU_CACHE = (time.time(), ps.run_list(WU_SEARCH_PS, timeout=600))
        return _WU_CACHE[1]


def forget_windows_updates() -> None:
    """À appeler après une installation, pour que la prochaine analyse relance une vraie recherche."""
    global _WU_CACHE
    _WU_CACHE = (0.0, [])


def _wu_severity(update: dict) -> Severity:
    cats = {c.lower() for c in update["Categories"] or []}
    if _SECURITY in cats or update["Severity"] in ("Critical", "Important"):
        return Severity.CRITICAL
    if cats & {_CRITICAL, _DEFINITIONS, _ROLLUPS, _UPGRADES}:
        return Severity.WARNING
    return Severity.INFO


def _is_feature_upgrade(update: dict) -> bool:
    return _UPGRADES in {c.lower() for c in update["Categories"] or []}


def install_windows_updates(ids: list[str]) -> Callable[[], str]:
    def go() -> str:
        quoted = "@(" + ",".join(ps.quote(i) for i in ids) + ")"
        try:
            return ps.run(WU_INSTALL_PS.replace("__IDS__", quoted), timeout=None)
        finally:
            forget_windows_updates()
    return go


def windows_update() -> list[Finding]:
    """Windows Update"""
    updates = [u for u in search_windows_updates() if not u["Driver"]]  # pilotes : page Pilotes
    if not updates:
        return [Finding("Windows est à jour", Severity.OK)]
    findings = []
    for u in updates:
        upgrade = _is_feature_upgrade(u)
        install = Action("Installer", install_windows_updates([u["Id"]]), admin=True, auto=not upgrade,
                         confirm=("Mise à niveau de Windows : téléchargement de plusieurs Go puis redémarrage. "
                                  "Continuer ?") if upgrade else None)
        detail = f"Taille : {human_size(u['Size'])}" if u["Size"] else ""
        findings.append(Finding(u["Title"], _wu_severity(u), detail, [install]))
    return findings


# --- Logiciels (winget) -----------------------------------------------------

_WINGET_LOCK = threading.Lock()  # deux winget simultanés se bloquent sur leur index de sources


def _winget(*args: str, timeout: float | None = 300) -> tuple[int, str]:
    if not shutil.which("winget"):
        raise RuntimeError("winget introuvable : installez « App Installer » depuis le Microsoft Store.")
    with _WINGET_LOCK:
        proc = subprocess.run(["winget", *args, "--accept-source-agreements", "--disable-interactivity"],
                              capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
    return proc.returncode, proc.stdout.decode("utf-8", errors="replace")


def _parse_table(text: str, required: tuple[int, ...] = (1, 3)) -> list[list[str]]:
    """Découpe le tableau texte de winget (colonnes Nom, ID, Version, Disponible, Source).

    `required` : colonnes qui doivent être remplies ; la première ligne qui n'en a pas termine le tableau.
    """
    lines = [line.rsplit("\r", 1)[-1].rstrip() for line in text.splitlines()]
    for i, line in enumerate(lines):
        if i and re.fullmatch(r"-{10,}", line.strip()):
            header = lines[i - 1]
            break
    else:
        return []
    starts = [m.start() for m in re.finditer(r"\S+", header)]
    if len(starts) < 4:
        return []
    starts[0] = 0
    rows = []
    for line in lines[i + 1:]:
        cells = [line[a:b].strip() for a, b in zip(starts, starts[1:] + [None])]
        footer = " " in cells[1] and not cells[1].startswith(("ARP\\", "MSIX\\"))  # phrase de fin de winget
        if footer or not all(cells[i] for i in required):
            break
        rows.append(cells)
    return rows


_PRERELEASE = re.compile(r"alpha|beta|\brc\d*\b|preview|canary|nightly|insider", re.I)


def _winget_upgrades() -> list[dict]:
    """Mises à jour disponibles, sans les versions de test (alpha, bêta…) que winget propose parfois."""
    _, out = _winget("upgrade")
    return [{"name": r[0], "id": r[1], "version": r[2], "available": r[3]} for r in _parse_table(out)
            if not _PRERELEASE.search(r[3])]


def _is_urgent(pkg: dict) -> bool:
    return pkg["id"].lower().startswith(URGENT_PACKAGES)


def _winget_upgrade(pkg: dict) -> Callable[[], str]:
    def go() -> str:
        code, out = _winget("upgrade", "--id", pkg["id"], "--exact", "--silent",
                            "--accept-package-agreements", timeout=1800)
        if code != 0:
            last = [line.strip() for line in out.splitlines() if line.strip()]
            raise RuntimeError(f"{pkg['name']} : échec (code {code}). {last[-1] if last else ''}")
        return f"{pkg['name']} mis à jour en {pkg['available']}."
    return go


def software() -> list[Finding]:
    """Logiciels installés"""
    packages = _winget_upgrades()
    if not packages:
        return [Finding("Tous les logiciels sont à jour", Severity.OK)]
    findings = []
    for pkg in packages:
        urgent = _is_urgent(pkg)
        detail = f"{pkg['version']} → {pkg['available']}"
        if urgent:
            detail += "\nLogiciel exposé aux attaques (navigateur, lecteur, runtime…) : mise à jour prioritaire."
        findings.append(Finding(pkg["name"], Severity.WARNING if urgent else Severity.INFO, detail,
                                [Action("Mettre à jour", _winget_upgrade(pkg))]))
    return findings


# --- Redémarrage et automatisation -------------------------------------------

def _reboot() -> str:
    subprocess.run(["shutdown", "/r", "/t", "60", "/c", "Autowash : redémarrage pour terminer les mises à jour."],
                   creationflags=CREATE_NO_WINDOW)
    return "Le PC redémarre dans 60 secondes (annuler : shutdown /a)."


def pending_reboot() -> list[Finding]:
    """Redémarrage en attente"""
    keys = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired",
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending")
    if any(reg_exists(HKLM, k) for k in keys):
        return [Finding("Redémarrage nécessaire", Severity.WARNING,
                        "Des mises à jour déjà installées ne seront actives qu'après redémarrage.",
                        [Action("Redémarrer", _reboot, auto=False, batch=False,
                                confirm="Le PC va redémarrer dans 60 secondes. Enregistrez votre travail. Continuer ?")])]
    return []


def auto_update(log: Callable[[str], None] = print) -> None:
    """Installe tout ce qui est urgent : signatures antivirus, correctifs Windows, logiciels exposés."""
    steps: list[tuple[str, Callable[[], str]]] = [("Signatures antivirus", update_signatures)]
    try:
        ids = [u["Id"] for u in search_windows_updates()
               if _wu_severity(u) >= Severity.WARNING and not _is_feature_upgrade(u)]
        if ids:
            steps.append(("Windows Update", install_windows_updates(ids)))
    except Exception as exc:
        log(f"Windows Update : recherche impossible ({exc})")
    try:
        steps += [(p["name"], _winget_upgrade(p)) for p in _winget_upgrades() if _is_urgent(p)]
    except Exception as exc:
        log(f"winget : recherche impossible ({exc})")
    for label, step in steps:
        try:
            log(step())
        except Exception as exc:
            log(f"{label} : échec ({exc})")
    if len(steps) == 1:
        log("Aucune mise à jour urgente en attente.")


def _auto_update_now() -> str:
    lines: list[str] = []
    auto_update(lines.append)
    return "\n".join(lines)


def run_scheduled() -> None:
    """Point d'entrée de la tâche planifiée : point de restauration, urgences, analyse complète, notification.

    Journal détaillé : %LOCALAPPDATA%\\Autowash\\auto-update.log.
    """
    from . import MODULES  # import local : le paquet importe ce module
    from .models import score

    with open(data_dir() / "auto-update.log", "a", encoding="utf-8") as f:
        def log(message: str) -> None:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {message}\n")
            f.flush()

        log("--- Mise à jour automatique ---")
        try:
            log(restore.create("mises à jour automatiques"))
        except Exception as exc:
            log(f"Point de restauration impossible ({exc})")
        lines: list[str] = []
        auto_update(lambda message: (log(message), lines.append(message)))
        journal.record(NAME, "Mise à jour automatique", "Installer les urgences", True, "\n".join(lines))

        results = {}
        for module in MODULES:
            try:
                results[module.NAME] = module.scan()
            except Exception as exc:
                log(f"Analyse {module.NAME} impossible ({exc})")
        findings = [f for fs in results.values() for f in fs]
        critical = [f for f in findings if f.severity == Severity.CRITICAL]
        journal.record_score(score(results), len(critical), sum(f.severity == Severity.WARNING for f in findings))
        log(f"Score : {score(results)}/100, {len(critical)} problème(s) critique(s)")
        if critical:
            notify(f"Autowash : {len(critical)} problème(s) critique(s)",
                   "; ".join(f.title for f in critical[:3]) + ". Ouvrez Autowash pour les corriger.")


def _enable_schedule() -> str:
    exe, argv = app_command("--auto")
    ps.run(f"""
$a = New-ScheduledTaskAction -Execute {ps.quote(exe)} -Argument {ps.quote(subprocess.list2cmdline(argv))}
$t = New-ScheduledTaskTrigger -Daily -At 12:00
$p = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Highest
$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 3)
Register-ScheduledTask -TaskName {ps.quote(TASK_NAME)} -Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null
""")
    return "Mise à jour automatique activée : tous les jours à 12 h (ou dès que le PC est allumé)."


def _disable_schedule() -> str:
    ps.run(f"Unregister-ScheduledTask -TaskName {ps.quote(TASK_NAME)} -Confirm:$false")
    return "Mise à jour automatique désactivée."


def schedule() -> list[Finding]:
    """Mise à jour automatique"""
    exists = ps.run(f"[bool](Get-ScheduledTask -TaskName {ps.quote(TASK_NAME)} -ErrorAction SilentlyContinue)") == "True"
    if exists:
        return [Finding("Mise à jour automatique des urgences activée", Severity.OK,
                        f"Chaque jour à 12 h. Journal : {data_dir() / 'auto-update.log'}",
                        [Action("Désactiver", _disable_schedule, admin=True, auto=False)])]
    return [Finding("Mise à jour automatique des urgences désactivée", Severity.INFO,
                    "Autowash peut installer chaque jour les signatures antivirus, les correctifs de sécurité "
                    "Windows et les mises à jour des logiciels exposés.",
                    [Action("Activer (tous les jours)", _enable_schedule, admin=True, auto=False)])]


TOOLS = [
    Action("Installer toutes les urgences", _auto_update_now, admin=True,
           confirm="Installer maintenant les signatures antivirus, les correctifs de sécurité Windows et les mises "
                   "à jour des logiciels exposés ? Les applications concernées peuvent se fermer. Continuer ?"),
    Action("Ouvrir Windows Update", lambda: open_target("ms-settings:windowsupdate"), auto=False, batch=False),
]


def scan() -> list[Finding]:
    return run_checks([windows_update, software, pending_reboot, schedule], NAME)
