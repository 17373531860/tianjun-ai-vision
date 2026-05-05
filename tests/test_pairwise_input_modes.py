"""Phase 1.4 — input_file_mode × 文本格式 组合测试。

input_file_mode 三种：
  - none:           不读输入文件，直接用 template_content 渲染
  - read_template:  把 input_dir/filename 作为 Jinja2 base 模板（已有 SN.txt 改写）
  - append:         把渲染结果附加到 input_dir/filename 末尾

格式只对文本格式有意义：txt / csv（docx/xlsx/pdf 二进制 input_file_mode 必须 none）
"""
from __future__ import annotations

import os

import pytest

from backend.services.export_renderer import render_to_file


CTX = {
    "app": {"version": "v3.5.0"},
    "system": {"now": "2026-05-06T01:00:00"},
    "cycle": {"id": 42},
    "project": {"name": "t"},
    "stats": {},
    "license": {},
    "display": {},
}


PAIRWISE_INPUT_MODES = [
    ("none", "txt"),
    ("none", "csv"),
    ("read_template", "txt"),
    ("read_template", "csv"),
    ("append", "txt"),
    ("append", "csv"),
]


@pytest.mark.parametrize("mode,fmt", PAIRWISE_INPUT_MODES,
                          ids=[f"{m}-{f}" for m, f in PAIRWISE_INPUT_MODES])
def test_render_to_file_input_modes(tmp_path, mode, fmt):
    """三种 input_file_mode 各跑一次，验证最终文件内容符合预期"""
    output_dir = str(tmp_path / "out")
    input_dir = str(tmp_path / "input")
    os.makedirs(input_dir, exist_ok=True)

    filename_template = f"sample.{fmt}"
    template_content = "GENERATED={{ app.version or 'v3.5.0' }}"

    if mode == "none":
        result = render_to_file(
            template_content,
            filename_template=filename_template,
            output_dir=output_dir,
            context=CTX,
            fmt=fmt,
            input_file_mode="none",
        )
        assert result.status == "success", f"{result.status}: {result.error_msg}"
        content = _read(result.output_path)
        assert "v3.5.0" in content
        return

    # read_template / append 都需要先准备 input 文件
    input_file = os.path.join(input_dir, filename_template)
    if mode == "read_template":
        # 输入文件作为 Jinja2 模板
        with open(input_file, "w", encoding="utf-8") as f:
            f.write("INPUT_HEADER\nVER={{ app.version }}\nINPUT_FOOTER\n")
    else:  # append
        with open(input_file, "w", encoding="utf-8") as f:
            f.write("ORIGINAL_LINE_1\nORIGINAL_LINE_2\n")

    result = render_to_file(
        template_content,
        filename_template=filename_template,
        output_dir=output_dir,
        context=CTX,
        fmt=fmt,
        input_file_mode=mode,
        input_dir=input_dir,
    )
    assert result.status == "success", f"{result.status}: {result.error_msg}"
    content = _read(result.output_path)

    if mode == "read_template":
        assert "INPUT_HEADER" in content, "read_template 应保留输入头"
        assert "v3.5.0" in content, "read_template 应渲染输入中的 Jinja 变量"
    elif mode == "append":
        assert "ORIGINAL_LINE_1" in content, "append 模式应包含原文件内容"
        assert "v3.5.0" in content, "append 模式应附加渲染结果"


@pytest.mark.parametrize("fmt", ["docx", "xlsx", "pdf"])
def test_binary_formats_reject_input_file_mode(tmp_path, fmt):
    """docx/xlsx/pdf 三种二进制格式不支持 input_file_mode != none"""
    output_dir = str(tmp_path / "out")
    input_dir = str(tmp_path / "input")
    os.makedirs(input_dir, exist_ok=True)
    open(os.path.join(input_dir, f"x.{fmt}"), "wb").write(b"dummy")

    result = render_to_file(
        "{{ app.version }}",
        filename_template=f"x.{fmt}",
        output_dir=output_dir,
        context=CTX,
        fmt=fmt,
        input_file_mode="append",
        input_dir=input_dir,
    )
    assert result.status == "failed", f"二进制格式应当拒绝 input_file_mode=append; got {result.status}"
    assert "input_file_mode" in (result.error_msg or "")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()
