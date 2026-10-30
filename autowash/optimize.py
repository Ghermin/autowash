"""Optimisation : réglages Windows recommandés (confidentialité, jeu, confort) et réglages risqués à annuler.

Inspiré des options des « optimiseurs » populaires, en ne retenant que des réglages sûrs, réversibles et utiles.
Chaque réglage mémorise les valeurs d'origine : il s'annule depuis l'Historique.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import journal
from .models import Action, Finding, Severity, run_checks
from .system import HKCU, HKLM, reg_delete, reg_get, reg_set

NAME = "Optimisation"
DESCRIPTION = "Réglages Windows recommandés (confidentialité, jeu, confort) et réglages risqués laissés par un optimiseur."
BATCH_LABEL = "Appliquer la sélection"
TOOLS: list[Action] = []

_HIVES = {"HKCU": HKCU, "HKLM": HKLM}


@dataclass
class Tweak:
    title: str
    detail: str
    values: list[tuple[str, str, str, int | str | None]]  # (ruche, clé, valeur, cible) ; cible None = supprimer
    severity: Severity = Severity.INFO  # quand le réglage n'est pas appliqué
    label: str = "Appliquer"

    @property
    def admin(self) -> bool:
        return any(hive == "HKLM" for hive, *_ in self.values)

    def applied(self) -> bool:
        return all(reg_get(_HIVES[h], path, name, None) == target for h, path, name, target in self.values)


def _write(values: list[tuple[str, str, str, int | str | None]]) -> None:
    for hive, path, name, value in values:
        if value is None:
            reg_delete(_HIVES[hive], path, name)
        else:
            reg_set(_HIVES[hive], path, name, value)


def _restore(spec: dict) -> str:
    _write([tuple(v) for v in spec["previous"]])
    return f"« {spec['title']} » annulé : réglages d'origine rétablis (certains après redémarrage de session)."


journal.register_undo("registry", _restore)


def _action(tweak: Tweak) -> Action:
    previous = [(h, path, name, reg_get(_HIVES[h], path, name, None)) for h, path, name, _ in tweak.values]

    def go() -> str:
        _write(tweak.values)
        done = tweak.title if tweak.label == "Appliquer" else tweak.label  # réglage risqué : le titre décrit le problème
        return f"« {done} » appliqué (effet complet après fermeture de session pour certains réglages)."
    return Action(tweak.label, go, admin=tweak.admin, auto=False,
                  undo={"kind": "registry", "title": tweak.title, "previous": previous})


_CDM = r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"
_ADV = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"

RECOMMENDED = [
    Tweak("Afficher les extensions des fichiers",
          "Sécurité : rend visibles les pièges du type « facture.pdf.exe », très utilisés pour diffuser des malwares.",
          [("HKCU", _ADV, "HideFileExt", 0)]),
    Tweak("Bloquer l'installation automatique d'applis promotionnelles",
          "Empêche Windows d'installer tout seul des jeux et applis sponsorisés (Candy Crush, TikTok…).",
          [("HKCU", _CDM, "SilentInstalledAppsEnabled", 0), ("HKCU", _CDM, "OemPreInstalledAppsEnabled", 0),
           ("HKCU", _CDM, "PreInstalledAppsEnabled", 0)]),
    Tweak("Supprimer les publicités et suggestions du menu Démarrer",
          "Retire les applis suggérées, conseils et recommandations sponsorisées du menu Démarrer et de l'écran de verrouillage.",
          [("HKCU", _CDM, "SystemPaneSuggestionsEnabled", 0), ("HKCU", _CDM, "SubscribedContent-338388Enabled", 0),
           ("HKCU", _CDM, "SubscribedContent-338389Enabled", 0), ("HKCU", _CDM, "SubscribedContent-353694Enabled", 0),
           ("HKCU", _CDM, "SubscribedContent-353696Enabled", 0), ("HKCU", _CDM, "RotatingLockScreenOverlayEnabled", 0),
           ("HKCU", _ADV, "Start_IrisRecommendations", 0)]),
    Tweak("Désactiver l'identifiant publicitaire",
          "Les applis ne peuvent plus vous suivre d'une appli à l'autre pour cibler les publicités.",
          [("HKCU", r"Software\Microsoft\Windows\CurrentVersion\AdvertisingInfo", "Enabled", 0),
           ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Privacy", "TailoredExperiencesWithDiagnosticDataEnabled", 0)]),
    Tweak("Recherche du menu Démarrer sans Bing",
          "La recherche n'envoie plus ce que vous tapez sur Internet : résultats locaux, plus rapides.",
          [("HKCU", r"Software\Policies\Microsoft\Windows\Explorer", "DisableSearchBoxSuggestions", 1)]),
    Tweak("Télémétrie au minimum",
          "Réduit les données de diagnostic envoyées à Microsoft au strict nécessaire, et coupe l'historique d'activités "
          "et les demandes d'avis.",
          [("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\DataCollection", "AllowTelemetry", 0),
           ("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\System", "PublishUserActivities", 0),
           ("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\System", "UploadUserActivities", 0),
           ("HKCU", r"Software\Microsoft\Siuf\Rules", "NumberOfSIUFInPeriod", 0)]),
    Tweak("Désactiver les widgets (actualités)",
          "Le panneau Widgets charge un navigateur en arrière-plan pour afficher actualités et publicités.",
          [("HKLM", r"SOFTWARE\Policies\Microsoft\Dsh", "AllowNewsAndInterests", 0)]),
    Tweak("Jeu : couper l'enregistrement en arrière-plan",
          "La Xbox Game Bar enregistre en continu les dernières minutes de jeu, ce qui coûte des images par seconde. "
          "Le mode Jeu reste actif.",
          [("HKCU", r"System\GameConfigStore", "GameDVR_Enabled", 0),
           ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\GameDVR", "AppCaptureEnabled", 0),
           ("HKCU", r"Software\Microsoft\GameBar", "AutoGameModeEnabled", 1)]),
    Tweak("Jeu : désactiver le raccourci des touches rémanentes",
          "Appuyer 5 fois sur Maj (fréquent en jeu) n'ouvre plus la fenêtre des touches rémanentes.",
          [("HKCU", r"Control Panel\Accessibility\StickyKeys", "Flags", "506")]),
    Tweak("Mises à jour sans partage pair à pair",
          "Windows ne sert plus de relais pour envoyer des mises à jour à d'autres PC : moins d'envoi de données sur "
          "votre connexion.",
          [("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\DeliveryOptimization", "DODownloadMode", 0)]),
]

# Réglages appliqués par certains optimiseurs, qui affaiblissent la sécurité : on propose de les annuler.
RISKY = [
    Tweak("SmartScreen désactivé", "SmartScreen avertit avant d'ouvrir un fichier ou programme téléchargé inconnu ou "
          "signalé comme malveillant. Il a été coupé par une stratégie système (souvent par un optimiseur).",
          [("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\System", "EnableSmartScreen", None)],
          Severity.WARNING, "Réactiver SmartScreen"),
    Tweak("Mises à jour automatiques du Microsoft Store désactivées",
          "Les applications du Store (Terminal, Outlook, Photos…) ne reçoivent plus leurs correctifs de sécurité.",
          [("HKLM", r"SOFTWARE\Policies\Microsoft\WindowsStore", "AutoDownload", None)],
          Severity.WARNING, "Réactiver les mises à jour"),
    Tweak("Windows Update bloqué par une stratégie",
          "Les correctifs de sécurité de Windows ne s'installent plus automatiquement.",
          [("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU", "NoAutoUpdate", None),
           ("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate", "DisableWindowsUpdateAccess", None)],
          Severity.CRITICAL, "Débloquer Windows Update"),
    Tweak("Microsoft Defender désactivé par une stratégie",
          "L'antivirus intégré est coupé : le PC n'est plus protégé si aucun autre antivirus n'est installé.",
          [("HKLM", r"SOFTWARE\Policies\Microsoft\Windows Defender", "DisableAntiSpyware", None),
           ("HKLM", r"SOFTWARE\Policies\Microsoft\Windows Defender\Real-Time Protection", "DisableRealtimeMonitoring", None)],
          Severity.CRITICAL, "Réactiver Defender"),
    Tweak("Partage de fichiers réseau (SMBv2) désactivé",
          "Ce PC ne peut plus partager de dossiers ni d'imprimante sur le réseau local. Sans conséquence si vous ne "
          "partagez rien ; à réactiver sinon.",
          [("HKLM", r"SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters", "SMB2", None)],
          Severity.OK, "Réactiver le partage"),
]


def recommended() -> list[Finding]:
    """Réglages recommandés"""
    findings, done = [], []
    for tweak in RECOMMENDED:
        if tweak.applied():
            done.append(tweak.title)
        else:
            findings.append(Finding(tweak.title, tweak.severity, tweak.detail, [_action(tweak)]))
    if done:
        findings.append(Finding(f"{len(done)} réglage(s) recommandé(s) déjà en place", Severity.OK, "\n".join(done)))
    return findings


def risky() -> list[Finding]:
    """Réglages risqués"""
    findings = []
    for tweak in RISKY:
        if not tweak.applied():  # « appliqué » = valeurs absentes = situation normale de Windows
            findings.append(Finding(tweak.title, tweak.severity, tweak.detail, [_action(tweak)]))
    return findings or [Finding("Aucun réglage risqué laissé par un optimiseur", Severity.OK)]


def scan() -> list[Finding]:
    return run_checks([risky, recommended], NAME)
