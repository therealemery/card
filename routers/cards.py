"""
授权管理面（5 个接口），统一用面板 admin session 鉴权，?project_id= 指定目标项目。

card_key 即客户的交易账号（纯数字 4~32 位），由调用方在创建时传入，服务不生成。

状态机：
  suspend  仅 active → suspended
  resume   仅 suspended → active
  revoke   任意状态 → revoked（终态）
  reset    任意状态（含 revoked）→ active，不动 expires_at（误吊销恢复用；期限靠续费调）
续费语义：expires_at = max(expires_at, now) + days（未过期顺延，已过期从现在起算；
GREATEST 语义在 Python 层计算，与 SQLite 的 ISO 字符串存储配套）
"""
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from database import get_db, utcnow_iso
from deps import get_project_by_session
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
    # 时间字段存的就是 ISO8601 字符串，直接透传
    return {
        "card_key": row["card_key"],
        "project_id": row["project_id"],
        "expires_at": row["expires_at"],
        "status": row["status"],
        "remark": row["remark"],
        "renewed_from": row["renewed_from"],
        "reminded_at": row["reminded_at"],
        "expired_notified_at": row["expired_notified_at"],
        "created_at": row["created_at"],
    }


def _get_card(conn, card_key: str, project_id: int) -> dict:
    row = conn.execute(
        "SELECT * FROM cards WHERE card_key = ? AND project_id = ?",
        (card_key, project_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="账号不存在")
    return dict(row)


@router.post("", summary="创建授权（card_key = 交易账号）")
def create_card(body: CardCreateRequest, project: dict = Depends(get_project_by_session)):
    expires_at = (datetime.now(timezone.utc) + timedelta(days=body.days)).isoformat()
    with get_db() as conn:
        # card_key 是全局主键：任何项目下已存在都视为冲突
        exists = conn.execute(
            "SELECT 1 FROM cards WHERE card_key = ?", (body.card_key,)
        ).fetchone()
        if exists:
            raise HTTPException(status_code=409, detail="该交易账号已存在授权")
        row = conn.execute(
            """
            INSERT INTO cards (card_key, project_id, remark, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?)
            RETURNING *
            """,
            (body.card_key, project["id"], body.remark, expires_at, utcnow_iso()),
        ).fetchone()
    return {"card": _card_to_dict(dict(row))}


@router.get("", summary="授权列表（?status= / ?expiring_in=7d）")
def list_cards(
    status: Optional[str] = Query(None, pattern="^(active|suspended|revoked)$"),
    expiring_in: Optional[str] = Query(None, description="如 7d，筛 N 天内到期且未过期的"),
    project: dict = Depends(get_project_by_session),
):
    where = ["project_id = ?"]
    params: list = [project["id"]]

    if status:
        where.append("status = ?")
        params.append(status)

    if expiring_in:
        m = re.fullmatch(r"(\d+)d", expiring_in)
        if not m:
            raise HTTPException(status_code=400, detail="expiring_in 格式应为如 7d")
        now = datetime.now(timezone.utc)
        deadline = (now + timedelta(days=int(m.group(1)))).isoformat()
        # 未过期 且 N 天内到期（ISO 字符串同格式下字典序即时间序）
        where.append("expires_at > ? AND expires_at <= ?")
        params += [now.isoformat(), deadline]

    with get_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM cards WHERE {' AND '.join(where)} ORDER BY created_at DESC",
            params,
        ).fetchall()
    return {"cards": [_card_to_dict(dict(r)) for r in rows], "count": len(rows)}


@router.get("/{card_key}", summary="授权详情")
def card_detail(card_key: str, project: dict = Depends(get_project_by_session)):
    with get_db() as conn:
        card = _get_card(conn, card_key, project["id"])
    return {"card": _card_to_dict(card)}


@router.post("/{card_key}/renew", summary="续费（GREATEST 语义）")
def renew_card(card_key: str, body: CardRenewRequest, project: dict = Depends(get_project_by_session)):
    with get_db() as conn:
        card = _get_card(conn, card_key, project["id"])
        now = datetime.now(timezone.utc)
        expires = datetime.fromisoformat(card["expires_at"])
        # GREATEST 语义：未过期从原到期顺延，已过期从现在起算
        new_expires = (max(expires, now) + timedelta(days=body.days)).isoformat()
        conn.execute(
            "UPDATE cards SET expires_at = ? WHERE card_key = ?",
            (new_expires, card_key),
        )
    return {"card_key": card_key, "expires_at": new_expires}


@router.patch("/{card_key}", summary="状态变更 suspend/resume/revoke/reset、改备注（至少给一项）")
def patch_card(card_key: str, body: CardPatchRequest, project: dict = Depends(get_project_by_session)):
    if body.action is None and body.remark is None:
        raise HTTPException(status_code=422, detail="action 与 remark 至少提供一个")

    with get_db() as conn:
        card = _get_card(conn, card_key, project["id"])

        sets, params = [], []
        if body.action is not None:
            from_states, to_state = _TRANSITIONS[body.action]
            if card["status"] not in from_states:
                raise HTTPException(
                    status_code=409,
                    detail=f"非法状态流转：{body.action} 不允许从 {card['status']} 发起",
                )
            sets.append("status = ?")
            params.append(to_state)
        if body.remark is not None:
            sets.append("remark = ?")
            params.append(body.remark)

        params.append(card_key)
        row = conn.execute(
            f"UPDATE cards SET {', '.join(sets)} WHERE card_key = ? RETURNING *",
            params,
        ).fetchone()
    return {"card": _card_to_dict(dict(row))}
