"""Nettoyage : fichiers temporaires, restes de programmes désinstallés, dossiers vides, ancien Windows."""
from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path

from . import ps
from .models import Action, Finding, Severity, run_checks
from .system import HKCU, human_size, launch, open_target, recycle, reg_get

NAME = "Nettoyage"
DESCRIPTION = ("Fichiers temporaires, restes de programmes, dossiers vides, gros fichiers, doublons et ancienne "
               "installation de Windows.")
BATCH_LABEL = "Nettoyer la sélection"

_ENV = os.environ.get
PROGRAM_ROOTS = [r for r in (_ENV("ProgramFiles"), _ENV("ProgramFiles(x86)"),
                             os.path.join(_ENV("LOCALAPPDATA", ""), "Programs")) if r and os.path.isdir(r)]
DATA_ROOTS = [r for r in (_ENV("APPDATA"), _ENV("LOCALAPPDATA"), _ENV("ProgramData")) if r and os.path.isdir(r)]
_ADMIN_ROOTS = tuple(p.lower() for p in (_ENV("ProgramFiles"), _ENV("ProgramFiles(x86)"), _ENV("ProgramData"),
                                         _ENV("SystemDrive", "C:") + "\\Windows.old") if p)

# Dossiers fournis par Windows ou partagés entre programmes : jamais considérés comme des restes.
_SYSTEM_FOLDERS = {
    "commonfiles", "common", "dotnet", "internetexplorer", "modifiablewindowsapps", "msbuild", "referenceassemblies",
    "uninstallinformation", "packagemanagement", "packagecache", "packages", "temp", "programs", "history",
    "connecteddevicesplatform", "d3dscache", "crashdumps", "virtualstore", "comms", "publishers",
    "placeholdertilelogofolder", "toastnotificationmanagercompat", "elevateddiagnostics", "squirreltemp",
    "softwaredistribution", "usoprivate", "usoshared", "ssh", "whesvc", "propagation", "onedrive", "autowash",
}
_STALE_DAYS = 180
_SCAN_LIMIT = 50_000  # au-delà, le dossier est jugé actif plutôt que d'être parcouru en entier

_FILE_ATTRIBUTE_HIDDEN, _FILE_ATTRIBUTE_SYSTEM, _FILE_ATTRIBUTE_REPARSE_POINT = 0x2, 0x4, 0x400


def _norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _is_system(name: str) -> bool:
    folder = _norm(name)
    return folder in _SYSTEM_FOLDERS or folder.startswith(("windows", "microsoft"))


def _walk_files(path: str):
    """Fichiers d'un dossier, récursivement, sans suivre les jonctions.

    Sous Windows, DirEntry.stat() réutilise les infos de l'énumération : beaucoup plus rapide qu'os.walk + os.stat.
    """
    stack = [path]
    while stack:
        try:
            entries = os.scandir(stack.pop())
        except OSError:
            continue
        with entries:
            for entry in entries:
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        yield entry
                    elif not entry.stat(follow_symlinks=False).st_file_attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                        stack.append(entry.path)
                except OSError:
                    pass


def _needs_admin(path: str) -> bool:
    return path.lower().startswith(_ADMIN_ROOTS)


def _subfolders(root: str) -> list[os.DirEntry]:
    """Sous-dossiers directs, hors dossiers cachés, système et liens (jonctions)."""
    try:
        entries = list(os.scandir(root))
    except OSError:
        return []
    skip = _FILE_ATTRIBUTE_HIDDEN | _FILE_ATTRIBUTE_SYSTEM | _FILE_ATTRIBUTE_REPARSE_POINT
    return [e for e in entries
            if e.is_dir(follow_symlinks=False) and not e.stat(follow_symlinks=False).st_file_attributes & skip]


def _folder_stats(path: str) -> tuple[int, int, float, bool] | None:
    """(taille, nb de fichiers, dernière modification, contient un .exe) ou None si trop gros pour être parcouru."""
    size = count = 0
    newest = os.stat(path).st_mtime
    has_exe = False
    for entry in _walk_files(path):
        count += 1
        if count > _SCAN_LIMIT:
            return None
        stat = entry.stat(follow_symlinks=False)
        size += stat.st_size
        newest = max(newest, stat.st_mtime)
        has_exe = has_exe or entry.name.lower().endswith(".exe")
    return size, count, newest, has_exe


# --- Fichiers temporaires --------------------------------------------------

def _temp_dirs() -> list[Path]:
    dirs = [_ENV("TEMP"), r"C:\Windows\Temp", os.path.join(_ENV("LOCALAPPDATA", ""), "CrashDumps")]
    return [Path(d) for d in dirs if d and os.path.isdir(d)]


def _temp_size() -> int:
    return sum(entry.stat(follow_symlinks=False).st_size for root in _temp_dirs() for entry in _walk_files(str(root)))


def clean_temp() -> str:
    """Supprime les fichiers temporaires de plus de 24 h (ceux en cours d'utilisation sont ignorés)."""
    limit, freed, count = time.time() - 86400, 0, 0
    for root in _temp_dirs():
        for dirpath, _, files in os.walk(root, topdown=False, onerror=lambda e: None):
            for name in files:
                path = os.path.join(dirpath, name)
                try:
                    stat = os.stat(path)
                    if stat.st_mtime < limit:
                        os.remove(path)
                        freed += stat.st_size
                        count += 1
                except OSError:
                    pass
            if Path(dirpath) != root:
                try:
                    os.rmdir(dirpath)  # échoue si le dossier n'est pas vide
                except OSError:
                    pass
    return f"{count} fichiers temporaires supprimés, {human_size(freed)} libérés."


CLEAN_TEMP = Action("Nettoyer les fichiers temporaires", clean_temp)
STORAGE_SENSE = Action("Assistant de stockage", lambda: open_target("ms-settings:storagesense"), auto=False, batch=False)

TOOLS = [CLEAN_TEMP, STORAGE_SENSE]


def temp_files() -> list[Finding]:
    """Fichiers temporaires"""
    size = _temp_size()
    if size > 2 * 1024**3:
        return [Finding(f"{human_size(size)} de fichiers temporaires", Severity.WARNING,
                        "Fichiers inutiles laissés par Windows et les applications.", [CLEAN_TEMP])]
    if size > 300 * 1024**2:
        return [Finding(f"{human_size(size)} de fichiers temporaires", Severity.INFO,
                        "Peuvent être supprimés sans risque.", [CLEAN_TEMP])]
    return [Finding(f"Fichiers temporaires : {human_size(size)}", Severity.OK)]


# --- Restes de programmes ----------------------------------------------------

REFERENCES_PS = r"""
$keys = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
$names = @(); $paths = @()
foreach ($e in Get-ItemProperty $keys -ErrorAction SilentlyContinue) {
  if (-not $e.DisplayName) { continue }
  $names += [string]$e.DisplayName, [string]$e.Publisher
  $paths += [string]$e.InstallLocation, [string]$e.DisplayIcon, [string]$e.UninstallString
}
$names += Get-AppxPackage | ForEach-Object { $_.Name }
$paths += Get-CimInstance Win32_Service | ForEach-Object { [string]$_.PathName }
$paths += Get-CimInstance Win32_SystemDriver | ForEach-Object { [string]$_.PathName }
$paths += Get-Process | ForEach-Object { [string]$_.Path }
$paths += Get-ScheduledTask | ForEach-Object { $_.Actions | ForEach-Object { [string]$_.Execute } }
[pscustomobject]@{ Names = @($names | Where-Object { $_ }); Paths = @($paths | Where-Object { $_ }) }
"""


def _installed_references() -> tuple[list[str], list[str]]:
    """Noms (normalisés) et chemins (minuscules) de tout ce qui est installé ou en cours d'exécution."""
    refs = ps.run_obj(REFERENCES_PS, timeout=240) or {"Names": [], "Paths": []}
    names = [n for n in (_norm(x) for x in refs["Names"]) if len(n) >= 3]
    paths = [os.path.expandvars(p).strip().strip('"').lower() for p in refs["Paths"]]
    # outils en ligne de commande sans désinstalleur (chocolatey, scoop…) : présents dans le PATH
    paths += [p.strip().rstrip("\\").lower() + "\\" for p in _ENV("PATH", "").split(";") if p.strip()]
    return names, paths


def _is_referenced(entry: os.DirEntry, names: list[str], paths: list[str]) -> bool:
    folder = _norm(entry.name)
    if _is_system(entry.name):
        return True
    prefix = entry.path.lower() + "\\"
    if any(p.startswith(prefix) or ('"' + prefix) in p or (" " + prefix) in p for p in paths):
        return True
    return len(folder) >= 3 and any(folder in n or (len(n) >= 4 and n in folder) for n in names)


def _recycle_action(path: str, label: str = "Mettre à la corbeille"):
    def go() -> str:
        recycle(path)
        return f"{path} déplacé dans la corbeille."
    return Action(label, go, admin=_needs_admin(path), auto=False, undo={"kind": "recycle", "paths": [path]},
                  confirm=f"Déplacer ce dossier dans la corbeille ?\n\n{path}\n\n"
                          "Vous pourrez le restaurer depuis la corbeille si un programme en avait besoin.")


def leftovers() -> list[Finding]:
    """Restes de programmes désinstallés"""
    names, paths = _installed_references()
    now, found = time.time(), []  # (taille, constat)

    for root in PROGRAM_ROOTS:
        for entry in _subfolders(root):
            if _is_referenced(entry, names, paths):
                continue
            stats = _folder_stats(entry.path)
            if not stats or not stats[1]:
                continue  # trop gros pour être un reste, ou vide (traité par « Dossiers vides »)
            size, count, _, has_exe = stats
            if has_exe:
                title = f"Programme sans désinstalleur : {entry.name}"
                detail = (f"{entry.path} — {human_size(size)}, {count} fichiers\nContient des programmes mais "
                          "n'est rattaché à aucune application installée : reste d'une désinstallation ratée, "
                          "logiciel portable, ou programme installé à votre insu. Supprimez-le si vous ne le reconnaissez pas.")
            else:
                title = f"Reste d'un programme désinstallé : {entry.name}"
                detail = f"{entry.path} — {human_size(size)}, {count} fichiers (sans programme exécutable)."
            found.append((size, Finding(title, Severity.INFO, detail, [_recycle_action(entry.path)])))

    for root in DATA_ROOTS:
        for entry in _subfolders(root):
            if _is_referenced(entry, names, paths):
                continue
            stats = _folder_stats(entry.path)
            if not stats or not stats[1]:
                continue
            size, count, newest, _ = stats
            days = (now - newest) / 86400
            if days < _STALE_DAYS or size < 1024**2:
                continue  # encore utilisé, ou trop petit pour valoir le coup
            found.append((size, Finding(
                f"Données d'application abandonnées : {entry.name}", Severity.INFO,
                f"{entry.path} — {human_size(size)}, inutilisé depuis {days / 30:.0f} mois\n"
                "Aucun programme installé ne correspond à ce dossier (paramètres, caches ou profils d'une application "
                "désinstallée). Vérifiez qu'il ne contient rien à conserver avant de le supprimer.",
                [_recycle_action(entry.path)])))

    if not found:
        return [Finding("Aucun reste de programme désinstallé", Severity.OK)]
    found.sort(key=lambda item: -item[0])
    summary = Finding(f"{len(found)} reste(s) de programmes, {human_size(sum(s for s, _ in found))} au total",
                      Severity.INFO, "Dossiers qui ne correspondent à aucun programme installé. Chacun peut être "
                                     "déplacé dans la corbeille ci-dessous, après vérification.")
    return [summary] + [finding for _, finding in found]


# --- Dossiers vides -------------------------------------------------------------

def _empty_folders() -> list[str]:
    return [e.path for root in PROGRAM_ROOTS + DATA_ROOTS for e in _subfolders(root)
            if not _is_system(e.name) and next(_walk_files(e.path), None) is None]


def _remove_empty(folders: list[str]):
    def go() -> str:
        removed = 0
        for folder in folders:
            for dirpath, _, _ in os.walk(folder, topdown=False, onerror=lambda e: None):
                try:
                    os.rmdir(dirpath)  # ne supprime que des dossiers vides
                    removed += dirpath == folder
                except OSError:
                    pass
        return f"{removed} dossier(s) vide(s) supprimé(s) sur {len(folders)}."
    return go


def empty_folders() -> list[Finding]:
    """Dossiers vides"""
    folders = _empty_folders()
    if not folders:
        return [Finding("Aucun dossier de programme vide", Severity.OK)]
    return [Finding(f"{len(folders)} dossier(s) de programmes vide(s)", Severity.INFO,
                    "\n".join(folders[:15]) + (f"\n… et {len(folders) - 15} autre(s)" if len(folders) > 15 else ""),
                    [Action("Supprimer les dossiers vides", _remove_empty(folders),
                            admin=any(_needs_admin(f) for f in folders), auto=False)])]


# --- Gros fichiers et doublons -----------------------------------------------------

_SHELL_FOLDERS = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
_USER_FOLDERS = ("Desktop", "Personal", "My Pictures", "My Music", "My Video",
                 "{374DE290-123F-4565-9164-39C4925E467B}")  # Bureau, Documents, Images, Musique, Vidéos, Téléchargements
_CLOUD_ONLY = 0x1000 | 0x40000 | 0x400000  # fichiers OneDrive non téléchargés : les lire les téléchargerait
LARGE_FILE = 500 * 1024**2
DUPLICATE_MIN = 5 * 1024**2
# Doublons cherchés seulement parmi les contenus personnels : une DLL ou un fichier de projet en double est
# souvent voulu (jeux, code), et le supprimer casserait le programme qui l'attend à cet endroit.
DUPLICATE_TYPES = {".jpg", ".jpeg", ".png", ".gif", ".heic", ".webp", ".bmp", ".tif", ".tiff", ".raw", ".cr2", ".nef",
                   ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".mp3", ".flac", ".wav", ".m4a", ".ogg", ".aac",
                   ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".epub",
                   ".zip", ".rar", ".7z", ".iso", ".msi", ".exe"}
_PROJECT_MARKERS = ("\\.git\\", "\\node_modules\\", "\\.venv\\", "\\venv\\", "\\steamapps\\", "\\.claude\\")


def _user_folders() -> list[str]:
    folders = []
    for name in _USER_FOLDERS:
        path = reg_get(HKCU, _SHELL_FOLDERS, name)
        if path and os.path.isdir(os.path.expandvars(path)):
            path = os.path.normcase(os.path.expandvars(path))
            if not any(path.startswith(f + os.sep) or path == f for f in folders):
                folders.append(path)
    return folders


def _hash(path: str, partial: bool) -> str | None:
    digest = hashlib.blake2b(digest_size=20)
    try:
        with open(path, "rb") as f:
            if partial:  # début et fin du fichier : écarte vite les fichiers différents de même taille
                digest.update(f.read(65536))
                f.seek(max(0, os.path.getsize(path) - 65536))
                digest.update(f.read(65536))
            else:
                while chunk := f.read(1024 * 1024):
                    digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _group_by(paths: list[str], key) -> list[list[str]]:
    groups: dict = {}
    for path in paths:
        k = key(path)
        if k is not None:
            groups.setdefault(k, []).append(path)
    return [g for g in groups.values() if len(g) > 1]


def _keep_one(paths: list[str]):
    """Garde la copie la plus ancienne (souvent l'originale) et met les autres à la corbeille."""
    keep = min(paths, key=os.path.getmtime)
    extra = [p for p in paths if p != keep]

    def go() -> str:
        for path in extra:
            recycle(path)
        return f"{len(extra)} copie(s) mise(s) à la corbeille, conservée : {keep}"
    return Action("Garder une seule copie", go, auto=False, undo={"kind": "recycle", "paths": extra},
                  confirm=f"Conserver :\n{keep}\n\nMettre à la corbeille :\n" + "\n".join(extra))


def user_files() -> list[Finding]:
    """Gros fichiers et doublons"""
    deadline = time.time() + 60
    large, by_size = [], {}
    for folder in _user_folders():
        for entry in _walk_files(folder):
            stat = entry.stat(follow_symlinks=False)
            if stat.st_file_attributes & _CLOUD_ONLY or stat.st_size < DUPLICATE_MIN:
                continue
            path = entry.path.lower()
            if os.path.splitext(path)[1] in DUPLICATE_TYPES and not any(m in path for m in _PROJECT_MARKERS) \
                    and not (os.path.splitext(path)[1] == ".exe" and "\\downloads\\" not in path
                             and "\\desktop\\" not in path):  # un .exe hors Téléchargements/Bureau fait partie d'un programme
                by_size.setdefault(stat.st_size, []).append(entry.path)
            if stat.st_size >= LARGE_FILE:
                large.append((stat.st_size, stat.st_mtime, entry.path))
            if time.time() > deadline:
                break

    findings = []
    for size, mtime, path in sorted(large, reverse=True)[:15]:
        findings.append(Finding(
            f"{os.path.basename(path)} — {human_size(size)}", Severity.INFO,
            f"{path}\nModifié le {time.strftime('%d/%m/%Y', time.localtime(mtime))}",
            [_recycle_action(path), Action("Afficher dans l'Explorateur", lambda p=path: launch("explorer", f"/select,{p}"),
                                           auto=False, batch=False)]))
    if large:
        findings.insert(0, Finding(f"{len(large)} gros fichier(s) de plus de {human_size(LARGE_FILE)}, "
                                   f"{human_size(sum(s for s, _, _ in large))} au total", Severity.INFO,
                                   "Dans vos dossiers personnels (Bureau, Documents, Téléchargements, Images, Vidéos, "
                                   "Musique). Les 15 plus gros sont listés ci-dessous."))

    duplicates = []
    for same_size in (g for g in by_size.values() if len(g) > 1):
        for candidates in _group_by(same_size, lambda p: _hash(p, partial=True)):
            if time.time() > deadline:
                break
            duplicates += _group_by(candidates, lambda p: _hash(p, partial=False))
    if duplicates:
        duplicates.sort(key=lambda g: -os.path.getsize(g[0]) * (len(g) - 1))
        wasted = sum(os.path.getsize(g[0]) * (len(g) - 1) for g in duplicates)
        findings.append(Finding(f"{len(duplicates)} fichier(s) en double, {human_size(wasted)} récupérables",
                                Severity.INFO, "Contenu strictement identique (vérifié octet par octet). "
                                               "Les 20 plus coûteux sont listés ci-dessous."))
        for group in duplicates[:20]:
            findings.append(Finding(
                f"{len(group)} copies de « {os.path.basename(group[0])} » ({human_size(os.path.getsize(group[0]))} chacune)",
                Severity.INFO, "\n".join(group), [_keep_one(group)]))
    return findings or [Finding("Aucun gros fichier ni doublon dans vos dossiers personnels", Severity.OK)]


# --- Ancienne installation de Windows ----------------------------------------------

def windows_old() -> list[Finding]:
    """Ancienne installation de Windows"""
    path = _ENV("SystemDrive", "C:") + "\\Windows.old"
    if not os.path.isdir(path):
        return []
    size, deadline = 0, time.time() + 5  # dizaines de Go : une estimation partielle suffit
    for entry in _walk_files(path):
        size += entry.stat(follow_symlinks=False).st_size
        if time.time() > deadline:
            break
    age = (time.time() - os.stat(path).st_mtime) / 86400
    rollback = ("Windows permet de revenir à la version précédente pendant 10 jours après la mise à jour : "
                "attendez d'être sûr que tout fonctionne. " if age < 10 else "")
    return [Finding(f"Ancienne installation de Windows : au moins {human_size(size)}", Severity.INFO,
                    f"{path}\n{rollback}Pour la supprimer : Assistant de stockage > Fichiers temporaires > "
                    "« Installation(s) précédente(s) de Windows ».", [STORAGE_SENSE])]


def scan() -> list[Finding]:
    return run_checks([temp_files, windows_old, leftovers, empty_folders, user_files], NAME)
