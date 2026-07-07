#!/usr/bin/env python3
"""把安装包 exe 分成 N 个分卷（微信传输 <1GB 限制用）+ 生成一键合并 bat。

用法:
    python scripts/release/split_wechat_parts.py <setup.exe> [--parts 7] [--out 输出目录]

产物 (全部放进输出目录, 整个目录发给客户):
    <exe名>.part00.part ... .part{N-1}.part   分卷 (命名与 CI Release 分卷约定一致)
    checksums.txt                              每个分卷的 SHA256 (合并前自动校验)
    merge_installer.bat                        一键合并脚本 (双击运行)

merge_installer.bat 与 CI (.github/workflows/build.yml) 的同名脚本同源同策略,
每条约束都是客户现场翻过车的:
  - 纯 cmd 内置命令 (copy /b + certutil + findstr), 不依赖 powershell
  - ASCII + CRLF + 无 BOM (中文/UTF8-BOM/LF 任一个都会让 cmd 第一行就炸)
  - 不硬编码版本号/文件名, 从 *.part00.part 自动推断输出名
  - echo 文本不含 ( ) ! | & 等 cmd 特殊字符
"""
from __future__ import annotations

import argparse
import hashlib
import math
import sys
from pathlib import Path

MERGE_BAT_LINES = [
    '@echo off',
    'cd /d "%~dp0"',
    'title TianJun AI Vision - Merge Tool',
    '',
    'echo ============================================',
    'echo   TianJun AI Vision - Merge Tool',
    'echo ============================================',
    'echo.',
    'echo Working folder: %cd%',
    '',
    'rem --- detect output exe name from *.part00.part (no hardcoded version) ---',
    'set "P0="',
    "for /f \"delims=\" %%f in ('dir /b *.part00.part 2^>nul') do if not defined P0 set \"P0=%%f\"",
    'if not defined P0 goto NOFILES',
    'set "OUT=%P0:.part00.part=%"',
    '',
    'set PARTCOUNT=0',
    'for %%f in (*.part) do set /a PARTCOUNT+=1',
    '',
    'echo Output file:     %OUT%',
    'echo Parts found:     %PARTCOUNT%',
    'echo.',
    '',
    'if %PARTCOUNT% LSS 2 goto TOOFEW',
    '',
    'rem --- SHA256 verify (best-effort, warn only on mismatch) ---',
    'set BAD=0',
    'set MISSING=0',
    'if not exist "checksums.txt" goto SKIPVERIFY',
    'echo Verifying SHA256 against checksums.txt ...',
    'echo.',
    'for /f "usebackq tokens=1,2" %%a in ("checksums.txt") do call :VERIFY "%%a" "%%b"',
    'if not "%MISSING%"=="0" (',
    '  echo.',
    '  echo [ERROR] %MISSING% part file missing on disk.',
    '  echo Re-send and put all parts in this folder.',
    '  goto FAILPAUSE',
    ')',
    'if not "%BAD%"=="0" (',
    '  echo.',
    '  echo [WARN] %BAD% part file failed SHA256 check.',
    '  echo Continuing merge anyway. If installer fails to run later,',
    '  echo re-send the [BAD] parts.',
    ')',
    'echo.',
    'goto DOMERGE',
    '',
    ':SKIPVERIFY',
    'echo [WARN] checksums.txt not found, skipping SHA256 verification.',
    'echo.',
    '',
    ':DOMERGE',
    'echo Merging %PARTCOUNT% parts into "%OUT%" ...',
    'if exist "%OUT%" del /f /q "%OUT%"',
    'set "ARGS="',
    "for /f \"delims=\" %%p in ('dir /b /o:n *.part') do call :APPEND \"%%p\"",
    'if not defined ARGS goto NOFILES',
    'copy /b %ARGS% "%OUT%" >nul',
    'set "COPYERR=%errorlevel%"',
    'if not "%COPYERR%"=="0" goto MERGEFAIL',
    '',
    'echo.',
    'echo ============================================',
    'echo   Merge complete',
    'echo   File: %OUT%',
    'echo ============================================',
    'for %%i in ("%OUT%") do echo   Size: %%~zi bytes',
    'echo.',
    'echo Double-click the output file to install.',
    'echo.',
    'pause',
    'exit /b 0',
    '',
    ':APPEND',
    'if defined ARGS (',
    '  set "ARGS=%ARGS% + %~1"',
    ') else (',
    '  set "ARGS=%~1"',
    ')',
    'goto :eof',
    '',
    ':VERIFY',
    'if not exist %2 (',
    '  echo   [MISSING] %~2',
    '  set /a MISSING+=1',
    '  goto :eof',
    ')',
    'certutil -hashfile %2 SHA256 | findstr /i /c:%1 >nul',
    'if errorlevel 1 (',
    '  echo   [BAD]  %~2',
    '  set /a BAD+=1',
    '  goto :eof',
    ')',
    'echo   [OK]   %~2',
    'goto :eof',
    '',
    ':NOFILES',
    'echo [ERROR] No *.part00.part file in this folder.',
    'echo.',
    'echo Put ALL these files in the SAME folder as this script:',
    'echo   - every *.part file',
    'echo   - checksums.txt',
    'echo   - merge_installer.bat   ^<-- this script',
    'goto FAILPAUSE',
    '',
    ':TOOFEW',
    'echo [ERROR] Only %PARTCOUNT% .part file found, at least 2 required.',
    'goto FAILPAUSE',
    '',
    ':MERGEFAIL',
    'echo.',
    'echo [ERROR] copy /b failed, exit code %COPYERR%.',
    'echo Possible causes: disk full, access denied, file in use.',
    'goto FAILPAUSE',
    '',
    ':FAILPAUSE',
    'echo.',
    'pause',
    'exit /b 1',
]


def sha256_file(path: Path, chunk=1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest().upper()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('exe', help='安装包 exe 路径')
    ap.add_argument('--parts', type=int, default=7, help='分卷数量 (默认 7)')
    ap.add_argument('--out', default=None, help='输出目录 (默认 exe 同级 wechat-parts/)')
    ap.add_argument('--max-part-mb', type=int, default=1000,
                    help='单卷体积硬上限 MB (默认 1000, 超了直接报错拒绝产出)')
    args = ap.parse_args()

    src = Path(args.exe).expanduser().resolve()
    if not src.is_file():
        sys.exit(f'[错误] 文件不存在: {src}')
    out_dir = Path(args.out).expanduser().resolve() if args.out \
        else src.parent / 'wechat-parts'
    out_dir.mkdir(parents=True, exist_ok=True)

    total = src.stat().st_size
    n = max(2, args.parts)
    chunk_size = math.ceil(total / n)
    if chunk_size > args.max_part_mb * 1024 * 1024:
        sys.exit(f'[错误] {n} 卷时单卷 {chunk_size/1024/1024:.0f}MB 超过 '
                 f'{args.max_part_mb}MB 上限, 请加大 --parts')

    print(f'源文件: {src.name} ({total/1024/1024/1024:.2f} GB)')
    print(f'分卷: {n} 卷, 每卷约 {chunk_size/1024/1024:.0f} MB → {out_dir}')

    checksum_lines = []
    with open(src, 'rb') as f:
        for i in range(n):
            data = f.read(chunk_size)
            if not data:
                break
            part_name = f'{src.name}.part{i:02d}.part'
            part_path = out_dir / part_name
            with open(part_path, 'wb') as pf:
                pf.write(data)
            digest = sha256_file(part_path)
            checksum_lines.append(f'{digest}  {part_name}')
            print(f'  [{i+1}/{n}] {part_name}  {len(data)/1024/1024:.1f} MB  sha256={digest[:12]}...')

    # checksums.txt: 与 CI 同格式 (hash两空格文件名), UTF-8 无 BOM 纯 ASCII 内容
    (out_dir / 'checksums.txt').write_text('\n'.join(checksum_lines) + '\n', encoding='ascii')

    # merge_installer.bat: ASCII + CRLF + 无 BOM (关键! 见模块 docstring)
    bat_bytes = ('\r\n'.join(MERGE_BAT_LINES) + '\r\n').encode('ascii')
    (out_dir / 'merge_installer.bat').write_bytes(bat_bytes)

    print(f'\n完成。整个目录发给客户: {out_dir}')
    print('客户操作: 全部文件放同一文件夹 → 双击 merge_installer.bat → 自动校验+合并出安装包')
    # 自校验: 分卷连起来的 SHA256 必须与源文件一致
    src_digest = sha256_file(src)
    merged = hashlib.sha256()
    for i in range(n):
        p = out_dir / f'{src.name}.part{i:02d}.part'
        if p.exists():
            merged.update(p.read_bytes())
    if merged.hexdigest().upper() == src_digest:
        print(f'自校验通过: 分卷拼接 SHA256 == 源文件 ({src_digest[:16]}...)')
    else:
        sys.exit('[错误] 自校验失败: 分卷拼接与源文件不一致!')


if __name__ == '__main__':
    main()
