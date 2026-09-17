"""登记表：人员职责、方法版本时效、对照材料批号效期。

登记表本身也是证据链的一部分：报告只能引用检验当时有效的方法版本，
过期对照材料不得支持签发。
"""

from dataclasses import dataclass
from datetime import datetime

from domain.contracts import TestKind

from .errors import (
    InvalidMethodVersion,
    RegistrationError,
    UnknownReferenceLot,
)


class Role:
    IDENTIFIER = "identifier"  # 鉴定师
    REVIEWER = "reviewer"      # 复核人
    SIGNER = "signer"          # 签发人


@dataclass(frozen=True)
class Staff:
    staff_id: str
    name: str
    roles: frozenset[str]


@dataclass(frozen=True)
class MethodVersion:
    kind: TestKind
    version: str
    valid_from: datetime
    valid_until: datetime | None  # None 表示持续有效


@dataclass(frozen=True)
class ReferenceLot:
    lot: str
    name: str
    expires_at: datetime


class Registry:
    """人员、方法版本与对照材料的只读边界登记。

    方法版本一经登记不得修改；新版本只影响其生效之后的检验，
    不能用来追溯改写旧报告引用的版本。
    """

    def __init__(self) -> None:
        self._staff: dict[str, Staff] = {}
        self._methods: dict[tuple[TestKind, str], MethodVersion] = {}
        self._references: dict[str, ReferenceLot] = {}

    # ---- 人员 ----------------------------------------------------------
    def register_staff(self, staff_id: str, name: str, roles: set[str]) -> Staff:
        if staff_id in self._staff:
            raise RegistrationError(f"人员已登记：{staff_id}")
        if not roles or not roles <= {Role.IDENTIFIER, Role.REVIEWER, Role.SIGNER}:
            raise RegistrationError(f"角色集合不合法：{sorted(roles)}")
        staff = Staff(staff_id, name, frozenset(roles))
        self._staff[staff_id] = staff
        return staff

    def require_role(self, staff_id: str, role: str) -> Staff:
        staff = self._staff.get(staff_id)
        if staff is None:
            raise RegistrationError(f"未登记人员：{staff_id}")
        if role not in staff.roles:
            raise RegistrationError(
                f"{staff_id} 不具备 {role} 职责，不能承担该步骤"
            )
        return staff

    # ---- 方法版本 ------------------------------------------------------
    def register_method(
        self,
        kind: TestKind,
        version: str,
        valid_from: datetime,
        valid_until: datetime | None = None,
    ) -> MethodVersion:
        key = (kind, version)
        if key in self._methods:
            raise RegistrationError(f"方法版本已登记：{kind} {version}")
        if valid_until is not None and valid_until <= valid_from:
            raise RegistrationError("方法废止时间必须晚于生效时间")
        mv = MethodVersion(kind, version, valid_from, valid_until)
        self._methods[key] = mv
        return mv

    def require_valid_method(
        self, kind: TestKind, version: str, at: datetime
    ) -> MethodVersion:
        """返回 ``at`` 时刻有效的方法版本，否则拒绝。"""
        mv = self._methods.get((kind, version))
        if mv is None:
            raise InvalidMethodVersion(f"方法版本未登记：{kind} {version}")
        if at < mv.valid_from:
            raise InvalidMethodVersion(
                f"{kind} {version} 于 {mv.valid_from:%Y-%m-%d} 才生效，"
                f"检验时间 {at:%Y-%m-%d} 不能引用"
            )
        if mv.valid_until is not None and at >= mv.valid_until:
            raise InvalidMethodVersion(
                f"{kind} {version} 已于 {mv.valid_until:%Y-%m-%d} 废止，"
                f"检验时间 {at:%Y-%m-%d} 不能引用"
            )
        return mv

    # ---- 对照材料 ------------------------------------------------------
    def register_reference(self, lot: str, name: str, expires_at: datetime) -> ReferenceLot:
        if lot in self._references:
            raise RegistrationError(f"对照材料批号已登记：{lot}")
        ref = ReferenceLot(lot, name, expires_at)
        self._references[lot] = ref
        return ref

    def reference(self, lot: str) -> ReferenceLot:
        ref = self._references.get(lot)
        if ref is None:
            raise UnknownReferenceLot(f"对照材料批号未登记：{lot}")
        return ref

    def require_reference_fresh(self, lot: str, at: datetime) -> ReferenceLot:
        ref = self.reference(lot)
        if at >= ref.expires_at:
            raise UnknownReferenceLot(
                f"对照材料 {lot} 已于 {ref.expires_at:%Y-%m-%d} 过期，"
                f"{at:%Y-%m-%d} 的检验不能使用"
            )
        return ref
