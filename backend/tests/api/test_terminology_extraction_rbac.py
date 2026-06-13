"""terminology (P) + extraction_failure (P list + D retry/ignore) 作用域化。

注: namespaces.created_by 有 FK, "他人拥有"的 ns 必须由真实 foreign 用户创建。
"""
import pytest
from app.models.user import User
from app.models.namespace import Namespace
from app.models.extraction_failure_log import ExtractionFailureLog


async def _foreign_owner(db, uname):
    fo = User(username=uname, role="admin", password_hash="x")
    db.add(fo); await db.flush()
    return fo


@pytest.mark.asyncio
async def test_terminology_conflicts_foreign_403(make_client, db):
    a = User(username="te_a", role="admin", password_hash="x")
    db.add(a); await db.flush()
    fo = await _foreign_owner(db, "fo_te_a")
    foreign = Namespace(name="te-f", slug="te-f-36", created_by=fo.id)
    db.add(foreign); await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="te_a")
    resp = await client.get(f"/api/namespaces/{foreign.id}/terminology/conflicts")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_extraction_list_foreign_403(make_client, db):
    a = User(username="te_d", role="admin", password_hash="x")
    db.add(a); await db.flush()
    fo = await _foreign_owner(db, "fo_te_d")
    foreign = Namespace(name="te-f3", slug="te-f3-36", created_by=fo.id)
    db.add(foreign); await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="te_d")
    resp = await client.get(f"/api/namespaces/{foreign.id}/extraction-failures")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_extraction_retry_foreign_403(make_client, db):
    a = User(username="te_b", role="admin", password_hash="x")
    db.add(a); await db.flush()
    fo = await _foreign_owner(db, "fo_te_b")
    foreign = Namespace(name="te-f2", slug="te-f2-36", created_by=fo.id)
    db.add(foreign); await db.flush()
    f = ExtractionFailureLog(
        namespace_id=foreign.id, extraction_kind="enum_class",
        failure_type="llm_parse_error", failure_message="x",
    )
    db.add(f); await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="te_b")
    resp = await client.post(f"/api/extraction-failures/{f.id}/retry")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_extraction_retry_own_ok(make_client, db):
    a = User(username="te_c", role="admin", password_hash="x")
    db.add(a); await db.flush()
    ns = Namespace(name="te-own", slug="te-own-36", created_by=a.id)
    db.add(ns); await db.flush()
    f = ExtractionFailureLog(
        namespace_id=ns.id, extraction_kind="enum_class",
        failure_type="llm_parse_error", failure_message="x",
    )
    db.add(f); await db.commit()
    client = await make_client(role="admin", user_id=a.id, username="te_c")
    resp = await client.post(f"/api/extraction-failures/{f.id}/retry")
    assert resp.status_code == 200
