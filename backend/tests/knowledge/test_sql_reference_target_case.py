"""SQL referenced_targets 表名大小写匹配回归测试."""

from app.knowledge.schema_canonical import _is_referenced_target
from app.knowledge.trainer import _collect_referenced_sql_tables


def test_collect_referenced_sql_tables_matches_oracle_uppercase_targets():
    coll_to_db = {
        "USER_ORDER": "orcl",
        "ORDER_ITEM": "orcl",
        "PAYMENT_LOG": "orcl",
    }

    result = _collect_referenced_sql_tables(
        mybatis_entries=[
            {
                "type": "select",
                "canonical_sql": (
                    "select * from user_order u "
                    "join order_item oi on oi.order_id = u.id"
                ),
            }
        ],
        jpa_entities=[{"table_name": "payment_log"}],
        coll_to_db=coll_to_db,
    )

    assert result == {"USER_ORDER", "ORDER_ITEM", "PAYMENT_LOG"}


def test_collect_referenced_sql_tables_preserves_exact_mysql_target():
    coll_to_db = {
        "user_order": "mysql_db",
        "USER_ORDER": "orcl",
    }

    result = _collect_referenced_sql_tables(
        mybatis_entries=[
            {"type": "select", "canonical_sql": "select * from user_order"}
        ],
        jpa_entities=[],
        coll_to_db=coll_to_db,
    )

    assert result == {"user_order"}


def test_schema_canonical_filter_matches_oracle_case_insensitively_only():
    referenced = {"user_order"}
    referenced_keys = {target.casefold() for target in referenced}

    assert _is_referenced_target("oracle", "USER_ORDER", referenced, referenced_keys)
    assert _is_referenced_target("oracle", "user_order", referenced, referenced_keys)
    assert not _is_referenced_target("mysql", "USER_ORDER", referenced, referenced_keys)
