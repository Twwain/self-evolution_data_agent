"""
认证核心 — JWT + Password + Dependencies
零散的 token 验证是安全的癌症, 统一在此处理
"""

from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.metadata import get_db
from app.models.user import User, UserNamespaceAccess

# ════════════════════════════════════════════
#  密码加密
# ════════════════════════════════════════════

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    """明文 → 哈希"""
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """验证密码"""
    return pwd_context.verify(plain, hashed)


# ════════════════════════════════════════════
#  JWT Token
# ════════════════════════════════════════════

def create_access_token(user_id: int, role: str) -> str:
    """生成 JWT access token"""
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
    payload = {
        "sub": str(user_id),  # JWT 规范要求 sub 为字符串
        "role": role,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


# ════════════════════════════════════════════
#  FastAPI Dependencies
# ════════════════════════════════════════════

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    从 JWT token 获取当前用户
    - 401: token 无效/过期/用户不存在
    - 403: 用户被禁用
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        user_id_str: str | None = payload.get("sub")
        if user_id_str is None:
            raise credentials_exception
        user_id = int(user_id_str)
    except (JWTError, ValueError):
        raise credentials_exception

    # 查询用户
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception

    # 检查账号状态
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is disabled",
        )

    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """
    要求管理员权限
    - 403: 非 admin 角色
    """
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


async def require_ns_access(
    ns_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    要求命名空间访问权限
    - Admin 跳过检查
    - User 检查 user_namespace_access 表
    - 403: 无访问权限
    """
    if user.role == "admin":
        return user

    # 检查普通用户的命名空间权限
    result = await db.execute(
        select(UserNamespaceAccess).where(
            UserNamespaceAccess.user_id == user.id,
            UserNamespaceAccess.namespace_id == ns_id,
        )
    )
    access = result.scalar_one_or_none()
    if access is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"No access to namespace {ns_id}",
        )

    return user
