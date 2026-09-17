"""样品监管链与鉴定意见的台账服务。

把送检批次、封签分样、检验结果与签发意见串成可核验的证据链：

- 分样保持来源与数量守恒，每次交接都留下监管链事件；
- 检验结果按原始数据摘要去重，重复上传不新增检验；
- 方法版本与对照材料以观察时有效性为准，签发时复核拦截；
- 迟到结果不得改写已签发结论，只能形成补充意见；
- 鉴定、复核、签发职责分离，撤销必须留下原因；
- 封签断裂或存在争议时阻止签发。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import count

from .contracts import SignedOpinion, Specimen, TestKind, TestResult
from .errors import (
    AlreadyRevoked,
    ChainError,
    DuplicateSpecimen,
    DutySeparationError,
    InvalidResultCitation,
    LateResultRequiresSupplement,
    MassConservationError,
    MixedSubmissionError,
    OpinionAlreadyExists,
    RevocationReasonRequired,
    SealCompromised,
    SupplementTargetError,
    UnknownOpinion,
    UnknownResult,
    UnknownSpecimen,
)
from .registry import MethodRegistry, ReferenceRegistry


@dataclass(frozen=True)
class CustodyEvent:
    """监管链事件：收样、分样交接、封签争议、结果登记、签发与撤销。"""

    event_id: str
    kind: str  # receive | split | seal-dispute | result | result-duplicate | opinion | revocation
    specimen_id: str | None
    at: datetime
    detail: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ResultRecord:
    """台账中的检验结果及其有效性、迟到标注。"""

    result: TestResult
    recorded_at: datetime
    method_issue: str | None  # None 表示方法版本在观察时有效
    reference_issue: str | None  # None 表示对照批次在观察时有效
    late_after: tuple[str, ...] = ()  # 记录时已签发、被本结果迟到的意见编号

    @property
    def citable(self) -> bool:
        """是否具备被报告引用的资格（方法与对照在观察时均有效）。"""
        return self.method_issue is None and self.reference_issue is None


@dataclass(frozen=True)
class RecordOutcome:
    """登记结果的返回值；created=False 表示重复上传，未新增检验。"""

    result: TestResult
    created: bool


@dataclass(frozen=True)
class Revocation:
    """撤销留痕：谁、何时、因何撤销。"""

    opinion_id: str
    reason: str
    revoked_by: str
    revoked_at: datetime


@dataclass
class OpinionRecord:
    """台账中的意见及其结论文字、冲突处理说明与撤销状态。"""

    opinion: SignedOpinion
    conclusion: str = ""
    handling: dict[str, str] = field(default_factory=dict)  # 结果编号 -> 处理说明
    root_id: str = ""
    revocation: Revocation | None = None


class EvidenceChain:
    """一个检验机构的监管链与意见台账。"""

    def __init__(
        self,
        methods: MethodRegistry,
        references: ReferenceRegistry,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._methods = methods
        self._references = references
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._submissions: dict[str, str | None] = {}
        self._specimens: dict[str, Specimen] = {}
        self._children: dict[str, list[str]] = {}
        self._specimen_submission: dict[str, str] = {}
        self._results: dict[str, ResultRecord] = {}
        self._digest_index: dict[tuple[str, TestKind, str], str] = {}
        self._opinions: dict[str, OpinionRecord] = {}
        self._events: list[CustodyEvent] = []
        self._seal_disputes: dict[str, list[CustodyEvent]] = {}
        self._seq = count(1)

    # ------------------------------------------------------------------
    # 收样与分样
    # ------------------------------------------------------------------

    def declare_submission(self, submission_id: str, *, declared_name: str | None = None) -> None:
        """登记送检批次及其标示品名。"""
        self._submissions.setdefault(submission_id, declared_name)

    def receive_specimen(self, specimen: Specimen, *, submission_id: str | None = None) -> None:
        """登记收样的封存标本（监管链的根）。"""
        if specimen.parent_id is not None:
            raise ChainError("收样标本不得携带父标本，分样请使用 split_specimen")
        if specimen.specimen_id in self._specimens:
            raise DuplicateSpecimen(f"标本 {specimen.specimen_id} 已登记")
        submission = submission_id or specimen.specimen_id
        if submission_id is not None:
            self._submissions.setdefault(submission_id, None)
        self._specimens[specimen.specimen_id] = specimen
        self._children.setdefault(specimen.specimen_id, [])
        self._specimen_submission[specimen.specimen_id] = submission
        self._event(
            "receive",
            specimen.specimen_id,
            specimen.received_at,
            seal=specimen.seal_code,
            mass_grams=f"{specimen.sealed_mass_grams:g}",
            submission=submission,
        )

    def split_specimen(
        self,
        *,
        parent_id: str,
        child_id: str,
        mass_grams: float,
        seal_code: str,
        at: datetime,
        custodian: str = "",
    ) -> Specimen:
        """从父标本分样，保持来源可追溯与数量守恒。"""
        self._require_specimen(parent_id)
        if child_id in self._specimens:
            raise DuplicateSpecimen(f"标本 {child_id} 已登记")
        if mass_grams <= 0:
            raise MassConservationError("分样量必须为正数")
        remaining = self.remaining_mass(parent_id)
        if mass_grams > remaining + 1e-9:
            raise MassConservationError(
                f"分样 {mass_grams:g}g 超出 {parent_id} 留存 {remaining:g}g，数量不守恒"
            )
        child = Specimen(child_id, parent_id, mass_grams, seal_code, at)
        self._specimens[child_id] = child
        self._children.setdefault(child_id, [])
        self._children[parent_id].append(child_id)
        self._specimen_submission[child_id] = self._specimen_submission[parent_id]
        self._event(
            "split",
            child_id,
            at,
            parent=parent_id,
            mass_grams=f"{mass_grams:g}",
            seal=seal_code,
            custodian=custodian,
        )
        return child

    def record_seal_dispute(self, specimen_id: str, *, at: datetime, note: str) -> CustodyEvent:
        """登记封签断裂或换标争议；争议未解除前阻止相关签发。"""
        self._require_specimen(specimen_id)
        event = self._event("seal-dispute", specimen_id, at, note=note)
        self._seal_disputes.setdefault(specimen_id, []).append(event)
        return event

    # ------------------------------------------------------------------
    # 检验结果
    # ------------------------------------------------------------------

    def record_result(
        self,
        *,
        result_id: str,
        specimen_id: str,
        kind: TestKind,
        method_version: str,
        reference_lot: str,
        observed_at: datetime,
        source_digest: str,
        findings: dict[str, str] | None = None,
        recorded_at: datetime | None = None,
    ) -> RecordOutcome:
        """登记一份检验结果。

        同一标本、同一检验类型、同一原始数据摘要的重复上传不新增检验，
        只在监管链上留下重复记录。方法版本与对照材料按观察时间核验，
        不合格的结果仍可入档（证据不丢弃），但不得被意见引用。
        """
        kind = TestKind(kind)
        key = (specimen_id, kind, source_digest)
        existing_id = self._digest_index.get(key)
        if existing_id is not None:
            self._event(
                "result-duplicate",
                specimen_id,
                recorded_at or self._clock(),
                result_id=existing_id,
                kind=str(kind),
                note="重复上传同一原始结果，未新增检验",
            )
            return RecordOutcome(self._results[existing_id].result, created=False)
        self._require_specimen(specimen_id)
        if result_id in self._results:
            raise ChainError(f"结果编号 {result_id} 已存在")
        recorded_at = recorded_at or self._clock()
        method_issue = self._methods.check(method_version, kind, observed_at)
        reference_issue = self._references.check(reference_lot, observed_at)
        root_id = self.root_of(specimen_id)
        late_after = tuple(
            rec.opinion.opinion_id
            for rec in self._opinions.values()
            if rec.root_id == root_id
            and rec.revocation is None
            and rec.opinion.signed_at < recorded_at
        )
        result = TestResult(
            result_id=result_id,
            specimen_id=specimen_id,
            kind=kind,
            method_version=method_version,
            reference_lot=reference_lot,
            observed_at=observed_at,
            source_digest=source_digest,
            findings=dict(findings or {}),
        )
        self._results[result_id] = ResultRecord(
            result, recorded_at, method_issue, reference_issue, late_after
        )
        self._digest_index[key] = result_id
        self._event("result", specimen_id, recorded_at, result_id=result_id, kind=str(kind))
        return RecordOutcome(result, created=True)

    # ------------------------------------------------------------------
    # 签发、补充与撤销
    # ------------------------------------------------------------------

    def sign_opinion(
        self,
        *,
        opinion_id: str,
        result_ids: tuple[str, ...] | list[str],
        author_id: str,
        reviewer_id: str,
        signer_id: str,
        signed_at: datetime | None = None,
        supplements_opinion_id: str | None = None,
        conclusion: str = "",
        handling: dict[str, str] | None = None,
    ) -> SignedOpinion:
        """签发鉴定意见；签发后不可改写，迟到结果只能以补充意见收录。"""
        if opinion_id in self._opinions:
            raise OpinionAlreadyExists(f"意见 {opinion_id} 已签发，结论不可改写")
        if not result_ids:
            raise ChainError("意见必须引用至少一项检验结果")
        if len({author_id, reviewer_id, signer_id}) != 3:
            raise DutySeparationError("鉴定人、复核人、签发人必须为不同人员")
        records = [self._require_result(rid) for rid in result_ids]
        roots = {self.root_of(rec.result.specimen_id) for rec in records}
        if len(roots) != 1:
            raise MixedSubmissionError("一份意见只能对应一个送检批次")
        root_id = roots.pop()
        problems = [
            f"{rec.result.result_id}: {issue}"
            for rec in records
            for issue in (rec.method_issue, rec.reference_issue)
            if issue
        ]
        if problems:
            raise InvalidResultCitation(
                "引用的检验结果不具备当时有效性；" + "；".join(problems)
            )
        for rec in records:
            for sid in self.lineage_ids(rec.result.specimen_id):
                disputes = self._seal_disputes.get(sid)
                if disputes:
                    note = disputes[-1].detail.get("note", "")
                    raise SealCompromised(f"标本 {sid} 存在封签争议（{note}），禁止签发")
        if supplements_opinion_id is None:
            late = []
            for rec in records:
                effective = [
                    oid for oid in rec.late_after if self._opinions[oid].revocation is None
                ]
                if effective:
                    late.append(f"{rec.result.result_id} 晚于 {'、'.join(effective)} 签发")
            if late:
                raise LateResultRequiresSupplement(
                    "迟到结果不得改写已签发结论，须形成补充意见；" + "；".join(late)
                )
        else:
            target = self._opinions.get(supplements_opinion_id)
            if target is None:
                raise UnknownOpinion(f"被补充的意见 {supplements_opinion_id} 不存在")
            if target.revocation is not None:
                raise SupplementTargetError(f"意见 {supplements_opinion_id} 已撤销，不得再补充")
            if target.root_id != root_id:
                raise SupplementTargetError("补充意见须针对同一送检批次")
        signed_at = signed_at or self._clock()
        opinion = SignedOpinion(
            opinion_id=opinion_id,
            result_ids=tuple(result_ids),
            author_id=author_id,
            reviewer_id=reviewer_id,
            signer_id=signer_id,
            signed_at=signed_at,
            supplements_opinion_id=supplements_opinion_id,
        )
        self._opinions[opinion_id] = OpinionRecord(
            opinion, conclusion, dict(handling or {}), root_id=root_id
        )
        self._event(
            "opinion",
            None,
            signed_at,
            opinion_id=opinion_id,
            results=",".join(result_ids),
            supplements=supplements_opinion_id or "",
        )
        return opinion

    def revoke_opinion(
        self,
        opinion_id: str,
        *,
        reason: str,
        revoked_by: str,
        at: datetime | None = None,
    ) -> Revocation:
        """撤销已签发意见；必须留下原因，原意见保留在台账中。"""
        record = self._opinions.get(opinion_id)
        if record is None:
            raise UnknownOpinion(f"意见 {opinion_id} 不存在")
        if not reason or not reason.strip():
            raise RevocationReasonRequired("撤销签名必须留下原因")
        if record.revocation is not None:
            raise AlreadyRevoked(f"意见 {opinion_id} 已撤销")
        revocation = Revocation(opinion_id, reason.strip(), revoked_by, at or self._clock())
        record.revocation = revocation
        self._event(
            "revocation",
            None,
            revocation.revoked_at,
            opinion_id=opinion_id,
            reason=revocation.reason,
            revoked_by=revoked_by,
        )
        return revocation

    # ------------------------------------------------------------------
    # 查询（供回放与测试）
    # ------------------------------------------------------------------

    def specimen(self, specimen_id: str) -> Specimen:
        return self._require_specimen(specimen_id)

    def root_of(self, specimen_id: str) -> str:
        sid = self._require_specimen(specimen_id).specimen_id
        while True:
            parent = self._specimens[sid].parent_id
            if parent is None:
                return sid
            sid = parent

    def lineage_ids(self, specimen_id: str) -> list[str]:
        """从本标本一路向上到原始留样的编号序列。"""
        ids = []
        sid: str | None = self._require_specimen(specimen_id).specimen_id
        while sid is not None:
            ids.append(sid)
            sid = self._specimens[sid].parent_id
        return ids

    def lineage(self, specimen_id: str) -> list[Specimen]:
        return [self._specimens[sid] for sid in self.lineage_ids(specimen_id)]

    def children_of(self, specimen_id: str) -> list[Specimen]:
        self._require_specimen(specimen_id)
        return [self._specimens[cid] for cid in self._children.get(specimen_id, [])]

    def family(self, root_id: str) -> list[Specimen]:
        """以原始留样为根的整株标本树（广度优先）。"""
        self._require_specimen(root_id)
        ordered = []
        queue = [root_id]
        while queue:
            sid = queue.pop(0)
            ordered.append(self._specimens[sid])
            queue.extend(self._children.get(sid, []))
        return ordered

    def remaining_mass(self, specimen_id: str) -> float:
        """标本扣除各子样后的留存量。"""
        spec = self._require_specimen(specimen_id)
        used = sum(self._specimens[cid].sealed_mass_grams for cid in self._children.get(specimen_id, []))
        return spec.sealed_mass_grams - used

    def seal_disputes(self, specimen_id: str) -> list[CustodyEvent]:
        return list(self._seal_disputes.get(specimen_id, []))

    def custody_events(self) -> list[CustodyEvent]:
        return list(self._events)

    def record_of_result(self, result_id: str) -> ResultRecord:
        return self._require_result(result_id)

    def results_of_root(self, root_id: str) -> list[ResultRecord]:
        records = [
            rec
            for rec in self._results.values()
            if self.root_of(rec.result.specimen_id) == root_id
        ]
        return sorted(records, key=lambda rec: (rec.recorded_at, rec.result.result_id))

    def opinion_record(self, opinion_id: str) -> OpinionRecord:
        record = self._opinions.get(opinion_id)
        if record is None:
            raise UnknownOpinion(f"意见 {opinion_id} 不存在")
        return record

    def opinions_of_root(self, root_id: str) -> list[OpinionRecord]:
        records = [rec for rec in self._opinions.values() if rec.root_id == root_id]
        return sorted(records, key=lambda rec: (rec.opinion.signed_at, rec.opinion.opinion_id))

    def submission_of_root(self, root_id: str) -> tuple[str, str | None]:
        submission = self._specimen_submission.get(root_id, root_id)
        return submission, self._submissions.get(submission)

    @property
    def result_count(self) -> int:
        return len(self._results)

    @property
    def opinion_count(self) -> int:
        return len(self._opinions)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _require_specimen(self, specimen_id: str) -> Specimen:
        try:
            return self._specimens[specimen_id]
        except KeyError:
            raise UnknownSpecimen(f"标本 {specimen_id} 不存在") from None

    def _require_result(self, result_id: str) -> ResultRecord:
        try:
            return self._results[result_id]
        except KeyError:
            raise UnknownResult(f"检验结果 {result_id} 不存在") from None

    def _event(
        self, event_kind: str, specimen_id: str | None, at: datetime, **detail: str
    ) -> CustodyEvent:
        event = CustodyEvent(
            event_id=f"EV-{next(self._seq):04d}",
            kind=event_kind,
            specimen_id=specimen_id,
            at=at,
            detail={key: str(value) for key, value in detail.items()},
        )
        self._events.append(event)
        return event
