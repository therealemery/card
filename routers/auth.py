"""
面板管理员认证：账号密码登录（单一管理员，账号配在环境变量），颁发 Bearer session。
API 密钥体系（X-Admin-Key / X-Master-Key / resolve_token）不受影响。
"""
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from config import ADMIN_PASSWORD, ADMIN_SESSION_DAYS, ADMIN_USERNAME
from database import get_db, utcnow_iso
from deps import verify_admin_session

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login", summary="管理员登录，返回 session token")
def login(body: LoginRequest):
    # 统一提示，不区分用户名错还是密码错
    if body.username != ADMIN_USERNAME or body.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = secrets.token_hex(32)
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(days=ADMIN_SESSION_DAYS)).isoformat()

    with get_db() as conn:
        # 顺手清理过期 session
        conn.execute("DELETE FROM admin_sessions WHERE expires_at <= ?", (now.isoformat(),))
        conn.execute(
            "INSERT INTO admin_sessions (token, created_at, expires_at) VALUES (?, ?, ?)",
            (token, utcnow_iso(), expires_at),
        )
    return {"token": token, "expires_at": expires_at}


@router.post("/logout", summary="退出登录（删除 session）")
def logout(token: str = Depends(verify_admin_session)):
    with get_db() as conn:
        conn.execute("DELETE FROM admin_sessions WHERE token = ?", (token,))
    return {"ok": True}
