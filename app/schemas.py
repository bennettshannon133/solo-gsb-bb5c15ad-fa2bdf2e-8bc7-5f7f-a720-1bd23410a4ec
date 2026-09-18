"""请求 / 响应模型。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class PolicyIn(BaseModel):
    scope: Literal["global", "tenant", "route"]
    tenant_id: Optional[str] = Field(default=None, description="scope=tenant/route 时必填")
    route_id: Optional[str] = Field(default=None, description="scope=route 时必填")
    capacity: float = Field(..., ge=0, description="桶容量（令牌数上限）")
    refill_rate: float = Field(..., ge=0, description="每秒补充的令牌数（令牌/秒）")
    low_balance_threshold: Optional[float] = Field(
        default=None,
        description="低余额阈值：null 关闭；否则须在 0 到 capacity 之间。"
        "获准裁决令桶从阈值之上降至不高于阈值时产生 low_balance 事件",
    )


class PolicyOut(BaseModel):
    scope: str
    tenant_id: Optional[str]
    route_id: Optional[str]
    capacity: float
    refill_rate: float
    low_balance_threshold: Optional[float]
    updated_at: str


class PolicyRollbackIn(BaseModel):
    scope: Literal["global", "tenant", "route"]
    tenant_id: Optional[str] = Field(default=None, description="scope=tenant/route 时必填")
    route_id: Optional[str] = Field(default=None, description="scope=route 时必填")
    version: int = Field(..., ge=1, description="要恢复到的历史版本号")


class PolicyVersionOut(BaseModel):
    scope: str
    tenant_id: Optional[str]
    route_id: Optional[str]
    version: int
    capacity: float
    refill_rate: float
    low_balance_threshold: Optional[float]
    source: Literal["put", "scheduled", "rollback", "baseline"]
    change_id: Optional[str] = None
    restored_from_version: Optional[int] = None
    created_at: str


class PolicyChangeIn(BaseModel):
    change_id: str = Field(..., min_length=1, description="变更幂等键，全局唯一")
    scope: Literal["global", "tenant", "route"]
    tenant_id: Optional[str] = Field(default=None, description="scope=tenant/route 时必填")
    route_id: Optional[str] = Field(default=None, description="scope=route 时必填")
    capacity: float = Field(..., ge=0, description="生效后的新桶容量")
    refill_rate: float = Field(..., ge=0, description="生效后的新补充速率（令牌/秒）")
    low_balance_threshold: Optional[float] = Field(
        default=None,
        description="生效后的新低余额阈值：null 关闭；否则须在 0 到 capacity 之间",
    )
    effective_at: datetime = Field(
        ...,
        description="生效时刻（UTC），必须晚于当前时间；支持带偏移的 ISO 8601 时间",
    )

    @field_validator("effective_at")
    @classmethod
    def _effective_must_be_utc(cls, v: datetime) -> datetime:
        # 不带时区的时间无法可靠解释为 UTC，拒绝并提示显式带 Z / +00:00
        if v.tzinfo is None:
            raise ValueError("effective_at 必须显式带时区（UTC，例如 2026-10-01T00:00:00Z）")
        return v.astimezone(timezone.utc)


class PolicyChangeOut(BaseModel):
    change_id: str
    scope: str
    tenant_id: Optional[str]
    route_id: Optional[str]
    capacity: float
    refill_rate: float
    low_balance_threshold: Optional[float]
    effective_at: str
    status: Literal["pending", "applied", "cancelled"]
    created_at: str
    applied_at: Optional[str] = None
    cancelled_at: Optional[str] = None


class DecideIn(BaseModel):
    request_id: str = Field(..., min_length=1, description="幂等键，相同 request_id 重放原结果")
    tenant_id: Optional[str] = None
    route_id: Optional[str] = Field(default=None, description="提供 route_id 时必须带 tenant_id")
    cost: float = Field(..., gt=0, description="本次请求需要的令牌数，所有命中桶统一扣减")


class DecideBatchItemIn(BaseModel):
    request_id: str = Field(..., min_length=1, description="单笔裁决幂等键，与 /decide 语义一致")
    tenant_id: Optional[str] = None
    route_id: Optional[str] = Field(default=None, description="提供 route_id 时必须带 tenant_id")
    cost: float = Field(..., gt=0, description="本笔需要的令牌数，所有命中桶统一扣减")


class DecideBatchIn(BaseModel):
    batch_id: str = Field(..., min_length=1, description="批次幂等键，完整载荷一致才允许重放")
    requests: list[DecideBatchItemIn] = Field(
        ...,
        min_length=1,
        max_length=50,
        description="1～50 笔裁决请求，按数组顺序演算共享的多级桶",
    )

    @field_validator("requests")
    @classmethod
    def _route_requires_tenant(cls, items: list[DecideBatchItemIn]) -> list[DecideBatchItemIn]:
        for i, item in enumerate(items):
            if item.route_id and not item.tenant_id:
                raise ValueError(f"requests[{i}] 提供 route_id 时必须同时提供 tenant_id")
        return items


class DecideBatchOut(BaseModel):
    batch_id: str
    committed: bool = Field(..., description="true=整批获准并已落账；false=任一项失败，业务数据未改动")
    size: int
    failed_index: Optional[int] = Field(
        default=None,
        description="导致整批放弃的项下标（从 0 开始）；成功批次为 null",
    )
    reason: Optional[Literal["insufficient", "duplicate_request_id"]] = Field(
        default=None,
        description="失败原因：余额不足 / request_id 已存在（含批次内重复）",
    )
    failed_request_id: Optional[str] = Field(
        default=None, description="失败项的 request_id；成功批次为 null"
    )
    buckets: Optional[list[BucketOut]] = Field(
        default=None,
        description="失败项在演算时刻命中的多级桶状态（含前项扣减后的余额）；成功批次为 null",
    )
    decisions: Optional[list[DecisionOut]] = Field(
        default=None,
        description="成功批次按数组顺序产生的单笔裁决；失败批次为 null",
    )
    created_at: str
    replayed: bool = Field(default=False, description="true=该 batch_id 的完整载荷重放")


class BucketOut(BaseModel):
    scope: str
    tenant_id: Optional[str]
    route_id: Optional[str]
    capacity: float
    refill_rate: float
    balance: float


class DecisionOut(BaseModel):
    request_id: str
    allowed: bool
    cost: float
    tenant_id: Optional[str]
    route_id: Optional[str]
    decided_at: str
    retry_at: Optional[str]
    retry_after_seconds: Optional[float]
    retry_possible: bool
    replayed: bool = False
    revoked: bool = False
    revoked_at: Optional[str] = None
    buckets: list[BucketOut]


class RevokeOut(BaseModel):
    request_id: str
    allowed: bool
    revoked: bool
    revoked_at: Optional[str]
    replayed: bool
    cost: float
    buckets: list[BucketOut]


class LowBalanceEventOut(BaseModel):
    id: int = Field(..., description="单调递增的事件 id，用作分页游标")
    scope: str
    tenant_id: Optional[str]
    route_id: Optional[str]
    balance_before: float = Field(..., description="扣减前（时间补充后）的余额")
    balance_after: float = Field(..., description="扣减后的余额")
    threshold: float = Field(..., description="触发本事件时的低余额阈值")
    request_id: str = Field(..., description="触发本事件的裁决 request_id")
    created_at: str
