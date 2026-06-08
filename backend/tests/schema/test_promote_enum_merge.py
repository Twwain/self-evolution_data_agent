"""enum_values 集合等价 / 超集 / 冲突."""
import pytest

from app.knowledge.canonical_candidate import write_canonical_candidate
from app.knowledge.canonical_promote import promote_candidates_to_canonical

pytestmark = pytest.mark.asyncio


async def test_enum_set_equivalent_auto_promote(test_session, namespace_factory):
    """两候选 enum 顺序不同但 (name, db_value) 集合相同 → AUTO PROMOTE."""
    ns = await namespace_factory()
    a = [{"name": "CREATED", "db_value": 1}, {"name": "PAID", "db_value": 2}]
    b = [{"name": "PAID", "db_value": 2}, {"name": "CREATED", "db_value": 1}]
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="enum_values",
        candidate_value={"enum_values": a},
        evidence_sources=[{"source": "code_enum_class"}],
        confidence_status="confirmed_by_code",
    )
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="enum_values",
        candidate_value={"enum_values": b},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    await test_session.commit()

    report = await promote_candidates_to_canonical(test_session, ns.id)
    await test_session.commit()
    assert report.promoted_count == 1
    assert report.conflicted_count == 0


async def test_enum_superset_picks_larger(test_session, namespace_factory):
    """超集自动选大: A=[1,2], C=[1,2,3] → AUTO PROMOTE C."""
    ns = await namespace_factory()
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="enum_values",
        candidate_value={"enum_values": [
            {"name": "CREATED", "db_value": 1}, {"name": "PAID", "db_value": 2},
        ]},
        evidence_sources=[{"source": "code_enum_class"}],
        confidence_status="confirmed_by_code",
    )
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="enum_values",
        candidate_value={"enum_values": [
            {"name": "CREATED", "db_value": 1}, {"name": "PAID", "db_value": 2},
            {"name": "SHIPPED", "db_value": 3},
        ]},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    await test_session.commit()

    report = await promote_candidates_to_canonical(test_session, ns.id)
    await test_session.commit()
    assert report.promoted_count == 1


async def test_enum_db_value_conflict_writes_conflict(test_session, namespace_factory):
    """同 name 不同 db_value → Conflict."""
    ns = await namespace_factory()
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="enum_values",
        candidate_value={"enum_values": [{"name": "CREATED", "db_value": 1}]},
        evidence_sources=[{"source": "code_enum_class"}],
        confidence_status="confirmed_by_code",
    )
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="enum_values",
        candidate_value={"enum_values": [{"name": "CREATED", "db_value": 99}]},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    await test_session.commit()

    report = await promote_candidates_to_canonical(test_session, ns.id)
    await test_session.commit()
    assert report.conflicted_count == 1
