"""Stage 5 — mode='render' override 末步 LIMIT 用 render_row_limit, 撞限置疑似. 双引擎."""
from __future__ import annotations

from app.config import settings
from app.engine.drivers.mongo import _strip_tail_row_stages
from app.engine.drivers.mysql import MySQLDriver


def test_mysql_wrap_render_injects_when_no_limit():
    sql = MySQLDriver._wrap_by_mode("SELECT a FROM t", "render", 1000)
    assert f"LIMIT {settings.render_row_limit}" in sql


def test_mysql_wrap_render_overrides_existing_planner_limit():
    """critical: planner 末步必带 LIMIT; render 必须剥离并 override 为 render_row_limit."""
    sql = MySQLDriver._wrap_by_mode("SELECT a FROM t LIMIT 1000", "render", 1000)
    assert f"LIMIT {settings.render_row_limit}" in sql
    assert "LIMIT 1000" not in sql              # planner 的 1000 已被剥离
    assert sql.upper().count("LIMIT") == 1      # 外层 LIMIT 唯一


def test_mysql_wrap_render_overrides_limit_offset_form():
    sql = MySQLDriver._wrap_by_mode("SELECT a FROM t LIMIT 100, 1000", "render", 1000)
    assert f"LIMIT {settings.render_row_limit}" in sql
    assert "100, 1000" not in sql


def test_mysql_strip_outer_limit_leaves_subquery_limit():
    # 子查询内 LIMIT 不应被剥离, 仅剥外层
    sql = "SELECT * FROM (SELECT a FROM t LIMIT 5) _s LIMIT 1000"
    stripped = MySQLDriver._strip_outer_limit(sql)
    assert stripped.endswith("_s")
    assert "LIMIT 5" in stripped


def test_mongo_strip_tail_row_stages_removes_trailing_limit():
    pipeline = [{"$match": {"x": 1}}, {"$group": {"_id": "$d"}}, {"$limit": 1000}]
    out = _strip_tail_row_stages(pipeline)
    assert out == [{"$match": {"x": 1}}, {"$group": {"_id": "$d"}}]


def test_mongo_strip_tail_row_stages_removes_consecutive_tail():
    pipeline = [{"$match": {"x": 1}}, {"$skip": 10}, {"$limit": 1000}]
    out = _strip_tail_row_stages(pipeline)
    assert out == [{"$match": {"x": 1}}]


def test_mongo_strip_tail_row_stages_keeps_middle_stages():
    # 中间的 $limit 不剥 (只剥尾部连续行 stage)
    pipeline = [{"$limit": 100}, {"$group": {"_id": "$d"}}]
    out = _strip_tail_row_stages(pipeline)
    assert out == [{"$limit": 100}, {"$group": {"_id": "$d"}}]
