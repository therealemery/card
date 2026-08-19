"""
到期扫描：进程内 APScheduler 定时跑 run_expiry_scan。

两类事件（webhook POST 到项目 callback_url，header 带该项目 resolve_token 供校验）：
  card.expiring  剩余 ≤ EXPIRING_DAYS 天且未提醒过（reminded_at 去重）
  card.expired   已过期且未通知过（expired_notified_at 去重）
推送失败仅记日志，不重试（v1）。

时间比较：expires_at 存 UTC ISO8601 字符串，now/deadline 在 Python 层算成同格式
字符串传入，SQL 里直接用字典序比较（同格式同偏移下即时间序）。
"""
import logging
from datetime import datetime, timedelta, timezone

import httpx
from apscheduler.schedulers.background import BackgroundScheduler

from config import EXPIRING_DAYS, SCAN_INTERVAL_MINUTES
from database import get_db, utcnow_iso

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _send_webhook(callback_url: str, resolve_token: str, payload: dict) -> bool:
    """推送 webhook，成功返回 True；任何失败只记日志"""
    try:
        resp = httpx.post(
            callback_url,
            json=payload,
            headers={"Authorization": f"Bearer {resolve_token}"},
            timeout=10,
        )
        if resp.status_code >= 400:
            logger.warning("webhook %s 返回 %s: %s", payload["event"], resp.status_code, payload["card_key"])
            return False
        return True
    except Exception:
        logger.exception("webhook %s 推送失败: %s", payload["event"], payload["card_key"])
        return False


def run_expiry_scan() -> dict:
    """
    扫一次到期账号并推送 webhook。返回计数，便于测试与观测。
    标记列（reminded_at / expired_notified_at）只在推送成功后写入，失败则下轮再试。
    """
    sent = {"card.expiring": 0, "card.expired": 0}
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    deadline_iso = (now + timedelta(days=EXPIRING_DAYS)).isoformat()

    with get_db() as conn:
        # 临期：未过期、N 天内到期、未提醒过、仍处于 active
        expiring = conn.execute(
            """
            SELECT c.card_key, c.expires_at, c.project_id,
                   p.callback_url, p.resolve_token
            FROM cards c JOIN projects p ON p.id = c.project_id
            WHERE c.status = 'active'
              AND c.reminded_at IS NULL
              AND c.expires_at > ?
              AND c.expires_at <= ?
              AND p.callback_url IS NOT NULL
            """,
            (now_iso, deadline_iso),
        ).fetchall()

        # 已过期：未通知过（revoked 的不再打扰）
        expired = conn.execute(
            """
            SELECT c.card_key, c.expires_at, c.project_id,
                   p.callback_url, p.resolve_token
            FROM cards c JOIN projects p ON p.id = c.project_id
            WHERE c.status != 'revoked'
              AND c.expired_notified_at IS NULL
              AND c.expires_at <= ?
              AND p.callback_url IS NOT NULL
            """
            ,
            (now_iso,),
        ).fetchall()

        for row in expiring:
            payload = {
                "event": "card.expiring",
                "card_key": row["card_key"],
                "expires_at": row["expires_at"],
                "project_id": row["project_id"],
            }
            if _send_webhook(row["callback_url"], row["resolve_token"], payload):
                conn.execute(
                    "UPDATE cards SET reminded_at = ? WHERE card_key = ?",
                    (utcnow_iso(), row["card_key"]),
                )
                sent["card.expiring"] += 1

        for row in expired:
            payload = {
                "event": "card.expired",
                "card_key": row["card_key"],
                "expires_at": row["expires_at"],
                "project_id": row["project_id"],
            }
            if _send_webhook(row["callback_url"], row["resolve_token"], payload):
                conn.execute(
                    "UPDATE cards SET expired_notified_at = ? WHERE card_key = ?",
                    (utcnow_iso(), row["card_key"]),
                )
                sent["card.expired"] += 1

    return sent


def start_scheduler():
    """起进程内定时任务（幂等）"""
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        run_expiry_scan,
        "interval",
        minutes=SCAN_INTERVAL_MINUTES,
        id="expiry_scan",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    logger.info("到期扫描已启动，间隔 %s 分钟", SCAN_INTERVAL_MINUTES)
    return _scheduler


def stop_scheduler():
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
