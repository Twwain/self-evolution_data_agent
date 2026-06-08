"""Flavor detection 谓词 DSL 求值器 (设计 A13).

谓词树仅消费 buildInfo 的结构特征 (字段存在性 / 字段值), 禁止任何 host/port/URI 输入。
求值 total: 缺键 / 非 dict / 类型异常时不抛异常 —— field_present/equals/in 取假,
field_absent 取真; 未知谓词键取假。

叶子谓词:
  {"field_absent": "k"}                              build_info 顶层不含键 k
  {"field_present": "k"}                             build_info 顶层含键 k
  {"field_equals": {"field": "k", "value": v}}       build_info["k"] == v
  {"field_in": {"field": "k", "values": [...]}}      build_info["k"] in values
组合子:
  {"all": [..]}  合取    {"any": [..]}  析取    {"not": {..}}  否定
"""
from __future__ import annotations

# 已知谓词键集合 (供 schema 校验复用)
LEAF_PREDICATES = frozenset({"field_absent", "field_present", "field_equals", "field_in"})
COMBINATORS = frozenset({"all", "any", "not"})
KNOWN_PREDICATE_KEYS = LEAF_PREDICATES | COMBINATORS


def evaluate(predicate: object, build_info: object) -> bool:
    """对 build_info 求值 predicate 谓词树。total: 任何畸形输入都不抛异常, 返回 bool。"""
    if not isinstance(predicate, dict) or not predicate:
        return False
    info = build_info if isinstance(build_info, dict) else {}

    # 组合子
    if "all" in predicate:
        clauses = predicate["all"]
        if not isinstance(clauses, list):
            return False
        return all(evaluate(c, info) for c in clauses)
    if "any" in predicate:
        clauses = predicate["any"]
        if not isinstance(clauses, list):
            return False
        return any(evaluate(c, info) for c in clauses)
    if "not" in predicate:
        return not evaluate(predicate["not"], info)

    # 叶子谓词
    if "field_absent" in predicate:
        key = predicate["field_absent"]
        return key not in info
    if "field_present" in predicate:
        key = predicate["field_present"]
        return key in info
    if "field_equals" in predicate:
        spec = predicate["field_equals"]
        if not isinstance(spec, dict) or "field" not in spec:
            return False
        field = spec["field"]
        return field in info and info[field] == spec.get("value")
    if "field_in" in predicate:
        spec = predicate["field_in"]
        if not isinstance(spec, dict) or "field" not in spec:
            return False
        field = spec["field"]
        values = spec.get("values")
        if not isinstance(values, list):
            return False
        return field in info and info[field] in values

    # 未知谓词键
    return False


def collect_predicate_keys(predicate: object) -> set[str]:
    """递归收集谓词树用到的所有键 (供 schema 校验检测未知谓词)。"""
    keys: set[str] = set()
    if not isinstance(predicate, dict):
        return keys
    for k, v in predicate.items():
        keys.add(k)
        if k in ("all", "any") and isinstance(v, list):
            for clause in v:
                keys |= collect_predicate_keys(clause)
        elif k == "not":
            keys |= collect_predicate_keys(v)
    return keys
