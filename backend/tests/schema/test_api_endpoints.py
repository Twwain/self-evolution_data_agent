"""Phase 1 Task 7: schema canonical v2 API 端点测试.

覆盖: promote / conflicts / resolve / candidates / evidence /
       confirm-field / lock / unlock / audit-log / pending-counts / PATCH auto-lock.
"""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db.metadata import get_db
from app.knowledge.canonical_candidate import write_canonical_candidate
from app.knowledge.canonical_promote import promote_candidates_to_canonical
from app.main import app
from app.models import (
    Namespace,
    SchemaCanonicalAuditLog,
    SchemaCanonicalCandidate,
    SchemaCanonicalObject,
)
from app.models.user import User

pytestmark = pytest.mark.asyncio


# ─────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def admin_user(test_session: AsyncSession) -> User:
    user = User(username="admin_api_v2", password_hash="x", role="super_admin", is_active=True)
    test_session.add(user)
    await test_session.flush()
    return user


@pytest_asyncio.fixture
async def http_client(
    test_session: AsyncSession, admin_user: User
) -> AsyncGenerator[AsyncClient, None]:
    async def _override_db():
        yield test_session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: admin_user
    app.dependency_overrides[get_current_user] = lambda: admin_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def ns(test_session: AsyncSession, namespace_factory) -> Namespace:
    return await namespace_factory()


@pytest_asyncio.fixture
async def sco(test_session: AsyncSession, ns: Namespace) -> SchemaCanonicalObject:
    obj = SchemaCanonicalObject(
        namespace_id=ns.id,
        db_type="mysql",
        database="db1",
        target="t_order",
        fields_json=json.dumps([{"name": "status", "type": "VARCHAR(20)"}]),
        indexes_json="[]",
        description="订单表",
        purpose_detail="",
        sample_count=100,
        source="introspect",
        relationships_json="[]",
        sample_values_json="[]",
        user_locked=False,
    )
    test_session.add(obj)
    await test_session.flush()
    return obj


# ─────────────────────────────────────────────────────────────────
# POST /promote
# ─────────────────────────────────────────────────────────────────


async def test_promote_endpoint_returns_report(
    http_client: AsyncClient, ns: Namespace, test_session: AsyncSession
):
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "订单状态"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    await test_session.commit()

    resp = await http_client.post(f"/api/namespaces/{ns.id}/schema-canonical/promote")
    assert resp.status_code == 200
    body = resp.json()
    assert body["promoted_count"] >= 1
    assert "duration_seconds" in body


async def test_promote_endpoint_no_candidates(
    http_client: AsyncClient, ns: Namespace
):
    resp = await http_client.post(f"/api/namespaces/{ns.id}/schema-canonical/promote")
    assert resp.status_code == 200
    body = resp.json()
    assert body["promoted_count"] == 0
    assert body["candidates_processed"] == 0


# ─────────────────────────────────────────────────────────────────
# GET /conflicts + POST /conflicts/{cid}/resolve
# ─────────────────────────────────────────────────────────────────


async def test_list_conflicts_returns_open(
    http_client: AsyncClient, ns: Namespace, test_session: AsyncSession
):
    # 制造 conflict: 两个不同 value 的 confirmed candidate
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "A"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "B"},
        evidence_sources=[{"source": "code_jpa_javadoc"}],
        confidence_status="confirmed_by_code",
    )
    await test_session.commit()
    await promote_candidates_to_canonical(test_session, ns.id)
    await test_session.commit()

    resp = await http_client.get(f"/api/namespaces/{ns.id}/schema-canonical/conflicts")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["status"] == "open"
    assert body[0]["field_path"] == "status"


async def test_resolve_conflict_keep_a(
    http_client: AsyncClient, ns: Namespace, test_session: AsyncSession
):
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="desc_field", candidate_kind="field_description",
        candidate_value={"description": "Winner"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="desc_field", candidate_kind="field_description",
        candidate_value={"description": "Loser"},
        evidence_sources=[{"source": "code_jpa_javadoc"}],
        confidence_status="confirmed_by_code",
    )
    await test_session.commit()
    await promote_candidates_to_canonical(test_session, ns.id)
    await test_session.commit()

    # Get conflict id
    resp = await http_client.get(f"/api/namespaces/{ns.id}/schema-canonical/conflicts")
    conflicts = resp.json()
    assert len(conflicts) >= 1
    cid = conflicts[0]["id"]

    # Resolve
    resp = await http_client.post(
        f"/api/namespaces/{ns.id}/schema-canonical/conflicts/{cid}/resolve",
        json={"resolution_choice": "keep_a"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "resolved"
    assert body["resolution_choice"] == "keep_a"


async def test_resolve_conflict_invalid_choice_returns_422(
    http_client: AsyncClient, ns: Namespace, test_session: AsyncSession
):
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="inv_field", candidate_kind="field_description",
        candidate_value={"description": "X"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="inv_field", candidate_kind="field_description",
        candidate_value={"description": "Y"},
        evidence_sources=[{"source": "code_jpa_javadoc"}],
        confidence_status="confirmed_by_code",
    )
    await test_session.commit()
    await promote_candidates_to_canonical(test_session, ns.id)
    await test_session.commit()

    resp = await http_client.get(f"/api/namespaces/{ns.id}/schema-canonical/conflicts")
    cid = resp.json()[0]["id"]

    resp = await http_client.post(
        f"/api/namespaces/{ns.id}/schema-canonical/conflicts/{cid}/resolve",
        json={"resolution_choice": "invalid_choice"},
    )
    assert resp.status_code == 422


# ─────────────────────────────────────────────────────────────────
# GET /{sco_id}/candidates
# ─────────────────────────────────────────────────────────────────


async def test_list_candidates_for_canonical(
    http_client: AsyncClient, ns: Namespace, sco: SchemaCanonicalObject,
    test_session: AsyncSession,
):
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "候选1"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    await test_session.commit()

    resp = await http_client.get(
        f"/api/namespaces/{ns.id}/schema-canonical/{sco.id}/candidates"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) >= 1
    assert body[0]["field_path"] == "status"


async def test_list_candidates_filter_field_path(
    http_client: AsyncClient, ns: Namespace, sco: SchemaCanonicalObject,
    test_session: AsyncSession,
):
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "s"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="amount", candidate_kind="field_description",
        candidate_value={"description": "a"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    await test_session.commit()

    resp = await http_client.get(
        f"/api/namespaces/{ns.id}/schema-canonical/{sco.id}/candidates",
        params={"field_path": "amount"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert all(c["field_path"] == "amount" for c in body)


# ─────────────────────────────────────────────────────────────────
# GET /{sco_id}/evidence
# ─────────────────────────────────────────────────────────────────


async def test_evidence_endpoint(
    http_client: AsyncClient, ns: Namespace, sco: SchemaCanonicalObject,
    test_session: AsyncSession,
):
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "ev_test"},
        evidence_sources=[{"source": "introspect"}, {"source": "code_jpa_javadoc"}],
        confidence_status="confirmed_by_introspect",
    )
    await test_session.commit()

    resp = await http_client.get(
        f"/api/namespaces/{ns.id}/schema-canonical/{sco.id}/evidence",
        params={"field": "status"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["field_path"] == "status"
    assert len(body["candidates"]) >= 1


# ─────────────────────────────────────────────────────────────────
# POST /{sco_id}/confirm-field
# ─────────────────────────────────────────────────────────────────


async def test_confirm_field_action_confirm(
    http_client: AsyncClient, ns: Namespace, sco: SchemaCanonicalObject,
    test_session: AsyncSession,
):
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "待确认"},
        evidence_sources=[{"source": "code_jpa_javadoc"}],
        confidence_status="evidence_only",
    )
    await test_session.commit()

    resp = await http_client.post(
        f"/api/namespaces/{ns.id}/schema-canonical/{sco.id}/confirm-field",
        json={"field_path": "status", "action": "confirm"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["affected_candidates"] >= 1


async def test_confirm_field_action_correct(
    http_client: AsyncClient, ns: Namespace, sco: SchemaCanonicalObject,
    test_session: AsyncSession,
):
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "原始"},
        evidence_sources=[{"source": "code_jpa_javadoc"}],
        confidence_status="evidence_only",
    )
    await test_session.commit()

    resp = await http_client.post(
        f"/api/namespaces/{ns.id}/schema-canonical/{sco.id}/confirm-field",
        json={
            "field_path": "status",
            "action": "correct",
            "corrected_value": {"description": "修正后"},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


async def test_confirm_field_action_ignore(
    http_client: AsyncClient, ns: Namespace, sco: SchemaCanonicalObject,
    test_session: AsyncSession,
):
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "忽略"},
        evidence_sources=[{"source": "code_jpa_javadoc"}],
        confidence_status="evidence_only",
    )
    await test_session.commit()

    resp = await http_client.post(
        f"/api/namespaces/{ns.id}/schema-canonical/{sco.id}/confirm-field",
        json={"field_path": "status", "action": "ignore"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


async def test_confirm_field_correct_without_value_returns_422(
    http_client: AsyncClient, ns: Namespace, sco: SchemaCanonicalObject,
    test_session: AsyncSession,
):
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "x"},
        evidence_sources=[{"source": "code_jpa_javadoc"}],
        confidence_status="evidence_only",
    )
    await test_session.commit()

    resp = await http_client.post(
        f"/api/namespaces/{ns.id}/schema-canonical/{sco.id}/confirm-field",
        json={"field_path": "status", "action": "correct"},
    )
    assert resp.status_code == 422


# ─────────────────────────────────────────────────────────────────
# POST /{sco_id}/lock + unlock
# ─────────────────────────────────────────────────────────────────


async def test_lock_unlock(
    http_client: AsyncClient, ns: Namespace, sco: SchemaCanonicalObject,
    test_session: AsyncSession,
):
    # Lock
    resp = await http_client.post(
        f"/api/namespaces/{ns.id}/schema-canonical/{sco.id}/lock",
        json={"reason": "manual lock"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    await test_session.refresh(sco)
    assert sco.user_locked is True

    # Unlock
    resp = await http_client.post(
        f"/api/namespaces/{ns.id}/schema-canonical/{sco.id}/unlock",
        json={},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    await test_session.refresh(sco)
    assert sco.user_locked is False


async def test_lock_not_found(http_client: AsyncClient, ns: Namespace):
    resp = await http_client.post(
        f"/api/namespaces/{ns.id}/schema-canonical/99999/lock",
        json={},
    )
    assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────
# GET /audit-log
# ─────────────────────────────────────────────────────────────────


async def test_audit_log_returns_entries(
    http_client: AsyncClient, ns: Namespace, sco: SchemaCanonicalObject,
    test_session: AsyncSession,
):
    # Lock to generate an audit entry
    await http_client.post(
        f"/api/namespaces/{ns.id}/schema-canonical/{sco.id}/lock",
        json={"reason": "test"},
    )

    resp = await http_client.get(
        f"/api/namespaces/{ns.id}/schema-canonical/audit-log"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) >= 1
    assert body[0]["action"] == "user_lock"


async def test_audit_log_filter_action(
    http_client: AsyncClient, ns: Namespace, sco: SchemaCanonicalObject,
    test_session: AsyncSession,
):
    await http_client.post(
        f"/api/namespaces/{ns.id}/schema-canonical/{sco.id}/lock",
        json={"reason": "a"},
    )
    await http_client.post(
        f"/api/namespaces/{ns.id}/schema-canonical/{sco.id}/unlock",
        json={},
    )

    resp = await http_client.get(
        f"/api/namespaces/{ns.id}/schema-canonical/audit-log",
        params={"action": "user_unlock"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert all(e["action"] == "user_unlock" for e in body)


# ─────────────────────────────────────────────────────────────────
# GET /pending-counts
# ─────────────────────────────────────────────────────────────────


async def test_pending_counts(
    http_client: AsyncClient, ns: Namespace, test_session: AsyncSession
):
    # Write some pending candidates
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="f1", candidate_kind="field_description",
        candidate_value={"description": "p1"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="f2", candidate_kind="field_description",
        candidate_value={"description": "p2"},
        evidence_sources=[{"source": "code_jpa_javadoc"}],
        confidence_status="evidence_only",
    )
    await test_session.commit()

    resp = await http_client.get(
        f"/api/namespaces/{ns.id}/schema-canonical/pending-counts"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["pending_promote"] >= 2
    assert body["evidence_only"] >= 1
    assert "conflicts" in body
    assert "audit_today" in body


# ─────────────────────────────────────────────────────────────────
# PATCH auto-lock (修订 #3)
# ─────────────────────────────────────────────────────────────────


async def test_patch_auto_locks(
    http_client: AsyncClient, ns: Namespace, sco: SchemaCanonicalObject,
    test_session: AsyncSession,
):
    assert sco.user_locked is False

    resp = await http_client.patch(
        f"/api/namespaces/{ns.id}/schema-canonical/{sco.id}",
        json={"description": "新描述"},
    )
    assert resp.status_code == 200

    await test_session.refresh(sco)
    assert sco.user_locked is True
