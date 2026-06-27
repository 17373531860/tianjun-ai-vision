; Inno Setup script - UTF-8 BOM will be added by CI before compilation
; Version is passed via /DMyAppVersion="x.y.z" on the command line

#define MyAppExeName "tianjun-ai-vision.exe"

[Setup]
AppId={{com.tianjun.ai-vision}
AppName=TianJun AI Vision
AppVersion={#MyAppVersion}
AppPublisher=TianJun Tech
AppCopyright=Copyright (C) 2024 TianJun Tech
DefaultDirName={autopf}\tianjun-ai-vision
DefaultGroupName=TianJun AI Vision
LicenseFile=license.txt
OutputDir=..\dist
OutputBaseFilename=TianJun-AI-Vision-{#MyAppVersion}-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=4
DiskSpanning=no
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern
ShowLanguageDialog=no
DisableWelcomePage=no
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; 开机自启 (可选, 默认不勾选). 工控机/无人值守场景勾上, 普通安装保持原样不污染.
Name: "autostart"; Description: "Auto-start on Windows boot (recommended for industrial PC)"; GroupDescription: "Startup options:"; Flags: unchecked

[Files]
Source: "..\dist\win-unpacked\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\TianJun AI Vision"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\TianJun AI Vision"; Filename: "{app}\{#MyAppExeName}"
; 勾选"开机自启"时, 在全体用户的启动文件夹放快捷方式 (装机为管理员权限, 可写 commonstartup).
; 工控机自动登录单账号场景: 登录后即拉起. 卸载随安装目录清理.
Name: "{commonstartup}\TianJun AI Vision"; Filename: "{app}\{#MyAppExeName}"; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch application"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "pnputil"; Parameters: "/delete-driver ""{app}\resources\drivers\CH341SER\CH341SER.INF"" /uninstall"; Flags: runhidden; RunOnceId: "RemoveCH341"

[Code]
var
  LicenseBackupDir: String;
  AppDataDir: String;

procedure BackupLicenseFiles;
begin
  AppDataDir := ExpandConstant('{userappdata}\tianjun-ai-vision');
  LicenseBackupDir := ExpandConstant('{tmp}\tianjun-license-backup');

  if FileExists(AppDataDir + '\license.lic') then
  begin
    ForceDirectories(LicenseBackupDir);
    FileCopy(AppDataDir + '\license.lic', LicenseBackupDir + '\license.lic', False);
    if FileExists(AppDataDir + '\machine_id.txt') then
      FileCopy(AppDataDir + '\machine_id.txt', LicenseBackupDir + '\machine_id.txt', False);
    if FileExists(AppDataDir + '\hw_verify.txt') then
      FileCopy(AppDataDir + '\hw_verify.txt', LicenseBackupDir + '\hw_verify.txt', False);
    Log('License files backed up');
  end;
end;

procedure BackupUserData;
var
  OldInstDir: String;
begin
  AppDataDir := ExpandConstant('{userappdata}\tianjun-ai-vision');
  OldInstDir := ExpandConstant('{app}');

  if FileExists(OldInstDir + '\resources\backend\sql_app.db') then
  begin
    ForceDirectories(AppDataDir);
    if not FileExists(AppDataDir + '\sql_app.db') then
      FileCopy(OldInstDir + '\resources\backend\sql_app.db', AppDataDir + '\sql_app.db', False);
  end;
end;

procedure RestoreLicenseFiles;
begin
  AppDataDir := ExpandConstant('{userappdata}\tianjun-ai-vision');
  LicenseBackupDir := ExpandConstant('{tmp}\tianjun-license-backup');

  if FileExists(LicenseBackupDir + '\license.lic') then
  begin
    ForceDirectories(AppDataDir);
    if not FileExists(AppDataDir + '\license.lic') then
      FileCopy(LicenseBackupDir + '\license.lic', AppDataDir + '\license.lic', False);
    if FileExists(LicenseBackupDir + '\machine_id.txt') and not FileExists(AppDataDir + '\machine_id.txt') then
      FileCopy(LicenseBackupDir + '\machine_id.txt', AppDataDir + '\machine_id.txt', False);
    if FileExists(LicenseBackupDir + '\hw_verify.txt') and not FileExists(AppDataDir + '\hw_verify.txt') then
      FileCopy(LicenseBackupDir + '\hw_verify.txt', AppDataDir + '\hw_verify.txt', False);
    DelTree(LicenseBackupDir, True, True, True);
    Log('License files restored');
  end;
end;

procedure InstallCH341Driver;
var
  ResultCode: Integer;
  DriverInf: String;
  DriverSetup: String;
begin
  DriverInf := ExpandConstant('{app}\resources\drivers\CH341SER\CH341SER.INF');
  DriverSetup := ExpandConstant('{app}\resources\drivers\CH341SER\SETUP.EXE');

  if Exec('pnputil', '/add-driver "' + DriverInf + '" /install', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    if ResultCode = 0 then
      Log('CH341 driver installed via pnputil')
    else
    begin
      Log('pnputil returned ' + IntToStr(ResultCode) + ', trying SETUP.EXE');
      if FileExists(DriverSetup) then
        Exec(DriverSetup, '/S', ExtractFilePath(DriverSetup), SW_HIDE, ewWaitUntilTerminated, ResultCode);
    end;
  end;
end;

// v3.15.4: 覆盖安装前清掉旧的前端产物目录.
// 前端 assets 用 content-hash 命名 (index-xxxx.js), 升级后新文件名不同, Inno 的
// ignoreversion 只覆盖同名文件, 旧 hash 文件永远残留, 新旧 bundle 混叠 (现场出现
// 5/7 + 5/31 两批文件同存). app/dist 是纯静态可完全重建, 不含任何用户数据
// (用户数据在 userappdata 目录), 安装时 Electron 已关闭不占用, 删了由本次安装重新铺.
// 注意: Inno 的 { } 块注释不支持内部再出现花括号, 故此处用 // 行注释, 文字里不写花括号常量.
procedure CleanStaleFrontend;
var
  DistDir: String;
begin
  DistDir := ExpandConstant('{app}\resources\app\dist');
  if DirExists(DistDir) then
  begin
    DelTree(DistDir, True, True, True);
    Log('Stale frontend dist removed before reinstall: ' + DistDir);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  BackupLicenseFiles;
  BackupUserData;
  CleanStaleFrontend;
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    RestoreLicenseFiles;
    InstallCH341Driver;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  { Keep AppData on uninstall - user data should be preserved }
end;
