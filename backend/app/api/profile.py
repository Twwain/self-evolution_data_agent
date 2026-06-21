"""Profile CRUD API — extractor_profiles 管理 (require_admin_or_above)."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin_or_above
from app.db.metadata import get_db
from app.models.extractor_profile import ExtractorProfile
from app.models.user import User

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


class ProfileCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    display_name: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    languages: list[str] = Field(default_factory=lambda: ["Java"])
    hint_text: str = ""


class ProfileUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    languages: list[str] | None = None
    hint_text: str | None = None
    is_enabled: bool | None = None


class ProfileOut(BaseModel):
    id: int
    name: str
    display_name: str
    description: str
    languages: list[str]
    hint_text: str
    is_builtin: bool
    is_enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


@router.get("", response_model=list[ProfileOut])
async def list_profiles(
    actor: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ExtractorProfile).order_by(
        ExtractorProfile.is_builtin.desc(), ExtractorProfile.name
    ))
    return result.scalars().all()


@router.post("", response_model=ProfileOut)
async def create_profile(
    data: ProfileCreate,
    actor: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(
        select(ExtractorProfile).where(ExtractorProfile.name == data.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(400, f"Profile '{data.name}' 已存在")
    pf = ExtractorProfile(
        name=data.name, display_name=data.display_name,
        description=data.description, languages=data.languages,
        hint_text=data.hint_text, is_builtin=False,
    )
    db.add(pf)
    await db.commit()
    await db.refresh(pf)
    return pf


@router.get("/{profile_id}", response_model=ProfileOut)
async def get_profile(
    profile_id: int,
    actor: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
):
    pf = await db.get(ExtractorProfile, profile_id)
    if not pf:
        raise HTTPException(404, "Profile 不存在")
    return pf


@router.patch("/{profile_id}", response_model=ProfileOut)
async def update_profile(
    profile_id: int,
    data: ProfileUpdate,
    actor: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
):
    pf = await db.get(ExtractorProfile, profile_id)
    if not pf:
        raise HTTPException(404, "Profile 不存在")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(pf, k, v)
    await db.commit()
    await db.refresh(pf)
    return pf


@router.delete("/{profile_id}")
async def delete_profile(
    profile_id: int,
    actor: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
):
    pf = await db.get(ExtractorProfile, profile_id)
    if not pf:
        raise HTTPException(404, "Profile 不存在")
    if pf.is_builtin:
        raise HTTPException(400, "内置模板不可删除")
    await db.delete(pf)
    await db.commit()
    return {"ok": True}
