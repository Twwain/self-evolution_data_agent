"""Phase 2 Task 4: MyBatis WHERE 字面量证据 (parse_xml_for_where_evidence)."""
import pytest

from app.knowledge.code_parser import parse_xml_for_where_evidence


@pytest.mark.asyncio
async def test_literal_int_collected(fake_llm):
    """字面量 int 值被收集到 observed_db_values."""
    fake_llm.queue_response({
        "where_evidence": [
            {
                "table": "t_order", "column": "status",
                "observed_db_values": [1, 2], "observed_string_values": [],
                "occurrence_count": 2,
                "method_ids": ["selectByStatus"],
            }
        ]
    })
    xml = """<mapper namespace="OrderMapper">
        <select id="selectByStatus">
            SELECT * FROM t_order WHERE status = 1 OR status = 2
        </select>
    </mapper>"""

    out = await parse_xml_for_where_evidence(xml, mapper_namespace="OrderMapper")

    assert len(out) == 1
    assert out[0]["table"] == "t_order"
    assert sorted(out[0]["observed_db_values"]) == [1, 2]


@pytest.mark.asyncio
async def test_placeholder_not_collected(fake_llm):
    """status = #{status} 占位符不进 observed_*."""
    fake_llm.queue_response({
        "where_evidence": [
            {
                "table": "t_order", "column": "status",
                "observed_db_values": [], "observed_string_values": [],
                "occurrence_count": 0, "method_ids": ["selectDyn"],
            }
        ]
    })
    xml = """<mapper namespace="OrderMapper">
        <select id="selectDyn">
            SELECT * FROM t_order WHERE status = #{status}
        </select>
    </mapper>"""
    out = await parse_xml_for_where_evidence(xml, mapper_namespace="OrderMapper")
    assert out[0]["observed_db_values"] == []
    assert out[0]["observed_string_values"] == []


@pytest.mark.asyncio
async def test_string_literal_collected(fake_llm):
    """字符串字面量被收集到 observed_string_values."""
    fake_llm.queue_response({
        "where_evidence": [
            {
                "table": "t_order", "column": "type",
                "observed_db_values": [], "observed_string_values": ["NORMAL", "VIP"],
                "occurrence_count": 2, "method_ids": ["selectByType"],
            }
        ]
    })
    xml = """<mapper namespace="OrderMapper">
        <select id="selectByType">
            SELECT * FROM t_order WHERE type = 'NORMAL' OR type = 'VIP'
        </select>
    </mapper>"""
    out = await parse_xml_for_where_evidence(xml, mapper_namespace="OrderMapper")
    assert "NORMAL" in out[0]["observed_string_values"]
    assert "VIP" in out[0]["observed_string_values"]
