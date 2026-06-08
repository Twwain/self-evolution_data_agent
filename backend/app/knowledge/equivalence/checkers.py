"""Equivalence checker 注册声明.

在 app/main.py 启动时 import 本模块, side effect 触发 register.
"""

from app.knowledge.equivalence.registry import EquivalenceRule, register
from app.knowledge.equivalence.strategies.enum_set import enum_set_checker
from app.knowledge.equivalence.strategies.mongo_struct import mongo_struct_checker
from app.knowledge.equivalence.strategies.non_empty_wins import non_empty_wins_checker
from app.knowledge.equivalence.strategies.sample_values_union import sample_values_union_checker
from app.knowledge.equivalence.strategies.semantic_llm import semantic_llm_checker

# priority 数字越小越先尝试; 业务约定:
#   0..4   = kind-specific 确定性 (零成本, 精确匹配)
#   5..9   = db_type-specific 结构等价 (零成本, 递归比较)
#   10..19 = LLM 兜底 (有成本, 需在确定性 helper miss 后才跑)

register(EquivalenceRule(
    db_type="*", kind="enum_values", priority=0,
    checker=enum_set_checker, name="enum_set",
))

register(EquivalenceRule(
    db_type="*", kind="field_description", priority=1,
    checker=non_empty_wins_checker, name="non_empty_wins",
))

register(EquivalenceRule(
    db_type="*", kind="sample_values", priority=2,
    checker=sample_values_union_checker, name="sample_values_union",
))

register(EquivalenceRule(
    db_type="mongodb", kind="field_description", priority=5,
    checker=mongo_struct_checker, name="mongo_struct",
))

register(EquivalenceRule(
    db_type="*", kind="*", priority=15,
    checker=semantic_llm_checker, name="semantic_llm",
))
