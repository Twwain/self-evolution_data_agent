"""mongo_flavor + 谓词 DSL 测试 (Properties 1-9, 13-15; tasks 3.2/4.x/5.x/6.x/7.x).

属性测试: hypothesis, max_examples>=100. 运行:
  cd backend && python -m pytest tests/drivers/test_mongo_flavor.py --timeout=120 --timeout-method=thread
"""
from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.engine.drivers.mongo_capabilities import compute_unsupported_ops
from app.engine.drivers.mongo_flavor import (
    NATIVE_FLAVOR,
    ProfileLoadError,
    ProfileRegistry,
    build_capabilities,
    compute_capabilities,
    detect_flavor,
    get_profile_registry,
)
from app.engine.drivers.mongo_flavor_predicate import evaluate

PROP = "mongo-flavor-capabilities-and-error-clarify"

# ── build_info 生成器: 覆盖空/单字段/双缺/双有/类型异常/嵌套 ──
_scalar = st.one_of(st.none(), st.booleans(), st.integers(), st.text(max_size=8))
_build_info = st.dictionaries(
    keys=st.sampled_from(["version", "gitVersion", "modules", "bits", "storageEngines", "x"]),
    values=st.one_of(_scalar, st.lists(_scalar, max_size=3), st.dictionaries(st.text(max_size=3), _scalar, max_size=2)),
    max_size=6,
)


# ════════════════════════════════════════════
#  谓词 DSL (task 3.2)
# ════════════════════════════════════════════

def test_predicate_total_on_garbage():
    # 缺键 / 非 dict / 空 → 不抛, field_absent 真 / 其余假
    assert evaluate({"field_absent": "k"}, {}) is True
    assert evaluate({"field_present": "k"}, {}) is False
    assert evaluate({"field_absent": "k"}, "not-a-dict") is True
    assert evaluate({}, {}) is False
    assert evaluate("bad", {}) is False


def test_predicate_combinators_truth_table():
    bi = {"a": 1}
    assert evaluate({"all": [{"field_present": "a"}, {"field_absent": "b"}]}, bi) is True
    assert evaluate({"all": [{"field_present": "a"}, {"field_present": "b"}]}, bi) is False
    assert evaluate({"any": [{"field_present": "b"}, {"field_absent": "b"}]}, bi) is True
    assert evaluate({"not": {"field_present": "a"}}, bi) is False
    assert evaluate({"field_equals": {"field": "a", "value": 1}}, bi) is True
    assert evaluate({"field_in": {"field": "a", "values": [1, 2]}}, bi) is True


def test_predicate_unknown_key_false():
    assert evaluate({"totally_unknown": "x"}, {"x": 1}) is False


# ════════════════════════════════════════════
#  detect_flavor (Properties 1-5)
# ════════════════════════════════════════════

# Feature: mongo-flavor-capabilities-and-error-clarify, Property 1: detect_flavor 纯性与输入封闭
@settings(max_examples=100)
@given(bi=_build_info)
def test_property_1_detect_pure(bi):
    r1 = detect_flavor(dict(bi))
    r2 = detect_flavor(dict(bi))
    assert r1 == r2


# Feature: mongo-flavor-capabilities-and-error-clarify, Property 2: detect_flavor 全域性与已注册值域
@settings(max_examples=100)
@given(bi=_build_info)
def test_property_2_detect_total(bi):
    reg = get_profile_registry()
    registered = {p.flavor for p in reg.load()} | {NATIVE_FLAVOR}
    result = detect_flavor(dict(bi))  # 不抛
    assert result in registered


# Feature: mongo-flavor-capabilities-and-error-clarify, Property 3: 原生 flavor 是默认回退
@settings(max_examples=100)
@given(extra=st.dictionaries(st.text(max_size=4), _scalar, max_size=3))
def test_property_3_native_fallback(extra):
    bi = {**extra, "gitVersion": "abc123", "modules": []}
    assert detect_flavor(bi) == NATIVE_FLAVOR


# Feature: mongo-flavor-capabilities-and-error-clarify, Property 4: DocumentDB 谓词命中 + exactly-one-field 边界
@settings(max_examples=100)
@given(ver=st.text(max_size=6))
def test_property_4_documentdb_and_exactly_one(ver):
    # 双缺 → documentdb
    assert detect_flavor({"version": ver}) == "documentdb"
    # 恰缺其一 → 非 documentdb
    assert detect_flavor({"version": ver, "gitVersion": "x"}) != "documentdb"
    assert detect_flavor({"version": ver, "modules": []}) != "documentdb"


# task 5.7 exactly-one-field edge
def test_exactly_one_field_edge_explicit():
    assert detect_flavor({"gitVersion": "x"}) != "documentdb"
    assert detect_flavor({"modules": []}) != "documentdb"


# Feature: mongo-flavor-capabilities-and-error-clarify, Property 5: 多命中按固定优先级唯一收敛
def test_property_5_priority(tmp_path):
    (tmp_path / "low.json").write_text(json.dumps({
        "flavor": "low", "display_name": "Low", "priority": 1,
        "detection": {"field_absent": "gitVersion"},
        "capability_restrictions": {"unsupported_ops": [], "unsupported_stage_variants": [], "syntax_constraints": []},
        "equivalent_hints": [],
    }))
    (tmp_path / "high.json").write_text(json.dumps({
        "flavor": "high", "display_name": "High", "priority": 99,
        "detection": {"field_absent": "gitVersion"},
        "capability_restrictions": {"unsupported_ops": [], "unsupported_stage_variants": [], "syntax_constraints": []},
        "equivalent_hints": [],
    }))
    reg = ProfileRegistry(profiles_dir=tmp_path)
    assert detect_flavor({"version": "5.0.0"}, registry=reg) == "high"


# ════════════════════════════════════════════
#  compute_capabilities (Properties 6-9)
# ════════════════════════════════════════════

# Feature: mongo-flavor-capabilities-and-error-clarify, Property 6: 能力装配完整性不变量
@settings(max_examples=100)
@given(ver=st.text(min_size=1, max_size=8))
def test_property_6_assembly(ver):
    reg = get_profile_registry()
    registered = {p.flavor for p in reg.load()} | {NATIVE_FLAVOR}
    for flavor in (NATIVE_FLAVOR, "documentdb"):
        caps = compute_capabilities(flavor, ver)
        assert caps["flavor"] in registered
        assert caps["version"] == ver
        assert isinstance(caps["unsupported_ops"], list)
        assert isinstance(caps["unsupported_stage_variants"], list)
        assert isinstance(caps["syntax_constraints"], list)
        assert caps["agg_ops_unsupported"] == caps["unsupported_ops"]


# Feature: mongo-flavor-capabilities-and-error-clarify, Property 7: 原生路径沿用 Op_Version_Table
@settings(max_examples=100)
@given(ver=st.text(min_size=1, max_size=8))
def test_property_7_native_uses_version_table(ver):
    caps = compute_capabilities(NATIVE_FLAVOR, ver)
    assert caps["unsupported_ops"] == compute_unsupported_ops(ver)
    assert caps["unsupported_stage_variants"] == []
    assert caps["syntax_constraints"] == []


# Feature: mongo-flavor-capabilities-and-error-clarify, Property 8: 非原生路径忠实反映 profile 限制
def test_property_8_nonnative_reflects_profile():
    reg = get_profile_registry()
    profile = reg.get("documentdb")
    caps = compute_capabilities("documentdb", "5.0.0")
    assert caps["unsupported_ops"] == profile.capability_restrictions["unsupported_ops"]
    assert caps["unsupported_stage_variants"] == profile.capability_restrictions["unsupported_stage_variants"]
    assert caps["syntax_constraints"] == profile.capability_restrictions["syntax_constraints"]


# Feature: mongo-flavor-capabilities-and-error-clarify, Property 9: equivalent_hints 关联完整性
def test_property_9_hints_resolve():
    caps = compute_capabilities("documentdb", "5.0.0")
    union = set(caps["unsupported_ops"]) | set(caps["unsupported_stage_variants"]) | set(caps["syntax_constraints"])
    for hint in caps["equivalent_hints"]:
        assert hint["restriction"] in union


# task 2.2: documentdb 档案声明实测限制 + 基础 $lookup / $getField 不在 unsupported
def test_documentdb_profile_declares_three_restrictions():
    caps = compute_capabilities("documentdb", "5.0.0")
    # 实测确认不支持的代表算子 (错误码 305)
    assert "$function" in caps["unsupported_ops"]
    assert "$percentile" in caps["unsupported_ops"]
    # stage 层限制: 变体 + 整段 stage 同桶
    assert "$lookup.let_pipeline" in caps["unsupported_stage_variants"]
    assert "$facet" in caps["unsupported_stage_variants"]
    assert "$graphLookup" in caps["unsupported_stage_variants"]
    # 语法约束 (覆盖 16410 + 5654600)
    assert "project_no_dollar_fieldpath" in caps["syntax_constraints"]
    # 实测支持的不应被列入 (基础 $lookup 形态 + $getField — 修正了原始误判)
    assert "$lookup" not in caps["unsupported_ops"]
    assert "$getField" not in caps["unsupported_ops"]
    assert "$reduce" not in caps["unsupported_ops"]


# task 4.4 / 4.3: 内置目录 flavor 集合 == {documentdb}
def test_builtin_profiles_only_documentdb():
    reg = get_profile_registry()
    assert {p.flavor for p in reg.load()} == {"documentdb"}


# task 4.4 / 4.2: 临时目录放自定义合法 profile 被识别
def test_custom_profile_loaded(tmp_path):
    (tmp_path / "cosmos.json").write_text(json.dumps({
        "flavor": "cosmos", "display_name": "Cosmos", "priority": 50,
        "detection": {"field_equals": {"field": "version", "value": "cosmos-x"}},
        "capability_restrictions": {"unsupported_ops": ["$x"], "unsupported_stage_variants": [], "syntax_constraints": []},
        "equivalent_hints": [],
    }))
    reg = ProfileRegistry(profiles_dir=tmp_path)
    assert detect_flavor({"version": "cosmos-x"}, registry=reg) == "cosmos"


# task 4.4: 内置档案 JSON 不含 host/ip/账号字段
def test_builtin_profile_no_business_fields():
    from pathlib import Path
    import app.engine.drivers.mongo_flavor as mf
    raw = (Path(mf.__file__).parent / "flavor_profiles" / "aws_documentdb.json").read_text()
    low = raw.lower()
    for banned in ("host", "password", "username", "amazonaws", "192.168", "10.0"):
        assert banned not in low


# ════════════════════════════════════════════
#  ProfileRegistry (Properties 13-15)
# ════════════════════════════════════════════

def _valid_profile(flavor: str, priority: int = 10) -> dict:
    return {
        "flavor": flavor, "display_name": flavor, "priority": priority,
        "detection": {"field_absent": "gitVersion"},
        "capability_restrictions": {"unsupported_ops": [], "unsupported_stage_variants": [], "syntax_constraints": []},
        "equivalent_hints": [],
    }


# Feature: mongo-flavor-capabilities-and-error-clarify, Property 13: 每个加载的 profile 结构齐备
@settings(max_examples=100)
@given(names=st.lists(st.text(alphabet="abcdefgh", min_size=1, max_size=4), min_size=1, max_size=5, unique=True))
def test_property_13_loaded_profiles_complete(tmp_path_factory, names):
    d = tmp_path_factory.mktemp("p13")
    for i, n in enumerate(names):
        (d / f"{n}.json").write_text(json.dumps(_valid_profile(n, i)))
    reg = ProfileRegistry(profiles_dir=d)
    for p in reg.load():
        assert "unsupported_ops" in p.capability_restrictions
        assert "unsupported_stage_variants" in p.capability_restrictions
        assert "syntax_constraints" in p.capability_restrictions
        assert isinstance(p.equivalent_hints, list)
        assert p.detection


# Feature: mongo-flavor-capabilities-and-error-clarify, Property 15: 全合法档案全部加载
@settings(max_examples=100)
@given(n=st.integers(min_value=0, max_value=6))
def test_property_15_load_all_valid(tmp_path_factory, n):
    d = tmp_path_factory.mktemp("p15")
    for i in range(n):
        (d / f"f{i}.json").write_text(json.dumps(_valid_profile(f"f{i}", i)))
    reg = ProfileRegistry(profiles_dir=d)
    assert len(reg.load()) == n


# Feature: mongo-flavor-capabilities-and-error-clarify, Property 14: 单坏档案触发全量失败 + 先日志后原生回退
@settings(max_examples=50)
@given(
    good=st.integers(min_value=0, max_value=3),
    bad_kind=st.sampled_from(["badjson", "missing_key", "unknown_predicate"]),
)
def test_property_14_malformed_fails_load(tmp_path_factory, good, bad_kind):
    d = tmp_path_factory.mktemp("p14")
    for i in range(good):
        (d / f"ok{i}.json").write_text(json.dumps(_valid_profile(f"ok{i}", i)))
    if bad_kind == "badjson":
        (d / "bad.json").write_text("{not valid json")
    elif bad_kind == "missing_key":
        (d / "bad.json").write_text(json.dumps({"flavor": "x"}))
    else:
        bad = _valid_profile("bad")
        bad["detection"] = {"nonsense_predicate": "k"}
        (d / "bad.json").write_text(json.dumps(bad))
    reg = ProfileRegistry(profiles_dir=d)
    with pytest.raises(ProfileLoadError):
        reg.load()


# task 7.3: detect/compute 注入异常 → build_capabilities 回退原生
def test_build_capabilities_fallback_on_error(monkeypatch):
    import app.engine.drivers.mongo_flavor as mf

    def boom(*a, **k):
        raise RuntimeError("detect blew up")

    monkeypatch.setattr(mf, "detect_flavor", boom)
    caps = mf.build_capabilities({"version": "5.0.0"}, "5.0.0")
    assert caps["flavor"] == NATIVE_FLAVOR
    assert caps["unsupported_stage_variants"] == []
