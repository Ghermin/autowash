"""Vulnérabilités : support de Windows, pare-feu, UAC, protocoles obsolètes, réglages de sécurité."""
from __future__ import annotations

from datetime import date

from . import journal, ps
from .models import Action, Finding, Severity, run_checks
from .system import HKLM, open_target, reg_exists, reg_get, reg_set_dword

NAME = "Vulnérabilités"
DESCRIPTION = "Version de Windows encore supportée, pare-feu, UAC, protocoles obsolètes et réglages de sécurité."

TOOLS = [
    Action("Ouvrir Sécurité Windows", lambda: open_target("windowsdefender:"), auto=False, batch=False),
]

_NT = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
_POLICIES = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
_TS = r"SYSTEM\CurrentControlSet\Control\Terminal Server"

# build -> (version, fin de support Famille/Pro, fin de support Entreprise/Éducation)
WINDOWS_RELEASES = {
    19045: ("Windows 10 22H2", date(2025, 10, 14), date(2025, 10, 14)),
    22000: ("Windows 11 21H2", date(2023, 10, 10), date(2024, 10, 8)),
    22621: ("Windows 11 22H2", date(2024, 10, 8), date(2025, 10, 14)),
    22631: ("Windows 11 23H2", date(2025, 11, 11), date(2026, 11, 10)),
    26100: ("Windows 11 24H2", date(2026, 10, 13), date(2027, 10, 12)),
    26200: ("Windows 11 25H2", date(2027, 10, 12), date(2028, 10, 10)),
}


def _open_windows_update() -> str:
    return open_target("ms-settings:windowsupdate")


def windows_support() -> list[Finding]:
    """Support de Windows"""
    build = int(reg_get(HKLM, _NT, "CurrentBuildNumber", "0"))
    edition = reg_get(HKLM, _NT, "EditionID", "") or ""
    if "EnterpriseS" in edition or "IoT" in edition:
        return [Finding(f"Windows LTSC/IoT (build {build})", Severity.OK, "Édition à support long terme.")]
    if build > max(WINDOWS_RELEASES):
        return [Finding(f"Windows à jour (build {build})", Severity.OK)]
    known = [b for b in WINDOWS_RELEASES if b <= build]
    if not known or (build < 22000 and build != 19045):
        version, end = f"Windows (build {build})", date(2000, 1, 1)
    else:
        version, end_home, end_ent = WINDOWS_RELEASES[max(known)]
        end = end_ent if edition.startswith(("Enterprise", "Education")) else end_home

    upgrade = [Action("Ouvrir Windows Update", _open_windows_update, auto=False, batch=False),
               Action("Assistant d'installation Windows 11",
                      lambda: open_target("https://www.microsoft.com/software-download/windows11"), auto=False, batch=False)]
    days = (end - date.today()).days
    if days < 0:
        return [Finding(f"{version} n'est plus supporté depuis le {end:%d/%m/%Y}", Severity.CRITICAL,
                        "Windows ne reçoit plus de correctifs de sécurité : les failles découvertes depuis "
                        "restent ouvertes. Installez la dernière version de Windows 11 (gratuit) via "
                        "Windows Update ou l'Assistant d'installation.", upgrade)]
    if days < 90:
        return [Finding(f"Fin du support de {version} dans {days} jours", Severity.WARNING,
                        f"Plus de correctifs de sécurité après le {end:%d/%m/%Y}. "
                        "Prévoyez la mise à niveau vers la dernière version.", upgrade)]
    return [Finding(f"{version} supporté jusqu'au {end:%d/%m/%Y}", Severity.OK)]


def _enable_firewall() -> str:
    ps.run("Set-NetFirewallProfile -Profile Domain,Private,Public -Enabled True")
    return "Pare-feu Windows activé sur tous les profils."


def firewall() -> list[Finding]:
    """Pare-feu"""
    rows = ps.run_list("Get-NetFirewallProfile | ForEach-Object { [pscustomobject]@{ Name = $_.Name; Enabled = [string]$_.Enabled } }")
    names = {"Domain": "Domaine", "Private": "Privé", "Public": "Public"}
    off = [names.get(r["Name"], r["Name"]) for r in rows if r["Enabled"] != "True"]
    if not off:
        return [Finding("Pare-feu Windows actif", Severity.OK)]
    try:
        others = ps.run_list("Get-CimInstance -Namespace root/SecurityCenter2 -ClassName FirewallProduct | Select-Object displayName")
    except ps.PSError:
        others = []
    if others:
        return [Finding("Pare-feu tiers actif : " + ", ".join(o["displayName"] for o in others), Severity.OK,
                        "Le pare-feu Windows est désactivé au profit de celui-ci.")]
    severity = Severity.CRITICAL if "Public" in off else Severity.WARNING
    return [Finding("Pare-feu désactivé (" + ", ".join(off) + ")", severity,
                    "Les connexions entrantes ne sont pas filtrées : le PC est exposé sur le réseau.",
                    [Action("Activer le pare-feu", _enable_firewall, admin=True)])]


def _set_policy(name: str, value: int, message: str):
    def go() -> str:
        reg_set_dword(HKLM, _POLICIES, name, value)
        return message
    return go


def uac() -> list[Finding]:
    """Contrôle de compte d'utilisateur (UAC)"""
    if reg_get(HKLM, _POLICIES, "EnableLUA", 1) == 0:
        return [Finding("UAC désactivé", Severity.CRITICAL,
                        "Tous les programmes s'exécutent avec les droits administrateur, sans demande de confirmation.",
                        [Action("Réactiver l'UAC", _set_policy("EnableLUA", 1, "UAC réactivé. Redémarrez le PC pour l'appliquer."), admin=True)])]
    if reg_get(HKLM, _POLICIES, "ConsentPromptBehaviorAdmin", 5) == 0:
        return [Finding("UAC en mode « ne jamais avertir »", Severity.WARNING,
                        "Les programmes obtiennent les droits administrateur sans votre accord.",
                        [Action("Rétablir les alertes UAC", _set_policy("ConsentPromptBehaviorAdmin", 5, "Alertes UAC rétablies."), admin=True)])]
    return [Finding("UAC actif", Severity.OK)]


def _disable_smb1() -> str:
    ps.run("Disable-WindowsOptionalFeature -Online -FeatureName SMB1Protocol -NoRestart | Out-Null", timeout=600)
    return "SMBv1 désactivé. Redémarrez le PC pour finaliser."


def smb1() -> list[Finding]:
    """Protocole SMBv1"""
    service = r"SYSTEM\CurrentControlSet\Services\mrxsmb10"
    if reg_exists(HKLM, service) and reg_get(HKLM, service, "Start", 4) != 4:
        return [Finding("SMBv1 activé", Severity.CRITICAL,
                        "Protocole de partage obsolète exploité par les rançongiciels WannaCry et NotPetya.",
                        [Action("Désactiver SMBv1", _disable_smb1, admin=True)])]
    return [Finding("SMBv1 désactivé", Severity.OK)]


def _disable_rdp() -> str:
    reg_set_dword(HKLM, _TS, "fDenyTSConnections", 1)
    return "Bureau à distance désactivé."


def remote_desktop() -> list[Finding]:
    """Bureau à distance"""
    if reg_get(HKLM, _TS, "fDenyTSConnections", 1) != 0:
        return [Finding("Bureau à distance désactivé", Severity.OK)]
    disable = Action("Désactiver le Bureau à distance", _disable_rdp, admin=True, auto=False)
    if reg_get(HKLM, _TS + r"\WinStations\RDP-Tcp", "UserAuthentication", 1) == 0:
        return [Finding("Bureau à distance activé sans authentification réseau (NLA)", Severity.CRITICAL,
                        "N'importe qui sur le réseau peut atteindre l'écran de connexion. Activez NLA ou "
                        "désactivez le Bureau à distance si vous ne l'utilisez pas.", [disable])]
    return [Finding("Bureau à distance activé", Severity.INFO,
                    "Désactivez-le si vous ne vous connectez pas à ce PC à distance.", [disable])]


def guest_account() -> list[Finding]:
    """Compte Invité"""
    guest = ps.run_obj("Get-LocalUser | Where-Object { $_.SID.Value -like '*-501' } "
                       "| Select-Object Name, Enabled, @{n='Sid';e={$_.SID.Value}}")
    if guest and guest["Enabled"]:
        def disable() -> str:
            ps.run("Disable-LocalUser -SID " + ps.quote(guest["Sid"]))
            return "Compte Invité désactivé."
        return [Finding(f"Compte « {guest['Name']} » activé", Severity.WARNING,
                        "Permet d'ouvrir une session sans mot de passe.",
                        [Action("Désactiver le compte Invité", disable, admin=True)])]
    return [Finding("Compte Invité désactivé", Severity.OK)]


def secure_boot() -> list[Finding]:
    """Démarrage sécurisé"""
    state = reg_get(HKLM, r"SYSTEM\CurrentControlSet\Control\SecureBoot\State", "UEFISecureBootEnabled")
    if state is None:
        return [Finding("Démarrage sécurisé non disponible (BIOS ancien mode)", Severity.INFO,
                        "Sans Secure Boot, un bootkit peut se charger avant Windows.")]
    if state == 0:
        return [Finding("Démarrage sécurisé (Secure Boot) désactivé", Severity.WARNING,
                        "Protège contre les bootkits. À activer dans le BIOS/UEFI de la carte mère.")]
    return [Finding("Démarrage sécurisé actif", Severity.OK)]


DEFENDER_PREFS_PS = r"""
$st = Get-MpComputerStatus
$p = Get-MpPreference
[pscustomobject]@{ Mode = [string]$st.AMRunningMode; PUA = [int]$p.PUAProtection; CFA = [int]$p.EnableControlledFolderAccess }
"""


def _defender_pref(setting: str, message: str):
    def go() -> str:
        ps.run(f"Set-MpPreference -{setting} Enabled")
        return message
    return go


def defender_settings() -> list[Finding]:
    """Réglages de Defender"""
    prefs = ps.run_obj(DEFENDER_PREFS_PS)
    if not prefs or prefs["Mode"] != "Normal":
        return []  # Defender n'est pas l'antivirus principal
    findings = []
    if prefs["PUA"] != 1:
        findings.append(Finding("Blocage des logiciels indésirables (PUA) désactivé", Severity.WARNING,
                                "Defender laisse passer adwares, toolbars et faux optimiseurs.",
                                [Action("Activer le blocage PUA", _defender_pref("PUAProtection", "Blocage des logiciels indésirables activé."), admin=True)]))
    else:
        findings.append(Finding("Blocage des logiciels indésirables actif", Severity.OK))
    if prefs["CFA"] != 1:
        findings.append(Finding("Protection anti-rançongiciel (dossiers contrôlés) désactivée", Severity.INFO,
                                "Empêche les programmes inconnus de modifier Documents, Images, etc. "
                                "Peut bloquer certains logiciels légitimes.",
                                [Action("Activer", _defender_pref("EnableControlledFolderAccess", "Accès contrôlé aux dossiers activé."), admin=True, auto=False)]))
    return findings


def disk_encryption() -> list[Finding]:
    """Chiffrement du disque"""
    state = ps.run("(New-Object -ComObject Shell.Application).NameSpace('C:').Self.ExtendedProperty('System.Volume.BitLockerProtection')")
    if state in ("1", "3", "5"):
        return [Finding("Disque système chiffré (BitLocker)", Severity.OK)]
    if state == "2":
        return [Finding("Disque système non chiffré", Severity.INFO,
                        "En cas de vol du PC, vos fichiers sont lisibles. Activez le chiffrement de l'appareil "
                        "ou BitLocker si disponible (gardez la clé de récupération).",
                        [Action("Ouvrir les paramètres", lambda: open_target("ms-settings:deviceencryption"), auto=False, batch=False)])]
    return []


PORTS_PS = r"""
$procs = @{}
Get-Process | ForEach-Object { $procs[$_.Id] = $_ }
$blocked = @(Get-NetFirewallRule -DisplayName 'Autowash - bloquer *' -ErrorAction SilentlyContinue | ForEach-Object DisplayName)
Get-NetTCPConnection -State Listen |
  Where-Object { $_.LocalAddress -notlike '127.*' -and $_.LocalAddress -ne '::1' } |
  Group-Object OwningProcess | ForEach-Object {
    $p = $procs[[int]$_.Name]
    $path = $(if ($p -and $p.Path) { $p.Path } else { '' })
    if (-not $path) {
      # processus élevé : son chemin est lisible dans la définition du service qui l'a lancé
      $svc = Get-CimInstance Win32_Service -Filter "ProcessId=$($_.Name)" | Select-Object -First 1
      if ($svc -and $svc.PathName -match '^\s*"?([^"]+?\.exe)') { $path = $Matches[1] }
    }
    [pscustomobject]@{
      Name = $(if ($p) { $p.ProcessName } else { '?' })
      Path = $path
      Ports = @($_.Group.LocalPort | Sort-Object -Unique)
      Blocked = $blocked -contains ('Autowash - bloquer ' + $(if ($p) { $p.ProcessName } else { '?' }))
    }
  }
"""
_SYSTEM_LISTENERS = {"system", "svchost", "lsass", "wininit", "services", "spoolsv", "idle"}
# Services d'accès à distance ou bases de données : dangereux s'ils sont joignables depuis Internet.
_RISKY_PORTS = {21: "FTP", 22: "SSH", 23: "Telnet", 3389: "Bureau à distance", 5800: "VNC", 5900: "VNC",
                5938: "TeamViewer", 7070: "AnyDesk", 3306: "MySQL", 5432: "PostgreSQL", 6379: "Redis",
                27017: "MongoDB", 1433: "SQL Server"}


def _rule_name(process: str) -> str:
    return f"Autowash - bloquer {process}"


def _block(name: str, path: str, ports: list[int]):
    # par programme si son chemin est connu, sinon par ports (processus élevé illisible sans droits admin)
    target = f"-Program {ps.quote(path)}" if path else f"-Protocol TCP -LocalPort {','.join(map(str, ports))}"

    def go() -> str:
        ps.run(f"New-NetFirewallRule -DisplayName {ps.quote(_rule_name(name))} -Direction Inbound "
               f"{target} -Action Block -Profile Any | Out-Null")
        return f"Connexions entrantes vers {name} bloquées par le pare-feu."
    return go


def _unblock(spec: dict) -> str:
    ps.run(f"Remove-NetFirewallRule -DisplayName {ps.quote(spec['rule'])}")
    return f"Règle « {spec['rule']} » supprimée : les connexions entrantes sont de nouveau autorisées."


journal.register_undo("firewall_rule", _unblock)


def open_ports() -> list[Finding]:
    """Ports ouverts sur le réseau"""
    findings, system_ports = [], []
    for item in ps.run_list(PORTS_PS):
        ports = item["Ports"]
        if item["Name"].lower() in _SYSTEM_LISTENERS:
            system_ports += ports
            continue
        risky = sorted({_RISKY_PORTS[p] for p in ports if p in _RISKY_PORTS})
        listing = ", ".join(map(str, ports[:8])) + ("…" if len(ports) > 8 else "")
        if item["Blocked"]:
            findings.append(Finding(f"{item['Name']} : connexions entrantes bloquées par Autowash", Severity.OK,
                                    f"Ports {listing}", [Action("Débloquer", lambda r=_rule_name(item["Name"]): _unblock({"rule": r}),
                                                                admin=True, auto=False)]))
            continue
        actions = [Action("Bloquer dans le pare-feu", _block(item["Name"], item["Path"], ports), admin=True,
                          auto=False, undo={"kind": "firewall_rule", "rule": _rule_name(item["Name"])})]
        detail = (f"Ports {listing}" + (f" · {item['Path']}" if item["Path"] else "") + "\n"
                  "Ce programme accepte des connexions venant des autres appareils du réseau. "
                  "Si vous ne l'utilisez pas à distance, bloquez-le.")
        if risky:
            findings.append(Finding(f"{item['Name']} expose un accès à distance ({', '.join(risky)})", Severity.WARNING,
                                    detail + "\nCe type de service est très ciblé par les attaques automatisées.",
                                    actions))
        else:
            findings.append(Finding(f"{item['Name']} écoute sur le réseau", Severity.INFO, detail, actions))
    if system_ports:
        findings.append(Finding("Ports des services Windows", Severity.OK,
                                f"{len(set(system_ports))} ports (partage de fichiers, RPC…) filtrés par le pare-feu Windows."))
    return findings


def scan() -> list[Finding]:
    return run_checks([windows_support, firewall, uac, smb1, remote_desktop, guest_account, secure_boot,
                       defender_settings, disk_encryption, open_ports], NAME)
