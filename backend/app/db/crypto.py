"""数据源凭证字段级加密.

`EncryptedString` 是 SQLAlchemy TypeDecorator: 写入时 Fernet 加密、读取时解密,
对 Python 层完全透明 (ORM 属性仍是明文 str)。驱动代码 / 测试无需改动。

存量兼容: 旧明文行无法被 Fernet 解密 (InvalidToken), 此时原样返回明文 ——
保证升级后旧数据仍可读, 下一次写入即自动转为密文。
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.config import settings

_fernet = Fernet(settings.datasource_encryption_key.encode())


class EncryptedString(TypeDecorator):
    """落库前 Fernet 加密, 取出后解密的字符串列类型."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        return _fernet.encrypt(value.encode()).decode()

    def process_result_value(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        try:
            return _fernet.decrypt(value.encode()).decode()
        except InvalidToken:
            # 存量明文行 — 原样返回, 下次写入自动加密
            return value
