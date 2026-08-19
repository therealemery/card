"""
授权管理面（5 个接口），统一用项目级 X-Admin-Key 鉴权，只能操作本项目下的授权。

card_key 即客户的交易账号（纯数字 4~32 位），由调用方在创建时传入，服务不生成。

状态机：
  suspend  仅 active → suspended
  resume   仅 suspended → active
  revoke   任意状态 → revoked（终态）
  reset    任意状态（含 revoked）→ active，不动 expires_at（误吊销恢复用；期限靠续费调）
续费语义：expires_at = GREATEST(expires_at, NOW()) + days（未过期顺延，已过期从现在起算）
"""
import re
from typing import Optional

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException, Query

from database import get_db
from deps import get_project_by_admin
from schemas import CardCreateRequest, CardPatchRequest, CardRenewRequest

router = APIRouter(prefix="/api/cards", tags=["cards"])

# 各 action 允许的起始状态与目标状态
_TRANSITIONS = {
    "suspend": ("active", "suspended"),
    "resume": ("suspended", "active"),
    "revoke": (("active", "suspended", "revoked"), "revoked"),
    "reset": (("active", "suspended", "revoked"), "active"),
}


def _card_to_dict(row: dict) -> dict:
    def iso(v):
        return v.isoformat() if v else None

    return {
        "card_key": row["card_key"],
        "project_id": row["project_id"],
        "plan_code": row["plan_code"],
        "expires_at": iso(row["expires_at"]),
        "status": row["status"],
        "remark": row["remark"],
        "renewed_from": row["renewed_from"],
        "reminded_at": iso(row["reminded_at"]),
        "expired_notified_at": iso(row["expired_notified_at"]),
        "created_at": iso(row["created_at"]),
    }


def _get_card(cur, card_key: str, project_id: int) -> dict:
    cur.execute(
        "SELECT * FROM cards WHERE card_key = %s AND project_id = %s",
        (card_key, project_id),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="账号不存在")
    return dict(row)


@router.post("", summary="创建授权（card_key = 交易账号）")
def create_card(body: CardCreateRequest, project: dict = Depends(get_project_by_admin)):
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # card_key 是全局主键：任何项目下已存在都视为冲突
            cur.execute("SELECT 1 FROM cards WHERE card_key = %s", (body.card_key,))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="该交易账号已存在授权")
            cur.execute(
                """
                INSERT INTO cards (card_key, project_id, plan_code, remark, expires_at)
                VALUES (%s, %s, %s, %s, NOW() + make_interval(days => %s))
                RETURNING *
                """,
                (body.card_key, project["id"], body.plan_code, body.remark, body.days),
            )
            card = dict(cur.fetchone())
    return {"card": _card_to_dict(card)}


@router.get("", summary="授权列表（?status= / ?expiring_in=7d）")
def list_cards(
    status: Optional[str] = Query(None, pattern="^(active|suspended|revoked)$"),
    expiring_in: Optional[str] = Query(None, description="如 7d，筛 N 天内到期且未过期的"),
    project: dict = Depends(get_project_by_admin),
):
    where = ["project_id = %(pid)s"]
    params = {"pid": project["id"]}

    if status:
        where.append("status = %(status)s")
        params["status"] = status

    if expiring_in:
        m = re.fullmatch(r"(\d+)d", expiring_in)
        if not m:
            raise HTTPException(status_code=400, detail="expiring_in 格式应为如 7d")
        params["days"] = int(m.group(1))
        # 未过期 且 N 天内到期
        where.append("expires_at > NOW() AND expires_at <= NOW() + make_interval(days => %(days)s)")

    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"SELECT * FROM cards WHERE {' AND '.join(where)} ORDER BY created_at DESC",
                params,
            )
            rows = cur.fetchall()
    return {"cards": [_card_to_dict(dict(r)) for r in rows], "count": len(rows)}


@router.get("/{card_key}", summary="授权详情")
def card_detail(card_key: str, project: dict = Depends(get_project_by_admin)):
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            card = _get_card(cur, card_key, project["id"])
    return {"card": _card_to_dict(card)}


@router.post("/{card_key}/renew", summary="续费（GREATEST 语义）")
def renew_card(card_key: str, body: CardRenewRequest, project: dict = Depends(get_project_by_admin)):
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            _get_card(cur, card_key, project["id"])
            cur.execute(
                """
                UPDATE cards
                SET expires_at = GREATEST(expires_at, NOW()) + make_interval(days => %s)
                WHERE card_key = %s
                RETURNING expires_at
                """,
                (body.days, card_key),
            )
            new_expires = cur.fetchone()["expires_at"]
    return {"card_key": card_key, "expires_at": new_expires.isoformat()}


@router.patch("/{card_key}", summary="状态变更 suspend/resume/revoke/reset、改备注（至少给一项）")
def patch_card(card_key: str, body: CardPatchRequest, project: dict = Depends(get_project_by_admin)):
    if body.action is None and body.remark is None:
        raise HTTPException(status_code=422, detail="action 与 remark 至少提供一个")

    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            card = _get_card(cur, card_key, project["id"])

            sets, params = [], []
            if body.action is not None:
                from_states, to_state = _TRANSITIONS[body.action]
                if card["status"] not in from_states:
                    raise HTTPException(
                        status_code=409,
                        detail=f"非法状态流转：{body.action} 不允许从 {card['status']} 发起",
                    )
                sets.append("status = %s")
                params.append(to_state)
            if body.remark is not None:
                sets.append("remark = %s")
                params.append(body.remark)

            params.append(card_key)
            cur.execute(
                f"UPDATE cards SET {', '.join(sets)} WHERE card_key = %s RETURNING *",
                params,
            )
            updated = dict(cur.fetchone())
    return {"card": _card_to_dict(updated)}
