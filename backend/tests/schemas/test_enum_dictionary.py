"""EnumDictionary Pydantic schema 校验.

覆盖:
- 正常 create
- mixed db_value 类型拒绝
- 空 values 拒绝
- 超长 enum_class_name 拒绝
- partial update 允许
"""
import pytest
from pydantic import ValidationError

from app.schemas.enum_dictionary import (
    EnumDictionaryCreate,
    EnumDictionaryUpdate,
    EnumValueItem,
)


def test_valid_create():
    p = EnumDictionaryCreate(
        namespace_id=1,
        enum_class_name="DeleteStatus",
        values=[
            EnumValueItem(name="NORMAL", db_value=0, description="正常"),
            EnumValueItem(name="DELETED", db_value=1, description="已删除"),
        ],
        comment="ok",
    )
    assert len(p.values) == 2


def test_mixed_db_value_rejected():
    with pytest.raises(ValidationError):
        EnumDictionaryCreate(
            namespace_id=1,
            enum_class_name="X",
            values=[
                EnumValueItem(name="A", db_value=1),
                EnumValueItem(name="B", db_value="b"),
            ],
        )


def test_empty_values_rejected():
    with pytest.raises(ValidationError):
        EnumDictionaryCreate(
            namespace_id=1,
            enum_class_name="X",
            values=[],
        )


def test_long_name_rejected():
    with pytest.raises(ValidationError):
        EnumDictionaryCreate(
            namespace_id=1,
            enum_class_name="X" * 101,
            values=[EnumValueItem(name="A", db_value=1)],
        )


def test_update_partial_allowed():
    p = EnumDictionaryUpdate(
        values=[EnumValueItem(name="A", db_value=1)],
    )
    assert p.comment is None
