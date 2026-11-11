; Inno Setup script: a per-user Windows installer (no administrator rights needed).
; Built by packaging/build.py when ISCC is available:  ISCC /DAppVersion=0.5.0 installer.iss
;
; Up to 0.4 the app was called voice-annotate. The AppId is the same, so this installer
; updates such an installation: it stops the old app, removes its folder and shortcuts,
; and carries its "start when I sign in" entry over.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F2B8C1E-5D0A-4B7E-9C36-1A7D2E4F8B90}
AppName=Scribble
AppVersion={#AppVersion}
AppPublisher=Johannes Roth
AppPublisherURL=https://github.com/andropar/scribble
AppSupportURL=https://github.com/andropar/scribble/issues
DefaultDirName={localappdata}\Programs\Scribble
DefaultGroupName=Scribble
; Not the folder of voice-annotate.
UsePreviousAppDir=no
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=scribble-windows-setup
SetupIconFile=..\build\scribble.ico
UninstallDisplayIcon={app}\scribble.exe
UninstallDisplayName=Scribble
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
LicenseFile=..\..\LICENSE

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "autostart"; Description: "Start Scribble when I sign in"

[InstallDelete]
; What voice-annotate left behind.
Type: filesandordirs; Name: "{localappdata}\Programs\voice-annotate"
Type: filesandordirs; Name: "{userprograms}\voice-annotate"
Type: files; Name: "{userdesktop}\voice-annotate.lnk"

[Files]
Source: "..\dist\scribble\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Scribble"; Filename: "{app}\scribble.exe"
Name: "{userdesktop}\Scribble"; Filename: "{app}\scribble.exe"; Tasks: desktopicon

[Registry]
; The same value the app's own "Launch at login" setting writes and removes.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Scribble"; ValueData: """{app}\scribble.exe"""; Tasks: autostart; Flags: uninsdeletevalue
; voice-annotate started at sign-in (set in its settings): Scribble does too.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Scribble"; ValueData: """{app}\scribble.exe"""; Check: HadOldAutostart; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "voice-annotate"; Flags: deletevalue

[Run]
Filename: "{app}\scribble.exe"; Description: "Start Scribble"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\scribble-cli.exe"; Parameters: "quit"; Flags: runhidden; RunOnceId: "QuitApp"

[Code]
var
  OldAutostart: Boolean;

function HadOldAutostart(): Boolean;
begin
  Result := OldAutostart;
end;

// A running copy, of this name or the old one, is asked to quit before files are replaced.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
  Old, Current: String;
begin
  OldAutostart := RegValueExists(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'voice-annotate');
  Old := ExpandConstant('{localappdata}\Programs\voice-annotate\voice-annotate-cli.exe');
  Current := ExpandConstant('{app}\scribble-cli.exe');
  if FileExists(Old) then
  begin
    Exec(Old, 'quit', '', SW_HIDE, ewWaitUntilTerminated, Code);
    Sleep(2000);
  end;
  if FileExists(Current) then
  begin
    Exec(Current, 'quit', '', SW_HIDE, ewWaitUntilTerminated, Code);
    Sleep(1000);
  end;
  Result := '';
end;
