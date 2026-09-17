"""``python -m chain``：回放样例并打印可核验的溯源报告。"""

from .replay import replay, trace_final
from .tracer import OpinionTrace, ResultTrace


def _line(char: str = "─", width: int = 72) -> str:
    return char * width


def _render_result(rt: ResultTrace, index: int) -> list[str]:
    r = rt.result
    lineage = " <- ".join(s.specimen_id for s in rt.lineage)
    lines = [
        f"  [{index}] 检验 {r.result_id}  类型 {r.kind}",
        f"      留样路径：{lineage}",
        f"      观察时间：{r.observed_at:%Y-%m-%d %H:%M}",
        f"      方法依据：{r.method_version}（检验当时"
        f"{'有效' if rt.method_valid_at_observation else '无效！'}）",
        f"      对照材料：{rt.reference.lot} {rt.reference.name}"
        f"（检验当时{'有效' if rt.reference_fresh_at_observation else '已过期！'}，"
        f"效期至 {rt.reference.expires_at:%Y-%m-%d}）",
        f"      原始摘要：{r.source_digest}",
        "      观察记录：",
    ]
    for key, value in r.findings.items():
        lines.append(f"        · {key}: {value}")
    if rt.seal_events:
        for event in rt.seal_events:
            status = "已排除" if event.cleared else "未排除"
            lines.append(
                f"      封签事件：{event.specimen_id} {event.at:%Y-%m-%d} "
                f"{event.note} [{status}]"
            )
    else:
        lines.append("      封签事件：无")
    return lines


def _render_trace(trace: OpinionTrace) -> str:
    opinion = trace.opinion
    out: list[str] = []
    out.append(_line("═"))
    out.append(f"最终意见 {opinion.opinion_id}（状态 {opinion.state}）")
    out.append(f"  鉴定师 {opinion.identifier_id}  复核人 {opinion.reviewer_id}  "
               f"签发人 {opinion.signer_id}")
    signed_label = (
        opinion.signed_at.strftime("%Y-%m-%d %H:%M") if opinion.signed_at else "-"
    )
    out.append(f"  起草 {opinion.created_at:%Y-%m-%d %H:%M}  签发 {signed_label}")
    out.append(f"  结论：{opinion.conclusion}")
    out.append("")
    out.append("证据依据（可逐项追到原始留样）：")
    for i, rt in enumerate(trace.results, 1):
        out.extend(_render_result(rt, i))
    if trace.conflicts:
        out.append("")
        out.append("冲突证据处理：")
        for c in trace.conflicts:
            status = "已处理" if c.resolved else "未处理"
            out.append(
                f"  · {c.conflict_id} {c.subject} [{status}]"
            )
            out.append(f"      冲突记录：{c.note}")
            if c.resolved:
                out.append(
                    f"      处理结论：{c.resolution}"
                    f"（{c.resolved_by} {c.resolved_at:%Y-%m-%d %H:%M}）"
                )
    if trace.supplement_chain:
        out.append("")
        out.append("补充意见链：")
        out.append(
            f"  补充原因：{opinion.supplement_reason}"
        )
        for base in trace.supplement_chain:
            revoked = f"，签名已于 {base.revoked_at:%Y-%m-%d} 撤销（{base.revoked_reason}）" \
                if base.state == "signature-revoked" else ""
            out.append(
                f"  {opinion.opinion_id} 补充 -> {base.opinion_id}"
                f"（{base.state}，{base.signed_at:%Y-%m-%d} 签发，原文未被改写{revoked}）"
            )
        for base_id, base_conflicts in trace.chain_conflicts:
            out.append(f"  被补充意见 {base_id} 的冲突证据处理：")
            for c in base_conflicts:
                status = "已处理" if c.resolved else "未处理"
                out.append(f"    · {c.conflict_id} {c.subject} [{status}]")
                if c.resolved:
                    out.append(f"        处理结论：{c.resolution}")
    if opinion.revoked_reason:
        out.append("")
        out.append(f"  签名撤销：{opinion.revoked_reason}（{opinion.revoked_by}）")
    out.append("")
    if trace.problems:
        out.append("核验发现的问题：")
        for p in trace.problems:
            out.append(f"  ! {p}")
    else:
        out.append("核验结果：封签、方法时效、对照效期、冲突处理均通过。")
    out.append(_line("═"))
    return "\n".join(out)


def main() -> int:
    service, fixture, notes = replay()
    print(f"送检批次 {fixture['submission']}  标签名称：{fixture['declaredName']}")
    print(f"哈希链事件 {len(service.journal.entries)} 条，"
          f"完整性校验：{'通过' if service.verify_journal() else '失败'}")
    print("")
    print("规则拦截记录：")
    print(f"  · 未注明留样的 DNA 报告：{notes['unlinked_dna_rejected']}")
    for blocker in notes["first_issuance_blocked"]:
        print(f"  · 首次签发阻断：{blocker}")
    print(f"  · 重复上传原始结果：{notes['duplicate_rejected']}")
    print("")
    trace = trace_final(service)
    print(_render_trace(trace))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
