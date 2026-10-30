"""Navigateurs (Chrome, Edge, Brave, Vivaldi, Opera) : extensions, mots de passe enregistrés, caches."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import ps
from .models import Action, Finding, Severity, run_checks
from .system import HKCU, HKLM, human_size, launch, reg_get

NAME = "Navigateurs"
DESCRIPTION = "Extensions installées, mots de passe enregistrés dans le navigateur et caches à vider."
BATCH_LABEL = "Vider la sélection"
TOOLS: list[Action] = []

_LOCAL, _ROAMING = os.environ.get("LOCALAPPDATA", ""), os.environ.get("APPDATA", "")


@dataclass
class Browser:
    name: str
    data: Path  # dossier « User Data » (ou le profil lui-même pour Opera)
    exe: str  # nom de l'exécutable, pour savoir s'il tourne et l'ouvrir
    scheme: str  # préfixe des pages internes (chrome://, edge://…)

    def profiles(self) -> list[Path]:
        if (self.data / "Preferences").exists():
            return [self.data]  # Opera : un seul profil, à la racine
        return sorted(p for p in self.data.glob("*")
                      if (p / "Preferences").exists() and p.name not in ("System Profile", "Guest Profile"))

    def open_page(self, page: str) -> str:
        path = reg_get(HKLM, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{self.exe}", "") \
            or reg_get(HKCU, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{self.exe}", "")
        if not path:
            raise RuntimeError(f"{self.name} introuvable.")
        return launch(path, f"{self.scheme}{page}")


BROWSERS = [b for b in (
    Browser("Google Chrome", Path(_LOCAL, "Google", "Chrome", "User Data"), "chrome.exe", "chrome://"),
    Browser("Microsoft Edge", Path(_LOCAL, "Microsoft", "Edge", "User Data"), "msedge.exe", "edge://"),
    Browser("Brave", Path(_LOCAL, "BraveSoftware", "Brave-Browser", "User Data"), "brave.exe", "brave://"),
    Browser("Vivaldi", Path(_LOCAL, "Vivaldi", "User Data"), "vivaldi.exe", "vivaldi://"),
    Browser("Opera", Path(_ROAMING, "Opera Software", "Opera Stable"), "opera.exe", "opera://"),
    Browser("Opera GX", Path(_ROAMING, "Opera Software", "Opera GX Stable"), "opera.exe", "opera://"),
) if b.data.is_dir()]


def _profile_label(browser: Browser, profile: Path) -> str:
    try:
        prefs = json.loads((profile / "Preferences").read_text("utf-8", errors="ignore"))
        name = prefs.get("profile", {}).get("name") or profile.name
    except (OSError, ValueError):
        name = profile.name
    return browser.name if len(browser.profiles()) == 1 else f"{browser.name} ({name})"


# --- Extensions -----------------------------------------------------------------

# Extensions très répandues et réputées : jamais signalées malgré leurs larges permissions.
TRUSTED_EXTENSIONS = {
    "cjpalhdlnbpafiamejdnhcphjbkeiagm",  # uBlock Origin
    "ddkjiahejlhfcafbddmgiahcphecmpfh",  # uBlock Origin Lite
    "odfafepnkmbhccpbejgmiehpchacaeak",  # uBlock Origin (Edge)
    "nngceckbapebfimnlniiiahkandclblb",  # Bitwarden
    "jbkfoedolllekgbhcbcoahefnbanhhlh",  # Bitwarden (Edge)
    "aeblfdkhhhdcdjpifhhbdiojplfjncoa",  # 1Password
    "hdokiejnpimakedhajhdlcegeplioahd",  # LastPass
    "fdjamakpfbbddfjaooikfcpapjohcfmg",  # Dashlane
    "oboonakemofpalcgghocfoadofidjkkk",  # KeePassXC-Browser
    "cfhdojbkjhnklbpkdaibdccddilifddb",  # Adblock Plus
    "gighmmpiobklfepjocnamgkkbiglidom",  # AdBlock
    "eimadpbcbfnmbkopoojfekhnkhdbieeh",  # Dark Reader
    "kbfnbcaeplbcioakkpcpgfkobkghlhen",  # Grammarly
    "ghbmnnjooekpmoecnnnilnnbdlolhkhi",  # Google Docs hors connexion
    "nmmhkkegccagdldgiimedpiccmgmieda",  # Paiements Chrome Web Store
    "aapocclcgogkmnckokdopfmhonfmgoek",  # Google Slides
    "felcaaldnbdncclmgdcncolpebgiejap",  # Google Sheets
    "pjkljhegncpnkpknbcohdijeoejaedia",  # Gmail
    "jmjflgjpcpepeafmmgdpfkogkghcpiha",  # Edge : modifications de texte
}
_BROAD_HOSTS = {"<all_urls>", "*://*/*", "http://*/*", "https://*/*"}
_SENSITIVE = {"webRequest", "webRequestBlocking", "proxy", "debugger", "management", "nativeMessaging", "cookies",
              "history", "declarativeNetRequestWithHostAccess", "clipboardRead", "privacy", "downloads"}
_STORE_LOCATIONS = {1, 5, 10}  # store, composant intégré, composant externe
_SIDELOADED_LOCATIONS = {2, 3, 4, 6, 7, 8}  # installées par un autre programme, en mode développeur ou par stratégie

_SENSITIVE_FR = {"webRequest": "voir et modifier le trafic", "webRequestBlocking": "bloquer/réécrire le trafic",
                 "proxy": "rediriger la connexion (proxy)", "debugger": "piloter les pages (débogueur)",
                 "management": "gérer les autres extensions", "nativeMessaging": "lancer des programmes sur le PC",
                 "cookies": "lire les cookies (sessions)", "history": "lire l'historique",
                 "declarativeNetRequestWithHostAccess": "réécrire le trafic", "clipboardRead": "lire le presse-papiers",
                 "privacy": "modifier les réglages de confidentialité", "downloads": "gérer les téléchargements"}


def _message(ext_dir: Path, manifest: dict, text: str) -> str:
    """Résout les noms traduits « __MSG_cle__ » des manifestes."""
    if not text.startswith("__MSG_"):
        return text
    key = text[6:-2].lower()
    for locale in ("fr", manifest.get("default_locale", "en"), "en", "en_US"):
        try:
            messages = json.loads((ext_dir / "_locales" / locale / "messages.json").read_text("utf-8-sig"))
        except (OSError, ValueError):
            continue
        for name, value in messages.items():
            if name.lower() == key:
                return value.get("message", text)
    return text


def _extension_settings(profile: Path) -> dict:
    settings = {}
    for file in ("Preferences", "Secure Preferences"):
        try:
            prefs = json.loads((profile / file).read_text("utf-8", errors="ignore"))
        except (OSError, ValueError):
            continue
        for ext_id, data in prefs.get("extensions", {}).get("settings", {}).items():
            settings.setdefault(ext_id, {}).update(data)
    return settings


def extensions() -> list[Finding]:
    """Extensions"""
    findings, quiet = [], 0
    for browser in BROWSERS:
        for profile in browser.profiles():
            settings = _extension_settings(profile)
            for ext_id_dir in (profile / "Extensions").glob("*"):
                versions = sorted(ext_id_dir.glob("*/manifest.json"))
                if not versions:
                    continue
                ext_id, manifest_path = ext_id_dir.name, versions[-1]
                try:
                    manifest = json.loads(manifest_path.read_text("utf-8-sig"))
                except (OSError, ValueError):
                    continue
                info = settings.get(ext_id, {})
                if info.get("location") in (5, 10) or ext_id in TRUSTED_EXTENSIONS:
                    quiet += 1
                    continue
                name = _message(manifest_path.parent, manifest, manifest.get("name", ext_id))
                perms = {p for p in manifest.get("permissions", []) + manifest.get("host_permissions", [])
                         if isinstance(p, str)}
                hosts = perms | {m for cs in manifest.get("content_scripts", []) for m in cs.get("matches", [])}
                broad, sensitive = bool(hosts & _BROAD_HOSTS), sorted(perms & _SENSITIVE)
                sideloaded = info.get("location") in _SIDELOADED_LOCATIONS and not info.get("from_webstore", True)
                disabled = bool(info.get("disable_reasons")) or info.get("state") == 0
                where = _profile_label(browser, profile)
                open_page = Action(f"Gérer dans {browser.name}",
                                   lambda b=browser, e=ext_id: b.open_page(f"extensions/?id={e}"),
                                   auto=False, batch=False)
                rights = ", ".join(_SENSITIVE_FR[p] for p in sensitive)
                if sideloaded and not disabled:
                    findings.append(Finding(
                        f"Extension installée hors du store : {name}", Severity.WARNING,
                        f"{where} · {ext_id}\nAjoutée par un programme ou en mode développeur, sans passer par le "
                        "store officiel : technique classique des adwares. Supprimez-la si vous ne l'avez pas "
                        "installée vous-même." + (f"\nPeut : {rights}." if rights else ""), [open_page]))
                elif broad and sensitive and not disabled:
                    findings.append(Finding(
                        f"Extension avec accès à tous les sites : {name}", Severity.INFO,
                        f"{where}\nPeut lire et modifier toutes les pages visitées, et aussi : {rights}. "
                        "Gardez-la seulement si vous l'utilisez et lui faites confiance.", [open_page]))
                else:
                    quiet += 1
    if not BROWSERS:
        return []
    findings.append(Finding(f"{quiet} extension(s) sans risque particulier", Severity.OK,
                            "Extensions du store aux permissions limitées, désactivées, ou très répandues."))
    return findings


# --- Mots de passe ----------------------------------------------------------------

def _saved_passwords(profile: Path) -> int:
    source = profile / "Login Data"
    if not source.exists():
        return 0
    with tempfile.TemporaryDirectory() as tmp:  # la base est verrouillée quand le navigateur est ouvert
        copy = Path(tmp, "logins.db")
        shutil.copyfile(source, copy)
        with sqlite3.connect(copy) as db:
            count = db.execute("SELECT COUNT(*) FROM logins WHERE blacklisted_by_user = 0").fetchone()[0]
        db.close()
    return count


def passwords() -> list[Finding]:
    """Mots de passe enregistrés"""
    findings = []
    for browser in BROWSERS:
        for profile in browser.profiles():
            count = _saved_passwords(profile)
            if count:
                findings.append(Finding(
                    f"{count} mot(s) de passe enregistré(s) dans {_profile_label(browser, profile)}", Severity.INFO,
                    "Ils sont chiffrés avec votre session Windows : n'importe quel programme lancé sous votre compte, "
                    "dont les malwares voleurs de mots de passe (infostealers), peut les récupérer. Un gestionnaire "
                    "dédié avec mot de passe maître (Bitwarden, KeePassXC…) est plus sûr ; exportez-les puis "
                    "supprimez-les du navigateur.",
                    [Action("Ouvrir les mots de passe",
                            lambda b=browser: b.open_page("password-manager/passwords" if b.scheme == "chrome://"
                                                          else "settings/passwords"),
                            auto=False, batch=False)]))
    return findings or ([Finding("Aucun mot de passe enregistré dans les navigateurs", Severity.OK)] if BROWSERS else [])


# --- Caches -----------------------------------------------------------------------

_CACHE_DIRS = ("Cache", "Code Cache", "GPUCache", "DawnGraphiteCache", "DawnWebGPUCache", os.path.join("Service Worker", "CacheStorage"))


def _dir_size(path: Path) -> int:
    total = 0
    for dirpath, _, files in os.walk(path, onerror=lambda e: None):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return total


def _is_running(exe: str) -> bool:
    return ps.run(f"@(Get-Process -Name {ps.quote(exe.removesuffix('.exe'))} -ErrorAction SilentlyContinue).Count") != "0"


def _clear_cache(browser: Browser):
    def go() -> str:
        if _is_running(browser.exe):
            raise RuntimeError(f"Fermez {browser.name} (toutes ses fenêtres) puis relancez le nettoyage.")
        freed = 0
        for profile in browser.profiles():
            for name in _CACHE_DIRS:
                folder = profile / name
                if folder.is_dir():
                    freed += _dir_size(folder)
                    shutil.rmtree(folder, ignore_errors=True)
        return f"Cache de {browser.name} vidé : {human_size(freed)} libérés (historique, mots de passe et sessions conservés)."
    return go


def caches() -> list[Finding]:
    """Caches"""
    findings = []
    for browser in BROWSERS:
        size = sum(_dir_size(p / name) for p in browser.profiles() for name in _CACHE_DIRS)
        clear = Action("Vider le cache", _clear_cache(browser))
        if size > 500 * 1024**2:
            findings.append(Finding(f"Cache de {browser.name} : {human_size(size)}", Severity.INFO,
                                    "Fichiers de pages déjà visitées. Les vider libère de la place sans toucher à "
                                    "l'historique, aux mots de passe ni aux sessions (navigateur fermé).", [clear]))
        else:
            findings.append(Finding(f"Cache de {browser.name} : {human_size(size)}", Severity.OK, "", [clear]))
    return findings


def scan() -> list[Finding]:
    if not BROWSERS:
        return [Finding("Aucun navigateur Chromium détecté (Chrome, Edge, Brave, Vivaldi, Opera)", Severity.OK)]
    return run_checks([extensions, passwords, caches], NAME)
