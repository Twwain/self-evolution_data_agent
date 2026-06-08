"""Unit tests for plan_executor.validate_pipeline_against_caps (spec task 7.1).

Pure-function pre-validation of a resolved pipeline against resolved-ds caps —
no DB, no async. Calls the validator directly.

Evidence-backed detectors (probed against DocumentDB 5.0.0 / ds=3):
- unsupported_ops: operator nested anywhere in the pipeline → violation.
- R5 (project_no_dollar_fieldpath, trace c85ccb16 / OperationFailure 16410):
  only an embedded-$ fieldpath ("$a.$id_str") is flagged; a plain rename
  ("$brandName") and a nested path ("$a.b") must NOT be flagged (regression guard).
- R2 ($lookup.let_pipeline, OperationFailure 304): only the let/pipeline sub-query
  form is flagged; a basic localField/foreignField $lookup must NOT be flagged.
- Flat variant ("$facet"): flagged by stage-name presence.
- Dotted/constraint id without a registered detector → not flagged (falls through).
- caps=None / all-empty restriction lists → None.

Validates: Requirements 5.2, 5.4, 5.6, 5.7
"""
from __future__ import annotations

from app.engine.plan_executor import (
    GENERIC_RESTRICTION_HINT,
    validate_pipeline_against_caps,
)

# ════════════════════════════════════════════
#  Caps fixtures
# ════════════════════════════════════════════


def _documentdb_caps() -> dict:
    """DocumentDB-profile subset carrying real restrictions (from aws_documentdb.json)."""
    return {
        "version": "5.0.0",
        "flavor": "documentdb",
        "unsupported_ops": ["$round", "$function"],
        "unsupported_stage_variants": ["$facet", "$lookup.let_pipeline"],
        "syntax_constraints": ["project_no_dollar_fieldpath"],
        "equivalent_hints": [
            {"restriction": "$round", "suggestion": "改用 $floor/$ceil 组合"},
            {"restriction": "project_no_dollar_fieldpath", "suggestion": "字段引用用裸名, 不要嵌入 $"},
            {"restriction": "$lookup.let_pipeline", "suggestion": "改用 localField/foreignField 基础 join"},
            {"restriction": "$facet", "suggestion": "拆成多个独立查询"},
        ],
        "agg_ops_unsupported": ["$round", "$function"],
    }


def _native_caps() -> dict:
    """Native MongoDB: all-empty restriction lists → no restrictions."""
    return {
        "version": "5.0.0",
        "flavor": "mongodb",
        "unsupported_ops": [],
        "unsupported_stage_variants": [],
        "syntax_constraints": [],
        "equivalent_hints": [],
        "agg_ops_unsupported": [],
    }


# ════════════════════════════════════════════
#  unsupported_ops
# ════════════════════════════════════════════


def test_unsupported_op_flagged_with_matching_hint():
    """An operator in unsupported_ops → violation; its equivalent_hint is the suggested_next_step."""
    pipeline = [{"$project": {"r": {"$round": ["$amount", 2]}}}]
    out = validate_pipeline_against_caps(pipeline, _documentdb_caps())
    assert out is not None
    assert out["error"] == "capability_violation"
    assert out["restriction"] == "$round"
    assert out["suggested_next_step"] == "改用 $floor/$ceil 组合"


def test_unsupported_op_nested_deep_is_detected():
    """unsupported op nested deep inside an expression is still detected (_iter_operators recurses)."""
    pipeline = [{"$addFields": {"x": {"$cond": [{"$gt": ["$a", 0]}, {"$function": {}}, 0]}}}]
    out = validate_pipeline_against_caps(pipeline, _documentdb_caps())
    assert out is not None
    assert out["restriction"] == "$function"


def test_unsupported_op_falls_back_to_generic_hint_when_unconfigured():
    """An unsupported op without a configured equivalent_hint → GENERIC_RESTRICTION_HINT."""
    caps = _documentdb_caps()
    caps["equivalent_hints"] = []  # no hints configured
    pipeline = [{"$project": {"r": {"$round": ["$amount", 2]}}}]
    out = validate_pipeline_against_caps(pipeline, caps)
    assert out is not None
    assert out["suggested_next_step"] == GENERIC_RESTRICTION_HINT


# ════════════════════════════════════════════
#  R5: project_no_dollar_fieldpath (16410)
# ════════════════════════════════════════════


def test_r5_positive_embedded_dollar_fieldpath_in_project_flagged():
    """R5 positive (trace c85ccb16 / 16410): $project with embedded-$ fieldpath → violation."""
    pipeline = [{"$project": {"x": "$a.$id_str"}}]
    out = validate_pipeline_against_caps(pipeline, _documentdb_caps())
    assert out is not None
    assert out["restriction"] == "project_no_dollar_fieldpath"
    assert out["suggested_next_step"] == "字段引用用裸名, 不要嵌入 $"


def test_r5_negative_plain_rename_not_flagged():
    """R5 negative (regression guard): plain rename {new: "$brandName"} → None."""
    pipeline = [{"$project": {"new": "$brandName"}}]
    out = validate_pipeline_against_caps(pipeline, _documentdb_caps())
    assert out is None


def test_r5_negative_nested_path_in_addfields_not_flagged():
    """R5 negative (regression guard): nested path {x: "$a.b"} in $addFields → None."""
    pipeline = [{"$addFields": {"x": "$a.b"}}]
    out = validate_pipeline_against_caps(pipeline, _documentdb_caps())
    assert out is None


# ════════════════════════════════════════════
#  R2: $lookup.let_pipeline (304)
# ════════════════════════════════════════════


def test_r2_positive_lookup_let_pipeline_flagged():
    """R2 positive: $lookup with let/pipeline sub-query body → violation."""
    pipeline = [
        {
            "$lookup": {
                "from": "orders",
                "let": {"cid": "$_id"},
                "pipeline": [{"$match": {"$expr": {"$eq": ["$customer_id", "$$cid"]}}}],
                "as": "orders",
            }
        }
    ]
    out = validate_pipeline_against_caps(pipeline, _documentdb_caps())
    assert out is not None
    assert out["restriction"] == "$lookup.let_pipeline"
    assert out["suggested_next_step"] == "改用 localField/foreignField 基础 join"


def test_r2_negative_basic_lookup_not_flagged():
    """R2 negative (regression guard): basic localField/foreignField $lookup → None."""
    pipeline = [
        {
            "$lookup": {
                "from": "orders",
                "localField": "_id",
                "foreignField": "customer_id",
                "as": "orders",
            }
        }
    ]
    out = validate_pipeline_against_caps(pipeline, _documentdb_caps())
    assert out is None


# ════════════════════════════════════════════
#  Flat stage variant
# ════════════════════════════════════════════


def test_flat_variant_facet_present_flagged():
    """Flat variant $facet listed in caps + present in pipeline → violation (stage-name match)."""
    pipeline = [{"$facet": {"a": [{"$match": {"x": 1}}], "b": [{"$count": "n"}]}}]
    out = validate_pipeline_against_caps(pipeline, _documentdb_caps())
    assert out is not None
    assert out["restriction"] == "$facet"


def test_flat_variant_not_present_not_flagged():
    """Flat variant $facet in caps but absent from pipeline → None."""
    pipeline = [{"$match": {"x": 1}}]
    out = validate_pipeline_against_caps(pipeline, _documentdb_caps())
    assert out is None


# ════════════════════════════════════════════
#  Unknown / unregistered restriction ids → not flagged
# ════════════════════════════════════════════


def test_dotted_variant_without_registered_detector_not_flagged():
    """A dotted stage variant with no registered detector → None (falls through, INV-ERRCODE)."""
    caps = _native_caps()
    caps["unsupported_stage_variants"] = ["$graphLookup.recursive"]  # no detector registered
    pipeline = [{"$graphLookup": {"from": "x", "connectFromField": "a", "connectToField": "b"}}]
    out = validate_pipeline_against_caps(pipeline, caps)
    assert out is None


def test_syntax_constraint_without_registered_detector_not_flagged():
    """A syntax constraint id with no registered detector → None (falls through)."""
    caps = _native_caps()
    caps["syntax_constraints"] = ["some_unmodeled_constraint"]  # no detector registered
    pipeline = [{"$project": {"x": "$a.$b"}}]  # would trip the real detector, but id differs
    out = validate_pipeline_against_caps(pipeline, caps)
    assert out is None


# ════════════════════════════════════════════
#  No-restriction / empty caps → None
# ════════════════════════════════════════════


def test_caps_none_returns_none():
    """caps=None → None (no restrictions to enforce)."""
    pipeline = [{"$project": {"x": "$a.$id_str"}}]
    assert validate_pipeline_against_caps(pipeline, None) is None


def test_all_empty_restriction_lists_returns_none():
    """Native caps with all-empty restriction lists → None."""
    pipeline = [{"$project": {"x": "$a.$id_str"}}, {"$facet": {"a": []}}]
    assert validate_pipeline_against_caps(pipeline, _native_caps()) is None


def test_clean_pipeline_under_documentdb_caps_returns_none():
    """A clean pipeline (only supported ops/stages) under documentdb caps → None."""
    pipeline = [
        {"$match": {"status": "active"}},
        {"$group": {"_id": "$category", "total": {"$sum": "$amount"}}},
        {"$sort": {"total": -1}},
        {"$project": {"category": "$_id", "total": 1}},
    ]
    assert validate_pipeline_against_caps(pipeline, _documentdb_caps()) is None
