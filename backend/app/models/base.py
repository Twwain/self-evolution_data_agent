"""ORM 基类"""

from sqlalchemy import text
from sqlalchemy.orm import DeclarativeBase

# 显式将 now() (绝对时刻/timestamptz) 转为北京墙上时间的 naive timestamp，
# 写入 `timestamp without time zone` 列恒为本地时间，不依赖连接级 timezone。
LOCAL_NOW = text("(now() AT TIME ZONE 'Asia/Shanghai')")


class Base(DeclarativeBase):
    pass
