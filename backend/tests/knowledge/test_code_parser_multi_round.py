"""多轮分层展开单测合集 (Phase 4-7).

覆盖 02-acceptance.md UT-1 ~ UT-14.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.config import settings
from app.knowledge.code_parser import (
    BatchComplexity,
    _chunked,
    _estimate_batch_complexity,
    _extract_inner_type,
    _fill_sub_fields,
    _lookup_expanded,
    _parse_complex_batch_multi_round,
)


def _make_batch_file(filename: str, content: str) -> tuple[str, str, bool]:
    """与 _parse_java_batch 中 batch 元素同形 (path, content, is_ref)."""
    return (filename, content, False)


# ════════════════════════════════════════════════════════════════
#  Phase 4: 复杂度估算
# ════════════════════════════════════════════════════════════════


class TestBatchComplexity:
    def test_simple_entity(self):
        """UT-1: 单文件简单 entity, score ≈ 1.0, 不复杂."""
        src = """
        @Document(collection = "users")
        public class User {
            private String id;
            private String name;
        }
        """
        batch = [_make_batch_file("User.java", src)]
        c = _estimate_batch_complexity(batch)
        assert c.is_complex is False
        assert c.total < 1.5

    def test_heavy_entity(self):
        """UT-3: 10+ static class + 100 字段, 单文件即超阈值."""
        inner_classes = "\n".join(
            f"    public static class Inner{i} {{ private Foo{i} foo; }}"
            for i in range(12)
        )
        fields = "\n".join(
            f"    private CustomType{i} f{i};" for i in range(100)
        )
        src = f"""
@Document(collection = "complex")
public class Complex {{
{fields}
{inner_classes}
}}
"""
        batch = [_make_batch_file("Complex.java", src)]
        c = _estimate_batch_complexity(batch)
        assert c.is_complex is True
        assert c.total > 15

    def test_batch_sum(self):
        """UT-2: 3 文件累加超阈值."""
        src_each = "\n".join(
            f"    public static class Inner{i} {{ private Foo{i} foo; }}"
            for i in range(10)
        )
        batch = [
            _make_batch_file(
                f"E{i}.java",
                f"public class E{i} {{\n{src_each}\n}}",
            )
            for i in range(3)
        ]
        c = _estimate_batch_complexity(batch)
        assert c.is_complex is True

    def test_threshold_boundary(self):
        """UT-4: 严格大于阈值才视为复杂; 等于阈值不算."""
        fake = BatchComplexity(file_score={"x.java": 15.0}, total=15.0)
        assert fake.is_complex is False
        fake2 = BatchComplexity(file_score={"x.java": 15.01}, total=15.01)
        assert fake2.is_complex is True


# ════════════════════════════════════════════════════════════════
#  Phase 5: 拼接与类型查找
# ════════════════════════════════════════════════════════════════


class TestExtractInnerType:
    def test_simple(self):
        assert _extract_inner_type("Foo") == "Foo"

    def test_list(self):
        assert _extract_inner_type("List<Foo>") == "Foo"

    def test_map(self):
        assert _extract_inner_type("Map<String, Bar>") == "Bar"

    def test_array(self):
        assert _extract_inner_type("Foo[]") == "Foo"

    def test_nested_list(self):
        assert _extract_inner_type("List<List<Baz>>") == "Baz"


class TestLookupExpanded:
    def test_exact_match(self):
        expanded = {"Address": [{"field": "city", "type": "String"}]}
        res = _lookup_expanded("Address", expanded)
        assert res == [{"field": "city", "type": "String"}]

    def test_qualified_to_short(self):
        """限定名 -> 短名 fallback."""
        expanded = {"Content": [{"field": "label", "type": "String"}]}
        res = _lookup_expanded("User.Content", expanded)
        assert res == [{"field": "label", "type": "String"}]

    def test_short_to_qualified(self):
        """短名 -> 单一限定名命中."""
        expanded = {
            "User.AddressTag": [{"field": "label", "type": "String"}]
        }
        res = _lookup_expanded("AddressTag", expanded, batch_classes={"User"})
        assert res == [{"field": "label", "type": "String"}]

    def test_ambiguous_returns_none(self):
        """多个限定名结尾匹配 -> 歧义返回 None."""
        expanded = {
            "User.AddressTag": [{"field": "a"}],
            "Order.AddressTag": [{"field": "b"}],
        }
        res = _lookup_expanded(
            "AddressTag", expanded, batch_classes={"User", "Order"}
        )
        assert res is None


class TestFillSubFields:
    def test_short_name_fallback(self):
        """UT-5: Round 2 返 'Content', Round 1 用限定名 'User.Content'."""
        fields = [
            {"field": "tag", "type": "User.Content", "needs_expansion": True}
        ]
        expanded = {"Content": [{"field": "label", "type": "String"}]}
        _fill_sub_fields(fields, expanded)
        assert fields[0]["sub_fields"] == [{"field": "label", "type": "String"}]
        assert "needs_expansion" not in fields[0]

    def test_self_reference(self):
        """UT-6: TreeNode 自引用, 第二次不再展开."""
        fields = [
            {"field": "value", "type": "String"},
            {
                "field": "children",
                "type": "List<TreeNode>",
                "needs_expansion": True,
            },
        ]
        expanded = {
            "TreeNode": [
                {"field": "value", "type": "String"},
                {
                    "field": "children",
                    "type": "List<TreeNode>",
                    "needs_expansion": True,
                },
            ]
        }
        _fill_sub_fields(fields, expanded)
        children_field = fields[1]
        assert "sub_fields" in children_field
        inner = children_field["sub_fields"][1]
        assert inner.get("needs_expansion") is True
        assert inner.get("sub_fields") in (None, [])

    def test_depth_limit(self, monkeypatch):
        """UT-7: 深度上限触底, 保留 needs_expansion=True."""
        monkeypatch.setattr(
            "app.knowledge.code_parser.settings.code_parse_expansion_max_depth",
            2,
        )
        expanded = {
            "L": [{"field": "next", "type": "L", "needs_expansion": True}],
        }
        fields = [{"field": "head", "type": "L", "needs_expansion": True}]
        _fill_sub_fields(fields, expanded)
        cur = fields[0]
        depth = 0
        while cur.get("sub_fields"):
            cur = cur["sub_fields"][0]
            depth += 1
            if depth > 5:
                pytest.fail("递归未截断")
        assert depth <= 2

    def test_empty_expanded(self):
        """UT-8: expanded={}, 仅骨架, 不抛."""
        fields = [
            {"field": "addr", "type": "Address", "needs_expansion": True}
        ]
        _fill_sub_fields(fields, {})
        assert fields[0].get("needs_expansion") is True


# ════════════════════════════════════════════════════════════════
#  Phase 6: 多轮主路径
# ════════════════════════════════════════════════════════════════


@pytest.fixture
def stub_round1_skeleton():
    return {
        "mongo_docs": [
            {
                "class_name": "User",
                "collection": "users",
                "file": "User.java",
                "fields": [
                    {"field": "id", "type": "String"},
                    {
                        "field": "addr",
                        "type": "Address",
                        "needs_expansion": True,
                    },
                ],
            }
        ],
        "types_to_expand": ["Address"],
    }


@pytest.fixture
def stub_round2_full():
    return {
        "expanded_classes": [
            {
                "class_name": "Address",
                "fields": [{"field": "city", "type": "String"}],
            }
        ]
    }


def _fake_batch():
    return [("User.java", "public class User {}", False)]


class TestMultiRoundMainPath:
    def test_no_expansion_needed(self, stub_round1_skeleton):
        """UT-9: types_to_expand=[], 跳过 Round 2."""
        skel = {**stub_round1_skeleton, "types_to_expand": []}
        with patch(
            "app.knowledge.code_parser._call_round1_skeleton",
            return_value=skel,
        ) as r1, patch(
            "app.knowledge.code_parser._call_round2_expand"
        ) as r2:
            result = _parse_complex_batch_multi_round(_fake_batch())
        assert r1.called
        assert not r2.called
        assert (
            result["mongo_docs"][0]["fields"][1].get("needs_expansion") is True
        )
        assert result["partial"] is False

    def test_round1_then_round2_full(
        self, stub_round1_skeleton, stub_round2_full
    ):
        """UT-10: Round 1 + Round 2 全成功."""
        with patch(
            "app.knowledge.code_parser._call_round1_skeleton",
            return_value=stub_round1_skeleton,
        ), patch(
            "app.knowledge.code_parser._call_round2_expand",
            return_value=stub_round2_full,
        ):
            result = _parse_complex_batch_multi_round(_fake_batch())
        addr = result["mongo_docs"][0]["fields"][1]
        assert "needs_expansion" not in addr
        assert addr.get("sub_fields") == [{"field": "city", "type": "String"}]
        assert result["partial"] is False

    def test_round2_partial_failure(self, stub_round1_skeleton):
        """UT-11: Round 2 第 1 批 OK 第 2 批超时降级仍失败."""
        skel = {
            **stub_round1_skeleton,
            "types_to_expand": [f"T{i}" for i in range(8)],
        }
        call_log: list[list[str]] = []

        def fake_round2(_batch, target_classes, **_kw):
            call_log.append(list(target_classes))
            if "T0" in target_classes:
                return {
                    "expanded_classes": [
                        {"class_name": t, "fields": []}
                        for t in target_classes
                    ]
                }
            raise TimeoutError("simulated round2 timeout")

        with patch(
            "app.knowledge.code_parser._call_round1_skeleton",
            return_value=skel,
        ), patch(
            "app.knowledge.code_parser._call_round2_expand",
            side_effect=fake_round2,
        ):
            result = _parse_complex_batch_multi_round(_fake_batch())
        assert result["partial"] is True
        assert result["errored"] is False
        # chunk1=[T0..T4] OK, chunk2=[T5..T7] fail, fallback=[T5..T7] fail
        assert len(call_log) == 3
        assert call_log[0] == [f"T{i}" for i in range(5)]
        assert call_log[1] == [f"T{i}" for i in range(5, 8)]
        assert call_log[2] == [f"T{i}" for i in range(5, 8)]

    def test_round2_fallback_subbatch_split(
        self, stub_round1_skeleton, monkeypatch
    ):
        """UT-11b: chunk_size=5 + fallback_size=2, 降级切成多子批."""
        skel = {
            **stub_round1_skeleton,
            "types_to_expand": [f"T{i}" for i in range(5)],
        }
        call_log: list[list[str]] = []

        def fake_round2(_batch, target_classes, **_kw):
            call_log.append(list(target_classes))
            raise TimeoutError("force fallback")

        monkeypatch.setattr(
            settings,
            "code_parse_round2_classes_per_call_fallback",
            2,
        )
        with patch(
            "app.knowledge.code_parser._call_round1_skeleton",
            return_value=skel,
        ), patch(
            "app.knowledge.code_parser._call_round2_expand",
            side_effect=fake_round2,
        ):
            result = _parse_complex_batch_multi_round(_fake_batch())
        assert result["partial"] is True
        # 1 次主 chunk (失败) + ceil(5/2)=3 子批各 1 次 = 4 次
        assert len(call_log) == 4
        assert call_log[0] == [f"T{i}" for i in range(5)]
        assert call_log[1] == ["T0", "T1"]
        assert call_log[2] == ["T2", "T3"]
        assert call_log[3] == ["T4"]

    def test_round1_failure_errors_batch(self):
        """UT-12: Round 1 全失败, 整 batch errored."""
        with patch(
            "app.knowledge.code_parser._call_round1_skeleton",
            return_value=None,
        ):
            result = _parse_complex_batch_multi_round(_fake_batch())
        assert result["errored"] is True
        assert result["mongo_docs"] == []


# ════════════════════════════════════════════════════════════════
#  Phase 7: 接入点路由
# ════════════════════════════════════════════════════════════════


class TestParseJavaBatchRouting:
    def test_routes_simple_to_single(self, tmp_path, monkeypatch):
        """UT-13: 复杂度低 → 走原单轮."""
        import app.knowledge.code_parser as cp

        called = {"single": 0, "multi": 0}

        def fake_call_validate(messages, **_):
            called["single"] += 1
            return {
                "entities": [],
                "mongo_docs": [],
                "mongo_query_patterns": [],
            }

        def fake_multi(_batch):
            called["multi"] += 1
            return {"mongo_docs": [], "errored": False, "partial": False}

        monkeypatch.setattr(cp, "_call_and_validate_java", fake_call_validate)
        monkeypatch.setattr(
            cp, "_parse_complex_batch_multi_round", fake_multi
        )

        src = tmp_path / "Simple.java"
        src.write_text(
            "public class Simple { private String id; }",
            encoding="utf-8",
        )

        cp._parse_java_batch([str(src)], ref_set=set(), ref_map=None)
        assert called["single"] == 1
        assert called["multi"] == 0

    def test_routes_complex_to_multi(self, tmp_path, monkeypatch):
        """UT-14: 复杂度高 → 走多轮."""
        import app.knowledge.code_parser as cp

        called = {"single": 0, "multi": 0}

        def fake_call_validate(messages, **_):
            called["single"] += 1
            return {
                "entities": [],
                "mongo_docs": [],
                "mongo_query_patterns": [],
            }

        def fake_multi(_batch):
            called["multi"] += 1
            return {"mongo_docs": [], "errored": False, "partial": False}

        monkeypatch.setattr(cp, "_call_and_validate_java", fake_call_validate)
        monkeypatch.setattr(
            cp, "_parse_complex_batch_multi_round", fake_multi
        )

        # 30 inner classes + non-leaf top-level fields to exceed threshold=15
        inner = "\n".join(
            f"    public static class Inner{i} "
            f"{{ private Foo{i} foo; }}"
            for i in range(30)
        )
        top_fields = "\n".join(
            f"    private Inner{i} ref{i};" for i in range(30)
        )
        src = tmp_path / "Heavy.java"
        src.write_text(
            f"public class Heavy {{\n{top_fields}\n{inner}\n}}",
            encoding="utf-8",
        )

        cp._parse_java_batch([str(src)], ref_set=set(), ref_map=None)
        assert called["multi"] == 1
        assert called["single"] == 0


# ════════════════════════════════════════════════════════════════
#  Prompt 加载
# ════════════════════════════════════════════════════════════════


class TestPromptLoading:
    def test_round1_prompt_loadable(self):
        """UT-15: 09-java-skeleton-extract 可加载."""
        from app.knowledge.extraction_prompts import load_prompt_or_fallback

        body = load_prompt_or_fallback("09-java-skeleton-extract")
        assert "Java schema 静态分析助手" in body
        assert "needs_expansion" in body

    def test_round2_prompt_loadable(self):
        """UT-16: 10-java-type-expand 可加载."""
        from app.knowledge.extraction_prompts import load_prompt_or_fallback

        body = load_prompt_or_fallback("10-java-type-expand")
        assert "递归展开嵌套类型" in body
        assert "target_classes" in body

    def test_fallback_present(self):
        """UT-17: prompt 文件加载失败时兜底到 _PROMPT_FALLBACK_MAP."""
        from app.knowledge.extraction_prompts import _PROMPT_FALLBACK_MAP

        assert "09-java-skeleton-extract" in _PROMPT_FALLBACK_MAP
        assert "10-java-type-expand" in _PROMPT_FALLBACK_MAP
        assert (
            "Java schema 静态分析助手"
            in _PROMPT_FALLBACK_MAP["09-java-skeleton-extract"]
        )


# ════════════════════════════════════════════════════════════════
#  Settings
# ════════════════════════════════════════════════════════════════


class TestSettings:
    def test_default_values(self):
        """UT-19: 6 个新 settings 默认值."""
        assert settings.code_parse_complex_threshold == 15
        assert settings.code_parse_round1_max_tokens == 4096
        assert settings.code_parse_round2_max_tokens == 8192
        assert settings.code_parse_round2_classes_per_call == 5
        assert settings.code_parse_round2_classes_per_call_fallback == 3
        assert settings.code_parse_expansion_max_depth == 4


# ════════════════════════════════════════════════════════════════
#  Helper: _chunked
# ════════════════════════════════════════════════════════════════


class TestChunked:
    def test_even_split(self):
        assert _chunked([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]

    def test_uneven_split(self):
        assert _chunked([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]

    def test_single_chunk(self):
        assert _chunked([1, 2], 5) == [[1, 2]]
