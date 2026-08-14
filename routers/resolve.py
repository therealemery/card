"""
查询面唯一接口：运行时校验卡号授权。Bearer resolve_token 鉴权，仅可查询本项目卡。

valid 判定：status = active 且未过期。
过期不是状态，由 expires_at 表达，reason 单独给出。
"""
import psycopg2.extras
from fastapi import APIRouter, Depends

from database import get_db
from deps import get_project_by_resolve
from schemas import ResolveRequest

router = APIRouter(prefix="/api", tags=["resolve"])


@router.post("/resolve", summary="校验卡号")
def resolve_card(body: ResolveRequest, project: dict = Depends(get_project_by_resolve)):
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT card_key, status, plan_code, expires_at,
                       (expires_at <= NOW()) AS expired
                FROM cards
                WHERE card_key = %s AND project_id = %s
                """,
                (body.card_key, project["id"]),
            )
            row = cur.fetchone()

    if not row:
        return {"valid": False, "reason": "not_found"}
    if row["status"] == "revoked":
        reason = "revoked"
    elif row["status"] == "suspended":
        reason = "suspended"
    elif row["expired"]:
        reason = "expired"
    else:
        reason = None

    return {
        "valid": reason is None,
        "reason": reason,
        "card_key": row["card_key"],
        "status": row["status"],
        "plan_code": row["plan_code"],
        "expires_at": row["expires_at"].isoformat(),
    }
