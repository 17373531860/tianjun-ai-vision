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
; 升级也永远显示"选择安装位置"页 (客户要求可自选目录).
; 配合下方 [Code] 的搬家逻辑: 升级时选了新目录 -> 新目录安装 + 旧目录自动清除, 不留双份.
DisableDirPage=no
UsePreviousAppDir=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; 开机自启 (可选, 默认不勾选). 工控机/无人值守场景勾上, 普通安装保持原样不污染.
Name: "autostart"; Description: "Auto-start on Windows boot (recommended for industrial PC)"; GroupDescription: "Startup options:"; Flags: unchecked
; WS5(PG): 嵌入式 PostgreSQL 可选组件 (默认不勾选, 勾选 = 高负载/集群站点).
; 编译期开关: CI 把 PG Windows 便携包铺到 ..\dist\pg-portable\ (含 bin\initdb.exe 等)
; 并以 /DIncludePostgres 编译本脚本时, 该任务与文件才进安装包; 否则零影响.
#ifdef IncludePostgres
Name: "pgdb"; Description: "Install embedded PostgreSQL database (for multi-station / high-load sites; default SQLite otherwise)"; GroupDescription: "Database options:"; Flags: unchecked
#endif

[Files]
Source: "..\dist\win-unpacked\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#ifdef IncludePostgres
Source: "..\dist\pg-portable\*"; DestDir: "{app}\pgsql"; Flags: ignoreversion recursesubdirs createallsubdirs; Tasks: pgdb
#endif

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
  // 升级换目录 (搬家) 时待清除的旧安装目录; '' = 本次不搬家
  OldInstallDirToRemove: String;

// 读上次安装目录. Inno 每次安装都把路径刷进本 AppId 的卸载注册表项,
// 64 位安装模式下 HKLM 默认映射 64 位视图, 与写入端一致.
function GetPreviousInstallDir: String;
var
  S: String;
begin
  Result := '';
  if RegQueryStringValue(HKLM, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{com.tianjun.ai-vision}_is1', 'Inno Setup: App Path', S) then
    Result := RemoveBackslashUnlessRoot(S)
  else if RegQueryStringValue(HKLM, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{com.tianjun.ai-vision}_is1', 'InstallLocation', S) then
    Result := RemoveBackslashUnlessRoot(S);
end;

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

// 兼容极老版本把数据库放在安装目录内的情况: 覆盖/搬家前抢救到用户数据目录.
// 同目录覆盖时旧库在 {app} 下; 搬家时旧库在上次安装目录下, 两处都查.
procedure BackupUserDataFrom(const InstDir: String);
begin
  AppDataDir := ExpandConstant('{userappdata}\tianjun-ai-vision');
  if FileExists(InstDir + '\resources\backend\sql_app.db') then
  begin
    ForceDirectories(AppDataDir);
    if not FileExists(AppDataDir + '\sql_app.db') then
      FileCopy(InstDir + '\resources\backend\sql_app.db', AppDataDir + '\sql_app.db', False);
  end;
end;

// 升级换目录 (搬家) 检测: 上次装在 A, 本次选了 B (A<>B) -> 记下 A, 装完后整目录清除.
// 只在"确认 A 里躺着我们的主程序 exe"时才敢删, 防止注册表脏值误删无关目录.
procedure DetectInstallDirMove;
var
  PrevDir: String;
  NewDir: String;
begin
  OldInstallDirToRemove := '';
  PrevDir := GetPreviousInstallDir;
  NewDir := RemoveBackslashUnlessRoot(ExpandConstant('{app}'));
  if (PrevDir = '') or (CompareText(PrevDir, NewDir) = 0) then
    Exit;
  if (Length(PrevDir) > 3) and DirExists(PrevDir)
     and FileExists(PrevDir + '\' + '{#MyAppExeName}') then
  begin
    OldInstallDirToRemove := PrevDir;
    Log('Install dir moved: ' + PrevDir + ' -> ' + NewDir + ', old dir will be removed after install');
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

// PL2303 (Prolific USB 转串口) 驱动预装. 电子秤多走 RS232, 客户用 PL2303 转 USB 接机.
// 两条路 (放哪种文件就走哪条, 全静默, 客户零操作):
//   路 1 (优先, 最干净): 目录有 *.inf -> pnputil /add-driver /install 把驱动灌进系统驱动库,
//          客户插上即自动绑定. 不写死 INF 名 (厂商不同批次命名不一), 枚举目录全部 *.inf 逐个装.
//   路 2 (兜底): 没有 INF (或 pnputil 全失败) 且目录有 *.exe -> 跑厂商官方安装器静默安装.
//          Prolific 官方安装器支持 /s 静默 (官网明示), 装完同样预装进驱动库.
// 目录为空 (未放任何驱动文件) 时整段 no-op, 不影响主程序安装.
procedure InstallPL2303Driver;
var
  ResultCode: Integer;
  DriverDir: String;
  FindRec: TFindRec;
  InfPath: String;
  ExePath: String;
  Installed: Boolean;
begin
  DriverDir := ExpandConstant('{app}\resources\drivers\PL2303');
  if not DirExists(DriverDir) then
    Exit;
  Installed := False;
  // 路 1: pnputil 预装所有 INF
  if FindFirst(DriverDir + '\*.inf', FindRec) then
  begin
    try
      repeat
        InfPath := DriverDir + '\' + FindRec.Name;
        if Exec('pnputil', '/add-driver "' + InfPath + '" /install', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
        begin
          Log('PL2303 pnputil ' + FindRec.Name + ' -> ' + IntToStr(ResultCode));
          if ResultCode = 0 then
            Installed := True;
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
  // 路 2: 没装上则跑厂商官方安装器静默安装 (/s)
  if not Installed then
  begin
    if FindFirst(DriverDir + '\*.exe', FindRec) then
    begin
      try
        repeat
          ExePath := DriverDir + '\' + FindRec.Name;
          Log('PL2303 跑厂商安装器静默装: ' + FindRec.Name);
          Exec(ExePath, '/s', DriverDir, SW_HIDE, ewWaitUntilTerminated, ResultCode);
        until not FindNext(FindRec);
      finally
        FindClose(FindRec);
      end;
    end;
  end;
end;

// v3.47: Windows Defender 排除项.
// 后端是约 1.5GB 的 conda 环境 (上千个 DLL/pyd), 开机后首次进程启动时 Defender
// 实时扫描会把 import torch/cv2 拖成分钟级 — 客户"开机首次启动等十分钟"的主要
// 环境放大器. 安装收尾把安装目录 + 用户数据目录加入排除清单.
// PrivilegesRequired=admin 已满足 Add-MpPreference 的权限要求; 无 Defender /
// 组策略接管 / 第三方杀软的机器上命令静默失败, 不影响安装 (Log 留痕).
procedure AddDefenderExclusions;
var
  ResultCode: Integer;
  Cmd: String;
begin
  Cmd := '-NoProfile -NonInteractive -Command "try { Add-MpPreference -ExclusionPath '''
       + ExpandConstant('{app}') + ''','''
       + ExpandConstant('{userappdata}\tianjun-ai-vision')
       + ''' -ErrorAction Stop; exit 0 } catch { exit 1 }"';
  if Exec('powershell.exe', Cmd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    Log('Defender exclusion add attempted, exit=' + IntToStr(ResultCode))
  else
    Log('Defender exclusion: powershell.exe could not be executed');
end;

// 卸载时把排除项撤掉, 不在客户机上留安全面残留.
procedure RemoveDefenderExclusions;
var
  ResultCode: Integer;
  Cmd: String;
begin
  Cmd := '-NoProfile -NonInteractive -Command "Remove-MpPreference -ExclusionPath '''
       + ExpandConstant('{app}') + ''','''
       + ExpandConstant('{userappdata}\tianjun-ai-vision')
       + ''' -ErrorAction SilentlyContinue"';
  Exec('powershell.exe', Cmd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
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

// WS5(PG): 嵌入式 PostgreSQL 初始化.
// 勾选 pgdb 任务时执行: initdb 数据目录 -> 注册 Windows 服务 -> 启动 -> 建业务库
// -> 写 db_config.json (backend-manager.js 启动后端时据此注入 DATABASE_URL).
// 全程失败只 Log 不中断安装: PG 初始化失败时应用仍以 SQLite 正常可用 (兜底不变).
// 端口固定 5433 (与开发 docker-compose 一致, 避开客户机可能已有的 5432 实例).
procedure SetupEmbeddedPostgres;
var
  ResultCode: Integer;
  PgBin, PgData, PwFile, CfgFile, Pwd: String;
  I: Integer;
begin
  PgBin := ExpandConstant('{app}\pgsql\bin');
  if not FileExists(PgBin + '\initdb.exe') then
  begin
    Log('PG: initdb.exe not found, skip embedded PostgreSQL setup');
    Exit;
  end;
  PgData := ExpandConstant('{userappdata}\tianjun-ai-vision\pgdata');
  CfgFile := ExpandConstant('{userappdata}\tianjun-ai-vision\db_config.json');

  // 已初始化过 (升级安装) -> 只确保服务在跑, 不动数据不改密码.
  if FileExists(PgData + '\PG_VERSION') then
  begin
    Log('PG: existing data dir found, ensure service running');
    Exec(PgBin + '\pg_ctl.exe', 'start -D "' + PgData + '" -w -t 60', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Exit;
  end;

  // 生成每机唯一密码: 随机字母 + 安装时间戳 (Random 种子可能固定, 时间戳兜底唯一性).
  // PG 只监听 127.0.0.1, 密码仅防本机误连, 不承担网络面安全.
  Pwd := '';
  for I := 1 to 8 do
    Pwd := Pwd + Chr(Ord('a') + Random(26));
  Pwd := Pwd + GetDateTimeString('hhnnss', #0, #0);
  PwFile := ExpandConstant('{tmp}\pg_pwfile.txt');
  SaveStringToFile(PwFile, Pwd, False);

  ForceDirectories(PgData);
  if not Exec(PgBin + '\initdb.exe',
      '-D "' + PgData + '" -U tianjun -E UTF8 --auth=scram-sha-256 --pwfile="' + PwFile + '"',
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
  begin
    Log('PG: initdb failed, exit=' + IntToStr(ResultCode) + ', app will fall back to SQLite');
    DeleteFile(PwFile);
    Exit;
  end;
  DeleteFile(PwFile);

  // 端口钉到 5433 (追加到 postgresql.conf 尾部, 后写覆盖先写).
  SaveStringToFile(PgData + '\postgresql.conf',
    #13#10 + 'port = 5433' + #13#10 + 'listen_addresses = ''127.0.0.1''' + #13#10, True);

  // 注册 Windows 服务 (开机自启) 并启动.
  Exec(PgBin + '\pg_ctl.exe',
    'register -N "TianjunPG" -D "' + PgData + '" -S auto',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Log('PG: service register exit=' + IntToStr(ResultCode));
  if not Exec('net', 'start TianjunPG', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
  begin
    Log('PG: service start failed (' + IntToStr(ResultCode) + '), trying pg_ctl start');
    Exec(PgBin + '\pg_ctl.exe', 'start -D "' + PgData + '" -w -t 60', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;

  // 建业务库 (createdb 需要 PGPASSWORD, 用 cmd /c set 传递).
  Exec('cmd.exe',
    '/c set PGPASSWORD=' + Pwd + '&& "' + PgBin + '\createdb.exe" -h 127.0.0.1 -p 5433 -U tianjun tianjun',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Log('PG: createdb exit=' + IntToStr(ResultCode));
  if ResultCode <> 0 then
  begin
    Log('PG: createdb failed, NOT writing db_config.json, app falls back to SQLite');
    Exit;
  end;

  // 写 db_config.json — 后端下次启动即用 PG (空库由后端 create_all 自动建表).
  ForceDirectories(ExpandConstant('{userappdata}\tianjun-ai-vision'));
  SaveStringToFile(CfgFile,
    '{"database_url": "postgresql+psycopg2://tianjun:' + Pwd + '@127.0.0.1:5433/tianjun"}',
    False);
  Log('PG: db_config.json written, backend will use embedded PostgreSQL');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  BackupLicenseFiles;
  DetectInstallDirMove;
  BackupUserDataFrom(ExpandConstant('{app}'));
  if OldInstallDirToRemove <> '' then
    BackupUserDataFrom(OldInstallDirToRemove);
  CleanStaleFrontend;
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    RestoreLicenseFiles;
    InstallCH341Driver;
    InstallPL2303Driver;
    AddDefenderExclusions;
    // WS5(PG): 勾选了嵌入式 PostgreSQL 组件才初始化 (文件已由 [Files] Tasks: pgdb 铺好).
    if WizardIsTaskSelected('pgdb') then
      SetupEmbeddedPostgres;
    // 搬家收尾: 新目录已装好、快捷方式/卸载注册表已指向新目录, 旧目录整体清除.
    // 用户数据在 userappdata, 不在安装目录, 删旧目录不碰数据.
    if OldInstallDirToRemove <> '' then
    begin
      if DelTree(OldInstallDirToRemove, True, True, True) then
        Log('Old install dir removed: ' + OldInstallDirToRemove)
      else
        Log('WARN: old install dir not fully removed (files in use?): ' + OldInstallDirToRemove);
      // 老安装勾过"开机自启"但本次没勾 -> 启动快捷方式还指着旧目录, 搬家后会失效.
      // 存在即按新目录重建 (勾了的话 [Icons] 已重建, 这里重复写一次也无害).
      if FileExists(ExpandConstant('{commonstartup}\TianJun AI Vision.lnk')) then
      begin
        CreateShellLink(ExpandConstant('{commonstartup}\TianJun AI Vision.lnk'),
          'TianJun AI Vision', ExpandConstant('{app}\{#MyAppExeName}'), '',
          ExpandConstant('{app}'), '', 0, SW_SHOWNORMAL);
        Log('Startup shortcut repointed to new install dir');
      end;
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // Keep AppData on uninstall - user data should be preserved
  if CurUninstallStep = usPostUninstall then
    RemoveDefenderExclusions;
end;
