"""溯源回放：从最终意见倒查原始留样、每次交接与方法依据。

审阅者拿到任意一份意见，都能看到：
- 每项检验取自哪份留样、留样沿哪条分样路径回到原始封签样；
- 检验时引用的方法版本是否在当时有效、对照材料是否过期；
- 封签争议及其排除记录；
- 冲突证据如何登记、如何处理；
- 补充意见指向哪份已冻结意见、签名是否被撤销及撤销原因。
"""

from dataclasses import dataclass, field

from domain.contracts import Specimen, TestResult

from .opinions import ConflictRecord, OpinionRecord
from .registry import MethodVersion, ReferenceLot
from .service import EvidenceChainService
from .specimens import SealEvent, Split


@dataclass
class ResultTrace:
    result: TestResult
    lineage: list[Specimen]
    splits: list[Split]
    method: MethodVersion
    reference: ReferenceLot
    method_valid_at_observation: bool
    reference_fresh_at_observation: bool
    seal_events: list[SealEvent]
    open_disputes: list[SealEvent]


@dataclass
class OpinionTrace:
    opinion: OpinionRecord
    results: list[ResultTrace]
    conflicts: list[ConflictRecord]
    supplement_chain: list[OpinionRecord] = field(default_factory=list)
    # 被补充意见链上各意见的冲突处理记录（opinion_id, conflicts）
    chain_conflicts: list[tuple[str, list[ConflictRecord]]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def trace_result(service: EvidenceChainService, result_id: str) -> ResultTrace:
    result = service.result(result_id)
    tree = service._tree()  # 溯源在同一服务边界内进行
    lineage = tree.lineage(result.specimen_id)
    splits = [s for s in tree.splits if s.child_id == result.specimen_id]
    seal_events: list[SealEvent] = []
    for specimen in lineage:
        seal_events.extend(tree.seal_events(specimen.specimen_id))
    open_disputes = tree.open_seal_disputes(result.specimen_id)

    try:
        method = service.registry.require_valid_method(
            result.kind, result.method_version, result.observed_at
        )
        method_valid = True
    except Exception:
        method = MethodVersion(result.kind, result.method_version, result.observed_at, None)
        method_valid = False
    reference = service.registry.reference(result.reference_lot)
    fresh = result.observed_at < reference.expires_at

    return ResultTrace(
        result=result,
        lineage=lineage,
        splits=splits,
        method=method,
        reference=reference,
        method_valid_at_observation=method_valid,
        reference_fresh_at_observation=fresh,
        seal_events=seal_events,
        open_disputes=open_disputes,
    )


def trace_opinion(service: EvidenceChainService, opinion_id: str) -> OpinionTrace:
    opinion = service.opinion(opinion_id)
    result_traces = [trace_result(service, rid) for rid in opinion.result_ids]

    problems: list[str] = []
    for rt in result_traces:
        if not rt.method_valid_at_observation:
            problems.append(
                f"检验 {rt.result.result_id} 引用的方法 {rt.result.method_version} "
                "在检验当时无效"
            )
        if not rt.reference_fresh_at_observation:
            problems.append(
                f"检验 {rt.result.result_id} 的对照材料 {rt.reference.lot} 已过期"
            )
        for event in rt.open_disputes:
            problems.append(f"留样 {event.specimen_id} 封签争议未排除：{event.note}")
    for conflict in opinion.unresolved_conflicts:
        problems.append(f"冲突 {conflict.conflict_id}（{conflict.subject}）未处理")

    chain: list[OpinionRecord] = []
    current = opinion
    seen: set[str] = set()
    while current.supplements_opinion_id and current.opinion_id not in seen:
        seen.add(current.opinion_id)
        current = service.opinion(current.supplements_opinion_id)
        chain.append(current)

    chain_conflicts: list[tuple[str, list[ConflictRecord]]] = [
        (base.opinion_id, list(base.conflicts)) for base in chain if base.conflicts
    ]
    for base in chain:
        for conflict in base.unresolved_conflicts:
            problems.append(
                f"被补充意见 {base.opinion_id} 的冲突 {conflict.conflict_id}"
                f"（{conflict.subject}）未处理"
            )

    return OpinionTrace(
        opinion=opinion,
        results=result_traces,
        conflicts=list(opinion.conflicts),
        supplement_chain=chain,
        chain_conflicts=chain_conflicts,
        problems=problems,
    )
