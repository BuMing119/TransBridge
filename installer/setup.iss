; TransBridge Inno Setup installer
; Requires Inno Setup 6.x: https://jrsoftware.org/isdl.php

#define AppName "TransBridge"
#ifndef AppVersion
  #error AppVersion must be supplied by build.bat from pyproject.toml
#endif
#define AppPublisher "BuMing119"
#define AppExeName "TransBridge.exe"
#ifndef DistDir
#define DistDir AddBackslash(SourcePath) + "..\dist\TransBridge"
#endif

[Setup]
AppId={{A3F2C1D4-5B6E-4F7A-8C9D-0E1F2A3B4C5D}
AppName={#AppName}
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
VersionInfoTextVersion={#AppVersion}
AppPublisher={#AppPublisher}
InfoBeforeFile=..\NOTICE
DefaultDirName={autopf}\{#AppName}
UsePreviousAppDir=yes
CloseApplications=yes
RestartApplications=no
; The runtime is fully replaced. Do not inherit legacy uninstall actions that delete user data.
UninstallLogMode=overwrite
DefaultGroupName={#AppName}
AllowNoIcons=yes
OutputDir=output
OutputBaseFilename=TransBridge_v{#AppVersion}_Setup
Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"; Flags: unchecked

[Files]
; The application links to the centrally installed Microsoft runtime.
Source: "{#DistDir}\*"; Excludes: "msvcp140*.dll,vcruntime140*.dll,vcomp140*.dll,vc-runtime-aliases.iss"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#include AddBackslash(DistDir) + "vc-runtime-aliases.iss"
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "..\NOTICE"; DestDir: "{app}"; DestName: "NOTICE.txt"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "立即运行 {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
var
  SystemDir: String;
begin
  SystemDir := ExpandConstant('{sysnative}\');
  Result :=
    FileExists(SystemDir + 'msvcp140.dll') and
    FileExists(SystemDir + 'msvcp140_1.dll') and
    FileExists(SystemDir + 'msvcp140_2.dll') and
    FileExists(SystemDir + 'vcruntime140.dll') and
    FileExists(SystemDir + 'vcruntime140_1.dll') and
    FileExists(SystemDir + 'vcomp140.dll');
  if not Result then
    SuppressibleMsgBox(
      'Install the Microsoft Visual C++ Redistributable (x64), then run TransBridge Setup again.' + #13#10#13#10 +
      'https://aka.ms/vc14/vc_redist.x64.exe', mbError, MB_OK, IDOK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  RuntimeDir, AppExe: String;
begin
  if CurStep <> ssInstall then
    Exit;

  (* Clean only application-owned binaries immediately before copying new files.
    Never run the legacy uninstaller: it may delete user data.
    AppData, {app}\data and all other user-created files remain untouched.
    DelTree removes reparse points without following them. *)
  RuntimeDir := ExpandConstant('{app}\_internal');
  AppExe := ExpandConstant('{app}\{#AppExeName}');
  if DirExists(RuntimeDir) then
  begin
    Log('Removing previous application runtime: ' + RuntimeDir);
    if not DelTree(RuntimeDir, True, True, True) then
      RaiseException('Cannot remove the previous TransBridge runtime. Close TransBridge and retry. Your user data has not been removed.');
  end;
  if FileExists(AppExe) then
    if not DeleteFile(AppExe) then
      RaiseException('Cannot remove the previous TransBridge executable. Close TransBridge and retry. Your user data has not been removed.');
end;
