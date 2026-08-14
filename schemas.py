"""Pydantic 请求模型"""
from typing import Optional

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    callback_url: Optional[str] = None


class CardGenerateRequest(BaseModel):
    count: int = Field(default=1, ge=1, le=100)
    days: int = Field(default=30, ge=1)
    plan_code: str = ""
    remark: str = ""


class CardRenewRequest(BaseModel):
    days: int = Field(ge=1)


# 状态机动作：suspend(active→suspended) / resume(suspended→active) / revoke(任意→revoked)
class CardPatchRequest(BaseModel):
    action: str = Field(pattern="^(suspend|resume|revoke)$")
    remark: Optional[str] = None


class ResolveRequest(BaseModel):
    card_key: str = Field(min_length=1)
