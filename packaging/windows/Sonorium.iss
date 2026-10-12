; Sonorium for Windows: installer (Inno Setup 6).
;
;   iscc /DAppVersion=1.6.3 /DSource=<folder> Sonorium.iss
;
; <folder> holds the build: python\, app\, Sonorium.exe (tray console and its files),
; SonoriumHost.exe, SonoriumSecrets.exe, SonoriumUpdater.exe, LICENSE.md, THIRD_PARTY_NOTICES.md.
;
; Install for me only (no administrator rights): %LOCALAPPDATA%\Programs\Sonorium, Sonorium
; runs when you sign in. Install for all users (administrator): C:\Program Files\Sonorium, and
; Sonorium can run as a Windows service that starts with Windows.
;
; Unsigned for now: Windows SmartScreen shows "Windows protected your PC / Unknown publisher"
; the first time; More info > Run anyway.
;
; Silent (the updater): /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER or /ALLUSERS.
; An all-users update keeps the service if it was installed.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef Source
  #define Source "..\..\dist\Sonorium"
#endif

[Setup]
AppId={{B6E1C0D2-5B7A-4D8E-9C1F-3A2B4C5D6E7F}
AppName=Sonorium
AppVersion={#AppVersion}
AppVerName=Sonorium {#AppVersion}
AppPublisher=Sonorium
AppPublisherURL=https://sonorium.app
AppSupportURL=https://github.com/synssins/sonorium/issues
DefaultDirName={autopf}\Sonorium
DefaultGroupName=Sonorium
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
OutputBaseFilename=Sonorium-Setup-{#AppVersion}
SetupIconFile=..\..\desktop\windows\Sonorium.Tray\Assets\Sonorium.ico
UninstallDisplayIcon={app}\Sonorium.exe
UninstallDisplayName=Sonorium
LicenseFile={#Source}\LICENSE.md
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "startup"; Description: "Start Sonorium when I sign in to Windows"
Name: "service"; Description: "Run Sonorium as a Windows service (starts with Windows, before anyone signs in)"; Check: IsAdminInstallMode; Flags: unchecked

[Files]
Source: "{#Source}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Sonorium"; Filename: "{app}\Sonorium.exe"; Parameters: "--settings"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Sonorium"; \
  ValueData: """{app}\Sonorium.exe"""; Tasks: startup; Flags: uninsdeletevalue

[Run]
; As a service: registered (with its folder ACL, the start/stop right for you and the firewall
; rules), then started
Filename: "{app}\SonoriumHost.exe"; Parameters: "install-service"; Check: WantService; Flags: runhidden waituntilterminated; StatusMsg: "Setting up the Sonorium service..."
Filename: "{sys}\sc.exe"; Parameters: "start Sonorium"; Check: WantService; Flags: runhidden waituntilterminated
; The tray console (it starts Sonorium per user); also after a silent update
Filename: "{app}\Sonorium.exe"; Description: "Start Sonorium"; Flags: postinstall nowait runasoriginaluser

[UninstallRun]
Filename: "{app}\SonoriumHost.exe"; Parameters: "uninstall-service"; Flags: runhidden waituntilterminated; Check: IsAdminInstallMode; RunOnceId: "RemoveService"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\app"

[Code]
// Before files are replaced: close the tray console, stop Sonorium (service or per user) and
// whatever Python of ours is still running.
procedure StopSonorium;
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM Sonorium.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  if IsAdminInstallMode then
    Exec(ExpandConstant('{sys}\sc.exe'), 'stop Sonorium', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM SonoriumHost.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(1500);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopSonorium;
  Result := '';
end;

var
  HadService: Boolean;

// An update of an all-users install keeps the service if it was there, even when silent
function InitializeSetup: Boolean;
var
  Code: Integer;
begin
  HadService := Exec(ExpandConstant('{sys}\sc.exe'), 'query Sonorium', '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
  Result := True;
end;

function WantService: Boolean;
begin
  Result := IsAdminInstallMode and (HadService or WizardIsTaskSelected('service'));
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then StopSonorium;
end;
