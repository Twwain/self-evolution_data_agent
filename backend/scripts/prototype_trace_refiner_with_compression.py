"""端到端验证 — 用压缩后的 trace 喂给现行 _PROMPT, 看 LLM 实际产出.

NOT 落地到主链路, 仅产出到 tmp/ 供审核.

用法:
    cd backend && python -m scripts.prototype_trace_refiner_with_compression
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from langfuse import observe
from sqlalchemy import select

from app.db.metadata import async_session
from app.engine.json_parser import parse_llm_json
from app.engine.llm import chat_completion
from app.knowledge.trace_refiner import _PROMPT
from app.models.agent_trace import AgentTrace

from scripts.prototype_trace_compression import compress


@observe(name="prototype.refine_with_compression", as_type="chain")
def refine_with_compressed_trace(trace: AgentTrace) -> tuple[str, dict]:
    """用压缩 payload 喂给现行 _PROMPT, 返回 (raw_llm_text, parsed_json)."""
    result = compress(trace)
    llm_payload = {
        "user_query": result["user_query"],
        "trace_summary": result["trace_summary"],
        "known_facts": result["known_facts"],
        "inflection_points": result["inflection_points"],
    }
    user_msg = json.dumps(llm_payload, ensure_ascii=False)

    raw = chat_completion(
        [{"role": "system", "content": _PROMPT}, {"role": "user", "content": user_msg}],
        temperature=0.2, max_tokens=4096,
    )
    parsed = parse_llm_json(raw, expect="dict") or {}
    return raw, parsed


async def main(trace_id: str) -> int:
    async with async_session() as db:
        r = await db.execute(select(AgentTrace).where(AgentTrace.trace_id == trace_id))
        trace = r.scalar_one()

    print(f"[INFO] trace_id={trace_id}, user_query={trace.user_query[:80]}")
    print(f"[INFO] 调真 LLM (trace_refiner._PROMPT + 压缩 payload)...\n")
    raw, parsed = await asyncio.to_thread(refine_with_compressed_trace, trace)

    proposed = parsed.get("proposed", [])
    print(f"[OK] LLM 产出 {len(proposed)} 条 proposal\n")
    for i, p in enumerate(proposed):
        print(f"--- #{i+1} {p.get('entry_type')} ---")
        print(f"  content: {p.get('content', '')[:120]}")
        payload = p.get("payload") or {}
        print(f"  payload: {json.dumps(payload, ensure_ascii=False)[:400]}")
        evidence = p.get("evidence") or {}
        print(f"  evidence.reasoning: {(evidence.get('reasoning') or '')[:200]}")
        print()

    # 输出到 tmp
    project_root = Path(__file__).resolve().parents[2]
    tmp_dir = project_root / "tmp"
    tmp_dir.mkdir(exist_ok=True)
    out_path = tmp_dir / f"trace_refine_compressed_{trace_id[:8]}.json"
    out_path.write_text(json.dumps({
        "trace_id": trace_id,
        "user_query": trace.user_query,
        "llm_raw_output": raw,
        "parsed_proposals": proposed,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] 完整产出: {out_path.relative_to(project_root)}")
    return 0


if __name__ == "__main__":
    tid = sys.argv[1] if len(sys.argv) > 1 else "c815e1ac-208c-4b83-b404-c859d6f4f447"
    sys.exit(asyncio.run(main(tid)))
