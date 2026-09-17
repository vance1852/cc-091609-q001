"""方法版本与对照材料的时效登记。

检验报告只能引用观察当时有效的方法版本与未过期的对照材料，
登记处负责回答"某版本/某批次在某时刻是否可用"。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .contracts import TestKind


@dataclass(frozen=True)
class MethodVersion:
    """某一检验方法的一个版本及其有效期。"""

    version_id: str
    kind: TestKind
    valid_from: datetime
    valid_to: datetime | None  # None 表示现行有效

    def is_valid_at(self, at: datetime) -> bool:
        if at < self.valid_from:
            return False
        return self.valid_to is None or at <= self.valid_to


@dataclass(frozen=True)
class ReferenceLot:
    """对照品/标准物质批次及其有效期。"""

    lot_id: str
    expires_at: datetime

    def is_valid_at(self, at: datetime) -> bool:
        return at <= self.expires_at


class MethodRegistry:
    """方法版本登记处。"""

    def __init__(self) -> None:
        self._versions: dict[str, MethodVersion] = {}

    def register(self, version: MethodVersion) -> None:
        self._versions[version.version_id] = version

    def get(self, version_id: str) -> MethodVersion | None:
        return self._versions.get(version_id)

    def check(self, version_id: str, kind: TestKind, at: datetime) -> str | None:
        """返回 None 表示当时有效，否则返回失效原因。"""
        version = self._versions.get(version_id)
        if version is None:
            return f"未知方法版本 {version_id}"
        if version.kind != kind:
            return f"方法版本 {version_id} 不适用于 {kind}"
        if not version.is_valid_at(at):
            return f"方法版本 {version_id} 在 {at:%Y-%m-%d %H:%M} 时已失效"
        return None


class ReferenceRegistry:
    """对照材料批次登记处。"""

    def __init__(self) -> None:
        self._lots: dict[str, ReferenceLot] = {}

    def register(self, lot: ReferenceLot) -> None:
        self._lots[lot.lot_id] = lot

    def get(self, lot_id: str) -> ReferenceLot | None:
        return self._lots.get(lot_id)

    def check(self, lot_id: str, at: datetime) -> str | None:
        """返回 None 表示当时有效，否则返回失效原因。"""
        lot = self._lots.get(lot_id)
        if lot is None:
            return f"未知对照批次 {lot_id}"
        if not lot.is_valid_at(at):
            return f"对照批次 {lot_id} 在 {at:%Y-%m-%d %H:%M} 时已过期"
        return None
