"""插件 hook 示例。"""


def on_cycle_end_post(ctx):
    """cycle_end post_cycle 后置 hook。

    约定：
    - 不抛异常影响主流程
    - 不直接 commit 主程序事务
    - 需要写 DB 时通过 host.get_db_session() 获取受控 session
    """
    cycle_id = ctx.get("cycle_id")
    barcode = ctx.get("barcode") or ctx.get("workpiece_code")
    result = ctx.get("result") or ctx.get("judgement") or "UNKNOWN"

    return {
        "cycle_id": cycle_id,
        "barcode": barcode,
        "result": result,
        "message": "internal-demo hook observed cycle_end",
    }
