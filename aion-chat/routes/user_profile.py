"""Manual control of the independent, shared post-sentinel document."""
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

import post_sentinel
from user_profile_policy import STATE_MAX_CHARS

router = APIRouter(prefix='/api/user-profile', tags=['user-profile'])


class ProfileEntry(BaseModel):
    key: str = Field(pattern=r'^[a-zA-Z0-9_.-]{1,100}$')
    category: Literal['recent']
    subcategory: str = Field(default='', max_length=40)
    content: str = Field(min_length=1, max_length=160)
    next_step: str = Field(default='', max_length=80)
    locked: bool = Field(default=False, strict=True)  # Accept old clients; saves are always unlocked.
    topic: str = Field(default='', max_length=40)
    kind: Literal['context', 'project', 'task', 'idea'] = 'context'
    status: Literal['active', 'stale', 'unknown', 'done', 'cancelled', 'retired'] = 'active'
    sustained: bool = Field(default=False, strict=True)
    importance: float = Field(default=0.6, ge=0, le=1, allow_inf_nan=False)
    certainty: Literal['fact', 'inferred', 'conflict'] = 'fact'
    keywords: list[str] = Field(default_factory=list, max_length=8)
    event_at: float | None = Field(default=None, allow_inf_nan=False)
    available_at: float | None = Field(default=None, allow_inf_nan=False)
    due_at: float | None = Field(default=None, allow_inf_nan=False)
    expires_at: float | None = Field(default=None, allow_inf_nan=False)

    @field_validator('content')
    @classmethod
    def trim_content(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('内容不能为空')
        return value


class CurrentState(BaseModel):
    text: str = Field(min_length=1, max_length=STATE_MAX_CHARS)
    phase: Literal['planned', 'ongoing', 'finished', 'unknown']
    expires_at: float = Field(allow_inf_nan=False)
    locked: bool = Field(default=False, strict=True)


class ProfileUpdate(BaseModel):
    revision: int = Field(ge=0, strict=True)
    entries: list[ProfileEntry] = Field(max_length=100)
    current_state: CurrentState | None = None


@router.get('')
async def get_profile(query: str = ''):
    await post_sentinel.ensure_schema()
    return await post_sentinel.snapshot(query=query)


@router.put('')
async def update_profile(body: ProfileUpdate):
    await post_sentinel.ensure_schema()
    try:
        return await post_sentinel.save_manual(body.model_dump(exclude_unset=True), body.revision)
    except ValueError as exc:
        code = 409 if '已更新' in str(exc) else 422
        raise HTTPException(code, str(exc))


@router.post('/initialize')
async def initialize_profile():
    await post_sentinel.ensure_schema()
    if not post_sentinel.enabled():
        raise HTTPException(409, '请先开启近期动态维护')
    from profile_initialize import start
    await start()
    return {'ok': True}
