"""
查询面唯一接口：运行时校验账号授权。Bearer resolve_token 鉴权，仅可查询本项目账号。

valid 判定：status = active 且未过期。
过期不是状态，由 expires_at 表达，reason 单独给出。
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from database import get_db
from deps import get_project_by_resolve
from schemas import ResolveRequest

router = APIRouter(prefix="/api", tags=["resolve"])


@router.post("/resolve", summary="校验账号授权")
def resolve_card(body: ResolveRequest, project: dict = Depends(get_project_by_resolve)):
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT card_key, status, expires_at
            FROM cards
            WHERE card_key = ? AND project_id = ?
            """,
            (body.card_key, project["id"]),
        ).fetchone()

    if not row:
        return {"valid": False, "reason": "not_found"}

    expired = datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc)
    if row["status"] == "revoked":
        reason = "revoked"
    elif row["status"] == "suspended":
        reason = "suspended"
    elif expired:
        reason = "expired"
    else:
        reason = None

    return {
        "valid": reason is None,
        "reason": reason,
        "card_key": row["card_key"],
        "status": row["status"],
        "expires_at": row["expires_at"],
    }
