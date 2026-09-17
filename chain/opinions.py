"""鉴定意见、冲突证据与补充意见模型。

意见生命周期：DRAFT（鉴定师起草）→ REVIEWED（复核通过）→ SIGNED（签发）。
签发后冻结：迟到结果只能形成指向旧意见的补充意见，绝不改写旧意见；
签名可撤销但必须留下原因，记录本身保留。
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

class OpinionState(StrEnum):
    DRAFT = "draft"
    REVIEWED = "reviewed"
    SIGNED = "signed"
    SIGNATURE_REVOKED = "signature-revoked"


@dataclass
class ConflictRecord:
    conflict_id: str
    subject: str
    result_ids: tuple[str, ...]
    note: str
    raised_at: datetime
    raised_by: str
    resolved: bool = False
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    resolution: str = ""


@dataclass
class OpinionRecord:
    opinion_id: str
    submission: str
    result_ids: tuple[str, ...]
    conclusion: str
    identifier_id: str
    created_at: datetime
    reviewer_id: str | None = None
    reviewed_at: datetime | None = None
    signer_id: str | None = None
    signed_at: datetime | None = None
    state: OpinionState = OpinionState.DRAFT
    conflicts: list[ConflictRecord] = field(default_factory=list)
    supplements_opinion_id: str | None = None
    supplement_reason: str | None = None
    amended_conclusion: str | None = None
    revoked_reason: str = ""
    revoked_at: datetime | None = None
    revoked_by: str | None = None

    @property
    def frozen(self) -> bool:
        return self.signed_at is not None

    @property
    def unresolved_conflicts(self) -> list[ConflictRecord]:
        return [c for c in self.conflicts if not c.resolved]
