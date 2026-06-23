"""
B6 回归测试: /detection/results 步骤截图按内容哈希去重 (默认关)。

痛点: 检测结果接口每 ~150ms 轮询都重发完全相同的 base64 缩略图大字符串。
方案: 前端把已持有指纹经 known_shots 传入 → 后端省略内容未变的截图, 始终回传指纹。

锁定:
  1. 不传 known_shots → result 字节级不动 (无 step_screenshot_hashes, 截图全在);
  2. 传入与当前完全一致的指纹 → 对应截图被省略, 但指纹照常回传;
  3. 指纹不匹配 / 缺失 → 截图照常下发;
  4. 任意异常 → 回退原样, 不破坏主数据。

先红后绿: 把 _apply_screenshot_dedup 短路成 pass → 用例 2/3 FAIL; 恢复后 PASS。
"""
import hashlib

from backend.api.source_routes import _apply_screenshot_dedup


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _make_result():
    return {
        "step_screenshots": {
            "stepA": "AAAA_base64_data",
            "stepB": "BBBB_base64_data",
            "stepC": "CCCC_base64_data",
        },
        "fps": 30,  # 无关字段, 验证不被动
    }


def test_b6_unchanged_screenshots_are_omitted():
    """客户端已持有 A/B 的最新指纹 → A/B 省略, C 仍下发, 指纹全回传。"""
    r = _make_result()
    known = ",".join([
        f"stepA:{_md5('AAAA_base64_data')}",
        f"stepB:{_md5('BBBB_base64_data')}",
    ])
    _apply_screenshot_dedup(r, known)

    shots = r["step_screenshots"]
    assert "stepA" not in shots, "内容未变的 A 应被省略"
    assert "stepB" not in shots, "内容未变的 B 应被省略"
    assert shots.get("stepC") == "CCCC_base64_data", "C 未在客户端 → 必须下发"

    h = r["step_screenshot_hashes"]
    assert h["stepA"] == _md5("AAAA_base64_data")
    assert h["stepB"] == _md5("BBBB_base64_data")
    assert h["stepC"] == _md5("CCCC_base64_data")
    assert r["fps"] == 30, "无关字段不应被改动"


def test_b6_changed_screenshot_is_resent():
    """客户端持有的 A 指纹是旧的 → A 必须重新下发。"""
    r = _make_result()
    known = f"stepA:{_md5('OLD_stale_data')}"
    _apply_screenshot_dedup(r, known)
    assert r["step_screenshots"].get("stepA") == "AAAA_base64_data", \
        "指纹不一致 → A 必须重新下发"


def test_b6_empty_known_sends_all():
    """known_shots 为空字符串 → 没有任何已知指纹 → 全量下发 + 回传指纹。"""
    r = _make_result()
    _apply_screenshot_dedup(r, "")
    assert set(r["step_screenshots"].keys()) == {"stepA", "stepB", "stepC"}
    assert "step_screenshot_hashes" in r


def test_b6_malformed_known_does_not_crash():
    """畸形 known_shots → 不崩, 退化为全量下发。"""
    r = _make_result()
    _apply_screenshot_dedup(r, "garbage_without_colon,:,:::")
    assert set(r["step_screenshots"].keys()) == {"stepA", "stepB", "stepC"}


def test_b6_no_screenshots_key_is_safe():
    """result 没有 step_screenshots 也不应崩。"""
    r = {"fps": 30}
    _apply_screenshot_dedup(r, "stepA:abc")
    assert r["step_screenshots"] == {}
    assert r["step_screenshot_hashes"] == {}
