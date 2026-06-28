# ==================== 规格→项目 统一匹配 单测 ====================
# 覆盖: 对照表精确 / 通配符(前缀变·后缀变·中间固定·最长键优先) /
#       自动同名子串(前/后/中/无分隔符·取最长) / 严格边界档 / 默认关零差异。
import os

os.environ.setdefault("BACKEND_SKIP_INIT", "1")

from backend.services.project_match import resolve_project_id_by_spec


class _P:
    def __init__(self, pid, name):
        self.id = pid
        self.name = name


class _Q:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _DB:
    def __init__(self, projects):
        self._projects = projects

    def query(self, _model):
        return _Q(self._projects)


def _db(*pairs):
    return _DB([_P(pid, name) for pid, name in pairs])


# ---------- ① 对照表精确 ----------
def test_map_exact():
    pid, hit = resolve_project_id_by_spec(_db(), "HGH20", {"HGH20": 3})
    assert pid == 3 and hit == "对照表"


# ---------- ② 对照表通配符 ----------
def test_wildcard_prefix_fixed_suffix_varies():
    # 前缀固定, 后缀天天变
    pid, hit = resolve_project_id_by_spec(_db(), "HGH20-250628", {"HGH20-*": 3})
    assert pid == 3 and hit == "通配符对照表"


def test_wildcard_suffix_fixed_prefix_varies():
    pid, hit = resolve_project_id_by_spec(_db(), "250628-HGW15", {"*-HGW15": 4})
    assert pid == 4 and hit == "通配符对照表"


def test_wildcard_middle_fixed_both_vary():
    pid, hit = resolve_project_id_by_spec(_db(), "A-ABC-B", {"*ABC*": 5})
    assert pid == 5 and hit == "通配符对照表"


def test_wildcard_longest_key_wins():
    # 通配 * 与 HGH20-* 同时命中 → 取最长键(最具体)
    pid, _ = resolve_project_id_by_spec(_db(), "HGH20-001", {"*": 99, "HGH20-*": 3})
    assert pid == 3


def test_wildcard_question_mark():
    pid, _ = resolve_project_id_by_spec(_db(), "HGH2", {"HGH?": 7})
    assert pid == 7


# ---------- ③ 自动同名子串 (默认关) ----------
def test_name_off_by_default_no_match():
    # match_by_name 默认 False → 子串不生效, 仅认对照表
    pid, hit = resolve_project_id_by_spec(_db((3, "HGH20")), "HGH20-001", {})
    assert pid is None and hit is None


def test_name_exact():
    pid, hit = resolve_project_id_by_spec(_db((3, "HGH20")), "HGH20", {}, match_by_name=True)
    assert pid == 3 and hit == "项目名精确"


def test_name_substring_prefix():
    pid, hit = resolve_project_id_by_spec(_db((3, "HGH20")), "HGH20-001", {}, match_by_name=True)
    assert pid == 3 and hit == "项目名子串"


def test_name_substring_suffix():
    pid, _ = resolve_project_id_by_spec(_db((3, "HGH20")), "001-HGH20", {}, match_by_name=True)
    assert pid == 3


def test_name_substring_middle():
    pid, _ = resolve_project_id_by_spec(_db((3, "HGH20")), "A-HGH20-B", {}, match_by_name=True)
    assert pid == 3


def test_name_substring_no_separator():
    # 无分隔符场景 (非严格档应命中)
    pid, _ = resolve_project_id_by_spec(_db((3, "HGH20")), "HGH20001", {}, match_by_name=True)
    assert pid == 3


def test_name_longest_wins():
    # HG 与 HGH20 都是子串 → 取最长项目名, 不误吞 HG
    pid, _ = resolve_project_id_by_spec(
        _db((1, "HG"), (3, "HGH20")), "HGH20-001", {}, match_by_name=True)
    assert pid == 3


# ---------- 严格边界档 ----------
def test_strict_blocks_short_name_eat():
    # 只有 HG: 非严格会误吞 HGH20-001; 严格档拒绝(右侧贴字母)
    loose, _ = resolve_project_id_by_spec(
        _db((1, "HG")), "HGH20-001", {}, match_by_name=True)
    assert loose == 1
    strict, _ = resolve_project_id_by_spec(
        _db((1, "HG")), "HGH20-001", {}, match_by_name=True, strict_boundary=True)
    assert strict is None


def test_strict_allows_separator_boundary():
    # HGH20- 后接分隔符 → 严格档放行
    pid, _ = resolve_project_id_by_spec(
        _db((3, "HGH20")), "HGH20-001", {}, match_by_name=True, strict_boundary=True)
    assert pid == 3


def test_strict_blocks_no_separator():
    # 无分隔符 HGH20001: 严格档右侧贴数字 → 不命中 (已知代价)
    pid, _ = resolve_project_id_by_spec(
        _db((3, "HGH20")), "HGH20001", {}, match_by_name=True, strict_boundary=True)
    assert pid is None


# ---------- 优先级 & 边界 ----------
def test_map_beats_name():
    # 对照表命中优先于自动同名
    pid, hit = resolve_project_id_by_spec(
        _db((9, "HGH20")), "HGH20", {"HGH20": 3}, match_by_name=True)
    assert pid == 3 and hit == "对照表"


def test_empty_spec():
    pid, hit = resolve_project_id_by_spec(_db((3, "HGH20")), "", {"HGH20": 3}, match_by_name=True)
    assert pid is None and hit is None
