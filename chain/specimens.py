"""样品接收、分样与封签监管。

规则：
- 每份留样必须能沿 parent_id 追到送检批次的原始封签样；
- 每次分样保持数量守恒：同一母样分出的子样质量不得超过母样余量；
- 封签断裂或封签争议未排除时，该样及其全部后代不得支持签发。
"""

from dataclasses import dataclass
from datetime import datetime

from domain.contracts import Specimen

from .errors import MassConservationError, SealIntegrityError, UnknownSpecimenError


@dataclass(frozen=True)
class Split:
    child_id: str
    parent_id: str
    mass_grams: float
    at: datetime
    by: str
    purpose: str


@dataclass
class SealEvent:
    specimen_id: str
    at: datetime
    reported_by: str
    broken: bool
    note: str
    cleared: bool = False
    cleared_at: datetime | None = None
    cleared_by: str | None = None


class SpecimenTree:
    def __init__(self, submission: str, declared_name: str) -> None:
        self.submission = submission
        self.declared_name = declared_name
        self._specimens: dict[str, Specimen] = {}
        self._splits: list[Split] = []
        self._seal_events: dict[str, list[SealEvent]] = {}
        # 分样累计质量：parent_id -> 已分出克数
        self._allocated: dict[str, float] = {}

    # ---- 接收 ----------------------------------------------------------
    def receive(
        self,
        specimen_id: str,
        mass_grams: float,
        seal_code: str,
        received_at: datetime,
    ) -> Specimen:
        if specimen_id in self._specimens:
            raise UnknownSpecimenError(f"样品编号已存在：{specimen_id}")
        if mass_grams <= 0:
            raise MassConservationError("收样质量必须为正数")
        specimen = Specimen(
            specimen_id=specimen_id,
            parent_id=None,
            sealed_mass_grams=mass_grams,
            seal_code=seal_code,
            received_at=received_at,
        )
        self._specimens[specimen_id] = specimen
        self._allocated[specimen_id] = 0.0
        return specimen

    # ---- 分样 ----------------------------------------------------------
    def split(
        self,
        child_id: str,
        parent_id: str,
        mass_grams: float,
        at: datetime,
        by: str,
        purpose: str = "",
        seal_code: str = "",
    ) -> Specimen:
        if child_id in self._specimens:
            raise MassConservationError(f"子样编号已存在：{child_id}")
        parent = self._specimens.get(parent_id)
        if parent is None:
            raise UnknownSpecimenError(f"母样不在监管链内：{parent_id}")
        if mass_grams <= 0:
            raise MassConservationError("分样质量必须为正数")
        used = self._allocated.get(parent_id, 0.0)
        # 浮点比较留 1e-9 容差
        if used + mass_grams > parent.sealed_mass_grams + 1e-9:
            raise MassConservationError(
                f"母样 {parent_id} 守恒破坏：已有分样 {used:g}g + 本次 {mass_grams:g}g"
                f" > 封签质量 {parent.sealed_mass_grams:g}g"
            )
        child = Specimen(
            specimen_id=child_id,
            parent_id=parent_id,
            sealed_mass_grams=mass_grams,
            seal_code=seal_code or f"{parent.seal_code}->{child_id}",
            received_at=at,
        )
        self._specimens[child_id] = child
        self._allocated[child_id] = 0.0
        self._allocated[parent_id] = used + mass_grams
        self._splits.append(Split(child_id, parent_id, mass_grams, at, by, purpose))
        return child

    # ---- 封签 ----------------------------------------------------------
    def report_seal_event(
        self, specimen_id: str, at: datetime, reported_by: str, note: str, broken: bool = True
    ) -> SealEvent:
        if specimen_id not in self._specimens:
            raise UnknownSpecimenError(f"样品不在监管链内：{specimen_id}")
        event = SealEvent(
            specimen_id=specimen_id,
            at=at,
            reported_by=reported_by,
            broken=broken,
            note=note,
        )
        self._seal_events.setdefault(specimen_id, []).append(event)
        return event

    def clear_seal_dispute(
        self, specimen_id: str, at: datetime, cleared_by: str, note: str
    ) -> SealEvent:
        """争议经核查排除（如换标争议澄清后换新封签），须留痕。"""
        events = self._seal_events.get(specimen_id)
        if not events:
            raise SealIntegrityError(f"样品没有待排除的封签事件：{specimen_id}")
        event = events[-1]
        if event.cleared:
            raise SealIntegrityError(f"封签事件已排除，不能重复排除：{specimen_id}")
        event.cleared = True
        event.cleared_at = at
        event.cleared_by = cleared_by
        event.note = f"{event.note} | 排除：{note}"
        return event

    def open_seal_disputes(self, specimen_id: str) -> list[SealEvent]:
        """返回该样及祖先链上尚未排除的封签事件。"""
        open_events: list[SealEvent] = []
        current: str | None = specimen_id
        seen: set[str] = set()
        while current is not None and current not in seen:
            seen.add(current)
            for event in self._seal_events.get(current, []):
                if not event.cleared:
                    open_events.append(event)
            current = self._specimens[current].parent_id
        return open_events

    # ---- 查询 ----------------------------------------------------------
    def get(self, specimen_id: str) -> Specimen:
        specimen = self._specimens.get(specimen_id)
        if specimen is None:
            raise UnknownSpecimenError(f"样品不在监管链内：{specimen_id}")
        return specimen

    def lineage(self, specimen_id: str) -> list[Specimen]:
        """从原始封签样到该样的完整传递路径。"""
        chain: list[Specimen] = []
        current: str | None = specimen_id
        seen: set[str] = set()
        while current is not None:
            if current in seen:
                raise UnknownSpecimenError(f"样品谱系成环：{current}")
            seen.add(current)
            specimen = self.get(current)
            chain.append(specimen)
            current = specimen.parent_id
        chain.reverse()
        return chain

    @property
    def specimens(self) -> tuple[Specimen, ...]:
        return tuple(self._specimens.values())

    @property
    def splits(self) -> tuple[Split, ...]:
        return tuple(self._splits)

    def seal_events(self, specimen_id: str | None = None) -> tuple[SealEvent, ...]:
        if specimen_id is not None:
            return tuple(self._seal_events.get(specimen_id, ()))
        return tuple(e for events in self._seal_events.values() for e in events)
