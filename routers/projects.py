"""
项目自举：创建/列出接入方。由环境变量 MASTER_KEY 保护。
admin_key / resolve_token 仅创建时生成并返回一次，请接入方妥善保存。
"""
import secrets

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException

from database import get_db
from deps import require_master
from schemas import ProjectCreate

router = APIRouter(prefix="/api/projects", tags=["projects"], dependencies=[Depends(require_master)])


@router.post("", summary="创建项目")
def create_project(body: ProjectCreate):
    admin_key = secrets.token_hex(32)
    resolve_token = secrets.token_hex(32)
    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO projects (name, admin_key, resolve_token, callback_url)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id, name, callback_url, created_at
                    """,
                    (body.name, admin_key, resolve_token, body.callback_url),
                )
                row = cur.fetchone()
    except Exception as e:
        # name 唯一约束冲突
        if "unique" in str(e).lower():
            raise HTTPException(status_code=409, detail="项目名已存在")
        raise
    return {
        "id": row["id"],
        "name": row["name"],
        "callback_url": row["callback_url"],
        "created_at": row["created_at"].isoformat(),
        "admin_key": admin_key,
        "resolve_token": resolve_token,
    }


@router.get("", summary="列出项目（不回传密钥）")
def list_projects():
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, name, callback_url, created_at FROM projects ORDER BY id"
            )
            rows = cur.fetchall()
    return {
        "projects": [
            {
                "id": r["id"],
                "name": r["name"],
                "callback_url": r["callback_url"],
                "created_at": r["created_at"].isoformat(),
            }
            for r in rows
        ]
    }
