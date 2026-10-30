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
        --icon assets\autowash.ico --add-data "assets\autowash.ico;assets" --collect-data customtkinter main.py
}
"Construit : $PSScriptRoot\dist\Autowash\Autowash.exe"
if ($NoInstall) { return }

if (Get-Process Autowash -ErrorAction SilentlyContinue) { throw "Fermez Autowash avant de le réinstaller." }
$target = Join-Path $env:LOCALAPPDATA 'Programs\Autowash'
if (Test-Path $target) { Remove-Item $target -Recurse -Force -ErrorAction Stop }
Copy-Item dist\Autowash $target -Recurse -ErrorAction Stop

$exe = Join-Path $target 'Autowash.exe'
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Autowash.lnk'))
$shortcut.TargetPath = $exe
$shortcut.Arguments = ''  # un ancien raccourci (version Python) pouvait encore passer main.py en argument
$shortcut.WorkingDirectory = $target
$shortcut.IconLocation = "$exe,0"
$shortcut.Description = 'Autowash - sécurité et santé du PC'
$shortcut.Save()
"Installé : $exe (raccourci sur le Bureau mis à jour)"
