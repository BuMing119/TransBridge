; TransBridge Inno Setup installer
; Requires Inno Setup 6.x: https://jrsoftware.org/isdl.php

#define AppName "TransBridge"
#ifndef AppVersion
  #error AppVersion must be supplied by build.bat from pyproject.toml
#endif
#define AppPublisher "BuMing119"
#define AppExeName "TransBridge.exe"
#ifndef DistDir
#define DistDir "..\dist\TransBridge"
#endif

[Setup]
AppId={{A3F2C1D4-5B6E-4F7A-8C9D-0E1F2A3B4C5D}
AppName={#AppName}
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
VersionInfoTextVersion={#AppVersion}
AppPublisher={#AppPublisher}
LicenseFile=..\LICENSE
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
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"; Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "立即运行 {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
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
