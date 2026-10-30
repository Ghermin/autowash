"""Pilotes : mises à jour proposées par Windows Update, pilote de la carte graphique, pilotes très anciens."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

from . import ps
from .models import Action, Finding, Severity, run_checks
from .system import data_dir, human_size, launch, open_target
from .updates import install_windows_updates, search_windows_updates

NAME = "Pilotes"
DESCRIPTION = "Pilotes proposés par Windows Update, dernier pilote de la carte graphique et pilotes très anciens."
BATCH_LABEL = "Installer la sélection"

TOOLS = [
    Action("Mises à jour facultatives", lambda: open_target("ms-settings:windowsupdate-optionalupdates"),
           auto=False, batch=False),
    Action("Gestionnaire de périphériques", lambda: launch("mmc", "devmgmt.msc"), auto=False, batch=False),
]

OLD_DRIVER_YEARS = 4
_USER_AGENT = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Autowash"}


# --- Windows Update -------------------------------------------------------------

def windows_update_drivers() -> list[Finding]:
    """Pilotes via Windows Update"""
    drivers = [u for u in search_windows_updates() if u["Driver"]]
    if not drivers:
        return [Finding("Aucun pilote en attente dans Windows Update", Severity.OK)]
    return [Finding(u["Title"], Severity.INFO, f"Pilote proposé par Windows Update · {human_size(u['Size'])}",
                    [Action("Installer", install_windows_updates([u["Id"]]), admin=True, auto=False)])
            for u in drivers]


# --- Carte graphique ---------------------------------------------------------------

GPU_PS = r"""
Get-CimInstance Win32_PnPSignedDriver -Filter "DeviceClass='DISPLAY'" | Where-Object DeviceName | ForEach-Object {
  [pscustomobject]@{ Name = $_.DeviceName; Version = $_.DriverVersion; Provider = [string]$_.DriverProviderName
                     Date = $(if ($_.DriverDate) { $_.DriverDate.ToString('yyyy-MM-dd') } else { '' }) }
}
"""


def _http(url: str, timeout: int = 20) -> str:
    request = urllib.request.Request(url, headers=_USER_AGENT)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _retry(func, attempts: int = 3):
    """Le service de NVIDIA renvoie parfois une page d'erreur : on réessaie avant d'abandonner."""
    for attempt in range(attempts):
        try:
            return func()
        except (OSError, ValueError, ET.ParseError):
            if attempt == attempts - 1:
                raise
            time.sleep(1.5)


def _nvidia_lookup(type_id: int, parent: int | None = None) -> dict[str, int]:
    url = f"https://www.nvidia.com/Download/API/lookupValueSearch.aspx?TypeID={type_id}"
    if parent is not None:
        url += f"&ParentID={parent}"
    root = ET.fromstring(_retry(lambda: _http(url)).encode())
    return {v.findtext("Name"): int(v.findtext("Value")) for v in root.iter("LookupValue")}


def _nvidia_ids(gpu: str) -> tuple[int, int]:
    """Identifiants NVIDIA (série, produit) d'une carte, mis en cache car le service est lent et capricieux."""
    cache_file = data_dir() / "nvidia.json"
    cache = json.loads(cache_file.read_text("utf-8")) if cache_file.exists() else {}
    if gpu in cache:
        return tuple(cache[gpu])
    product = gpu.replace("NVIDIA ", "").strip()
    match = re.search(r"(RTX|GTX)\s*(\d{2})\d{2}", product)
    if not match:
        raise LookupError(f"Série NVIDIA non reconnue pour {gpu}")
    notebook = "Laptop" in product
    wanted = f"GeForce {match.group(1)} {match.group(2)} Series" + (" (Notebooks)" if notebook else "")
    series = _nvidia_lookup(2)
    psid = series.get(wanted)
    if psid is None:
        raise LookupError(f"Série « {wanted} » introuvable chez NVIDIA")
    products = _nvidia_lookup(3, psid)
    pfid = products.get(product) or products.get(product.replace(" Laptop GPU", ""))
    if pfid is None:
        raise LookupError(f"« {product} » introuvable chez NVIDIA")
    cache[gpu] = [psid, pfid]
    cache_file.write_text(json.dumps(cache), "utf-8")
    return psid, pfid


def _nvidia_latest(psid: int, pfid: int) -> dict:
    query = urllib.parse.urlencode({"func": "DriverManualLookup", "psid": psid, "pfid": pfid, "osID": 57,
                                    "languageCode": 1036, "isWHQL": 1, "dch": 1, "upCRD": 0, "sort1": 0,
                                    "numberOfResults": 1})
    url = f"https://gfwsl.geforce.com/services_toolkit/services/com/nvidia/services/AjaxDriverService.php?{query}"
    info = json.loads(_retry(lambda: _http(url)))["IDS"][0]["downloadInfo"]
    return {"version": info["Version"], "url": info["DownloadURL"], "size": _parse_size(info.get("DownloadURLFileSize")),
            "details": info.get("DetailsURL", ""), "date": urllib.parse.unquote(info.get("ReleaseDateTime", ""))}


def _parse_size(text: str | None) -> int:
    """« 1.04 GB » -> octets (approximatif : sert seulement à l'affichage)."""
    match = re.match(r"([\d.,]+)\s*([KMG]?B)", text or "", re.I)
    if not match:
        return 0
    return int(float(match.group(1).replace(",", ".")) * {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}[match.group(2).upper()])


def _nvidia_version(windows_version: str) -> str:
    """Version Windows du pilote (32.0.16.1742) -> version NVIDIA (617.42) : les 5 derniers chiffres."""
    digits = "".join(windows_version.split(".")[-2:])[-5:]
    return f"{digits[:3]}.{digits[3:]}"


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", version))


def _install_nvidia(latest: dict):
    def go() -> str:
        folder = data_dir() / "downloads"
        folder.mkdir(exist_ok=True)
        target = folder / os.path.basename(urllib.parse.urlparse(latest["url"]).path)
        if not target.exists():  # un téléchargement interrompu reste en .part et sera repris de zéro
            partial = target.with_suffix(".part")
            request = urllib.request.Request(latest["url"], headers=_USER_AGENT)
            with urllib.request.urlopen(request, timeout=60) as response, open(partial, "wb") as out:
                while chunk := response.read(1024 * 1024):
                    out.write(chunk)
            partial.replace(target)
        signature = ps.run_obj(f"Get-AuthenticodeSignature -LiteralPath {ps.quote(str(target))} | Select-Object "
                               "@{n='Status';e={[string]$_.Status}}, @{n='Signer';e={$_.SignerCertificate.Subject}}")
        if not signature or signature["Status"] != "Valid" or "NVIDIA" not in (signature["Signer"] or ""):
            target.unlink(missing_ok=True)
            raise RuntimeError("Le fichier téléchargé n'est pas signé par NVIDIA : il a été supprimé par sécurité.")
        os.startfile(str(target))  # l'installeur NVIDIA demande lui-même les droits administrateur
        return (f"Installeur NVIDIA {latest['version']} lancé (signature NVIDIA vérifiée). Choisissez « Installation "
                "express » ; l'écran clignotera pendant l'installation.")
    return go


def graphics() -> list[Finding]:
    """Carte graphique"""
    findings = []
    for gpu in ps.run_list(GPU_PS):
        name, provider = gpu["Name"], gpu["Provider"].upper()
        if "NVIDIA" in provider or "NVIDIA" in name.upper():
            installed = _nvidia_version(gpu["Version"])
            try:
                latest = _nvidia_latest(*_nvidia_ids(name))
            except Exception as exc:
                findings.append(Finding(f"{name} : pilote {installed}", Severity.INFO,
                                        f"Vérification du dernier pilote impossible ({exc}).",
                                        [Action("Page des pilotes NVIDIA",
                                                lambda: open_target("https://www.nvidia.com/fr-fr/drivers/"),
                                                auto=False, batch=False)]))
                continue
            if _version_tuple(latest["version"]) > _version_tuple(installed):
                notes = Action("Notes de version", lambda u=latest["details"]: open_target(u), auto=False, batch=False)
                findings.append(Finding(
                    f"Nouveau pilote NVIDIA {latest['version']} pour {name.replace('NVIDIA ', '')}", Severity.WARNING,
                    f"Installé : {installed} ({gpu['Date']}) · Disponible : {latest['version']} ({latest['date']}) · "
                    f"{human_size(latest['size'])}\nCorrectifs de sécurité, de stabilité et optimisations pour les "
                    "jeux récents. Téléchargé depuis le serveur officiel de NVIDIA, signature vérifiée avant lancement.",
                    [Action("Télécharger et installer", _install_nvidia(latest), auto=False,
                            confirm=f"Télécharger le pilote NVIDIA {latest['version']} "
                                    f"({human_size(latest['size'])}) puis lancer son installeur ?"), notes]))
            else:
                findings.append(Finding(f"Pilote NVIDIA à jour ({installed})", Severity.OK, name))
        elif "AMD" in provider or "ADVANCED MICRO" in provider or "RADEON" in name.upper():
            findings.append(Finding(f"{name} : pilote {gpu['Version']} ({gpu['Date']})", Severity.INFO,
                                    "Utilisez l'outil de détection d'AMD pour obtenir le dernier pilote Adrenalin.",
                                    [Action("Pilotes AMD", lambda: open_target(
                                        "https://www.amd.com/fr/support/download/drivers.html"), auto=False, batch=False)]))
        elif "INTEL" in provider:
            findings.append(Finding(f"{name} : pilote {gpu['Version']} ({gpu['Date']})", Severity.INFO,
                                    "Intel Driver & Support Assistant détecte et installe les derniers pilotes Intel.",
                                    [Action("Assistant Intel", lambda: open_target(
                                        "https://www.intel.fr/content/www/fr/fr/support/detect.html"), auto=False, batch=False)]))
    return findings


# --- Pilotes anciens -----------------------------------------------------------------

OLD_PS = r"""
Get-CimInstance Win32_PnPSignedDriver | Where-Object {
  $_.DeviceName -and $_.DriverDate -and $_.DriverProviderName -and $_.DriverProviderName -notmatch 'Microsoft'
} | ForEach-Object {
  [pscustomobject]@{ Name = $_.DeviceName; Class = [string]$_.DeviceClass; Provider = $_.DriverProviderName
                     Version = $_.DriverVersion; Date = $_.DriverDate.ToString('yyyy-MM-dd') }
}
"""
_IMPORTANT_CLASSES = {"NET", "BLUETOOTH", "MEDIA", "HDC", "SCSIADAPTER", "USB", "SYSTEM", "AUDIOENDPOINT", "DISPLAY"}


def old_drivers() -> list[Finding]:
    """Pilotes anciens"""
    limit = datetime.now().year - OLD_DRIVER_YEARS
    old, seen = [], set()
    for d in ps.run_list(OLD_PS, timeout=180):
        if d["Class"].upper() in _IMPORTANT_CLASSES and int(d["Date"][:4]) < limit and d["Name"] not in seen:
            seen.add(d["Name"])
            old.append(d)
    if not old:
        return [Finding(f"Aucun pilote de fabricant de plus de {OLD_DRIVER_YEARS} ans", Severity.OK)]
    old.sort(key=lambda d: d["Date"])
    lines = "\n".join(f"{d['Name']} — {d['Provider']} {d['Version']} ({d['Date'][:4]})" for d in old[:12])
    return [Finding(f"{len(old)} pilote(s) de plus de {OLD_DRIVER_YEARS} ans", Severity.INFO,
                    lines + "\nS'ils fonctionnent bien, rien n'oblige à les changer. Sinon, cherchez une version "
                            "récente dans les mises à jour facultatives ou sur le site du fabricant (carte mère, "
                            "carte réseau…).", [TOOLS[0]])]


def scan() -> list[Finding]:
    return run_checks([graphics, windows_update_drivers, old_drivers], NAME)
