"""ORM 基类"""

from sqlalchemy import text
from sqlalchemy.orm import DeclarativeBase

# PostgreSQL: 连接级已设 timezone='Asia/Shanghai'，now() 直接返回本地时间
LOCAL_NOW = text("now()")


class Base(DeclarativeBase):
    pass
