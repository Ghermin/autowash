; Installeur Autowash (Inno Setup 6). Construit par release.ps1 :
;   ISCC.exe /DAppVersion=1.0.4 installer\autowash.iss  ->  dist\Autowash-Setup-1.0.4.exe
; Installation par utilisateur (sans droits admin), au même endroit que build.ps1 et la mise à jour automatique.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
; AppId fixe : ne jamais le changer, il relie les versions entre elles (mise à niveau, désinstallation)
AppId={{60D96E95-B24B-4216-865B-C393C3CAA293}
AppName=Autowash
AppVersion={#AppVersion}
AppVerName=Autowash {#AppVersion}
AppPublisher=Ghermin
AppPublisherURL=https://ghermin.github.io/autowash/
AppSupportURL=https://github.com/Ghermin/autowash/issues
AppUpdatesURL=https://github.com/Ghermin/autowash/releases
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\Autowash
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=Autowash-Setup-{#AppVersion}
SetupIconFile=..\assets\autowash.ico
UninstallDisplayIcon={app}\Autowash.exe
; prévient l'Explorateur en fin d'installation : les raccourcis reprennent l'icône du nouvel exe
ChangesAssociations=yes
UninstallDisplayName=Autowash
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; fichiers d'une version précédente qui n'existent plus dans la nouvelle
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\Autowash\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Autowash"; Filename: "{app}\Autowash.exe"
Name: "{autodesktop}\Autowash"; Filename: "{app}\Autowash.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Autowash.exe"; Description: "{cm:LaunchProgram,Autowash}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; tâche planifiée de mise à jour quotidienne, si elle a été activée
Filename: "schtasks.exe"; Parameters: "/Delete /TN ""Autowash AutoUpdate"" /F"; Flags: runhidden; RunOnceId: "DeleteTask"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
