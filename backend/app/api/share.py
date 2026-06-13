"""
分享 API — 创建/查看/停用 分享链接
公开查看端点无需认证, 通过 token 直接访问结果快照
"""

import json
import secrets
import string

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import accessible_namespace_ids, assert_ns_access, get_current_user
from app.db.metadata import get_db
from app.models import QueryHistory, SharedResult
from app.models.user import User
from app.schemas import QueryResponse, ShareCreate, ShareOut, ShareViewOut

router = APIRouter(prefix="/api/share", tags=["share"])


def _nanoid(size: int = 21) -> str:
    """生成 URL-safe nanoid token"""
    alphabet = string.ascii_letters + string.digits + "-_"
    return "".join(secrets.choice(alphabet) for _ in range(size))


@router.get("", response_model=list[ShareOut])
async def list_shares(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """列出分享链接 — 按 ns 作用域过滤 (share→history→ns); super_admin 全量。"""
    allowed = await accessible_namespace_ids(db, user)
    stmt = select(SharedResult).order_by(SharedResult.created_at.desc())
    if allowed is not None:
        stmt = (
            stmt.join(QueryHistory, SharedResult.query_history_id == QueryHistory.id)
            .where(QueryHistory.namespace_id.in_(allowed))
            .distinct()
        )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("", response_model=ShareOut, status_code=201)
async def create_share(
    body: ShareCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """创建分享链接 — 需对该 history 所属 ns 有访问权 (user 可分享自己 ns 的历史)。"""
    entry = await db.get(QueryHistory, body.query_history_id)
    if not entry or not entry.result_snapshot:
        raise HTTPException(404, "查询记录不存在或无结果快照")
    await assert_ns_access(db, user, entry.namespace_id)

    # 前端传 UTC ISO 字符串 (aware), 转为本地 naive 以匹配 DB 列类型
    expires_at = (
        body.expires_at.astimezone().replace(tzinfo=None)
        if body.expires_at
        else None
    )
    shared = SharedResult(
        token=_nanoid(),
        query_history_id=body.query_history_id,
        shared_by=user.id,
        expires_at=expires_at,
    )
    db.add(shared)
    await db.commit()
    await db.refresh(shared)
    return shared


@router.get("/{token}", response_model=ShareViewOut)
async def view_share(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """公开查看分享结果 — 无需认证"""
    result = await db.execute(
        select(SharedResult).where(
            SharedResult.token == token,
            SharedResult.is_active.is_(True),
        )
    )
    shared = result.scalars().first()
    if not shared:
        raise HTTPException(404, "分享链接无效或已停用")

    # 检查过期
    if shared.expires_at:
        from datetime import datetime
        if datetime.now() > shared.expires_at:
            raise HTTPException(410, "分享链接已过期")

    entry = await db.get(QueryHistory, shared.query_history_id)
    if not entry or not entry.result_snapshot:
        raise HTTPException(404, "查询结果已删除")

    # 查找分享人名称
    user = await db.get(User, shared.shared_by)
    shared_by_name = user.username if user else "unknown"

    snapshot = json.loads(entry.result_snapshot)
    # Provide defaults for fields that may be missing in old snapshots
    response_data = {
        "session_id": snapshot.get("session_id", ""),
        "history_id": shared.query_history_id,
        "needs_clarification": False,
        "clarification_message": snapshot.get(
            "clarification_message", snapshot.get("final_answer", "")
        ),
        "generated_query": snapshot.get("generated_query", ""),
        "columns": snapshot.get("columns", []),
        "rows": snapshot.get("rows", []),
        "row_count": snapshot.get("row_count", 0),
        "chart_type": snapshot.get("chart_type", "table"),
        "chart_option": snapshot.get("chart_option", {}),
        "performance_warning": snapshot.get("performance_warning", ""),
        "truncated": snapshot.get("truncated", False),
        "rendered_row_count": snapshot.get("rendered_row_count", 0),
        "total_row_count": snapshot.get("total_row_count", 0),
        "error": snapshot.get("error", ""),
        "clarification_questions": snapshot.get("clarification_questions", []),
        "pending_id": snapshot.get("pending_id", 0),
    }
    return ShareViewOut(
        shared_at=shared.created_at,
        shared_by_name=shared_by_name,
        result=QueryResponse(**response_data),
    )


@router.delete("/{token}", status_code=204)
async def deactivate_share(
    token: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """停用分享链接 — 需对该 share 所属 ns 有访问权。"""
    result = await db.execute(
        select(SharedResult).where(SharedResult.token == token)
    )
    shared = result.scalars().first()
    if not shared:
        raise HTTPException(404, "分享链接不存在")

    history = await db.get(QueryHistory, shared.query_history_id)
    if history is not None:
        await assert_ns_access(db, user, history.namespace_id)

    shared.is_active = False
    await db.commit()
