"""Programmes : liste des logiciels installés, bloatwares à supprimer, désinstallation."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from . import ps
from .models import Action, Finding, Severity, run_checks
from .system import human_size, open_target
from .updates import _parse_table, _winget

NAME = "Programmes"
DESCRIPTION = "Logiciels installés, bloatwares et programmes inutiles à supprimer, désinstallation groupée."
BATCH_LABEL = "Désinstaller la sélection"

TOOLS = [Action("Applications installées (Windows)", lambda: open_target("ms-settings:appsfeatures"),
                auto=False, batch=False)]


@dataclass
class Program:
    name: str
    id: str
    version: str
    publisher: str = ""
    size: int = 0  # octets, 0 si inconnu
    installed: str = ""  # AAAA-MM-JJ
    component: bool = False  # runtime, pilote, SDK… dont d'autres programmes dépendent


# Dernière liste lue par scan(), affichée en entier par l'interface.
PROGRAMS: list[Program] = []

_COMPONENT = re.compile(r"redistributable|runtime|\.net|framework|driver|pilote|\bsdk\b|webview2|directx|chipset|"
                        r"vcredist|visual c\+\+|windows app ?sdk|update health|edge", re.I)

# (motif sur « nom id », priorité, catégorie, raison). Priorité 1 = à supprimer, 2 = recommandé, 3 = selon usage.
BLOATWARE: list[tuple[str, int, str, str]] = [
    (r"reimage|restoro|fortect|pc ?repair|outbyte", 1, "Faux logiciel de réparation",
     "Scanner alarmiste qui exagère les problèmes pour vendre une licence."),
    (r"ccleaner|advanced ?systemcare|iobit|smart ?defrag|wise ?care|glary|auslogics|systweak|advanced ?pc ?care|"
     r"pc ?(cleaner|optimizer|accelerate|speed)|slimcleaner|registry ?(cleaner|mechanic|reviver)|reg ?clean|"
     r"\boptimizer\b|avast ?cleanup|avg ?tuneup|winoptimizer|totalav", 1, "Faux optimiseur / nettoyeur",
     "N'accélère pas Windows, installe souvent d'autres logiciels et collecte des données. Autowash et Windows "
     "font le même nettoyage."),
    (r"driver ?(booster|easy|support|genius|max|updater|reviver|talent|fix)|winzip ?driver", 1,
     "Mise à jour de pilotes tierce",
     "Installe parfois des pilotes inadaptés. Windows Update et le site du fabricant suffisent."),
    (r"onelaunch|wave ?browser|shift ?browser|\bshift\b|webcompanion|web companion|ask ?toolbar|conduit|mindspark|"
     r"hola|baidu|secure ?browser|search ?protect|safe ?search|bytefence|segurazo|santivirus|pc ?app ?store", 1,
     "Logiciel publicitaire (adware)",
     "Navigateur, barre d'outils ou « protection » installé en bundle, qui détourne les recherches et affiche "
     "des publicités."),
    (r"mcafee|norton(?! ?ghost)|avast|\bavg\b|kaspersky|bullguard|panda ?dome|totalav", 2,
     "Antivirus tiers",
     "Defender protège déjà le PC. Un antivirus d'essai ou gratuit ajoute publicités et relances, et deux "
     "antivirus se gênent. À garder seulement si vous payez un abonnement que vous utilisez."),
    (r"wildtangent|candy ?crush|king\.com|bubble ?witch|farm ?heroes|march of empires|hidden city|"
     r"booking\.com|amazon ?assistant|tiktok|bytedance|disney|facebook|instagram|linkedin|"
     r"expressvpn|dropbox ?promotion|mcafee ?webadvisor", 2, "Application préinstallée",
     "Installée par le fabricant ou Windows à des fins promotionnelles."),
    (r"microsoft\.bingnews|microsoft actualités|microsoft news|msn|bingweather|microsoft\.getstarted|astuces|"
     r"feedbackhub|hub de commentaires|solitaire|clipchamp|officehub|microsoft 365 \(office\)|"
     r"powerautomate|power automate|mixedreality|mixed reality|3d ?viewer|3d ?builder|paint ?3d|skypeapp|\bskype\b|"
     r"549981c3f5f10|cortana|microsoftfamily|microsoft family|microsoft\.people|windowsmaps|cartes|"
     r"microsoft\.todos|teams \(personnel\)|microsoftteams|msteams", 2, "Application Windows superflue",
     "Application intégrée rarement utilisée. Se réinstalle depuis le Microsoft Store si besoin."),
    (r"oracle ?java|java\(tm\)|java \d+ update|javaruntime", 2, "Java",
     "Cible fréquente d'attaques. À supprimer si aucun programme ou jeu (Minecraft Java inclut le sien) n'en a besoin."),
    (r"logitech ?download ?assistant|adobe ?genuine|adobe gc|bonjour|apple ?software ?update|"
     r"hp ?(jumpstart|support ?assistant|smart|sure)|dell ?(supportassist|update)|lenovo ?(vantage|now)|"
     r"asus ?(giftbox|armoury)|acer ?(care|jumpstart)|cyberlink|wps ?office|outlook for windows|"
     r"microsoft\.outlookforwindows|userbenchmark|steelseries ?engine|contrôle d.intégrité du pc|pc ?health ?check|"
     r"\bcopilot\b", 3, "Utilitaire facultatif",
     "Tourne souvent en arrière-plan pour peu d'utilité. À supprimer si vous ne vous en servez pas."),
]
_BLOAT_SEVERITY = {1: Severity.WARNING, 2: Severity.INFO, 3: Severity.INFO}
_PRIORITY_LABEL = {1: "à supprimer", 2: "recommandé", 3: "selon votre usage"}

ARP_PS = r"""
$keys = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
foreach ($e in Get-ItemProperty $keys -ErrorAction SilentlyContinue) {
  if ($e.DisplayName) {
    [pscustomobject]@{ Name = [string]$e.DisplayName; Publisher = [string]$e.Publisher
                       Size = [int64]$e.EstimatedSize; Date = [string]$e.InstallDate }
  }
}
"""


def installed() -> list[Program]:
    """Programmes connus de winget (installeurs classiques et applications du Store), enrichis par le registre."""
    _, out = _winget("list")
    arp = {r["Name"]: r for r in ps.run_list(ARP_PS)}
    programs = []
    for name, pkg_id, version, *_ in _parse_table(out, required=(1, 2)):
        info = arp.get(name) or next((r for n, r in arp.items() if n.startswith(name.rstrip("…"))), {})
        date = info.get("Date") or ""
        programs.append(Program(
            name=name, id=pkg_id, version=version, publisher=info.get("Publisher") or "",
            size=(info.get("Size") or 0) * 1024,
            installed=f"{date[:4]}-{date[4:6]}-{date[6:8]}" if re.fullmatch(r"\d{8}", date) else "",
            component=bool(_COMPONENT.search(name))))
    return sorted(programs, key=lambda p: p.name.lower())


def uninstall(program: Program) -> Callable[[], str]:
    def go() -> str:
        code, out = _winget("uninstall", "--id", program.id, "--exact", "--silent", timeout=1800)
        if code != 0:
            # certains désinstalleurs refusent le mode silencieux : on affiche le leur
            code, out = _winget("uninstall", "--id", program.id, "--exact", timeout=1800)
        if code != 0:
            last = [line.strip() for line in out.splitlines() if line.strip()]
            raise RuntimeError(f"{program.name} : échec (code {code}). {last[-1] if last else ''}")
        return f"{program.name} désinstallé."
    return go


def uninstall_action(program: Program) -> Action:
    warning = ("\n\nAttention : c'est un composant (runtime, pilote…) dont d'autres programmes peuvent dépendre."
               if program.component else "")
    return Action("Désinstaller", uninstall(program), auto=False,
                  confirm=f"Désinstaller « {program.name} » ?{warning}")


def bloatware() -> list[Finding]:
    """Bloatwares"""
    PROGRAMS[:] = installed()
    found = []
    for program in PROGRAMS:
        text = f"{program.name} {program.id}"
        for pattern, priority, category, reason in BLOATWARE:
            if re.search(pattern, text, re.I):
                found.append((priority, program, category, reason))
                break
    findings = []
    for priority, program, category, reason in sorted(found, key=lambda f: (f[0], -f[1].size)):
        facts = ", ".join(x for x in (program.publisher, human_size(program.size) if program.size else "") if x)
        findings.append(Finding(
            program.name, _BLOAT_SEVERITY[priority],
            f"Priorité {priority} ({_PRIORITY_LABEL[priority]}) · {category}" + (f" · {facts}" if facts else "")
            + f"\n{reason}", [uninstall_action(program)]))
    findings.append(Finding(f"{len(PROGRAMS)} programmes installés", Severity.OK,
                            "Liste complète ci-dessous : cochez-en plusieurs pour les désinstaller d'un coup."))
    if not found:
        findings.insert(0, Finding("Aucun bloatware connu détecté", Severity.OK))
    return findings


def scan() -> list[Finding]:
    return run_checks([bloatware], NAME)
