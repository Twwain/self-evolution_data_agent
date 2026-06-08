"""Equivalence checker registry — 可扩展等价比较器.

公开接口:
- register(rule): 注册一条等价规则
- applicable_rules(db_type, kind): 返回匹配的规则列表 (按 priority 升序)
- EquivalenceRule: 规则数据类
"""

from app.knowledge.equivalence.registry import (  # noqa: F401
    EquivalenceRule,
    applicable_rules,
    register,
)
