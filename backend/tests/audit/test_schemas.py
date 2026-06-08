"""Stage 3 Task 9 — schemas 严格校验体检 (extra=forbid + min_length + pattern).

按客户端最常见 6 类破坏断言, 每类锁一个 schema:
1. AuditQueueOut: items 列表必填 (空列表合法)
2. AuditApproveBody: extra=forbid 拼错字段 422
3. AuditRejectBody: reason min_length=1 空字符串 422
4. AuditBatchBody: actions min_length=1 空列表 422
5. AuditBatchAction: action pattern 非 approve/reject 422
6. EditCanonicalBody: reason 必填 + extra=forbid 双锁
"""

import pytest
from pydantic import ValidationError

from app.schemas import (
    AuditApproveBody,
    AuditBatchAction,
    AuditBatchBody,
    AuditQueueOut,
    AuditRejectBody,
    EditCanonicalBody,
)


def test_audit_queue_out_accepts_empty_items() -> None:
    """空队列也是合法响应 — items 是 list 不是 NonEmptyList."""
    out = AuditQueueOut(items=[], total=0, page=1, size=20)
    assert out.total == 0
    assert out.items == []


def test_audit_approve_body_rejects_unknown_field() -> None:
    """extra=forbid: typo 字段名直接 422 防客户端静默被忽略."""
    with pytest.raises(ValidationError) as exc:
        AuditApproveBody(supersedes_ids=[1])  # type: ignore[call-arg]  # 拼错: 应是 supersede_ids
    assert "supersedes_ids" in str(exc.value) or "Extra inputs" in str(exc.value)


def test_audit_reject_body_rejects_empty_reason() -> None:
    """reason min_length=1: 空字符串 422 强制审核员留痕."""
    with pytest.raises(ValidationError):
        AuditRejectBody(reason="")


def test_audit_batch_body_rejects_empty_actions() -> None:
    """actions min_length=1: 空列表 422 防 0 操作 200 误导."""
    with pytest.raises(ValidationError):
        AuditBatchBody(actions=[])


def test_audit_batch_action_rejects_invalid_action() -> None:
    """action pattern: 必须 ∈ {approve, reject}, 其他值 422."""
    with pytest.raises(ValidationError):
        AuditBatchAction(entry_id=1, action="delete")


def test_edit_canonical_body_rejects_missing_reason() -> None:
    """reason 必填 — 缺字段 422, 防止编辑无审计."""
    with pytest.raises(ValidationError):
        EditCanonicalBody(content="new")  # type: ignore[call-arg]  # 缺 reason
