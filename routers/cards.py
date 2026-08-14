"""
卡号管理面（6 个接口），统一用项目级 X-Admin-Key 鉴权，只能操作本项目下的卡。

状态机：
  suspend  仅 active → suspended
  resume   仅 suspended → active
  revoke   任意状态 → revoked（终态）
续费语义：expires_at = GREATEST(expires_at, NOW()) + days（未过期顺延，已过期从现在起算）
换卡：老卡 revoked，新卡继承老卡剩余有效期，renewed_from 溯源
"""
import random
import re
import string
from typing import Optional

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException, Query

from database import get_db
from deps import get_project_by_admin
from schemas import CardGenerateRequest, CardPatchRequest, CardRenewRequest

router = APIRouter(prefix="/api/cards", tags=["cards"])

# suspend/resume/revoke 各自允许的起始状态
_TRANSITIONS = {
    "suspend": ("active", "suspended"),
    "resume": ("suspended", "active"),
    "revoke": (("active", "suspended", "revoked"), "revoked"),
}


def generate_card_key() -> str:
    """卡号格式：XXXX-XXXX-XXXX-XXXX（大写字母+数字）"""
    chars = string.ascii_uppercase + string.digits
    return "-".join("".join(random.choices(chars, k=4)) for _ in range(4))


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
        raise HTTPException(status_code=404, detail="卡号不存在")
    return dict(row)


@router.post("", summary="批量生成卡号")
def generate_cards(body: CardGenerateRequest, project: dict = Depends(get_project_by_admin)):
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cards = []
            for _ in range(body.count):
                # 主键冲突概率极低，仍用 SAVEPOINT 兜底重试（不回滚整批）
                for attempt in range(5):
                    card_key = generate_card_key()
                    cur.execute("SAVEPOINT sp_insert")
                    try:
                        cur.execute(
                            """
                            INSERT INTO cards (card_key, project_id, plan_code, remark, expires_at)
                            VALUES (%s, %s, %s, %s, NOW() + make_interval(days => %s))
                            RETURNING *
                            """,
                            (card_key, project["id"], body.plan_code, body.remark, body.days),
                        )
                        cards.append(_card_to_dict(dict(cur.fetchone())))
                        cur.execute("RELEASE SAVEPOINT sp_insert")
                        break
                    except Exception as e:
                        cur.execute("ROLLBACK TO SAVEPOINT sp_insert")
                        if "unique" not in str(e).lower() or attempt == 4:
                            raise
    return {"cards": cards, "count": len(cards)}


@router.get("", summary="卡号列表（?status= / ?expiring_in=7d）")
def list_cards(
    status: Optional[str] = Query(None, pattern="^(active|suspended|revoked)$"),
    expiring_in: Optional[str] = Query(None, description="如 7d，筛 N 天内到期且未过期的卡"),
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


@router.get("/{card_key}", summary="卡号详情")
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


@router.patch("/{card_key}", summary="状态变更 suspend/resume/revoke（可顺带改备注）")
def patch_card(card_key: str, body: CardPatchRequest, project: dict = Depends(get_project_by_admin)):
    from_states, to_state = _TRANSITIONS[body.action]

    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            card = _get_card(cur, card_key, project["id"])

            if card["status"] not in from_states:
                raise HTTPException(
                    status_code=409,
                    detail=f"非法状态流转：{body.action} 不允许从 {card['status']} 发起",
                )

            sets = ["status = %s"]
            params = [to_state]
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


@router.post("/{card_key}/replace", summary="换卡：老卡 revoked，新卡继承剩余有效期")
def replace_card(card_key: str, project: dict = Depends(get_project_by_admin)):
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            old = _get_card(cur, card_key, project["id"])
            if old["status"] == "revoked":
                raise HTTPException(status_code=409, detail="已 revoked 的卡不能换卡")

            cur.execute(
                "UPDATE cards SET status = 'revoked' WHERE card_key = %s",
                (card_key,),
            )
            # 新卡直接沿用老卡 expires_at（即继承剩余有效期；已过期则新卡亦过期）
            for attempt in range(5):
                new_key = generate_card_key()
                cur.execute("SAVEPOINT sp_insert")
                try:
                    cur.execute(
                        """
                        INSERT INTO cards (card_key, project_id, plan_code, remark, expires_at, renewed_from)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        RETURNING *
                        """,
                        (new_key, project["id"], old["plan_code"], old["remark"],
                         old["expires_at"], card_key),
                    )
                    new_card = dict(cur.fetchone())
                    cur.execute("RELEASE SAVEPOINT sp_insert")
                    break
                except Exception as e:
                    cur.execute("ROLLBACK TO SAVEPOINT sp_insert")
                    if "unique" not in str(e).lower() or attempt == 4:
                        raise
    return {"old_card_key": card_key, "card": _card_to_dict(new_card)}
