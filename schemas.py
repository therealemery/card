"""Pydantic 请求模型"""
from typing import Optional

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    callback_url: Optional[str] = None


class CardCreateRequest(BaseModel):
    # card_key 即客户的交易账号：纯数字，4~32 位
    card_key: str = Field(pattern=r"^\d{4,32}$")
    days: int = Field(ge=1)
    remark: str = ""


class CardRenewRequest(BaseModel):
    days: int = Field(ge=1)


# 状态机动作：suspend(active→suspended) / resume(suspended→active) / revoke(任意→revoked)
# reset(任意状态含 revoked → active，不动 expires_at，用于误吊销恢复)
# action 可省略：只改备注不变状态（action 与 remark 至少给一个）
class CardPatchRequest(BaseModel):
    action: Optional[str] = Field(default=None, pattern="^(suspend|resume|revoke|reset)$")
    remark: Optional[str] = None


class ResolveRequest(BaseModel):
    card_key: str = Field(min_length=1)
