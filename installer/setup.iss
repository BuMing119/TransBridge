; TransBridge Inno Setup installer
; Requires Inno Setup 6.x: https://jrsoftware.org/isdl.php

#define AppName "TransBridge"
#ifndef AppVersion
  #error AppVersion must be supplied by build.bat from pyproject.toml
#endif
#define AppPublisher "BuMing119"
#define AppExeName "TransBridge.exe"
#define DistDir "..\dist\TransBridge"

[Setup]
AppId={{A3F2C1D4-5B6E-4F7A-8C9D-0E1F2A3B4C5D}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
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

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "立即运行 {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Only remove obsolete data accidentally stored inside the installation tree.
Type: filesandordirs; Name: "{app}\data"

[Code]
function InitializeUninstall(): Boolean;
begin
  Result := True;
end;

procedure CurUninstallStepChanged(CurStep: TUninstallStep);
var
  DataDir: string;
begin
  if CurStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\TransBridge');
    if DirExists(DataDir) then
    begin
      if MsgBox('是否删除用户数据目录（配置文件、缓存等）？', mbConfirmation, MB_YESNO) = IDYES then
      begin
        DelTree(DataDir, True, True, True);
      end;
    end;
  end;
end;
