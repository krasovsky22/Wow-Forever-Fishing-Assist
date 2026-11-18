; Inno Setup script for the Windows installer: installs wowfish-gui.exe and wowfish.exe into
; one folder, adds a Start menu entry and (optionally, on by default) a desktop shortcut, and
; registers an uninstaller under Settings > Apps.
;
; It installs per user, into %LOCALAPPDATA%\Programs\WoW Forever Fishing Assist, with no admin
; prompt. That folder is writable, so the in-app updater can still swap the .exe in place (it
; cannot inside Program Files). Running a newer installer over an old install upgrades it.
;
; Build with packaging/build_installer.ps1, which passes the defines below to ISCC.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef NumericVersion
  #define NumericVersion "0.0.0.0"
#endif
#ifndef GuiExe
  #define GuiExe "..\dist\wowfish-gui.exe"
#endif
#ifndef CliExe
  #define CliExe "..\dist\wowfish.exe"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif
#ifndef OutputBaseName
  #define OutputBaseName "wowfish-setup"
#endif

#define AppName "WoW Forever Fishing Assist"
#define AppUrl "https://github.com/krasovsky22/Wow-Forever-Fishing-Assist"

[Setup]
; Never change AppId: it is how a new installer finds the old install to upgrade.
AppId={{3CBE8C0F-C3BC-47F1-9DA7-3991994217B7}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=krasovsky22
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
AppUpdatesURL={#AppUrl}/releases
VersionInfoVersion={#NumericVersion}
; With PrivilegesRequired=lowest, {autopf} is %LOCALAPPDATA%\Programs.
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseName}
SetupIconFile=wowfish.ico
UninstallDisplayIcon={app}\wowfish-gui.exe
UninstallDisplayName={#AppName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
; Ask to close a running copy before replacing its files.
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#GuiExe}"; DestDir: "{app}"; DestName: "wowfish-gui.exe"; Flags: ignoreversion
Source: "{#CliExe}"; DestDir: "{app}"; DestName: "wowfish.exe"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\wowfish-gui.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\wowfish-gui.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\wowfish-gui.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Left behind by the in-app updater (update.py) if it was interrupted. Settings live in the
; config folder, not here, so uninstalling keeps them.
Type: files; Name: "{app}\wowfish-gui.exe.old"
Type: files; Name: "{app}\wowfish-gui.exe.new"
Type: files; Name: "{app}\wowfish.exe.old"
Type: files; Name: "{app}\wowfish.exe.new"
Type: dirifempty; Name: "{app}"
