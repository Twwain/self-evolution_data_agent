"""
用户管理 — CRUD + Namespace Access（仅 Admin）
每个端点只做一件事，无过度抽象
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password, require_admin
from app.db.metadata import get_db
from app.models.namespace import Namespace
from app.models.user import User, UserNamespaceAccess
from app.schemas import NamespaceOut, UserAccessUpdate, UserCreate, UserOut, UserUpdate

router = APIRouter()


# ════════════════════════════════════════════
#  创建用户
# ════════════════════════════════════════════

@router.post("/api/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    创建新用户（仅 admin）
    - 409: 用户名已存在
    """
    # 检查用户名唯一性
    result = await db.execute(select(User).where(User.username == body.username))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{body.username}' already exists",
        )

    # 创建用户
    new_user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        role=body.role,
        created_by=admin.id,
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
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """列出所有用户（仅 admin）"""
    result = await db.execute(select(User).order_by(User.id))
    users = result.scalars().all()
    return [UserOut.model_validate(u) for u in users]


# ════════════════════════════════════════════
#  更新用户
# ════════════════════════════════════════════

@router.put("/api/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int,
    body: UserUpdate,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    更新用户角色/激活状态（仅 admin）
    - 404: 用户不存在
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found",
        )

    # 应用更新（无冗余分支）
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
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
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    删除用户（仅 admin）
    - 400: 不允许删除自己
    - 404: 用户不存在
    """
    # 防止自杀
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete yourself",
        )

    # 查询用户
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found",
        )

    # 删除
    await db.delete(user)
    await db.commit()


# ════════════════════════════════════════════
#  设置用户的命名空间访问权限
# ════════════════════════════════════════════

@router.put("/api/users/{user_id}/access")
async def set_user_access(
    user_id: int,
    body: UserAccessUpdate,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    设置用户命名空间访问权限（仅 admin）
    - 404: 用户不存在
    - 一次性清除旧权限，批量插入新权限
    """
    # 验证用户存在
    result = await db.execute(select(User).where(User.id == user_id))
    if not result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found",
        )

    # 清除旧权限
    await db.execute(
        delete(UserNamespaceAccess).where(UserNamespaceAccess.user_id == user_id)
    )

    # 插入新权限
    for ns_id in body.namespace_ids:
        access = UserNamespaceAccess(user_id=user_id, namespace_id=ns_id)
        db.add(access)

    await db.commit()

    return {"status": "ok", "namespace_ids": body.namespace_ids}


# ════════════════════════════════════════════
#  获取用户可访问的命名空间列表
# ════════════════════════════════════════════

@router.get("/api/users/{user_id}/access", response_model=list[NamespaceOut])
async def get_user_access(
    user_id: int,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    获取用户可访问的命名空间（仅 admin）
    - 404: 用户不存在
    """
    # 验证用户存在
    result = await db.execute(select(User).where(User.id == user_id))
    if not result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found",
        )

    # JOIN 查询命名空间
    result = await db.execute(
        select(Namespace)
        .join(UserNamespaceAccess, Namespace.id == UserNamespaceAccess.namespace_id)
        .where(UserNamespaceAccess.user_id == user_id)
    )
    namespaces = result.scalars().all()

    return [NamespaceOut.model_validate(ns) for ns in namespaces]
