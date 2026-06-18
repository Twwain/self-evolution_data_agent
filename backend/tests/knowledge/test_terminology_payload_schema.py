import pytest
from pydantic import ValidationError

from app.schemas.knowledge_payload import TerminologyPayload


VALID = {
    "term": "商品", "primary_collection": "c_category",
    "primary_database": "db_q", "db_type": "mongodb",
    "synonyms": ["货品", "存货"], "source_collections": ["c_category"],
}


def test_valid_payload():
    p = TerminologyPayload(**VALID)
    assert p.term == "商品" and p.db_type == "mongodb"


def test_term_empty_rejected():
    with pytest.raises(ValidationError):
        TerminologyPayload(**{**VALID, "term": "  "})


def test_term_too_long_rejected():
    with pytest.raises(ValidationError, match="单一业务名词"):
        TerminologyPayload(**{**VALID, "term": "货" * 21})


def test_term_with_newline_rejected():
    with pytest.raises(ValidationError, match="换行"):
        TerminologyPayload(**{**VALID, "term": "商\n品"})


def test_term_with_period_rejected():
    with pytest.raises(ValidationError, match="句号"):
        TerminologyPayload(**{**VALID, "term": "商品。"})


def test_term_with_semicolon_rejected():
    with pytest.raises(ValidationError):
        TerminologyPayload(**{**VALID, "term": "商品；货品"})


def test_primary_collection_required():
    payload = {k: v for k, v in VALID.items() if k != "primary_collection"}
    with pytest.raises(ValidationError):
        TerminologyPayload(**payload)


def test_primary_database_required():
    payload = {k: v for k, v in VALID.items() if k != "primary_database"}
    with pytest.raises(ValidationError):
        TerminologyPayload(**payload)


def test_db_type_required():
    payload = {k: v for k, v in VALID.items() if k != "db_type"}
    with pytest.raises(ValidationError):
        TerminologyPayload(**payload)


def test_db_type_oracle_accepted():
    """oracle 是合法的 db_type，应通过校验."""
    p = TerminologyPayload(**{**VALID, "db_type": "oracle"})
    assert p.db_type == "oracle"


def test_db_type_invalid_value():
    """未支持的类型（如 postgresql）应被拒绝."""
    with pytest.raises(ValidationError):
        TerminologyPayload(**{**VALID, "db_type": "postgresql"})


def test_synonyms_too_long_rejected():
    with pytest.raises(ValidationError):
        TerminologyPayload(**{**VALID, "synonyms": ["货品" * 21]})


def test_synonyms_with_newline_rejected():
    with pytest.raises(ValidationError):
        TerminologyPayload(**{**VALID, "synonyms": ["货品\n标"]})


def test_synonyms_blank_element_rejected():
    with pytest.raises(ValidationError, match="空白"):
        TerminologyPayload(**{**VALID, "synonyms": ["货品", "  ", "存货"]})


def test_extra_field_forbidden():
    with pytest.raises(ValidationError):
        TerminologyPayload(**{**VALID, "unknown_field": "x"})
