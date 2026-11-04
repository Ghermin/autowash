"""Mise à jour d'Autowash lui-même depuis les releases GitHub (version .exe uniquement).

La release est téléchargée et vérifiée (empreinte SHA-256 publiée par GitHub) pendant qu'Autowash tourne, puis un
script PowerShell détaché remplace les fichiers une fois Autowash fermé, avec retour à l'ancienne version en cas
d'échec. Journal : %LOCALAPPDATA%\\Autowash\\update.log.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

from .system import CREATE_NO_WINDOW, data_dir

REPO = "Ghermin/autowash"
_API = f"https://api.github.com/repos/{REPO}/releases/latest"
_ASSET = re.compile(r"^Autowash-[\d.]+-windows\.zip$")
_HEADERS = {"User-Agent": "Autowash", "Accept": "application/vnd.github+json"}
_CREATE_NEW_PROCESS_GROUP = 0x200

_SCRIPT = r"""param([int]$WaitPid, [string]$Source, [string]$Target, [string]$Log, [string]$Version, [switch]$Relaunch)
$ErrorActionPreference = 'Stop'
function Write-Log($message) {
    $line = '{0}  {1}{2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $message, [Environment]::NewLine
    [IO.File]::AppendAllText($Log, $line, (New-Object Text.UTF8Encoding $false))  # même format que selfupdate.log
}
$exe = Join-Path $Target 'Autowash.exe'
Wait-Process -Id $WaitPid -Timeout 120 -ErrorAction SilentlyContinue
$deadline = (Get-Date).AddSeconds(120)
while (Get-Process -Name Autowash -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe }) {
    if ((Get-Date) -gt $deadline) { Write-Log 'Autowash est encore ouvert : mise à jour reportée.'; exit 1 }
    Start-Sleep -Seconds 1
}
$items = 'Autowash.exe', '_internal'
$renamed = @()
try {
    foreach ($item in $items) {
        $old = Join-Path $Target "$item.old"
        if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Recurse -Force }
    }
    foreach ($item in $items) {
        Rename-Item -LiteralPath (Join-Path $Target $item) -NewName "$item.old"
        $renamed += $item
    }
    foreach ($item in $items) {
        Copy-Item -LiteralPath (Join-Path $Source $item) -Destination (Join-Path $Target $item) -Recurse -Force
    }
} catch {
    Write-Log "Échec de la mise à jour ($_) : retour à la version précédente."
    foreach ($item in $renamed) {
        $new = Join-Path $Target $item
        if (Test-Path -LiteralPath $new) { Remove-Item -LiteralPath $new -Recurse -Force -ErrorAction SilentlyContinue }
        Rename-Item -LiteralPath (Join-Path $Target "$item.old") -NewName $item
    }
    exit 1
}
foreach ($item in $items) {
    Remove-Item -LiteralPath (Join-Path $Target "$item.old") -Recurse -Force -ErrorAction SilentlyContinue
}
Remove-Item -LiteralPath (Split-Path $Source) -Recurse -Force -ErrorAction SilentlyContinue
# version affichée dans « Applications installées » quand Autowash vient de l'installeur
$uninstall = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{60D96E95-B24B-4216-865B-C393C3CAA293}_is1'
if (Test-Path $uninstall) {
    Set-ItemProperty -Path $uninstall -Name DisplayVersion -Value $Version -ErrorAction SilentlyContinue
}
# Windows garde l'icône de l'exe en cache : sans ça, les raccourcis (menu Démarrer, bureau, barre des tâches)
# montrent encore l'ancienne après une mise à jour qui la change.
try {
    Add-Type -Namespace Autowash -Name Shell -MemberDefinition '[DllImport("shell32.dll", CharSet = CharSet.Unicode)] public static extern void SHChangeNotify(int e, uint f, string a, IntPtr b);'
    $links = @($exe,
        (Join-Path ([Environment]::GetFolderPath('Programs')) 'Autowash.lnk'),
        (Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) 'Autowash.lnk'),
        (Join-Path $env:APPDATA 'Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\Autowash.lnk'))
    foreach ($link in $links) {
        if (Test-Path -LiteralPath $link) { [Autowash.Shell]::SHChangeNotify(0x2000, 0x3005, $link, [IntPtr]::Zero) }  # UPDATEITEM, PATHW|FLUSHNOWAIT
    }
    [Autowash.Shell]::SHChangeNotify(0x08000000, 0, $null, [IntPtr]::Zero)  # ASSOCCHANGED
    $ie4uinit = Join-Path $env:WINDIR 'System32\ie4uinit.exe'  # vide le cache d'icônes
    if (Test-Path -LiteralPath $ie4uinit) { Start-Process -FilePath $ie4uinit -ArgumentList '-show' -WindowStyle Hidden }
} catch {
    Write-Log "Icônes non rafraîchies ($_)."
}
Write-Log "Mise à jour $Version installée dans $Target."
if ($Relaunch) { Start-Process -FilePath $exe }
"""


def current_version() -> str:
    from . import __version__
    return __version__


def version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", text)[:3])


def install_dir() -> Path | None:
    """Dossier de la version .exe en cours ; None depuis les sources (mise à jour via git)."""
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else None


def log(message: str) -> None:
    with open(data_dir() / "update.log", "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {message}\n")


def latest() -> dict | None:
    """Dernière release si elle est plus récente que la version en cours."""
    with urllib.request.urlopen(urllib.request.Request(_API, headers=_HEADERS), timeout=20) as response:
        release = json.load(response)
    version = release["tag_name"].lstrip("v")
    asset = next((a for a in release.get("assets", []) if _ASSET.match(a["name"])), None)
    if asset is None or version_tuple(version) <= version_tuple(current_version()):
        return None
    return {"version": version, "url": asset["browser_download_url"], "digest": asset.get("digest") or ""}


def prepare(release: dict) -> Path:
    """Télécharge, vérifie et extrait la release ; renvoie le dossier Autowash prêt à copier."""
    folder = data_dir() / "updates"
    folder.mkdir(exist_ok=True)
    version = release["version"]
    staged = folder / version / "Autowash"
    if (staged / "Autowash.exe").exists():
        return staged  # déjà préparée lors d'un lancement précédent
    for old in folder.iterdir():  # versions préparées puis jamais installées
        if old.is_dir():
            shutil.rmtree(old, ignore_errors=True)
        elif old.suffix in (".zip", ".part"):
            old.unlink(missing_ok=True)

    partial = folder / f"{version}.part"
    digest = hashlib.sha256()
    with urllib.request.urlopen(urllib.request.Request(release["url"], headers=_HEADERS), timeout=60) as response, \
            open(partial, "wb") as out:
        while chunk := response.read(1024 * 1024):
            digest.update(chunk)
            out.write(chunk)
    expected = release["digest"].removeprefix("sha256:").lower()
    if not expected or digest.hexdigest() != expected:
        partial.unlink()
        raise RuntimeError("le fichier téléchargé ne correspond pas à l'empreinte publiée par GitHub")

    extracting = folder / f"{version}.tmp"
    shutil.rmtree(extracting, ignore_errors=True)
    with zipfile.ZipFile(partial) as archive:
        archive.extractall(extracting)
    partial.unlink()
    if not (extracting / "Autowash" / "Autowash.exe").exists() or not (extracting / "Autowash" / "_internal").is_dir():
        shutil.rmtree(extracting, ignore_errors=True)
        raise RuntimeError("archive inattendue : Autowash\\Autowash.exe introuvable")
    extracting.rename(staged.parent)
    return staged


def check_and_prepare() -> dict | None:
    """Nouvelle version téléchargée et prête à installer (clé « staged »), ou None."""
    if install_dir() is None:
        return None
    release = latest()
    if release:
        release["staged"] = prepare(release)
        log(f"Version {release['version']} téléchargée et vérifiée.")
    return release


def apply(staged: Path, relaunch: bool = False, wait_pid: int | None = None, target: Path | None = None) -> None:
    """Lance l'installation détachée : elle attend la fermeture d'Autowash, remplace les fichiers et relance si demandé."""
    target = target or install_dir()
    if target is None:
        raise RuntimeError("la mise à jour automatique concerne la version .exe")
    script = data_dir() / "updates" / "apply.ps1"
    script.write_text(_SCRIPT, encoding="utf-8-sig")  # BOM : PowerShell 5.1 lit sinon les accents en ANSI
    args = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(script),
            "-WaitPid", str(wait_pid or os.getpid()), "-Source", str(staged), "-Target", str(target),
            "-Log", str(data_dir() / "update.log"), "-Version", staged.parent.name]  # dossier préparé = n° de version
    if relaunch:
        args.append("-Relaunch")
    # cwd hors du dossier d'installation, sinon il ne pourrait pas être renommé
    subprocess.Popen(args, cwd=str(data_dir()), creationflags=CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP,
                     close_fds=True)


def just_updated() -> str | None:
    """Version précédente si Autowash a changé de version depuis le dernier lancement."""
    marker = data_dir() / "version.txt"
    previous = marker.read_text(encoding="utf-8").strip() if marker.exists() else None
    current = current_version()
    if previous != current:
        marker.write_text(current, encoding="utf-8")
    return previous if previous and previous != current else None
