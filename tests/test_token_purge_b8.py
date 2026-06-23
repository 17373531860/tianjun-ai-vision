"""B8 过期登录 token 清理（内存缓存 + 落盘表）零风险测试。

只清"过期"token; 无过期时间(持久登录默认)与有效期内 token 一律不动。
"""
from datetime import datetime, timezone, timedelta


def test_b8_purge_removes_only_expired(app):
    from backend.core import auth as auth_mod
    from backend.db.database import SessionLocal
    from backend.models.auth_models import SessionToken

    now = datetime.now(timezone.utc)
    expired_tok = "tok_expired_b8"
    valid_tok = "tok_valid_b8"
    never_tok = "tok_never_b8"

    db = SessionLocal()
    try:
        db.query(SessionToken).filter(
            SessionToken.token.in_([expired_tok, valid_tok, never_tok])
        ).delete(synchronize_session=False)
        db.add(SessionToken(token=expired_tok, user_id=1,
                            expires_at=now - timedelta(hours=1)))
        db.add(SessionToken(token=valid_tok, user_id=1,
                            expires_at=now + timedelta(hours=1)))
        db.add(SessionToken(token=never_tok, user_id=1, expires_at=None))
        db.commit()
    finally:
        db.close()

    # 三个都进内存缓存
    auth_mod.cache_token(expired_tok, 1)
    auth_mod.cache_token(valid_tok, 1)
    auth_mod.cache_token(never_tok, 1)

    removed = auth_mod.purge_expired_tokens()
    assert removed >= 1

    # 过期的: 内存 + 库都没了
    assert auth_mod.resolve_token_cached(expired_tok) is None
    db = SessionLocal()
    try:
        assert db.query(SessionToken).filter(
            SessionToken.token == expired_tok).first() is None
        # 有效 + 无过期: 库里都还在
        assert db.query(SessionToken).filter(
            SessionToken.token == valid_tok).first() is not None
        assert db.query(SessionToken).filter(
            SessionToken.token == never_tok).first() is not None
    finally:
        db.close()

    # 有效 + 无过期: 内存缓存也都还在
    assert auth_mod.resolve_token_cached(valid_tok) == 1
    assert auth_mod.resolve_token_cached(never_tok) == 1


def test_b8_purge_noop_when_nothing_expired(app):
    """没有过期 token 时, 清理是 no-op, 不动任何有效会话。"""
    from backend.core import auth as auth_mod
    tok = "tok_persistent_b8"
    auth_mod.cache_token(tok, 2)
    auth_mod.purge_expired_tokens()
    assert auth_mod.resolve_token_cached(tok) == 2
