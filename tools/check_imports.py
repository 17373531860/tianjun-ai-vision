"""静态扫描 backend/api/*.py + backend/services/*.py:
找出 'foo.bar' / 'foo()' 引用了模块 foo 但 foo 既不在 import 里也不在赋值里的情况。

只扫常见标准库 + 项目熟知的工具函数 (hik_log, debug_log 等)。
保守起见, 不报告动态属性, 只报告 NAME (Name node) 引用 + 顶层 import 缺失。
"""
import ast, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGETS = [ROOT / "backend" / "api", ROOT / "backend" / "services"]

# 常被漏导的标准库 / 项目工具函数
WATCH_NAMES = {
    "os", "sys", "time", "threading", "uuid", "json", "platform",
    "datetime", "traceback", "cv2", "numpy", "np", "torch", "gc",
    "queue", "subprocess", "shutil", "tempfile", "io", "base64",
    "hik_log", "debug_log",
}


def collect_defined_names(tree: ast.AST):
    """收集模块顶层定义的所有名字 (import + assign + def + class)。"""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                names.add((n.asname or n.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for n in node.names:
                names.add(n.asname or n.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    names.add(tgt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    # 内置
    import builtins
    names.update(dir(builtins))
    # 常见 self / cls / args
    names.update({"self", "cls"})
    return names


def collect_used_names(tree: ast.AST):
    """收集所有 Name(Load) 引用。"""
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            used.add(node.id)
    return used


def main():
    issues = []
    for tgt_dir in TARGETS:
        for pyf in tgt_dir.rglob("*.py"):
            if "__pycache__" in str(pyf):
                continue
            try:
                src = pyf.read_text(encoding="utf-8")
                tree = ast.parse(src, filename=str(pyf))
            except Exception as e:
                issues.append(f"[PARSE-ERR] {pyf}: {e}")
                continue
            defined = collect_defined_names(tree)
            used = collect_used_names(tree)
            missing = (used & WATCH_NAMES) - defined
            for m in sorted(missing):
                issues.append(f"{pyf.relative_to(ROOT)}: 使用 `{m}` 但没 import")

    if not issues:
        print("OK: 所有 watch 名字都已正确 import")
        return 0
    print(f"发现 {len(issues)} 处可疑漏 import:")
    for x in issues:
        print("  -", x)
    return 1


if __name__ == "__main__":
    sys.exit(main())
