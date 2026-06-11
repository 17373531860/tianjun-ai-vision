"""
v3.5.0 自定义导出系统 — Jinja2 渲染器

职责：
1. 把 ExportTemplate.content + context dict 渲染成最终输出字符串/文件
2. 处理三种 input_file_mode:
   - none           : 直接渲染 ExportTemplate.content
   - read_template  : 从 input_dir 读"客户提供的原文件"作为模板（覆盖 content）
   - append         : 把渲染结果追加到现有 input_dir 中的同名文件后
3. 处理文件名模板（filename_template 也是 Jinja2）
4. 处理写入冲突 overwrite_policy: overwrite / rename / skip
5. 处理编码和换行符 (encoding / newline)

只支持 txt / csv 两种文本格式（docx/xlsx/pdf 走 Step 5 的专用 renderer）。

安全：用 SandboxedEnvironment 防止客户写 `{{ ''.__class__.__mro__ }}` 之类的 RCE。
"""
from __future__ import annotations

import io
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from jinja2.sandbox import SandboxedEnvironment
from jinja2 import StrictUndefined, ChainableUndefined, TemplateError, pass_context

from backend.core.config import BASE_DIR
from backend.core import debug_center


# ============================================================
# Jinja2 环境
# ============================================================

# ChainableUndefined 让 {{ a.b.c }} 在 a 为 None 时不抛异常，返回 undefined
# 这样客户模板里写 {{ workpiece.serial_no | default('-') }} 即使没工件也能渲染
class _SilentUndefined(ChainableUndefined):
    def __str__(self):
        return ""

    def __bool__(self):
        return False

    def __iter__(self):
        return iter([])

    def __len__(self):
        return 0


def _filter_round(value, digits=2):
    if value is None or value == "":
        return ""
    try:
        return round(float(value), int(digits))
    except (TypeError, ValueError):
        return value


def _filter_format_dt(value, fmt="%Y-%m-%d %H:%M:%S"):
    """格式化时间 — 接受 datetime/str/None"""
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        return value.strftime(fmt)
    try:
        # 允许 ISO 字符串
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime(fmt)
    except Exception:
        return str(value)


def _filter_pad(value, width=8, char="0"):
    """前导填充 — 用于对齐数值"""
    if value is None:
        return ""
    return str(value).rjust(int(width), char)


def _filter_pass_fail(value):
    """bool / 'OK' / 'NG' → Pass/Fail"""
    if value is True or value == "OK" or value == "良品" or value == "good":
        return "Pass"
    if value is False or value == "NG" or value == "ng":
        return "Fail"
    return ""


def _filter_yn(value):
    """bool → 是/否"""
    if value is True:
        return "是"
    if value is False:
        return "否"
    return ""


def _filter_yesno(value, yes="OK", no="NG"):
    if value is True:
        return yes
    if value is False:
        return no
    return ""


def _filter_csv_escape(value):
    """CSV 字段转义 — 含逗号/引号/换行时整体加引号"""
    if value is None:
        return ""
    s = str(value)
    if any(c in s for c in [",", '"', "\n", "\r"]):
        return '"' + s.replace('"', '""') + '"'
    return s


# ============================================================
# Jinja2 全局函数 — 外部文件桥接
# ============================================================
# v3.7.2 客户场景: 扫码器旁路 — 扫码器无法接入软件, 但会在固定目录里
# 生成 txt 文件 (空 / 含一行序列号). 客户希望我们读取该目录里最新的
# txt 文件名 (可用作输出文件命名) 和文件内容 (作为周期结果的首行),
# 再把"检测结果 + 步骤时长 + 版本号"追加, 输出到客户指定目录.
#
# 这两个 helper 注册成 Jinja2 globals, 客户在 ExportTemplate.content
# 或 ExportRealtimeRule.filename_template 里直接调用即可:
#
#   {{ latest_input_filename('D:/扫码器/') }}      -> 'XYZ001.txt'
#   {{ latest_input_text('D:/扫码器/') }}          -> 'XYZ001'
#
# 安全: 任何异常 (目录不存在 / 无权限 / 编码错 / 大文件) 都安静返回 '',
# 不让一个 helper 把 cycle_end 链路带崩.

def _glob_latest_file(directory: str, pattern: str = "*.txt",
                       max_age_sec: int = 0) -> Optional[str]:
    """返回目录里按 mtime 排序最新的文件绝对路径; 没有匹配返回 None.

    max_age_sec > 0 时, 过滤掉 mtime 距现在超过 N 秒的文件
    (防止"很久没换码, 误取上一轮旧 txt").
    """
    if not directory:
        return None
    try:
        import glob
        if not os.path.isdir(directory):
            print(f"[ExportRenderer] latest_input: 目录不存在 {directory}", flush=True)
            return None
        files = glob.glob(os.path.join(directory, pattern))
        files = [f for f in files if os.path.isfile(f)]
        if not files:
            print(f"[ExportRenderer] latest_input: {directory} 无匹配 {pattern}",
                  flush=True)
            return None
        if max_age_sec and max_age_sec > 0:
            cutoff = time.time() - max_age_sec
            files = [f for f in files if os.path.getmtime(f) >= cutoff]
            if not files:
                print(f"[ExportRenderer] latest_input: {directory} 内文件均超出 "
                      f"max_age_sec={max_age_sec}s, 拒绝采用", flush=True)
                return None
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return files[0]
    except Exception as e:
        print(f"[ExportRenderer] _glob_latest_file({directory}, {pattern}) 异常: {e}",
              flush=True)
        return None


def _wait_for_stable(path: str, wait_stable_ms: int = 100) -> bool:
    """检查文件 size+mtime 在 wait_stable_ms 间隔后是否一致 (即"写完了").

    返回 True 表示稳定, False 表示文件仍在写或读失败.
    wait_stable_ms <= 0 时直接返回 True (不检查).
    """
    if wait_stable_ms <= 0:
        return True
    try:
        st1 = os.stat(path)
        time.sleep(wait_stable_ms / 1000.0)
        st2 = os.stat(path)
        if st1.st_size == st2.st_size and st1.st_mtime == st2.st_mtime:
            return True
        print(f"[ExportRenderer] _wait_for_stable({path}) 未稳定: "
              f"{st1.st_size}/{st1.st_mtime} -> {st2.st_size}/{st2.st_mtime}",
              flush=True)
        return False
    except Exception as e:
        print(f"[ExportRenderer] _wait_for_stable({path}) 异常: {e}", flush=True)
        return False


def _resolve_dir_from_ctx(ctx) -> str:
    """从渲染上下文里取 ctx.export.input_dir 兜底.

    @pass_context 装饰过的函数, ctx 是 jinja2.runtime.Context.
    实时规则路径 (export_realtime._execute_rule) 会塞 'export.input_dir' 进 ctx.
    """
    try:
        export_ns = ctx.get("export") or {}
        if isinstance(export_ns, dict):
            return export_ns.get("input_dir") or ""
        return getattr(export_ns, "input_dir", "") or ""
    except Exception:
        return ""


def latest_input_filename(directory: str = "", pattern: str = "*.txt",
                          with_ext: bool = True,
                          wait_stable_ms: int = 0,
                          max_age_sec: int = 0) -> str:
    """取目录里 mtime 最新的文件名 (不含路径).

    Python 直接调用版本.
    目录为空 / 不存在 / 无匹配 / 超 max_age_sec -> 返回 ''.
    wait_stable_ms > 0 时, 必须在该毫秒数内文件 size+mtime 不变才采纳.
    """
    p = _glob_latest_file(directory, pattern, max_age_sec=max_age_sec)
    if not p:
        return ""
    if wait_stable_ms > 0 and not _wait_for_stable(p, wait_stable_ms):
        return ""
    name = os.path.basename(p)
    if not with_ext:
        name = os.path.splitext(name)[0]
    return name


def latest_input_text(directory: str = "", pattern: str = "*.txt",
                      encoding: str = "utf-8", strip: bool = True,
                      max_bytes: int = 1024 * 1024,
                      wait_stable_ms: int = 0,
                      max_age_sec: int = 0) -> str:
    """读目录里 mtime 最新文件的文本内容.

    Python 直接调用版本.
    目录为空 / 不存在 / 无匹配 / 超 max_age_sec -> 返回 ''.
    wait_stable_ms > 0 时, 必须在该毫秒数内文件 size+mtime 不变才采纳.
    编码错误用 errors='replace' 兜底. 大于 max_bytes 截断.
    """
    p = _glob_latest_file(directory, pattern, max_age_sec=max_age_sec)
    if not p:
        return ""
    if wait_stable_ms > 0 and not _wait_for_stable(p, wait_stable_ms):
        return ""
    try:
        size = os.path.getsize(p)
        read_size = max_bytes if size > max_bytes else size
        with open(p, "r", encoding=encoding, errors="replace") as f:
            content = f.read(read_size)
        if strip:
            content = content.strip()
        return content
    except Exception as e:
        print(f"[ExportRenderer] latest_input_text({p}) 读取失败: {e}", flush=True)
        return ""


def _resolve_locked_from_ctx(ctx, want: str) -> Optional[str]:
    """C 策略: 从 ctx 里读 cycle_start 锁定的快照. want='filename'|'text'.
    返回 None 表示没有锁定 (调用方应 fallback 到 mtime 路径)."""
    try:
        export_ns = ctx.get("export") or {}
        locked = export_ns.get("locked") if isinstance(export_ns, dict) else None
        if not locked or not isinstance(locked, dict):
            return None
        if want == "filename":
            return locked.get("filename")
        if want == "text":
            return locked.get("text")
    except Exception:
        return None
    return None


def _resolve_int_from_ctx(ctx, key: str, default: int = 0) -> int:
    """从 ctx.export.<key> 读 int. 缺失/异常返回 default."""
    try:
        export_ns = ctx.get("export") or {}
        if isinstance(export_ns, dict):
            v = export_ns.get(key)
            if v is None:
                return default
            return int(v)
    except Exception:
        return default
    return default


@pass_context
def _jinja_latest_input_filename(ctx, directory: str = "",
                                  pattern: str = "*.txt",
                                  with_ext: bool = True,
                                  wait_stable_ms: int = -1,
                                  max_age_sec: int = -1) -> str:
    """Jinja2 模板入口 (装饰版).

    解析顺序:
      1. C 策略 — 若 ctx.export.locked.filename 已被 cycle_start hook 写入, 直接用
      2. 否则按 ctx.export.input_dir / 显式 directory 走 mtime 路径
         wait_stable_ms / max_age_sec 默认 -1 表示"未显式传, 走 ctx.export 配置"
    """
    locked = _resolve_locked_from_ctx(ctx, "filename")
    if locked:
        if not with_ext:
            return os.path.splitext(locked)[0]
        return locked
    if not directory:
        directory = _resolve_dir_from_ctx(ctx)
    if wait_stable_ms < 0:
        wait_stable_ms = _resolve_int_from_ctx(ctx, "wait_stable_ms", 0)
    if max_age_sec < 0:
        max_age_sec = _resolve_int_from_ctx(ctx, "max_age_sec", 0)
    return latest_input_filename(directory, pattern, with_ext,
                                  wait_stable_ms, max_age_sec)


@pass_context
def _jinja_latest_input_text(ctx, directory: str = "",
                              pattern: str = "*.txt",
                              encoding: str = "utf-8",
                              strip: bool = True,
                              max_bytes: int = 1024 * 1024,
                              wait_stable_ms: int = -1,
                              max_age_sec: int = -1) -> str:
    """Jinja2 模板入口 (装饰版).

    解析顺序同 _jinja_latest_input_filename: 先看 ctx.export.locked.text, 再 mtime.
    """
    locked = _resolve_locked_from_ctx(ctx, "text")
    if locked is not None:
        return locked.strip() if strip else locked
    if not directory:
        directory = _resolve_dir_from_ctx(ctx)
    if wait_stable_ms < 0:
        wait_stable_ms = _resolve_int_from_ctx(ctx, "wait_stable_ms", 0)
    if max_age_sec < 0:
        max_age_sec = _resolve_int_from_ctx(ctx, "max_age_sec", 0)
    return latest_input_text(directory, pattern, encoding, strip, max_bytes,
                              wait_stable_ms, max_age_sec)


def _global_now(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """渲染时取当前时间, 给 filename_template 配时间戳命名用.

        {{ now('%Y%m%d_%H%M%S') }}.txt  -> 20260513_023045.txt
    """
    return datetime.now().strftime(fmt)


def _build_env() -> SandboxedEnvironment:
    env = SandboxedEnvironment(
        undefined=_SilentUndefined,
        autoescape=False,
        trim_blocks=False,
        lstrip_blocks=False,
        keep_trailing_newline=True,
    )
    env.filters["round"] = _filter_round  # 覆盖默认 round (允许 1 参数变 0)
    env.filters["format_dt"] = _filter_format_dt
    env.filters["dt"] = _filter_format_dt
    env.filters["pad"] = _filter_pad
    env.filters["pass_fail"] = _filter_pass_fail
    env.filters["yn"] = _filter_yn
    env.filters["yesno"] = _filter_yesno
    env.filters["csv_esc"] = _filter_csv_escape

    # v3.7.2 扫码器旁路场景: 模板里读外部 txt 文件
    # 用装饰版入口让模板可以省略 directory 参数 (自动用 ctx.export.input_dir)
    env.globals["latest_input_filename"] = _jinja_latest_input_filename
    env.globals["latest_input_text"] = _jinja_latest_input_text
    env.globals["now"] = _global_now
    return env


_ENV = _build_env()


# ============================================================
# 渲染入口
# ============================================================

def render_string(template_str: str, context: Dict[str, Any]) -> str:
    """单纯把模板字符串 + context 渲染为字符串

    模板错误（语法 / 沙盒禁用 / 未注册过滤器）会抛 TemplateError 给上层
    用于在前端"实时预览"时显示错误。
    """
    if template_str is None:
        return ""
    tpl = _ENV.from_string(template_str)
    return tpl.render(**context)


def render_filename(filename_template: str, context: Dict[str, Any],
                    fallback_ext: str = "txt") -> str:
    """渲染文件名（也是 Jinja2 模板）

    - 强制把空白字符替换为下划线
    - 自动补默认后缀（如果没写 .xxx）
    - 移除文件名非法字符 < > : " / \\ | ? *
    """
    raw = render_string(filename_template, context).strip()
    if not raw:
        raw = f"export_{int(time.time())}.{fallback_ext}"

    raw = re.sub(r"\s+", "_", raw)
    raw = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw)

    if "." not in os.path.basename(raw):
        raw = f"{raw}.{fallback_ext}"

    return raw


# ============================================================
# 输入文件改写
# ============================================================

def _resolve_input_template(input_file_mode: str,
                            input_dir: Optional[str],
                            filename: str,
                            base_template_content: str) -> Tuple[str, Optional[str], Optional[str]]:
    """根据 input_file_mode 确定真正用来渲染的 template_str

    返回 (template_str, input_path_used, prefix_to_keep)

    - none           : 用 base_template_content
    - read_template  : 读 input_dir/filename 的内容作为 template_str
    - append         : template_str = base_template_content,
                       同时返回 prefix_to_keep = input_dir/filename 的原文件内容
                       （会拼到最终输出文件前面）
    """
    if input_file_mode == "none" or not input_file_mode:
        return base_template_content, None, None

    if not input_dir:
        raise ValueError(f"input_file_mode={input_file_mode} 需要提供 input_dir")

    input_path = os.path.join(input_dir, filename)

    if input_file_mode == "read_template":
        if not os.path.isfile(input_path):
            raise FileNotFoundError(f"输入模板文件不存在: {input_path}")
        with open(input_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(), input_path, None

    if input_file_mode == "append":
        prefix = ""
        if os.path.isfile(input_path):
            with open(input_path, "r", encoding="utf-8", errors="replace") as f:
                prefix = f.read()
            if prefix and not prefix.endswith(("\n", "\r")):
                prefix += "\n"
        return base_template_content, input_path, prefix

    raise ValueError(f"不支持的 input_file_mode: {input_file_mode}")


# ============================================================
# 路径与冲突
# ============================================================

def _resolve_output_dir(output_dir: str) -> str:
    """相对路径相对于 BASE_DIR"""
    if os.path.isabs(output_dir):
        return output_dir
    return os.path.normpath(os.path.join(BASE_DIR, output_dir))


def _apply_overwrite_policy(target_path: str, policy: str) -> Tuple[Optional[str], Optional[str]]:
    """处理写入冲突，返回 (实际写入路径, 跳过原因)

    policy:
      - overwrite : 直接覆盖原文件
      - rename    : 加时间戳后缀
      - skip      : 文件存在则跳过 (返回 (None, "overwrite_skip"))
    """
    if not os.path.exists(target_path):
        return target_path, None

    if policy == "overwrite" or not policy:
        return target_path, None

    if policy == "skip":
        return None, "overwrite_skip"

    if policy == "rename":
        root, ext = os.path.splitext(target_path)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{root}_{ts}{ext}", None

    return target_path, None


def _convert_newline(content: str, newline: str) -> str:
    if newline == "crlf":
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        return normalized.replace("\n", "\r\n")
    if newline == "lf":
        return content.replace("\r\n", "\n").replace("\r", "\n")
    return content


# ============================================================
# 高级渲染 — 文件级
# ============================================================

class RenderResult:
    __slots__ = ("status", "output_path", "file_size", "duration_ms",
                 "skip_reason", "error_msg")

    def __init__(self, status: str, output_path: Optional[str] = None,
                 file_size: Optional[int] = None, duration_ms: int = 0,
                 skip_reason: Optional[str] = None, error_msg: Optional[str] = None):
        self.status = status
        self.output_path = output_path
        self.file_size = file_size
        self.duration_ms = duration_ms
        self.skip_reason = skip_reason
        self.error_msg = error_msg

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status, "output_path": self.output_path,
            "file_size": self.file_size, "duration_ms": self.duration_ms,
            "skip_reason": self.skip_reason, "error_msg": self.error_msg,
        }


def render_to_file(template_content: str,
                   filename_template: str,
                   output_dir: str,
                   context: Dict[str, Any],
                   *,
                   fmt: str = "txt",
                   input_file_mode: str = "none",
                   input_dir: Optional[str] = None,
                   overwrite_policy: str = "overwrite",
                   encoding: str = "utf-8",
                   newline: str = "lf",
                   ensure_dir: bool = True,
                   template_file_path: Optional[str] = None) -> RenderResult:
    """渲染模板并写入文件，返回 RenderResult

    用于：
    - 实时规则触发 (export_realtime_runner — Step 4 调用此函数)
    - 自定义导出 → 立即下载 (Step 3 通过 render_to_string 直接拿字节流，不落本地)

    参数说明同 ExportRealtimeRule 字段。失败时 status='failed'，error_msg 含 traceback。
    """
    t0 = time.perf_counter()
    try:
        if fmt not in ("txt", "csv", "docx", "xlsx", "pdf"):
            return RenderResult(
                status="failed",
                error_msg=f"不支持的格式: {fmt}",
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )

        filename = render_filename(filename_template, context, fallback_ext=fmt)

        out_dir = _resolve_output_dir(output_dir)
        if ensure_dir:
            os.makedirs(out_dir, exist_ok=True)
        target_path = os.path.join(out_dir, filename)
        actual_path, skip_reason = _apply_overwrite_policy(target_path, overwrite_policy)

        if actual_path is None:
            return RenderResult(
                status="skipped", output_path=target_path,
                skip_reason=skip_reason,
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )

        # 二进制格式（docx/xlsx/pdf）走 render_to_bytes — input_file_mode 不支持二进制
        if fmt in ("docx", "xlsx", "pdf"):
            if input_file_mode != "none":
                return RenderResult(
                    status="failed",
                    error_msg=f"input_file_mode={input_file_mode} 不支持二进制格式 {fmt}，"
                              f"请用 fmt=txt/csv 或 input_file_mode=none",
                    duration_ms=int((time.perf_counter() - t0) * 1000),
                )
            raw, _mime = render_to_bytes(
                template_content, context, fmt=fmt,
                template_file_path=template_file_path,
            )
            with open(actual_path, "wb") as f:
                f.write(raw)
            size = os.path.getsize(actual_path)
            return RenderResult(
                status="success", output_path=actual_path,
                file_size=size,
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )

        # 文本格式（txt/csv）原逻辑
        tpl_str, _input_path, prefix = _resolve_input_template(
            input_file_mode, input_dir, filename, template_content
        )

        body = render_string(tpl_str, context)
        if prefix:
            body = prefix + body

        body = _convert_newline(body, newline)

        with open(actual_path, "w", encoding=encoding, errors="replace", newline="") as f:
            f.write(body)

        size = os.path.getsize(actual_path)
        return RenderResult(
            status="success", output_path=actual_path,
            file_size=size,
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    except (TemplateError, FileNotFoundError, ValueError) as e:
        debug_center.dbg("backend.export", "render_to_file 渲染失败", f"fmt={fmt} output_dir={output_dir!r} err={type(e).__name__}: {e}")
        return RenderResult(
            status="failed",
            error_msg=f"{type(e).__name__}: {e}",
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
    except Exception as e:
        import traceback
        debug_center.dbg("backend.export", "render_to_file 未知异常", f"fmt={fmt} output_dir={output_dir!r} err={type(e).__name__}: {e}")
        return RenderResult(
            status="failed",
            error_msg=f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )


def render_to_bytes(template_content: str, context: Dict[str, Any],
                    *, fmt: str = "txt",
                    encoding: str = "utf-8",
                    newline: str = "lf",
                    template_file_path: Optional[str] = None) -> Tuple[bytes, str]:
    """渲染模板返回 bytes + 推荐 mime — 给 HTTP 下载用

    返回 (raw_bytes, mime_type)

    fmt 支持：
    - txt / csv: 纯文本（本模块原生）
    - docx / xlsx / pdf: 转发到对应专用渲染器（v3.5.0 Step 5）

    template_file_path: docx/xlsx 路线 B 用 — 用户上传的占位符模板路径
    """
    if fmt == "docx":
        from backend.services.export_renderer_docx import render_docx_to_bytes
        raw = render_docx_to_bytes(template_content, context, template_file_path)
        return raw, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    if fmt == "xlsx":
        from backend.services.export_renderer_xlsx import render_xlsx_to_bytes
        raw = render_xlsx_to_bytes(template_content, context, template_file_path)
        return raw, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    if fmt == "pdf":
        from backend.services.export_renderer_pdf import render_pdf_to_bytes
        raw = render_pdf_to_bytes(template_content, context, template_file_path)
        return raw, "application/pdf"

    # 默认 txt/csv 走文本路径
    body = render_string(template_content, context)
    body = _convert_newline(body, newline)

    if encoding == "utf-8-sig":
        raw = b"\xef\xbb\xbf" + body.encode("utf-8")
    else:
        raw = body.encode(encoding, errors="replace")

    mime = {
        "txt": "text/plain; charset=" + encoding,
        "csv": "text/csv; charset=" + encoding,
    }.get(fmt, "application/octet-stream")
    return raw, mime
