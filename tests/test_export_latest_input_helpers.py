"""v3.7.2 FIX-381-D: 自定义导出新加的 Jinja2 helper 测试

场景: 客户扫码器旁路 — 扫码器无法接入软件, 但会在固定目录里生成 txt 文件
(空 / 含一行序列号). 我们要在 cycle_end 时:
  1) 读该目录里 mtime 最新的 txt 文件名 (用作输出文件命名)
  2) 读该文件内容 (作为输出文件首行保留)
  3) 拼上 检测结果 / 步骤时长 / 版本号 三行后落到客户指定目录

被测对象:
  - latest_input_filename(directory, pattern, with_ext)
  - latest_input_text(directory, pattern, encoding, strip, max_bytes)
  - now(fmt) — 顺带一起测, 给 filename_template 用
  - 三个 helper 在 Jinja2 模板里的端到端行为
"""
from __future__ import annotations

import os
import time

import pytest

from backend.services.export_renderer import (
    _glob_latest_file,
    latest_input_filename,
    latest_input_text,
    _global_now,
    render_string,
    render_to_file,
)


# ============================================================
# latest_input_filename
# ============================================================

class TestLatestInputFilename:

    def test_empty_dir_returns_empty_string(self, tmp_path):
        result = latest_input_filename(str(tmp_path))
        assert result == ""

    def test_nonexistent_dir_returns_empty_string(self):
        result = latest_input_filename("/__nonexistent_path__")
        assert result == ""

    def test_empty_directory_arg_returns_empty(self):
        assert latest_input_filename("") == ""
        assert latest_input_filename(None) == ""  # type: ignore

    def test_single_txt_file(self, tmp_path):
        f = tmp_path / "XYZ001.txt"
        f.write_text("XYZ001", encoding="utf-8")
        result = latest_input_filename(str(tmp_path))
        assert result == "XYZ001.txt"

    def test_picks_most_recent_by_mtime(self, tmp_path):
        old = tmp_path / "old.txt"
        old.write_text("old", encoding="utf-8")
        time.sleep(0.05)  # 保证 mtime 顺序
        new = tmp_path / "new.txt"
        new.write_text("new", encoding="utf-8")
        result = latest_input_filename(str(tmp_path))
        assert result == "new.txt"

    def test_custom_pattern_dat(self, tmp_path):
        txt = tmp_path / "ignore.txt"
        txt.write_text("ignore", encoding="utf-8")
        time.sleep(0.05)
        dat = tmp_path / "real.dat"
        dat.write_text("real", encoding="utf-8")
        # *.dat 应跳过 .txt
        result = latest_input_filename(str(tmp_path), pattern="*.dat")
        assert result == "real.dat"

    def test_with_ext_false_strips_extension(self, tmp_path):
        f = tmp_path / "ABC123.txt"
        f.write_text("x", encoding="utf-8")
        result = latest_input_filename(str(tmp_path), with_ext=False)
        assert result == "ABC123"

    def test_ignores_subdirectories(self, tmp_path):
        sub = tmp_path / "subdir.txt"  # 是目录但用 .txt 后缀
        sub.mkdir()
        time.sleep(0.05)
        # 没有真正的 .txt 文件 → 空
        result = latest_input_filename(str(tmp_path))
        # _glob_latest_file 会 filter isfile, 子目录被滤掉
        assert result == ""


# ============================================================
# latest_input_text
# ============================================================

class TestLatestInputText:

    def test_empty_dir_returns_empty(self, tmp_path):
        assert latest_input_text(str(tmp_path)) == ""

    def test_nonexistent_dir_returns_empty(self):
        assert latest_input_text("/__nonexistent__") == ""

    def test_reads_content(self, tmp_path):
        f = tmp_path / "scan.txt"
        f.write_text("XYZ20260513001", encoding="utf-8")
        assert latest_input_text(str(tmp_path)) == "XYZ20260513001"

    def test_strip_default_true(self, tmp_path):
        f = tmp_path / "scan.txt"
        f.write_text("  XYZ001\n\n", encoding="utf-8")
        assert latest_input_text(str(tmp_path)) == "XYZ001"

    def test_strip_false_preserves(self, tmp_path):
        f = tmp_path / "scan.txt"
        f.write_text("  XYZ001\n", encoding="utf-8")
        assert latest_input_text(str(tmp_path), strip=False) == "  XYZ001\n"

    def test_picks_latest_among_multiple(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f1.write_text("OLD", encoding="utf-8")
        time.sleep(0.05)
        f2 = tmp_path / "b.txt"
        f2.write_text("NEW", encoding="utf-8")
        assert latest_input_text(str(tmp_path)) == "NEW"

    def test_empty_file_returns_empty(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("", encoding="utf-8")
        assert latest_input_text(str(tmp_path)) == ""

    def test_gbk_encoding(self, tmp_path):
        f = tmp_path / "gbk.txt"
        f.write_bytes("序列号XYZ".encode("gbk"))
        assert latest_input_text(str(tmp_path), encoding="gbk") == "序列号XYZ"

    def test_utf8_decoding_replace_on_bad_bytes(self, tmp_path):
        f = tmp_path / "bad.txt"
        # 故意写 GBK 字节, 用 utf-8 读 → errors='replace' 兜底, 不抛
        f.write_bytes("序列号XYZ".encode("gbk"))
        result = latest_input_text(str(tmp_path), encoding="utf-8")
        # 不抛异常即合格, 内容可能有 replace 字符
        assert isinstance(result, str)

    def test_max_bytes_truncation(self, tmp_path):
        f = tmp_path / "big.txt"
        big_payload = "A" * 5000
        f.write_text(big_payload, encoding="utf-8")
        result = latest_input_text(str(tmp_path), max_bytes=100, strip=False)
        # 截到 100 字节 (ASCII 1 byte/char)
        assert len(result) == 100
        assert result == "A" * 100


# ============================================================
# now()
# ============================================================

class TestGlobalNow:

    def test_default_format(self):
        result = _global_now()
        assert len(result) == len("2026-05-13 02:51:00")
        assert "-" in result and ":" in result

    def test_custom_format(self):
        result = _global_now("%Y%m%d")
        assert len(result) == 8
        assert result.isdigit()


# ============================================================
# Jinja2 端到端 — render_string 渲染含 helper 的模板
# ============================================================

class TestHelpersInJinja2:

    def test_filename_helper_in_template(self, tmp_path):
        f = tmp_path / "SERIAL_001.txt"
        f.write_text("SERIAL_001", encoding="utf-8")
        tpl = "out_{{ latest_input_filename('" + str(tmp_path) + "') }}"
        result = render_string(tpl, {})
        assert result == "out_SERIAL_001.txt"

    def test_text_helper_in_template(self, tmp_path):
        f = tmp_path / "scan.txt"
        f.write_text("XYZ-001", encoding="utf-8")
        tpl = "{{ latest_input_text('" + str(tmp_path) + "') }}"
        result = render_string(tpl, {})
        assert result == "XYZ-001"

    def test_full_customer_template(self, tmp_path):
        scan_dir = tmp_path / "scans"
        scan_dir.mkdir()
        (scan_dir / "PROD20260513.txt").write_text("PROD20260513-A1B2", encoding="utf-8")

        # 客户实际模板 (序列号 / 空行 / OK-NG / 步骤时长 / 版本号)
        tpl = (
            "{{ latest_input_text('" + str(scan_dir) + "') }}\n"
            "\n"
            "{{ '合格' if cycle.event in ['cycle_ok','OK'] else '不合格' }}\n"
            "{% for s in cycle.steps %}{{ s.label }}: {{ '%.2f' % (s.duration or 0) }}s"
            "{% if not loop.last %} | {% endif %}{% endfor %}\n"
            "{{ app.version }}"
        )
        ctx = {
            "app": {"version": "v3.7.2"},
            "cycle": {
                "event": "cycle_ok",
                "steps": [
                    {"label": "取件", "duration": 2.34},
                    {"label": "装配", "duration": 5.678},
                    {"label": "检查", "duration": 1.89},
                ],
            },
        }
        result = render_string(tpl, ctx)
        assert "PROD20260513-A1B2" in result
        assert "合格" in result
        assert "取件: 2.34s" in result
        assert "装配: 5.68s" in result
        assert "v3.7.2" in result
        lines = result.split("\n")
        # 行 0: 序列号; 行 1: 空; 行 2: 合格; 行 3: 步骤; 行 4: 版本
        assert lines[0] == "PROD20260513-A1B2"
        assert lines[1] == ""
        assert lines[2] == "合格"
        assert lines[3] == "取件: 2.34s | 装配: 5.68s | 检查: 1.89s"
        assert lines[4] == "v3.7.2"

    def test_template_ng_case(self, tmp_path):
        (tmp_path / "sn.txt").write_text("ABC001", encoding="utf-8")
        tpl = (
            "{{ latest_input_text('" + str(tmp_path) + "') }}\n"
            "{{ '合格' if cycle.event in ['cycle_ok','OK'] else '不合格' }}"
        )
        result = render_string(tpl, {"cycle": {"event": "cycle_ng"}})
        assert "ABC001" in result
        assert "不合格" in result

    def test_template_empty_scan_dir_renders_blank_line(self, tmp_path):
        # 客户配错路径 / 还没扫码 → 第一行空, 但 cycle_end 不能崩
        tpl = (
            "{{ latest_input_text('" + str(tmp_path) + "') }}\n"
            "{{ '合格' if cycle.event == 'cycle_ok' else '不合格' }}"
        )
        result = render_string(tpl, {"cycle": {"event": "cycle_ok"}})
        # 第一行空
        assert result.startswith("\n合格") or result == "\n合格" + "\n" or result.startswith("\n合格")
        # 不抛异常即合格

    def test_ctx_fallback_when_directory_omitted(self, tmp_path):
        """模板里 {{ latest_input_text() }} 不传参 → 自动用 ctx.export.input_dir.

        这是实时规则路径的关键能力: 客户只在表单填 input_dir, 模板里写空参数即可.
        """
        (tmp_path / "auto.txt").write_text("FROM_CTX_INPUT_DIR", encoding="utf-8")
        tpl = "{{ latest_input_text() }} / {{ latest_input_filename() }}"
        result = render_string(tpl, {"export": {"input_dir": str(tmp_path)}})
        assert "FROM_CTX_INPUT_DIR" in result
        assert "auto.txt" in result

    def test_ctx_fallback_returns_empty_when_no_export_key(self):
        """ctx 里没 export 字段时也不报错, 静默返回 ''."""
        tpl = "[{{ latest_input_text() }}][{{ latest_input_filename() }}]"
        result = render_string(tpl, {})
        assert result == "[][]"

    def test_explicit_path_overrides_ctx(self, tmp_path):
        """模板里显式传路径时, 忽略 ctx 里的兜底."""
        ctx_dir = tmp_path / "ctx"
        ctx_dir.mkdir()
        (ctx_dir / "wrong.txt").write_text("FROM_CTX", encoding="utf-8")

        explicit_dir = tmp_path / "explicit"
        explicit_dir.mkdir()
        (explicit_dir / "right.txt").write_text("FROM_EXPLICIT", encoding="utf-8")

        tpl = "{{ latest_input_text('" + str(explicit_dir) + "') }}"
        result = render_string(tpl, {"export": {"input_dir": str(ctx_dir)}})
        assert result == "FROM_EXPLICIT"


# ============================================================
# render_to_file 端到端 — 完整客户场景
# ============================================================

class TestRenderToFileWithHelpers:

    def test_full_scanner_bypass_scenario(self, tmp_path):
        """模拟客户完整链路: 扫码 → 检测 → cycle_end 渲染落盘"""
        scan_dir = tmp_path / "客户扫码器输入"
        scan_dir.mkdir()
        # 客户扫码器生成的 txt (内容 = 序列号)
        (scan_dir / "WP20260513_001.txt").write_text(
            "WP20260513_001", encoding="utf-8"
        )

        output_dir = tmp_path / "客户结果输出"

        # Jinja2 不支持 r'...' raw string, 路径直接 escape 反斜杠.
        scan_dir_lit = str(scan_dir).replace("\\", "\\\\")
        template_content = (
            "{{ latest_input_text('" + scan_dir_lit + "') }}\n"
            "\n"
            "{{ '合格' if cycle.event == 'cycle_ok' else '不合格' }}\n"
            "{% for s in cycle.steps %}{{ s.label }}: {{ '%.2f' % (s.duration or 0) }}s"
            "{% if not loop.last %} | {% endif %}{% endfor %}\n"
            "{{ app.version }}"
        )
        # 输出文件名跟扫码 txt 同名
        filename_template = "{{ latest_input_filename('" + scan_dir_lit + "') }}"

        ctx = {
            "app": {"version": "v3.7.2"},
            "system": {"now": "2026-05-13T02:51:00"},
            "cycle": {
                "id": 42,
                "event": "cycle_ok",
                "steps": [
                    {"label": "取件", "duration": 2.34},
                    {"label": "装配", "duration": 5.67},
                ],
            },
            "project": {"name": "客户工艺"},
            "stats": {},
            "license": {},
            "display": {},
        }

        result = render_to_file(
            template_content=template_content,
            filename_template=filename_template,
            output_dir=str(output_dir),
            context=ctx,
            fmt="txt",
            input_file_mode="none",  # 注意: 用 none, 内容靠 helper 取
            encoding="utf-8",
            newline="crlf",  # Windows 客户
        )

        assert result.status == "success", f"{result.status}: {result.error_msg}"
        # 输出文件应跟扫码 txt 同名
        assert result.output_path.endswith("WP20260513_001.txt")

        content = open(result.output_path, "r", encoding="utf-8", newline="").read()
        # CRLF
        assert "\r\n" in content
        assert "WP20260513_001" in content
        assert "合格" in content
        assert "取件: 2.34s" in content
        assert "v3.7.2" in content

        # 输出文件应位于 output_dir 而非 scan_dir
        assert str(output_dir) in result.output_path
        assert str(scan_dir) not in result.output_path

    def test_filename_via_now_helper(self, tmp_path):
        """时间戳命名 — filename_template 用 now()"""
        output_dir = tmp_path / "out"
        result = render_to_file(
            template_content="hello",
            filename_template="{{ now('%Y%m%d') }}_cycle{{ cycle.id }}.txt",
            output_dir=str(output_dir),
            context={"cycle": {"id": 7}, "app": {}, "system": {}, "project": {},
                     "stats": {}, "license": {}, "display": {}},
            fmt="txt",
        )
        assert result.status == "success", f"{result.status}: {result.error_msg}"
        basename = os.path.basename(result.output_path)
        # 格式: 20260513_cycle7.txt
        assert basename.endswith("_cycle7.txt")
        assert len(basename) == len("20260513_cycle7.txt")

    def test_helper_silent_on_missing_dir(self, tmp_path):
        """客户配错路径 → 渲染照样成功, 内容首行空, 不让 cycle_end 链路崩"""
        output_dir = tmp_path / "out"
        result = render_to_file(
            template_content=(
                "{{ latest_input_text('/__no_such_dir__') }}\n"
                "fallback line"
            ),
            filename_template="output.txt",
            output_dir=str(output_dir),
            context={"app": {}, "cycle": {}, "system": {}, "project": {},
                     "stats": {}, "license": {}, "display": {}},
            fmt="txt",
        )
        assert result.status == "success", f"{result.status}: {result.error_msg}"
        content = open(result.output_path, "r", encoding="utf-8").read()
        assert "fallback line" in content


# ============================================================
# _glob_latest_file 直接测试
# ============================================================

class TestGlobLatestFile:

    def test_returns_none_on_empty_dir(self, tmp_path):
        assert _glob_latest_file(str(tmp_path)) is None

    def test_returns_none_on_empty_directory_arg(self):
        assert _glob_latest_file("") is None
        assert _glob_latest_file(None) is None  # type: ignore

    def test_returns_none_on_nonexistent(self):
        assert _glob_latest_file("/__no_such_path__") is None

    def test_sorts_by_mtime_desc(self, tmp_path):
        a = tmp_path / "a.txt"
        a.write_text("a", encoding="utf-8")
        time.sleep(0.05)
        b = tmp_path / "b.txt"
        b.write_text("b", encoding="utf-8")
        time.sleep(0.05)
        c = tmp_path / "c.txt"
        c.write_text("c", encoding="utf-8")
        # 最新的应是 c
        latest = _glob_latest_file(str(tmp_path))
        assert latest is not None
        assert os.path.basename(latest) == "c.txt"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
