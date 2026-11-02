# Construit Autowash.exe (PyInstaller) puis l'installe dans %LOCALAPPDATA%\Programs\Autowash
# avec un raccourci sur le Bureau. -NoInstall : construit seulement (dist\Autowash).
param([switch]$NoInstall)
# pip et PyInstaller écrivent leurs messages sur stderr : on vérifie les codes de retour plutôt que de s'arrêter dessus
$ErrorActionPreference = 'Continue'
Set-Location $PSScriptRoot

function Invoke-Step([string]$what, [scriptblock]$command) {
    & $command 2>&1 | Where-Object { $_ -match 'error|erreur|warning: lib' } | ForEach-Object { "  $_" }
    if ($LASTEXITCODE -ne 0) { throw "Échec : $what (code $LASTEXITCODE)" }
}

Invoke-Step 'dépendances' { python -m pip install --quiet --no-warn-script-location -r requirements.txt pyinstaller==6.22.3 pillow }
Invoke-Step 'icône' { python tools\make_icon.py }
Invoke-Step 'PyInstaller' {
    python -m PyInstaller --noconfirm --clean --windowed --log-level WARN --name Autowash `
        --icon assets\autowash.ico --add-data "assets;assets" --collect-data customtkinter main.py
}
"Construit : $PSScriptRoot\dist\Autowash\Autowash.exe"
if ($NoInstall) { return }

if (Get-Process Autowash -ErrorAction SilentlyContinue) { throw "Fermez Autowash avant de le réinstaller." }
$target = Join-Path $env:LOCALAPPDATA 'Programs\Autowash'
# on remplace seulement l'application : le désinstalleur de l'installeur (unins000.*) reste en place
New-Item -ItemType Directory -Force $target | Out-Null
foreach ($item in 'Autowash.exe', '_internal') {
    $path = Join-Path $target $item
    if (Test-Path $path) { Remove-Item $path -Recurse -Force -ErrorAction Stop }
    Copy-Item (Join-Path 'dist\Autowash' $item) $path -Recurse -ErrorAction Stop
}

$exe = Join-Path $target 'Autowash.exe'
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Autowash.lnk'))
$shortcut.TargetPath = $exe
$shortcut.Arguments = ''  # un ancien raccourci (version Python) pouvait encore passer main.py en argument
$shortcut.WorkingDirectory = $target
$shortcut.IconLocation = "$exe,0"
$shortcut.Description = 'Autowash - sécurité et santé du PC'
$shortcut.Save()
"Installé : $exe (raccourci sur le Bureau mis à jour)"
