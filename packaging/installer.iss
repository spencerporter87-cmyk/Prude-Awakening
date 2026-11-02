; Inno Setup script: builds "Prude Awakening Setup.exe"
#ifndef AppVersion
  #define AppVersion "2.0.0"
#endif
#define AppName "Prude Awakening"
#define AppExe "PrudeAwakening.exe"

[Setup]
AppId={{6B0E5C1A-9B7D-4C1E-A6F2-5D3E2B8C7A41}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Spencer Porter
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=Prude-Awakening-Setup-{#AppVersion}
SetupIconFile=..\src\prude\data\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
WizardImageFile=wizard-side.bmp
WizardSmallImageFile=wizard-small.bmp
WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "sendto"; Description: "Add ""Clean with Prude Awakening"" when right-clicking video files"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\PrudeAwakening\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\video\shell\PrudeAwakening"; ValueType: string; ValueName: ""; ValueData: "Clean with Prude Awakening"; Flags: uninsdeletekey; Tasks: sendto
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\video\shell\PrudeAwakening"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\{#AppExe}"""; Tasks: sendto
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\video\shell\PrudeAwakening\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: sendto

[Run]
Filename: "{app}\{#AppExe}"; Description: "Open {#AppName} now"; Flags: nowait postinstall skipifsilent
