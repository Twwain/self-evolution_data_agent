"""Profile CRUD L2 contract — httpx + ASGITransport against real FastAPI router.

使用项目 make_client fixture (override get_current_user→admin + get_db→savepoint db),
真实路由 + 真实 require_admin_or_above 判定 + savepoint 隔离。
"""
import pytest
import pytest_asyncio

from app.models.git_repo import GitRepo
from app.models.namespace import Namespace

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client(make_client):
    return await make_client(role="super_admin", user_id=1, username="admin")


class TestListProfiles:
    async def test_list_profiles_status_and_shape(self, client):
        resp = await client.get("/api/profiles")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        spring = next((p for p in data if p.get("name") == "java-spring"), None)
        assert spring is not None, "java-spring 种子数据应存在"
        assert spring["is_builtin"] is True
        for key in ("id", "name", "display_name", "hint_text", "is_builtin", "is_enabled"):
            assert key in spring


class TestCreateGetUpdateDeleteLifecycle:
    async def test_create_get_update_delete(self, client):
        create_resp = await client.post("/api/profiles", json={
            "name": "contract-test-profile",
            "display_name": "Contract Test Profile",
            "description": "L2 test",
            "languages": ["Python"],
            "hint_text": "Use SQLAlchemy declarative_base",
        })
        assert create_resp.status_code == 200
        created = create_resp.json()
        pid = created["id"]
        assert created["name"] == "contract-test-profile"
        assert created["is_builtin"] is False

        get_resp = await client.get(f"/api/profiles/{pid}")
        assert get_resp.status_code == 200
        assert get_resp.json()["display_name"] == "Contract Test Profile"

        patch_resp = await client.patch(f"/api/profiles/{pid}", json={
            "display_name": "Updated Contract Profile",
            "hint_text": "Updated hint",
        })
        assert patch_resp.status_code == 200
        assert patch_resp.json()["display_name"] == "Updated Contract Profile"

        delete_resp = await client.delete(f"/api/profiles/{pid}")
        assert delete_resp.status_code == 200
        assert delete_resp.json() == {"ok": True}

        verify_resp = await client.get(f"/api/profiles/{pid}")
        assert verify_resp.status_code == 404


class TestDeleteBuiltin:
    async def test_delete_builtin_returns_400(self, client):
        list_resp = await client.get("/api/profiles")
        spring = next(p for p in list_resp.json() if p["name"] == "java-spring")
        resp = await client.delete(f"/api/profiles/{spring['id']}")
        assert resp.status_code == 400
        assert "内置模板不可删除" in str(resp.json().get("detail", ""))


class TestDuplicateName:
    async def test_create_duplicate_name_returns_400(self, client):
        resp = await client.post("/api/profiles", json={
            "name": "java-spring",
            "display_name": "Dup",
            "languages": ["Java"],
            "hint_text": "dup",
        })
        assert resp.status_code == 400


class TestGitRepoPatchProfile:
    """PATCH /api/namespaces/{ns_id}/repos/{repo_id} — profile_id 更新 (Task 3.1a)."""

    @pytest_asyncio.fixture
    async def ns_repo(self, db) -> tuple[int, int]:
        from app.models.base import local_now
        ns = Namespace(name="ns_patch", slug="ns_patch", description="patch test")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)
        repo = GitRepo(namespace_id=ns.id, url="https://example.invalid/patch.git",
                       created_at=local_now())
        db.add(repo)
        await db.commit()
        await db.refresh(repo)
        return ns.id, repo.id

    async def test_patch_profile_id_to_none_ok(self, client, ns_repo):
        ns_id, repo_id = ns_repo
        resp = await client.patch(f"/api/namespaces/{ns_id}/repos/{repo_id}", json={"profile_id": None})
        assert resp.status_code == 200
        assert resp.json()["profile_id"] is None

    async def test_patch_profile_id_to_int_ok(self, client, ns_repo):
        ns_id, repo_id = ns_repo
        create_prof = await client.post("/api/profiles", json={
            "name": "test-for-repo", "display_name": "Test for Repo",
            "languages": ["Java"], "hint_text": "test",
        })
        pid = create_prof.json()["id"]
        resp = await client.patch(f"/api/namespaces/{ns_id}/repos/{repo_id}", json={"profile_id": pid})
        assert resp.status_code == 200
        assert resp.json()["profile_id"] == pid

    async def test_patch_disallowed_field_400(self, client, ns_repo):
        ns_id, repo_id = ns_repo
        resp = await client.patch(f"/api/namespaces/{ns_id}/repos/{repo_id}", json={"url": "https://evil.com"})
        assert resp.status_code == 400

    async def test_patch_nonexistent_repo_404(self, client, ns_repo):
        ns_id, _ = ns_repo
        resp = await client.patch(f"/api/namespaces/{ns_id}/repos/99999", json={"profile_id": 1})
        assert resp.status_code == 404
