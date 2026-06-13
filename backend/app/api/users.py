"""
用户管理 — CRUD + Namespace Access（仅 Admin）
每个端点只做一件事，无过度抽象
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    ROLE_ADMIN,
    ROLE_SUPER_ADMIN,
    ROLE_USER,
    accessible_namespace_ids,
    hash_password,
    require_admin_or_above,
    role_at_least,
)
from app.db.metadata import get_db
from app.models.namespace import Namespace
from app.models.user import User, UserNamespaceAccess
from app.schemas import (
    NamespaceOut,
    PasswordResetRequest,
    UserAccessUpdate,
    UserCreate,
    UserOut,
    UserUpdate,
)

router = APIRouter()


# ════════════════════════════════════════════
#  管辖边界工具 (can_manage + 可建角色 + 自锁防护)
# ════════════════════════════════════════════

CREATABLE_ROLES: dict[str, set[str]] = {
    ROLE_SUPER_ADMIN: {ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_USER},
    ROLE_ADMIN: {ROLE_USER},
}


def can_manage(actor: User, target: User) -> bool:
    """actor 能否管理 target (改角色/禁用/删除/重置密码)。"""
    if role_at_least(actor, ROLE_SUPER_ADMIN):
        return True
    return (
        actor.role == ROLE_ADMIN
        and target.role == ROLE_USER
        and target.created_by == actor.id
    )


async def assert_not_last_super_admin(db: AsyncSession, target: User) -> None:
    """禁止删除/禁用/降级最后一个 active super_admin (防自锁)。
    SELECT ... FOR UPDATE 行锁防 TOCTOU: 并发删两个 super_admin 时, 后一事务
    阻塞到前者提交后才拿锁, 读到 count=1 → 正确拒绝, 不会双双放行出局。"""
    if target.role != ROLE_SUPER_ADMIN:
        return
    rows = (await db.execute(
        select(User.id).where(
            User.role == ROLE_SUPER_ADMIN, User.is_active.is_(True)
        ).with_for_update()
    )).scalars().all()
    if len(rows) <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove the last active super admin",
        )


# ════════════════════════════════════════════
#  创建用户
# ════════════════════════════════════════════

@router.post("/api/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    actor: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
):
    """创建新用户 (admin 及以上)。admin 仅可建 user; super_admin 可建任意角色。"""
    if body.role not in CREATABLE_ROLES.get(actor.role, set()):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Cannot create role '{body.role}'",
        )
    result = await db.execute(select(User).where(User.username == body.username))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{body.username}' already exists",
        )
    new_user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        role=body.role,
        created_by=actor.id,
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    return UserOut.model_validate(new_user)


# ════════════════════════════════════════════
#  列出所有用户
# ════════════════════════════════════════════

@router.get("/api/users", response_model=list[UserOut])
async def list_users(
    actor: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
):
    """列出用户。super_admin 见全部; admin 仅见自己创建的。"""
    stmt = select(User).order_by(User.id)
    if not role_at_least(actor, ROLE_SUPER_ADMIN):
        stmt = stmt.where(User.created_by == actor.id)
    users = (await db.execute(stmt)).scalars().all()
    return [UserOut.model_validate(u) for u in users]


# ════════════════════════════════════════════
#  更新用户
# ════════════════════════════════════════════

@router.put("/api/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int,
    body: UserUpdate,
    actor: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
):
    """更新用户角色/激活状态。受 can_manage 边界 + 最后超管防护约束。"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"User {user_id} not found")
    if not can_manage(actor, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot manage this user")

    # 改角色: 目标新角色必须在 actor 可建集合内 (admin 不能把 user 提成 admin)
    if body.role is not None and body.role != user.role:
        if body.role not in CREATABLE_ROLES.get(actor.role, set()):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Cannot assign role '{body.role}'")
        # 降级最后一个 super_admin 防护
        if user.role == ROLE_SUPER_ADMIN:
            await assert_not_last_super_admin(db, user)
        user.role = body.role
    # 禁用最后一个 super_admin 防护
    if body.is_active is not None:
        if body.is_active is False and user.role == ROLE_SUPER_ADMIN:
            await assert_not_last_super_admin(db, user)
        user.is_active = body.is_active

    await db.commit()
    await db.refresh(user)
    return UserOut.model_validate(user)


# ════════════════════════════════════════════
#  删除用户
# ════════════════════════════════════════════

@router.delete("/api/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    actor: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
):
    """删除用户。防自删 + can_manage 边界 + 最后超管防护。
    子用户 (created_by==此用户) 的 created_by 由 DB 层 ON DELETE SET NULL 自动置空
    (依赖 migration_021 FK 修复), 变"无主 user", 不级联删, 不抛 IntegrityError。"""
    if user_id == actor.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot delete yourself")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"User {user_id} not found")
    if not can_manage(actor, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot manage this user")
    await assert_not_last_super_admin(db, user)
    await db.delete(user)
    await db.commit()


# ════════════════════════════════════════════
#  设置用户的命名空间访问权限
# ════════════════════════════════════════════

@router.put("/api/users/{user_id}/access")
async def set_user_access(
    user_id: int,
    body: UserAccessUpdate,
    actor: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
):
    """设置用户命名空间访问权。admin 仅能分配自己可访问的 ns 子集; 受 can_manage 约束。"""
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"User {user_id} not found")
    if not can_manage(actor, target):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot manage this user")

    # 可授边界: admin 只能分配自己 accessible 的 ns (super_admin 返 None = 不限)
    allowed = await accessible_namespace_ids(db, actor)
    if allowed is not None:
        allowed_set = set(allowed)
        if not set(body.namespace_ids).issubset(allowed_set):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="namespace_ids 超出可分配范围",
            )

    await db.execute(
        delete(UserNamespaceAccess).where(UserNamespaceAccess.user_id == user_id)
    )
    for ns_id in body.namespace_ids:
        db.add(UserNamespaceAccess(user_id=user_id, namespace_id=ns_id))
    await db.commit()

    return {"status": "ok", "namespace_ids": body.namespace_ids}


# ════════════════════════════════════════════
#  获取用户可访问的命名空间列表
# ════════════════════════════════════════════

@router.get("/api/users/{user_id}/access", response_model=list[NamespaceOut])
async def get_user_access(
    user_id: int,
    actor: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
):
    """获取用户可访问的命名空间。受 can_manage 边界约束。"""
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"User {user_id} not found")
    if not can_manage(actor, target):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot manage this user")
    result = await db.execute(
        select(Namespace)
        .join(UserNamespaceAccess, Namespace.id == UserNamespaceAccess.namespace_id)
        .where(UserNamespaceAccess.user_id == user_id)
    )
    return [NamespaceOut.model_validate(ns) for ns in result.scalars().all()]


# ════════════════════════════════════════════
#  上级重置密码 (无需旧密码, 受 can_manage 约束)
# ════════════════════════════════════════════

@router.post("/api/users/{user_id}/reset-password")
async def reset_password(
    user_id: int,
    body: PasswordResetRequest,
    actor: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
):
    """重置下属密码。super_admin 重置任何人; admin 仅重置自己创建的 user。"""
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if not can_manage(actor, target):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot manage this user")
    target.password_hash = hash_password(body.new_password)
    await db.commit()
    return {"status": "ok"}
