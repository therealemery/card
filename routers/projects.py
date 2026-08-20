"""
项目自举：创建/列出/删除接入方。双通道：X-Master-Key（服务器间）或面板 admin session。
resolve_token 创建时返回，且列表接口始终可见（供面板复制）。
"""
import secrets
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from database import get_db, utcnow_iso
from deps import require_master_or_session
from schemas import ProjectCreate

router = APIRouter(
    prefix="/api/projects",
    tags=["projects"],
    dependencies=[Depends(require_master_or_session)],
)


@router.post("", summary="创建项目")
def create_project(body: ProjectCreate):
    resolve_token = secrets.token_hex(32)
    try:
        with get_db() as conn:
            # 不用 RETURNING（SQLite < 3.35 不支持），插入后按 lastrowid 回查
            cur = conn.execute(
                """
                INSERT INTO projects (name, resolve_token, callback_url, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (body.name, resolve_token, body.callback_url, utcnow_iso()),
            )
            row = conn.execute(
                "SELECT id, name, resolve_token, callback_url, created_at FROM projects WHERE id = ?",
                (cur.lastrowid,),
            ).fetchone()
    except sqlite3.IntegrityError as e:
        # name 唯一约束冲突
        if "unique" in str(e).lower():
            raise HTTPException(status_code=409, detail="项目名已存在")
        raise
    return {
        "id": row["id"],
        "name": row["name"],
        "callback_url": row["callback_url"],
        "created_at": row["created_at"],
        "resolve_token": row["resolve_token"],
    }


@router.get("", summary="列出项目（含 resolve_token，供面板复制）")
def list_projects():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, resolve_token, callback_url, created_at FROM projects ORDER BY id"
        ).fetchall()
    return {
        "projects": [
            {
                "id": r["id"],
                "name": r["name"],
                "resolve_token": r["resolve_token"],
                "callback_url": r["callback_url"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    }


@router.delete("/{project_id}", summary="删除项目（级联删除其下所有授权）")
def delete_project(project_id: int):
    with get_db() as conn:
        exists = conn.execute(
            "SELECT 1 FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="项目不存在")
        # 先删 cards 再删 project（0001 的 FK 无 ON DELETE CASCADE，代码层级联）
        conn.execute("DELETE FROM cards WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    return {"ok": True, "deleted_project_id": project_id}
