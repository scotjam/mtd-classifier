; Inno Setup script for MTD Classifier.
; Build order (from the repo root):
;   1. build\venv\Scripts\pyinstaller --distpath build\dist --workpath build\work -y installer\mtd_classifier.spec
;   2. ISCC installer\mtd-classifier.iss
; Produces build\dist\mtd-classifier-setup-<version>.exe

#define MyAppName "MTD Classifier"
#define MyAppVersion "0.1.0"
#define MyAppExeName "MTD Classifier.exe"
#define MyAppURL "https://github.com/scotjam/mtd-classifier"

[Setup]
AppId={{DB623002-6E94-467A-8659-D3722EF49533}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
; Per-user install: no admin rights needed, lands in %LOCALAPPDATA%\Programs
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\build\dist
OutputBaseFilename=mtd-classifier-setup-{#MyAppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\build\dist\mtd-classifier\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
