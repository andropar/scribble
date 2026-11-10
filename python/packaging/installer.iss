; Inno Setup script: a per-user Windows installer (no administrator rights needed).
; Built by packaging/build.py when ISCC is available:  ISCC /DAppVersion=0.4.0 installer.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F2B8C1E-5D0A-4B7E-9C36-1A7D2E4F8B90}
AppName=voice-annotate
AppVersion={#AppVersion}
AppPublisher=Johannes Roth
AppPublisherURL=https://github.com/andropar/voice-annotate
AppSupportURL=https://github.com/andropar/voice-annotate/issues
DefaultDirName={localappdata}\Programs\voice-annotate
DefaultGroupName=voice-annotate
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=voice-annotate-windows-setup
SetupIconFile=..\build\voice-annotate.ico
UninstallDisplayIcon={app}\voice-annotate.exe
UninstallDisplayName=voice-annotate
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
LicenseFile=..\..\LICENSE

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "autostart"; Description: "Start voice-annotate when I sign in"

[Files]
Source: "..\dist\voice-annotate\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\voice-annotate"; Filename: "{app}\voice-annotate.exe"
Name: "{userdesktop}\voice-annotate"; Filename: "{app}\voice-annotate.exe"; Tasks: desktopicon

[Registry]
; The same value the app's own "Launch at login" setting writes and removes.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "voice-annotate"; ValueData: """{app}\voice-annotate.exe"""; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\voice-annotate.exe"; Description: "Start voice-annotate"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\voice-annotate-cli.exe"; Parameters: "quit"; Flags: runhidden; RunOnceId: "QuitApp"
