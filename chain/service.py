"""证据链应用服务。

把送检批次、封签、分样、检验结果与签发意见串成同一条只增哈希链，
并强制业务规则：

- 分样保持来源与数量守恒；
- 报告只能引用检验当时有效的方法版本；
- 重复上传同一原始结果（source_digest 相同）不能新增检验；
- 封签断裂/争议未排除、对照材料过期，阻止签发；
- 鉴定师、复核人、签发人职责分离；
- 签发后的意见冻结，迟到结果只能形成补充意见；
- 撤销签名必须留下原因。
"""

from datetime import datetime

from domain.contracts import SignedOpinion, TestResult, TestKind

from .errors import (
    ChainError,
    DuplicateRawResult,
    DutySeparationError,
    InvalidMethodVersion,
    OpenConflictError,
    OpinionStateError,
    IssuanceBlocked,
    UnknownSpecimenError,
    UnlinkedResultError,
)
from .journal import Journal
from .opinions import ConflictRecord, OpinionRecord, OpinionState
from .registry import Registry, Role
from .specimens import SpecimenTree


class EvidenceChainService:
    def __init__(self, registry: Registry | None = None) -> None:
        self.registry = registry if registry is not None else Registry()
        self.tree: SpecimenTree | None = None
        self.journal = Journal()
        self._results: dict[str, TestResult] = {}
        self._digest_index: dict[str, str] = {}  # source_digest -> result_id
        self._opinions: dict[str, OpinionRecord] = {}

    # ------------------------------------------------------------------ #
    # 送检批次、分样与封签
    # ------------------------------------------------------------------ #
    def receive_submission(
        self,
        submission: str,
        declared_name: str,
        root_id: str,
        mass_grams: float,
        seal_code: str,
        at: datetime,
        actor: str = "intake",
    ):
        if self.tree is not None:
            raise ChainError("本服务已绑定送检批次，不能重复收样")
        self.tree = SpecimenTree(submission, declared_name)
        specimen = self.tree.receive(root_id, mass_grams, seal_code, at)
        self.journal.append(
            at,
            actor,
            "submission-received",
            {
                "submission": submission,
                "declaredName": declared_name,
                "root": root_id,
                "massGrams": mass_grams,
                "seal": seal_code,
            },
        )
        return specimen

    def split_specimen(
        self,
        child_id: str,
        parent_id: str,
        mass_grams: float,
        at: datetime,
        by: str,
        purpose: str = "",
    ):
        tree = self._tree()
        specimen = tree.split(child_id, parent_id, mass_grams, at, by, purpose)
        self.journal.append(
            at,
            by,
            "specimen-split",
            {
                "child": child_id,
                "parent": parent_id,
                "massGrams": mass_grams,
                "purpose": purpose,
                "seal": specimen.seal_code,
            },
        )
        return specimen

    def report_seal_dispute(
        self, specimen_id: str, at: datetime, reported_by: str, note: str
    ):
        tree = self._tree()
        event = tree.report_seal_event(specimen_id, at, reported_by, note, broken=True)
        self.journal.append(
            at,
            reported_by,
            "seal-dispute-reported",
            {"specimen": specimen_id, "note": note, "broken": event.broken},
        )
        return event

    def clear_seal_dispute(
        self, specimen_id: str, at: datetime, cleared_by: str, note: str
    ):
        tree = self._tree()
        event = tree.clear_seal_dispute(specimen_id, at, cleared_by, note)
        self.journal.append(
            at,
            cleared_by,
            "seal-dispute-cleared",
            {
                "specimen": specimen_id,
                "note": event.note,
                "clearedAt": at.isoformat(),
            },
        )
        return event

    # ------------------------------------------------------------------ #
    # 检验结果
    # ------------------------------------------------------------------ #
    def upload_result(
        self,
        result_id: str,
        specimen_id: str,
        kind: TestKind,
        method_version: str,
        reference_lot: str,
        observed_at: datetime,
        source_digest: str,
        findings: dict[str, str],
        actor: str,
        uploaded_at: datetime | None = None,
    ) -> TestResult:
        tree = self._tree()
        at = uploaded_at or observed_at

        def reject(reason: str, exc: ChainError) -> None:
            # 拒收证据本身必须留痕
            self.journal.append(
                at,
                actor,
                "evidence-rejected",
                {
                    "resultId": result_id,
                    "specimen": specimen_id,
                    "kind": str(kind),
                    "reason": reason,
                },
            )
            raise exc

        # 结果必须说明取自哪份留样；脱离取样过程的报告不能进入证据链
        if not specimen_id:
            reject(
                "未注明取自哪份留样",
                UnlinkedResultError(
                    f"检验 {result_id} 未注明取自哪份留样，拒绝进入证据链"
                ),
            )
        specimen = tree.get(specimen_id)
        if result_id in self._results:
            raise ChainError(f"检验结果编号已存在：{result_id}")
        # 同一原始结果重复上传不能新增检验
        existing = self._digest_index.get(source_digest)
        if existing is not None:
            reject(
                f"原始结果重复（已存在于检验 {existing}）",
                DuplicateRawResult(
                    f"原始结果摘要 {source_digest[:12]}… 已作为检验 {existing} 上传，"
                    "重复上传不能新增检验"
                ),
            )
        # 方法版本必须在检验当时有效；对照批号须真实存在（效期在签发时阻断）
        try:
            self.registry.require_valid_method(kind, method_version, observed_at)
        except InvalidMethodVersion as exc:
            reject(f"方法版本在检验当时无效：{exc}", exc)
        self.registry.reference(reference_lot)

        result = TestResult(
            result_id=result_id,
            specimen_id=specimen_id,
            kind=kind,
            method_version=method_version,
            reference_lot=reference_lot,
            observed_at=observed_at,
            source_digest=source_digest,
            findings=dict(findings),
        )
        self._results[result_id] = result
        self._digest_index[source_digest] = result_id
        self.journal.append(
            at,
            actor,
            "result-uploaded",
            {
                "resultId": result_id,
                "specimen": specimen_id,
                "lineage": [s.specimen_id for s in tree.lineage(specimen.specimen_id)],
                "kind": str(kind),
                "methodVersion": method_version,
                "referenceLot": reference_lot,
                "observedAt": observed_at.isoformat(),
                "sourceDigest": source_digest,
            },
        )
        return result

    # ------------------------------------------------------------------ #
    # 意见起草、冲突与复核
    # ------------------------------------------------------------------ #
    def draft_opinion(
        self,
        opinion_id: str,
        result_ids: tuple[str, ...] | list[str],
        conclusion: str,
        identifier_id: str,
        at: datetime,
        supplements_opinion_id: str | None = None,
        supplement_reason: str | None = None,
    ) -> OpinionRecord:
        if opinion_id in self._opinions:
            raise ChainError(f"意见编号已存在：{opinion_id}")
        ids = tuple(dict.fromkeys(result_ids))
        if not ids:
            raise ChainError("意见必须至少引用一项检验结果")
        for rid in ids:
            if rid not in self._results:
                raise ChainError(f"引用了不存在的检验结果：{rid}")
        self.registry.require_role(identifier_id, Role.IDENTIFIER)

        base: OpinionRecord | None = None
        if supplements_opinion_id is not None:
            base = self._opinions.get(supplements_opinion_id)
            if base is None:
                raise ChainError(f"被补充的原意见不存在：{supplements_opinion_id}")
            if not base.frozen:
                raise OpinionStateError("只能补充已签发并冻结的意见")
            if not (supplement_reason or "").strip():
                raise OpinionStateError("补充意见必须说明补充原因（如仪器结果迟到）")

        record = OpinionRecord(
            opinion_id=opinion_id,
            submission=self._tree().submission,
            result_ids=ids,
            conclusion=conclusion,
            identifier_id=identifier_id,
            created_at=at,
            supplements_opinion_id=supplements_opinion_id,
            supplement_reason=supplement_reason,
        )
        self._opinions[opinion_id] = record
        payload = {
            "opinionId": opinion_id,
            "resultIds": list(ids),
            "identifier": identifier_id,
            "conclusion": conclusion,
        }
        if base is not None:
            payload["supplements"] = base.opinion_id
            payload["supplementReason"] = supplement_reason
        self.journal.append(at, identifier_id, "opinion-drafted", payload)
        return record

    def raise_conflict(
        self,
        opinion_id: str,
        conflict_id: str,
        subject: str,
        result_ids: tuple[str, ...] | list[str],
        note: str,
        raised_by: str,
        at: datetime,
    ) -> ConflictRecord:
        record = self._require_opinion(opinion_id)
        if record.state != OpinionState.DRAFT:
            raise OpinionStateError("只有起草中的意见可以登记冲突证据")
        if any(c.conflict_id == conflict_id for c in record.conflicts):
            raise ChainError(f"冲突编号已存在：{conflict_id}")
        ids = tuple(dict.fromkeys(result_ids))
        for rid in ids:
            if rid not in self._results:
                raise ChainError(f"冲突引用了不存在的检验结果：{rid}")
        conflict = ConflictRecord(
            conflict_id=conflict_id,
            subject=subject,
            result_ids=ids,
            note=note,
            raised_at=at,
            raised_by=raised_by,
        )
        record.conflicts.append(conflict)
        self.journal.append(
            at,
            raised_by,
            "conflict-raised",
            {
                "opinionId": opinion_id,
                "conflictId": conflict_id,
                "subject": subject,
                "resultIds": list(ids),
                "note": note,
            },
        )
        return conflict

    def resolve_conflict(
        self,
        opinion_id: str,
        conflict_id: str,
        resolution: str,
        resolved_by: str,
        at: datetime,
    ) -> ConflictRecord:
        record = self._require_opinion(opinion_id)
        if record.state != OpinionState.DRAFT:
            raise OpinionStateError("意见离开起草状态后，冲突处理须退回起草")
        if not resolution.strip():
            raise OpenConflictError("冲突处理结论不能为空")
        conflict = next(
            (c for c in record.conflicts if c.conflict_id == conflict_id), None
        )
        if conflict is None:
            raise OpenConflictError(f"冲突不存在：{conflict_id}")
        if conflict.resolved:
            raise OpenConflictError(f"冲突已处理，不能重复处理：{conflict_id}")
        conflict.resolved = True
        conflict.resolved_at = at
        conflict.resolved_by = resolved_by
        conflict.resolution = resolution
        self.journal.append(
            at,
            resolved_by,
            "conflict-resolved",
            {
                "opinionId": opinion_id,
                "conflictId": conflict_id,
                "resolution": resolution,
            },
        )
        return conflict

    def review_opinion(
        self, opinion_id: str, reviewer_id: str, at: datetime, note: str = ""
    ) -> OpinionRecord:
        record = self._require_opinion(opinion_id)
        self.registry.require_role(reviewer_id, Role.REVIEWER)
        if reviewer_id == record.identifier_id:
            raise DutySeparationError("复核人不能与鉴定师为同一人")
        if record.state != OpinionState.DRAFT:
            raise OpinionStateError(f"意见处于 {record.state}，不能复核")
        if record.unresolved_conflicts:
            raise OpenConflictError(
                "仍有 "
                + "、".join(c.conflict_id for c in record.unresolved_conflicts)
                + " 项冲突证据未处理，不能复核通过"
            )
        record.state = OpinionState.REVIEWED
        record.reviewer_id = reviewer_id
        record.reviewed_at = at
        self.journal.append(
            at,
            reviewer_id,
            "opinion-reviewed",
            {"opinionId": opinion_id, "note": note},
        )
        return record

    def return_for_revision(
        self, opinion_id: str, reviewer_id: str, at: datetime, note: str
    ) -> OpinionRecord:
        """复核人把已复核意见退回起草（复核留痕，不删除）。"""
        record = self._require_opinion(opinion_id)
        self.registry.require_role(reviewer_id, Role.REVIEWER)
        if record.state != OpinionState.REVIEWED:
            raise OpinionStateError("只有已复核意见可以退回起草")
        record.state = OpinionState.DRAFT
        self.journal.append(
            at,
            reviewer_id,
            "review-returned",
            {"opinionId": opinion_id, "note": note},
        )
        return record

    # ------------------------------------------------------------------ #
    # 签发与撤销
    # ------------------------------------------------------------------ #
    def sign_opinion(
        self, opinion_id: str, signer_id: str, at: datetime
    ) -> OpinionRecord:
        record = self._require_opinion(opinion_id)
        tree = self._tree()
        self.registry.require_role(signer_id, Role.SIGNER)
        if signer_id in (record.identifier_id, record.reviewer_id):
            raise DutySeparationError("签发人不能与鉴定师或复核人为同一人")
        if record.state == OpinionState.SIGNED:
            raise OpinionStateError("意见已签发，不能重复签发或改写")
        if record.state != OpinionState.REVIEWED:
            raise OpinionStateError(f"意见处于 {record.state}，尚未复核通过")

        blockers: list[str] = []
        seen_blockers: set[str] = set()

        def add_blocker(text: str) -> None:
            if text not in seen_blockers:
                seen_blockers.add(text)
                blockers.append(text)

        for rid in record.result_ids:
            result = self._results[rid]
            # 封签断裂/争议未排除：留样及其祖先链逐份检查
            for event in tree.open_seal_disputes(result.specimen_id):
                add_blocker(
                    f"留样 {event.specimen_id} 封签争议未排除：{event.note}"
                )
            # 对照材料在检验当时是否过期
            ref = self.registry.reference(result.reference_lot)
            if result.observed_at >= ref.expires_at:
                add_blocker(
                    f"检验 {rid} 使用的对照材料 {ref.lot} 已于 "
                    f"{ref.expires_at:%Y-%m-%d} 过期"
                )
        for conflict in record.unresolved_conflicts:
            add_blocker(f"冲突证据 {conflict.conflict_id} 未处理：{conflict.subject}")

        if blockers:
            self.journal.append(
                at,
                signer_id,
                "issuance-blocked",
                {"opinionId": opinion_id, "blockers": blockers},
            )
            raise IssuanceBlocked(blockers)

        record.state = OpinionState.SIGNED
        record.signer_id = signer_id
        record.signed_at = at
        self.journal.append(
            at,
            signer_id,
            "opinion-signed",
            {
                "opinionId": opinion_id,
                "resultIds": list(record.result_ids),
                "supplements": record.supplements_opinion_id,
            },
        )
        return record

    def revoke_signature(
        self, opinion_id: str, revoked_by: str, at: datetime, reason: str
    ) -> OpinionRecord:
        record = self._require_opinion(opinion_id)
        if not reason or not reason.strip():
            raise OpinionStateError("撤销签名必须留下原因")
        if record.state != OpinionState.SIGNED:
            raise OpinionStateError("只有已签发意见可以撤销签名")
        if revoked_by != record.signer_id:
            raise DutySeparationError("只能由原签发人撤销签名")
        record.state = OpinionState.SIGNATURE_REVOKED
        record.revoked_reason = reason.strip()
        record.revoked_at = at
        record.revoked_by = revoked_by
        self.journal.append(
            at,
            revoked_by,
            "signature-revoked",
            {"opinionId": opinion_id, "reason": record.revoked_reason},
        )
        return record

    # ------------------------------------------------------------------ #
    # 查询与契约视图
    # ------------------------------------------------------------------ #
    def result(self, result_id: str) -> TestResult:
        if result_id not in self._results:
            raise ChainError(f"检验结果不存在：{result_id}")
        return self._results[result_id]

    def opinion(self, opinion_id: str) -> OpinionRecord:
        return self._require_opinion(opinion_id)

    @property
    def opinions(self) -> tuple[OpinionRecord, ...]:
        return tuple(self._opinions.values())

    def signed_view(self, opinion_id: str) -> SignedOpinion:
        """按 domain.contracts 输出签发视图。"""
        record = self._require_opinion_id_signed(opinion_id)
        return SignedOpinion(
            opinion_id=record.opinion_id,
            result_ids=record.result_ids,
            author_id=record.identifier_id,
            reviewer_id=record.reviewer_id or "",
            signer_id=record.signer_id or "",
            signed_at=record.signed_at or record.created_at,
            supplements_opinion_id=record.supplements_opinion_id,
        )

    def verify_journal(self) -> bool:
        return self.journal.verify()

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _tree(self) -> SpecimenTree:
        if self.tree is None:
            raise UnknownSpecimenError("尚未接收送检批次")
        return self.tree

    def _require_opinion(self, opinion_id: str) -> OpinionRecord:
        record = self._opinions.get(opinion_id)
        if record is None:
            raise ChainError(f"意见不存在：{opinion_id}")
        return record

    def _require_opinion_id_signed(self, opinion_id: str) -> OpinionRecord:
        record = self._require_opinion(opinion_id)
        if record.signed_at is None:
            raise OpinionStateError(f"意见尚未签发：{opinion_id}")
        return record
