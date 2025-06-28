; installer.iss
; This script builds the installer for SVE.

#define MyAppName "SVE Simple Video Editor"
#define MyAppVersion "1.0"
#define MyAppPublisher "Maxhem2"
#define MyAppURL "https://github.com/Maxhem2/SVESimpleVideoEditor"
#define MyAppExeName "SVESimpleVideoEditor.exe"

[Setup]
; Unique AppId for your application.
AppId={{2ABE6BDD-5045-4DE9-8BBD-EBA75243B7F2}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=lowest
; The name of the final installer file.
OutputBaseFilename=SVE-Installer-v{#MyAppVersion}
; The output directory for the installer.
OutputDir=.\InstallerOutput
; Use the icon from the root of your repository.
SetupIconFile=.\icon.ico
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Source points to the .exe created by PyInstaller.
; The path is relative to the root of your repository.
Source: ".\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent