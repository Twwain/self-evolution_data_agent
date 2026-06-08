"""
认证端点 — Login + Password Change
每个端点只做一件事，无多余分支
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_access_token, get_current_user, hash_password, verify_password
from app.db.metadata import get_db
from app.models.user import User
from app.schemas import LoginRequest, LoginResponse, PasswordChangeRequest, UserOut

router = APIRouter()


# ════════════════════════════════════════════
#  登录
# ════════════════════════════════════════════

@router.post("/api/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    """
    用户登录 → JWT token
    - 401: 用户名不存在/密码错误/账号被禁用
    """
    # 查询用户
    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()

    # 验证存在性 + 密码 + 激活状态（单一判断路径，无分支树）
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is disabled",
        )

    # 生成 token
    token = create_access_token(user.id, user.role)
    user_out = UserOut.model_validate(user)

    return LoginResponse(access_token=token, user=user_out)


# ════════════════════════════════════════════
#  修改密码
# ════════════════════════════════════════════

@router.put("/api/auth/password")
async def change_password(
    body: PasswordChangeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    修改当前用户密码
    - 400: 旧密码错误
    """
    # 验证旧密码
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect old password",
        )

    # 更新密码
    user.password_hash = hash_password(body.new_password)
    await db.commit()

    return {"status": "ok"}
