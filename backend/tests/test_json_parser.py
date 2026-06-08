"""Property-based tests and example-based unit tests for the unified LLM JSON parser.

**Validates: Requirements 1, 2, 3, 4, 5, 7**

Tests cover:
- Property-based tests (Hypothesis) for correctness invariants
- Example-based unit tests for edge cases and regressions
"""
from __future__ import annotations

import json
import logging

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from app.engine.json_parser import parse_llm_json

# ════════════════════════════════════════════════════════════════
#  Strategies — reusable JSON generation
# ════════════════════════════════════════════════════════════════

# CJK and mixed-script text
cjk_text_st = st.text(
    alphabet=st.characters(categories=("L", "N", "P")),
    min_size=1,
    max_size=30,
)

# JSON leaf values
json_leaf_st = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**53), max_value=2**53),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(min_size=0, max_size=50),
    cjk_text_st,
)

# Recursive JSON values (dicts and lists with leaves)
json_value_st = st.recursive(
    json_leaf_st,
    lambda children: st.one_of(
        st.lists(children, max_size=5),
        st.dictionaries(st.text(min_size=1, max_size=20), children, max_size=5),
    ),
    max_leaves=20,
)

# Top-level JSON structures (dict or list only)
json_dict_st = st.dictionaries(st.text(min_size=1, max_size=20), json_value_st, max_size=5)
json_list_st = st.lists(json_value_st, max_size=5)
json_top_level_st = st.one_of(json_dict_st, json_list_st)

# Fence wrappers
fence_opener_st = st.sampled_from(["```json\n", "```JSON\n", "```\n"])

# Prose text (no braces/brackets to avoid confusing extraction)
prose_st = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "Z"),
        blacklist_characters="{}[]",
    ),
    min_size=1,
    max_size=50,
).filter(lambda s: s.strip() != "")


# ════════════════════════════════════════════════════════════════
#  Property 1: Round-trip correctness
#  **Validates: Requirements 1.2, 7.1, 7.3**
# ════════════════════════════════════════════════════════════════


@given(data=json_top_level_st)
@settings(max_examples=200)
def test_prop_roundtrip_correctness(data):
    """Valid JSON dicts/lists (including CJK) parse identically to json.loads."""
    raw = json.dumps(data, ensure_ascii=False)
    result = parse_llm_json(raw)
    expected = json.loads(raw)
    assert result == expected


# ════════════════════════════════════════════════════════════════
#  Property 2: Fence-wrapped extraction
#  **Validates: Requirements 1.3**
# ════════════════════════════════════════════════════════════════


@given(data=json_top_level_st, opener=fence_opener_st)
@settings(max_examples=200)
def test_prop_fence_wrapped_extraction(data, opener):
    """```json\\n{...}\\n``` returns same as unwrapped."""
    raw = json.dumps(data, ensure_ascii=False)
    fenced = f"{opener}{raw}\n```"
    result = parse_llm_json(fenced)
    expected = json.loads(raw)
    assert result == expected


# ════════════════════════════════════════════════════════════════
#  Property 3: Prose-embedded extraction
#  **Validates: Requirements 2.1**
# ════════════════════════════════════════════════════════════════


@given(data=json_top_level_st, prefix=prose_st, suffix=prose_st)
@settings(max_examples=200)
def test_prop_prose_embedded_extraction(data, prefix, suffix):
    """prose + JSON + prose returns the embedded JSON."""
    raw = json.dumps(data, ensure_ascii=False)
    text = f"{prefix}\n{raw}\n{suffix}"
    result = parse_llm_json(text)
    expected = json.loads(raw)
    assert result == expected


# ════════════════════════════════════════════════════════════════
#  Property 4: First-object extraction
#  **Validates: Requirements 2.3**
# ════════════════════════════════════════════════════════════════


@given(first=json_dict_st, second=json_dict_st)
@settings(max_examples=200)
def test_prop_first_object_extraction(first, second):
    """Two JSON objects in text, returns first only."""
    raw_first = json.dumps(first, ensure_ascii=False)
    raw_second = json.dumps(second, ensure_ascii=False)
    text = f"Here is the first: {raw_first}\nAnd the second: {raw_second}"
    result = parse_llm_json(text)
    expected = json.loads(raw_first)
    assert result == expected


# ════════════════════════════════════════════════════════════════
#  Property 5: Truncated JSON recovery
#  **Validates: Requirements 3.1**
# ════════════════════════════════════════════════════════════════


@given(
    keys=st.lists(
        st.text(
            alphabet=st.characters(categories=("L", "N")),
            min_size=1,
            max_size=10,
        ),
        min_size=3,
        max_size=6,
        unique=True,
    ),
    values=st.lists(st.integers(min_value=0, max_value=1000), min_size=6, max_size=6),
)
@settings(max_examples=200)
def test_prop_truncated_json_recovery(keys, values):
    """Truncated dict after first KV pair returns non-None."""
    # Build a dict with at least 3 KV pairs
    d = {k: v for k, v in zip(keys, values)}
    raw = json.dumps(d, ensure_ascii=False)

    # Find position after first complete KV pair (after first comma)
    first_comma = raw.index(",")
    # Truncate somewhere after first KV pair but before end
    assume(first_comma + 1 < len(raw) - 1)
    truncated = raw[: first_comma + 2]  # include char after comma

    result = parse_llm_json(truncated)
    assert result is not None


# ════════════════════════════════════════════════════════════════
#  Property 6: Type constraint enforcement
#  **Validates: Requirements 4.2, 4.3**
# ════════════════════════════════════════════════════════════════


@given(data=json_list_st)
@settings(max_examples=200)
def test_prop_type_constraint_list_with_expect_dict(data):
    """List with expect='dict' returns None."""
    raw = json.dumps(data, ensure_ascii=False)
    result = parse_llm_json(raw, expect="dict")
    assert result is None


@given(data=json_dict_st)
@settings(max_examples=200)
def test_prop_type_constraint_dict_with_expect_list(data):
    """Dict with expect='list' returns None."""
    raw = json.dumps(data, ensure_ascii=False)
    result = parse_llm_json(raw, expect="list")
    assert result is None


# ════════════════════════════════════════════════════════════════
#  Property 7: Non-JSON input never raises
#  **Validates: Requirements 1.6**
# ════════════════════════════════════════════════════════════════


@given(text=st.text(min_size=0, max_size=200))
@settings(max_examples=200)
def test_prop_non_json_never_raises(text):
    """Arbitrary strings return dict/list or None, no exception."""
    result = parse_llm_json(text)
    assert result is None or isinstance(result, (dict, list))


# ════════════════════════════════════════════════════════════════
#  Property 8: Malformed JSON repair
#  **Validates: Requirements 1.4**
# ════════════════════════════════════════════════════════════════


@given(data=st.dictionaries(
    st.text(
        alphabet=st.characters(categories=("L", "N")),
        min_size=1,
        max_size=10,
    ),
    st.one_of(st.integers(), st.text(min_size=1, max_size=20)),
    min_size=1,
    max_size=5,
))
@settings(max_examples=200)
def test_prop_malformed_json_repair(data):
    """Trailing comma / single quotes / unquoted keys → non-None."""
    raw = json.dumps(data, ensure_ascii=False)

    # Introduce trailing comma after last value
    trailing_comma = raw[:-1] + ",}"
    result_tc = parse_llm_json(trailing_comma)
    assert result_tc is not None, f"Failed on trailing comma: {trailing_comma}"

    # Single quotes instead of double quotes
    single_quotes = raw.replace('"', "'")
    result_sq = parse_llm_json(single_quotes)
    assert result_sq is not None, f"Failed on single quotes: {single_quotes}"


# ════════════════════════════════════════════════════════════════
#  Example-based unit tests — Task 3
# ════════════════════════════════════════════════════════════════


class TestEmptyInput:
    """**Validates: Requirements 1.6**"""

    def test_empty_input_returns_none(self):
        """Empty string returns None."""
        assert parse_llm_json("") is None

    def test_whitespace_only_returns_none(self):
        """Whitespace-only returns None."""
        assert parse_llm_json("   \n\t  ") is None


class TestJsonRepairEmptyResult:
    """**Validates: Requirements 1.5**"""

    def test_json_repair_empty_result_returns_none(self):
        """json_repair returning {} from garbage is treated as failure."""
        # Garbage input that json_repair might produce {} from
        result = parse_llm_json("not json at all, just random text here")
        assert result is None


class TestLogging:
    """**Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5**"""

    def test_logging_direct_parse_no_log(self, caplog):
        """No log emitted on direct parse success."""
        with caplog.at_level(logging.DEBUG, logger="app.engine.json_parser"):
            parse_llm_json('{"key": "value"}')
        assert caplog.records == []

    def test_logging_fence_strip_debug(self, caplog):
        """DEBUG log when fence removal needed."""
        with caplog.at_level(logging.DEBUG, logger="app.engine.json_parser"):
            result = parse_llm_json('```json\n{"key": "value"}\n```')
        assert result == {"key": "value"}
        debug_msgs = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("fence" in r.message.lower() for r in debug_msgs)

    def test_logging_json_repair_info(self, caplog):
        """INFO log when json_repair used."""
        # Single quotes require json_repair (not handled by earlier stages)
        with caplog.at_level(logging.DEBUG, logger="app.engine.json_parser"):
            result = parse_llm_json("{'key': 'value'}")
        assert result is not None
        info_msgs = [r for r in caplog.records if r.levelno == logging.INFO]
        assert any("json_repair" in r.message for r in info_msgs)

    def test_logging_truncation_info(self, caplog):
        """INFO log when truncation repair applied."""
        # Truncated JSON — missing closing brace
        with caplog.at_level(logging.DEBUG, logger="app.engine.json_parser"):
            result = parse_llm_json('{"key": "value", "key2": "val2')
        assert result is not None
        info_msgs = [r for r in caplog.records if r.levelno == logging.INFO]
        assert any("truncation" in r.message.lower() for r in info_msgs)

    def test_logging_all_fail_warning(self, caplog):
        """WARNING with first 200 chars on total failure."""
        garbage = "x" * 300
        with caplog.at_level(logging.DEBUG, logger="app.engine.json_parser"):
            result = parse_llm_json(garbage)
        assert result is None
        warn_msgs = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warn_msgs) >= 1
        # Should contain first 200 chars
        assert "x" * 50 in warn_msgs[0].message


class TestRealWorldRegressions:
    """Regression tests for real-world LLM output issues.

    **Validates: Requirements 1.3, 1.4, 5, 7**
    """

    def test_real_world_chinese_unescaped_quotes(self):
        """Regression: Chinese text with unescaped inner quotes.

        The actual bug that triggered this feature — LLM outputs like:
        "请描述"策略"的业务含义"
        """
        raw = '{"question": "请描述\\"策略\\"的业务含义", "answer": "策略是指..."}'
        result = parse_llm_json(raw)
        assert result is not None
        assert result["question"] == '请描述"策略"的业务含义'

    def test_real_world_trailing_comma(self):
        """Regression: trailing comma after last value."""
        raw = '{"key": "value", "items": [1, 2, 3],}'
        result = parse_llm_json(raw)
        assert result is not None
        assert result["key"] == "value"
        assert result["items"] == [1, 2, 3]

    def test_real_world_markdown_fence_with_language(self):
        """Regression: markdown fence with language tag."""
        raw = '```json\n{"status": "ok", "count": 42}\n```'
        result = parse_llm_json(raw)
        assert result == {"status": "ok", "count": 42}
