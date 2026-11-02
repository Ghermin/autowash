# Publie une nouvelle version d'Autowash sur GitHub : numéro de version, construction, installeur, zip, tag et release.
# L'installeur (Autowash-Setup-x.y.z.exe) sert aux nouvelles installations ; le zip à la mise à jour automatique
# des PC qui ont déjà Autowash (1.0.1 ou plus). Nécessite Inno Setup 6 (winget install JRSoftware.InnoSetup).
# Exemple : powershell -ExecutionPolicy Bypass -File release.ps1 -Version 1.0.2 -Notes "Correction de l'analyse des pilotes."
param([Parameter(Mandatory)][ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version, [string]$Notes = '')
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Invoke-Native([string]$what, [scriptblock]$command) {
    & $command
    if ($LASTEXITCODE -ne 0) { throw "Échec : $what (code $LASTEXITCODE)" }
}

if ((git branch --show-current) -ne 'main') { throw 'Les versions se publient depuis la branche main.' }
if (git status --porcelain --untracked-files=no) { throw 'Des modifications ne sont pas commitées.' }
if (git tag --list "v$Version") { throw "La version $Version existe déjà." }
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw 'Inno Setup 6 introuvable : winget install JRSoftware.InnoSetup' }

$init = Join-Path $PSScriptRoot 'autowash\__init__.py'
$source = [IO.File]::ReadAllText($init)
$updated = $source -replace '__version__ = "[^"]*"', "__version__ = `"$Version`""
[IO.File]::WriteAllText($init, $updated, (New-Object Text.UTF8Encoding $false))

& (Join-Path $PSScriptRoot 'build.ps1') -NoInstall
$zip = Join-Path $PSScriptRoot "dist\Autowash-$Version-windows.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path dist\Autowash -DestinationPath $zip -CompressionLevel Optimal
Invoke-Native 'installeur' { & $iscc /Q "/DAppVersion=$Version" installer\autowash.iss }
$setup = Join-Path $PSScriptRoot "dist\Autowash-Setup-$Version.exe"

if ($updated -ne $source) {
    Invoke-Native 'commit' { git commit --quiet -m "release: v$Version" -- autowash/__init__.py }
}
Invoke-Native 'tag' { git tag "v$Version" }
Invoke-Native 'push' { git push --quiet origin main "v$Version" }

$body = @"
$Notes

## Installation

**Déjà installé (version 1.0.1 ou plus) :** rien à faire, Autowash se met à jour tout seul. La nouvelle version est téléchargée au lancement et installée à la fermeture, ou tout de suite avec « Redémarrer maintenant ».

**Nouvelle installation :** télécharger ``Autowash-Setup-$Version.exe`` ci-dessous et le lancer. Il installe Autowash pour votre compte (sans droits administrateur), crée les raccourcis Bureau et menu Démarrer, et se désinstalle depuis *Paramètres › Applications installées*. Autowash demande ensuite les droits administrateur à son lancement, nécessaires pour la plupart des corrections.

Windows 10/11 64 bits, aucune installation de Python nécessaire. L'installeur n'est pas signé : SmartScreen peut afficher « Windows a protégé votre ordinateur », cliquer sur *Informations complémentaires* › *Exécuter quand même*.

Le zip contient la même version sans installeur (version portable, utilisée aussi par la mise à jour automatique).
"@
$notesFile = Join-Path $env:TEMP "autowash-release-$Version.md"
[IO.File]::WriteAllText($notesFile, $body.Trim(), (New-Object Text.UTF8Encoding $false))
Invoke-Native 'release GitHub' {
    gh release create "v$Version" $setup $zip --repo Ghermin/autowash --title "Autowash $Version" --notes-file $notesFile
}
"Version $Version publiée : https://github.com/Ghermin/autowash/releases/tag/v$Version"
