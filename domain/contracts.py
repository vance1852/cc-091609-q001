"""样品监管链与鉴定意见的数据边界。"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class TestKind(StrEnum):
    MACROSCOPIC = "macroscopic"
    MICROSCOPIC = "microscopic"
    TLC = "thin-layer-chromatography"
    DNA = "dna-barcode"


@dataclass(frozen=True)
class Specimen:
    specimen_id: str
    parent_id: str | None
    sealed_mass_grams: float
    seal_code: str
    received_at: datetime


@dataclass(frozen=True)
class TestResult:
    result_id: str
    specimen_id: str
    kind: TestKind
    method_version: str
    reference_lot: str
    observed_at: datetime
    source_digest: str
    findings: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SignedOpinion:
    opinion_id: str
    result_ids: tuple[str, ...]
    author_id: str
    reviewer_id: str
    signer_id: str
    signed_at: datetime
    supplements_opinion_id: str | None = None
