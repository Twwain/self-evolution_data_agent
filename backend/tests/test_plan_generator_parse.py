"""
plan_generator._parse_plan 跨引擎解析测试 (Plan A).

验证 db_type 流通 + db_type-aware operation 白名单 + 形态校验.
"""
from __future__ import annotations

import json

import pytest

from app.engine.plan_generator import PlanGenerationError, _parse_plan


def _raw(plan: dict) -> str:
    return json.dumps(plan, ensure_ascii=False)


def test_mongodb_step_parsed_with_default_db_type():
    plan = {
        "strategy": "single_aggregate",
        "steps": [{
            "step_idx": 1, "db_type": "mongodb",
            "database": "catalog_db", "collection": "products",
            "operation": "aggregate",
            "pipeline": [{"$group": {"_id": "$type", "n": {"$sum": 1}}}, {"$limit": 100}],
            "exports": ["_id", "n"],
        }],
    }
    qp = _parse_plan(_raw(plan))
    assert qp.steps[0].db_type == "mongodb"
    assert qp.steps[0].operation == "aggregate"


def test_mysql_step_db_type_flows_through():
    plan = {
        "strategy": "single_aggregate",
        "steps": [{
            "step_idx": 1, "db_type": "mysql",
            "database": "shop_db", "collection": "orders",
            "operation": "sql",
            "query": {"sql": "SELECT product_id FROM orders WHERE status = 1 LIMIT 100"},
            "exports": ["product_id"],
        }],
    }
    qp = _parse_plan(_raw(plan))
    assert qp.steps[0].db_type == "mysql"
    assert qp.steps[0].operation == "sql"


def test_cross_engine_multi_step():
    plan = {
        "strategy": "multi_step",
        "steps": [
            {
                "step_idx": 1, "db_type": "mysql",
                "database": "shop_db", "collection": "orders",
                "operation": "sql",
                "query": {"sql": "SELECT DISTINCT product_id FROM orders LIMIT 1000"},
                "exports": ["product_id"],
            },
            {
                "step_idx": 2, "db_type": "mongodb",
                "database": "catalog_db", "collection": "products",
                "operation": "aggregate",
                "pipeline": [
                    {"$match": {"productId": {"$in": "{{step1.product_id}}"}}},
                    {"$group": {"_id": "$productType", "count": {"$sum": 1}}},
                    {"$limit": 1000},
                ],
                "exports": ["_id", "count"],
            },
        ],
    }
    qp = _parse_plan(_raw(plan))
    assert [s.db_type for s in qp.steps] == ["mysql", "mongodb"]
    assert qp.databases == ["catalog_db", "shop_db"]


def test_mysql_op_on_mongodb_rejected():
    """mongodb step 给 operation=sql → 不在白名单."""
    plan = {
        "strategy": "single_aggregate",
        "steps": [{
            "step_idx": 1, "db_type": "mongodb",
            "database": "d", "collection": "c",
            "operation": "sql", "query": {"sql": "SELECT 1"},
        }],
    }
    with pytest.raises(PlanGenerationError):
        _parse_plan(_raw(plan))


def test_mysql_step_missing_sql_rejected():
    plan = {
        "strategy": "single_aggregate",
        "steps": [{
            "step_idx": 1, "db_type": "mysql",
            "database": "d", "collection": "t",
            "operation": "sql", "query": {},
        }],
    }
    with pytest.raises(PlanGenerationError):
        _parse_plan(_raw(plan))


def test_oracle_step_db_type_flows_through():
    """Oracle SQL step 应正常解析, db_type/operation 流通."""
    plan = {
        "strategy": "single_aggregate",
        "steps": [{
            "step_idx": 1, "db_type": "oracle",
            "database": "sales_svc", "collection": "ORDERS",
            "operation": "sql",
            "query": {"sql": "SELECT ORDER_DATE, SUM(AMOUNT) FROM ORDERS GROUP BY ORDER_DATE"},
            "exports": ["ORDER_DATE", "SUM(AMOUNT)"],
        }],
    }
    qp = _parse_plan(_raw(plan))
    assert qp.steps[0].db_type == "oracle"
    assert qp.steps[0].operation == "sql"


def test_oracle_non_sql_operation_rejected():
    """Oracle step 给 operation=aggregate → 不在白名单."""
    plan = {
        "strategy": "single_aggregate",
        "steps": [{
            "step_idx": 1, "db_type": "oracle",
            "database": "d", "collection": "T",
            "operation": "aggregate", "pipeline": [],
        }],
    }
    with pytest.raises(PlanGenerationError):
        _parse_plan(_raw(plan))


def test_oracle_step_missing_sql_rejected():
    plan = {
        "strategy": "single_aggregate",
        "steps": [{
            "step_idx": 1, "db_type": "oracle",
            "database": "d", "collection": "T",
            "operation": "sql", "query": {},
        }],
    }
    with pytest.raises(PlanGenerationError):
        _parse_plan(_raw(plan))


def test_unknown_db_type_rejected():
    plan = {
        "strategy": "single_aggregate",
        "steps": [{
            "step_idx": 1, "db_type": "postgres",
            "database": "d", "collection": "t",
            "operation": "sql", "query": {"sql": "SELECT 1 LIMIT 1"},
        }],
    }
    with pytest.raises(PlanGenerationError):
        _parse_plan(_raw(plan))
