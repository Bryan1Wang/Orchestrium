; Inno Setup script for Orchestrium. Compiled by build.ps1, which passes:
;   /DAppVersion=...   version string
;   /DSourceDir=...    the PyInstaller output folder (dist\Orchestrium)
;   /DOutputDir=...    where Orchestrium-Setup-<version>.exe goes
;   /DVpoDir=...       (optional) VPO sample library folder to bundle; omit for
;                      a small installer without the VPO component
;
; VPO licensing: it's redistributable free of charge with credit, but some
; samples are non-commercial only -- an installer that bundles VPO must never
; be sold. See packaging\README.md.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef SourceDir
  #error SourceDir must be defined (run build.ps1)
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif

[Setup]
AppId={{38E8A707-00B0-4A07-885A-7DFF9084DC96}
AppName=Orchestrium
AppVersion={#AppVersion}
AppVerName=Orchestrium {#AppVersion}
AppPublisher=Bryan Wang
AppPublisherURL=https://github.com/Bryan1Wang/Orchestrium
DefaultDirName={autopf}\Orchestrium
DefaultGroupName=Orchestrium
DisableProgramGroupPage=yes
; Installs per-user by default (no admin prompt); the user can choose
; "install for all users" instead, which elevates.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile={#SourceDir}\LICENSE.txt
SetupIconFile={#SourceDir}\assets\icon.ico
UninstallDisplayIcon={app}\Orchestrium.exe
OutputDir={#OutputDir}
OutputBaseFilename=Orchestrium-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=4
WizardStyle=modern
; The bundled-VPO installer is ~1 GB; keep it a single .exe.
DiskSpanning=no

[Types]
Name: "full"; Description: "Full installation (recommended)"
Name: "compact"; Description: "Orchestrium only (I'll choose my own VPO folder in the app)"
Name: "custom"; Description: "Custom"; Flags: iscustom

[Components]
Name: "app"; Description: "Orchestrium"; Types: full compact custom; Flags: fixed
#ifdef VpoDir
Name: "vpo"; Description: "Virtual Playing Orchestra 3 sample library (needed to render the orchestra; free, by Paul Battersby - virtualplaying.com)"; Types: full
#endif

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Components: app; Flags: ignoreversion recursesubdirs createallsubdirs
#ifdef VpoDir
Source: "{#VpoDir}\*"; DestDir: "{app}\VPO"; Components: vpo; Excludes: ".gitkeep"; Flags: ignoreversion recursesubdirs createallsubdirs
; Consolidated credits for every VPO source collection, so attribution travels with the samples.
Source: "VPO_ATTRIBUTION.txt"; DestDir: "{app}\VPO"; DestName: "CREDITS.txt"; Components: vpo; Flags: ignoreversion
#endif

[Icons]
Name: "{autoprograms}\Orchestrium"; Filename: "{app}\Orchestrium.exe"
Name: "{autoprograms}\Orchestrium Credits and Licenses"; Filename: "{app}\CREDITS.txt"
Name: "{autodesktop}\Orchestrium"; Filename: "{app}\Orchestrium.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Orchestrium.exe"; Description: "{cm:LaunchProgram,Orchestrium}"; Flags: nowait postinstall skipifsilent

[Code]
#ifdef VpoDir
function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpSelectComponents) and not WizardIsComponentSelected('vpo') then
    MsgBox('Without the VPO sample library, Orchestrium can''t render the orchestra until you ' +
           'download Virtual Playing Orchestra (virtualplaying.com) yourself and select its ' +
           'folder in the app.', mbInformation, MB_OK);
end;
#endif
