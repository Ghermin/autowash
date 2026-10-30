"""Démarrage : programmes lancés avec Windows, activation/désactivation et impact mesuré."""
from __future__ import annotations

from . import journal, ps
from .malware import STARTUP_PS, remove_startup_action
from .models import Action, Finding, Severity, run_checks
from .system import human_size, launch

NAME = "Démarrage"
DESCRIPTION = "Programmes lancés avec Windows : activez ou désactivez-les, et voyez ce qu'ils consomment."
BATCH_LABEL = "Activer / désactiver la sélection"

TOOLS = [Action("Gestionnaire des tâches", lambda: launch("taskmgr", "/0", "/startup"), auto=False, batch=False)]

_APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"

# Windows (et le Gestionnaire des tâches) mémorisent l'état activé/désactivé dans les clés StartupApproved :
# l'entrée de démarrage elle-même n'est pas touchée, ce qui rend l'opération réversible.
DETAILS_PS = r"""
$approved = @{
  'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' = 'HKCU:\__APPROVED__\Run'
  'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run' = 'HKLM:\__APPROVED__\Run'
  'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run' = 'HKLM:\__APPROVED__\Run32'
}
$userStartup = [Environment]::GetFolderPath('Startup')
$procs = @(Get-Process | Where-Object Path)
& { __STARTUP_PS__ } | ForEach-Object {
  $i = $_
  if ($i.Kind -eq 'reg') { $key = $approved[$i.Location]; $value = $i.Name }
  else {
    $key = $(if ($i.Location.StartsWith($userStartup)) { 'HKCU:\__APPROVED__\StartupFolder' } else { 'HKLM:\__APPROVED__\StartupFolder' })
    $value = Split-Path $i.Location -Leaf
  }
  $enabled = $true
  if ($key) {
    $bytes = (Get-ItemProperty -LiteralPath $key -Name $value -ErrorAction SilentlyContinue).$value
    if ($bytes -and ($bytes[0] -band 1)) { $enabled = $false }
  }
  $company = ''; $description = ''
  if ($i.Exists) { $vi = (Get-Item -LiteralPath $i.Path).VersionInfo; $company = $vi.CompanyName; $description = $vi.FileDescription }
  $running = @($procs | Where-Object { $_.Path -eq $i.Path })
  $i | Add-Member -NotePropertyMembers @{
    ApprovedKey = [string]$key; ValueName = $value; Enabled = $enabled
    Company = [string]$company; Description = [string]$description
    Ram = [int64]($running | Measure-Object WorkingSet64 -Sum).Sum
    Cpu = [double]($running | Measure-Object CPU -Sum).Sum
  }
  $i
}
"""


def _entries() -> list[dict]:
    return ps.run_list(DETAILS_PS.replace("__APPROVED__", _APPROVED).replace("__STARTUP_PS__", STARTUP_PS))


def _apply(spec: dict, enable: bool) -> str:
    # 02 = activé ; 03 + date de désactivation = désactivé (format du Gestionnaire des tâches)
    value = ("([byte[]](2,0,0,0,0,0,0,0,0,0,0,0))" if enable else
             "([byte[]](@(3,0,0,0) + [BitConverter]::GetBytes([DateTime]::Now.ToFileTime())))")
    key = ps.quote(spec["ApprovedKey"])
    ps.run(f"if (-not (Test-Path -LiteralPath {key})) {{ New-Item -Path {key} -Force | Out-Null }}\n"
           f"New-ItemProperty -LiteralPath {key} -Name {ps.quote(spec['ValueName'])} -Value {value} "
           "-PropertyType Binary -Force | Out-Null")
    return f"« {spec['Name']} » {'activé' if enable else 'désactivé'} au démarrage."


journal.register_undo("startup_toggle", lambda spec: _apply(spec, spec["enable"]))


def toggle_action(item: dict) -> Action:
    enable = not item["Enabled"]
    spec = {k: item[k] for k in ("ApprovedKey", "ValueName", "Name")}
    return Action("Activer" if enable else "Désactiver", lambda: _apply(spec, enable),
                  admin=item["ApprovedKey"].startswith("HKLM"), auto=False,
                  undo={"kind": "startup_toggle", **spec, "enable": not enable})


def _impact(item: dict) -> str:
    ram, cpu = item["Ram"], item["Cpu"]
    if not ram and not cpu:
        return "non lancé actuellement"
    level = "élevé" if ram > 300 * 1024**2 or cpu > 60 else "moyen" if ram > 100 * 1024**2 or cpu > 15 else "faible"
    return f"{level} ({human_size(ram)} de mémoire, {cpu:.0f} s de processeur depuis le démarrage)"


def programs() -> list[Finding]:
    """Programmes au démarrage"""
    items = sorted(_entries(), key=lambda i: (not i["Enabled"], -i["Ram"]))
    findings = []
    for item in items:
        heavy = item["Enabled"] and _impact(item).startswith("élevé")
        state = "Activé" if item["Enabled"] else "Désactivé"
        facts = " · ".join(x for x in (item["Company"], state, f"impact {_impact(item)}" if item["Enabled"] else "") if x)
        actions = [remove_startup_action(item)]
        if item["ApprovedKey"]:  # RunOnce n'a pas d'état activé/désactivé
            actions.insert(0, toggle_action(item))
        title = item["Description"] or item["Name"].removesuffix(".lnk")
        findings.append(Finding(title, Severity.INFO if heavy else Severity.OK,
                                f"{facts}\n{item['Path'] or item['Command']}", actions))
    enabled = sum(i["Enabled"] for i in items)
    findings.insert(0, Finding(f"{enabled} programme(s) lancé(s) avec Windows, {len(items) - enabled} désactivé(s)",
                               Severity.OK, "Désactiver un programme l'empêche seulement de démarrer avec Windows : "
                                            "il reste installé et utilisable."))
    return findings


def scan() -> list[Finding]:
    return run_checks([programs], NAME)
