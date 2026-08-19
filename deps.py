"""
身份体系：
  require_master            MASTER_KEY（环境变量）—— 服务器间项目自举/删除调用
  verify_admin_session      面板管理员会话（账号密码登录颁发的 Bearer token）
  get_project_by_session    面板会话 + ?project_id= 定位目标项目 —— 管理面接口
  get_project_by_resolve    Bearer resolve_token（项目级查询密钥）—— /api/resolve

面板（/admin）统一走 admin session；服务器间调用用 X-Master-Key / resolve_token。
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, Header, HTTPException, Query

from config import MASTER_KEY
from database import get_db

_PROJECT_COLS = "id, name, resolve_token, callback_url, created_at"


def require_master(x_master_key: Optional[str] = Header(None, alias="X-Master-Key")):
    if not x_master_key or x_master_key != MASTER_KEY:
        raise HTTPException(status_code=403, detail="MASTER_KEY 错误")


def _bearer_token(authorization: Optional[str]) -> Optional[str]:
    if authorization and authorization.startswith("Bearer "):
        return authorization[len("Bearer "):]
    return None


def _check_session(token: str):
    """admin_sessions 查 token，过期/不存在一律 403"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT expires_at FROM admin_sessions WHERE token = ?", (token,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="会话无效，请重新登录")
    if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=403, detail="会话已过期，请重新登录")


def verify_admin_session(authorization: Optional[str] = Header(None)) -> str:
    """面板会话校验，返回 token（供 logout 删除用）"""
    token = _bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=403, detail="缺少 Bearer token")
    _check_session(token)
    return token


def require_master_or_session(
    x_master_key: Optional[str] = Header(None, alias="X-Master-Key"),
    authorization: Optional[str] = Header(None),
):
    """/api/projects 双通道：X-Master-Key 或面板 admin session"""
    if x_master_key and x_master_key == MASTER_KEY:
        return
    token = _bearer_token(authorization)
    if token:
        _check_session(token)
        return
    raise HTTPException(status_code=403, detail="MASTER_KEY 错误或未登录")


def get_project_by_session(
    project_id: Optional[int] = Query(None),
    authorization: Optional[str] = Header(None),
) -> dict:
    """管理面：面板 admin session + ?project_id= 指定目标项目"""
    token = _bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=403, detail="缺少凭证（管理接口需管理员会话）")
    _check_session(token)
    if project_id is None:
        raise HTTPException(status_code=400, detail="使用管理员会话时必须传 project_id")
    with get_db() as conn:
        row = conn.execute(
            f"SELECT {_PROJECT_COLS} FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="项目不存在")
    return dict(row)


def get_project_by_resolve(authorization: Optional[str] = Header(None)) -> dict:
    token = _bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=403, detail="缺少 Bearer token")
    with get_db() as conn:
        row = conn.execute(
            f"SELECT {_PROJECT_COLS} FROM projects WHERE resolve_token = ?", (token,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="密钥错误")
    return dict(row)
