"""从最终意见回放证据链：留样、每次交接、方法依据与冲突处理。"""

from __future__ import annotations

from dataclasses import dataclass

from .ledger import EvidenceChain, OpinionRecord, ResultRecord

_NEGATIVE_VALUES = {"no", "false", "inconsistent", "mismatch", "否", "不符", "不一致"}
_CONSISTENCY_MARKERS = ("consistent", "matches", "符合", "一致")


@dataclass(frozen=True)
class ConflictNote:
    """一条与标示或互证结果相矛盾的发现，以及各意见对它的处理。"""

    result_id: str
    key: str
    value: str
    detail: str
    handling: tuple[str, ...]


def _is_consistency_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered or marker in key for marker in _CONSISTENCY_MARKERS)


def find_conflicts(chain: EvidenceChain, root_id: str) -> list[ConflictNote]:
    """汇总该送检批次内所有否定一致性的发现及其处理说明。"""
    handling_by_result: dict[str, list[str]] = {}
    for rec in chain.opinions_of_root(root_id):
        for result_id, note in rec.handling.items():
            handling_by_result.setdefault(result_id, []).append(
                f"{rec.opinion.opinion_id}: {note}"
            )
    notes: list[ConflictNote] = []
    for record in chain.results_of_root(root_id):
        findings = record.result.findings
        for key, value in findings.items():
            if _is_consistency_key(key) and value.strip().lower() in _NEGATIVE_VALUES:
                detail = "; ".join(
                    f"{k}={v}" for k, v in findings.items() if not _is_consistency_key(k)
                )
                notes.append(
                    ConflictNote(
                        result_id=record.result.result_id,
                        key=key,
                        value=value,
                        detail=detail,
                        handling=tuple(handling_by_result.get(record.result.result_id, ())),
                    )
                )
    return notes


def render_trace(chain: EvidenceChain, opinion_id: str) -> str:
    """以最终意见为入口，渲染可追至原始留样的完整证据链。"""
    target = chain.opinion_record(opinion_id)
    root_id = target.root_id
    submission_id, declared_name = chain.submission_of_root(root_id)
    opinions = chain.opinions_of_root(root_id)
    cited_by: dict[str, list[str]] = {}
    for rec in opinions:
        for rid in rec.opinion.result_ids:
            cited_by.setdefault(rid, []).append(rec.opinion.opinion_id)

    lines: list[str] = []
    lines.append("=" * 68)
    lines.append("证据链回放")
    lines.append("=" * 68)
    header = f"送检批次 {submission_id}"
    if declared_name:
        header += f"｜标示品名 {declared_name}"
    lines.append(header)
    lines.append(f"回放目标: 最终意见 {opinion_id}（可追至原始留样 {root_id}）")

    lines.append("")
    lines.append("一、意见链（签发后不可改写，迟到结果以补充意见收录）")
    for rec in opinions:
        lines.extend(_render_opinion(rec, current=rec.opinion.opinion_id == opinion_id))

    lines.append("")
    lines.append("二、检验依据（方法版本与对照材料按观察时有效性核验）")
    seen: set[str] = set()
    for rec in opinions:
        for rid in rec.opinion.result_ids:
            if rid not in seen:
                seen.add(rid)
                lines.extend(_render_result(chain, chain.record_of_result(rid), cited_by))

    lines.append("")
    lines.append("三、未收录进意见的检验记录")
    uncited = [rec for rec in chain.results_of_root(root_id) if rec.result.result_id not in cited_by]
    duplicates = [
        event
        for event in chain.custody_events()
        if event.kind == "result-duplicate"
        and event.specimen_id
        and chain.root_of(event.specimen_id) == root_id
    ]
    if not uncited and not duplicates:
        lines.append("  （无）")
    for rec in uncited:
        issues = "；".join(
            issue for issue in (rec.method_issue, rec.reference_issue) if issue
        ) or "未被任何意见引用"
        lines.append(
            f"  {rec.result.result_id} [{rec.result.kind}] 标本 {rec.result.specimen_id}｜{issues}"
        )
    for event in duplicates:
        lines.append(
            f"  重复上传 {event.at:%Y-%m-%d %H:%M}｜标本 {event.specimen_id}｜"
            f"{event.detail.get('note', '')}（仍指向 {event.detail.get('result_id', '')}）"
        )

    lines.append("")
    lines.append("四、监管链与交接（含数量守恒）")
    lines.extend(_render_custody(chain, root_id))

    lines.append("")
    lines.append("五、冲突证据与处理")
    conflicts = find_conflicts(chain, root_id)
    if not conflicts:
        lines.append("  （无）")
    for note in conflicts:
        suffix = f"（{note.detail}）" if note.detail else ""
        lines.append(f"  ✗ {note.result_id} {note.key}={note.value}{suffix}")
        if note.handling:
            for handling in note.handling:
                lines.append(f"    处理: {handling}")
        else:
            lines.append("    处理: 尚无意见处理该冲突")
    return "\n".join(lines)


def _render_opinion(rec: OpinionRecord, *, current: bool) -> list[str]:
    op = rec.opinion
    status = "已撤销" if rec.revocation else "有效"
    supplement = f"｜补充 {op.supplements_opinion_id}" if op.supplements_opinion_id else ""
    mark = " ◀ 回放目标" if current else ""
    lines = [f"  [{status}] {op.opinion_id}{supplement}{mark}"]
    lines.append(
        f"    鉴定 {op.author_id}｜复核 {op.reviewer_id}｜签发 {op.signer_id}"
        f"｜{op.signed_at:%Y-%m-%d %H:%M}"
    )
    if rec.conclusion:
        lines.append(f"    结论: {rec.conclusion}")
    for result_id, note in rec.handling.items():
        lines.append(f"    处理说明: {result_id} → {note}")
    if rec.revocation:
        rev = rec.revocation
        lines.append(
            f"    撤销: {rev.revoked_at:%Y-%m-%d %H:%M} {rev.revoked_by}｜原因: {rev.reason}"
        )
    return lines


def _render_result(
    chain: EvidenceChain, record: ResultRecord, cited_by: dict[str, list[str]]
) -> list[str]:
    result = record.result
    lines = [f"  {result.result_id} [{result.kind}] 标本 {result.specimen_id}"]
    lines.append(
        f"    观察 {result.observed_at:%Y-%m-%d %H:%M}｜记录 {record.recorded_at:%Y-%m-%d %H:%M}"
        f"｜原始数据 {result.source_digest}"
    )
    method_note = record.method_issue or "观察时有效"
    reference_note = record.reference_issue or "观察时有效"
    lines.append(
        f"    方法版本 {result.method_version}（{method_note}）"
        f"｜对照批次 {result.reference_lot}（{reference_note}）"
    )
    if record.late_after:
        late_desc = "、".join(record.late_after)
        supplements = [
            oid
            for oid in cited_by.get(result.result_id, [])
            if chain.opinion_record(oid).opinion.supplements_opinion_id
        ]
        if supplements:
            fate = f"，已按补充意见 {'、'.join(supplements)} 收录"
        else:
            fate = "，未收录进任何意见"
        lines.append(f"    迟到: 记录晚于 {late_desc} 签发{fate}")
    if result.findings:
        findings = "; ".join(f"{key}={value}" for key, value in result.findings.items())
        lines.append(f"    发现: {findings}")
    lineage = chain.lineage(result.specimen_id)
    chain_desc = " ← ".join(
        f"{spec.specimen_id}({spec.sealed_mass_grams:.1f}g, 封签 {spec.seal_code})"
        for spec in lineage
    )
    lines.append(f"    标本链: {chain_desc}")
    return lines


def _render_custody(chain: EvidenceChain, root_id: str) -> list[str]:
    lines: list[str] = []
    split_events = {
        event.specimen_id: event
        for event in chain.custody_events()
        if event.kind == "split"
    }
    family = chain.family(root_id)
    for spec in family:
        depth = len(chain.lineage_ids(spec.specimen_id)) - 1
        indent = "  " + "  " * depth
        if spec.parent_id is None:
            lines.append(
                f"{indent}● {spec.specimen_id}  收样 {spec.received_at:%Y-%m-%d %H:%M}"
                f"  封签 {spec.seal_code}  {spec.sealed_mass_grams:.1f}g"
            )
        else:
            event = split_events.get(spec.specimen_id)
            when = f"{event.at:%Y-%m-%d %H:%M}" if event else "时间不详"
            custodian = ""
            if event and event.detail.get("custodian"):
                custodian = f"  交接 {event.detail['custodian']}"
            lines.append(
                f"{indent}└─ {spec.specimen_id}  分样自 {spec.parent_id}"
                f"  {spec.sealed_mass_grams:.1f}g  封签 {spec.seal_code}  {when}{custodian}"
            )
        for dispute in chain.seal_disputes(spec.specimen_id):
            lines.append(
                f"{indent}   ⚠ 封签争议 {dispute.at:%Y-%m-%d %H:%M}: "
                f"{dispute.detail.get('note', '')}"
            )
    for spec in family:
        children = chain.children_of(spec.specimen_id)
        if children:
            allocated = " + ".join(
                f"{child.specimen_id} {child.sealed_mass_grams:.1f}g" for child in children
            )
            lines.append(
                f"  数量守恒: {spec.specimen_id} {spec.sealed_mass_grams:.1f}g → {allocated}"
                f"，留存 {chain.remaining_mass(spec.specimen_id):.1f}g ✓"
            )
    return lines
