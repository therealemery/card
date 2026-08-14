"""
三种身份：
  require_master        MASTER_KEY（环境变量）—— 仅项目自举接口
  get_project_by_admin  X-Admin-Key（项目级管理密钥）—— 管理面接口
  get_project_by_resolve  Bearer resolve_token（项目级查询密钥）—— /api/resolve
"""
from typing import Optional

import psycopg2.extras
from fastapi import Depends, Header, HTTPException

from config import MASTER_KEY
from database import get_db


def require_master(x_master_key: Optional[str] = Header(None, alias="X-Master-Key")):
    if not x_master_key or x_master_key != MASTER_KEY:
        raise HTTPException(status_code=403, detail="MASTER_KEY 错误")


def _find_project_by(column: str, value: str) -> dict:
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"SELECT id, name, admin_key, resolve_token, callback_url, created_at "
                f"FROM projects WHERE {column} = %s",
                (value,),
            )
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="密钥错误")
    return dict(row)


def get_project_by_admin(x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key")) -> dict:
    if not x_admin_key:
        raise HTTPException(status_code=403, detail="缺少 X-Admin-Key")
    return _find_project_by("admin_key", x_admin_key)


def get_project_by_resolve(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=403, detail="缺少 Bearer token")
    return _find_project_by("resolve_token", authorization[len("Bearer "):])
