"""证据链服务的领域错误。

所有违反业务规则的操作都抛出 ``ChainError`` 的子类，拒绝动作本身，
而不是悄悄改写既有记录。
"""


class ChainError(Exception):
    """证据链业务错误基类。"""


class RegistrationError(ChainError):
    """人员、方法版本或对照材料登记不合法。"""


class UnknownSpecimenError(ChainError):
    """引用了不在监管链内的样品/留样。"""


class UnlinkedResultError(ChainError):
    """检验结果没有说明取自哪份留样，不能进入证据链。"""


class MassConservationError(ChainError):
    """分样后子样数量之和超过母样，来源数量不守恒。"""


class SealIntegrityError(ChainError):
    """封签断裂或封签争议尚未排除，样品同一性不被支持。"""


class InvalidMethodVersion(ChainError):
    """报告引用的方法版本在检验当时尚未生效或已经废止。"""


class UnknownReferenceLot(ChainError):
    """引用了未登记的对照材料批号。"""


class DutySeparationError(ChainError):
    """鉴定师、复核人、签发人必须由不同人员担任。"""


class OpinionStateError(ChainError):
    """意见状态不允许该操作（如已签发意见被要求改写）。"""


class IssuanceBlocked(ChainError):
    """签发条件不满足，blockers 列出全部阻断原因。"""

    def __init__(self, blockers: list[str]):
        super().__init__("；".join(blockers))
        self.blockers = list(blockers)


class DuplicateRawResult(ChainError):
    """同一原始结果（source_digest 相同）重复上传，不能据此新增检验。"""


class OpenConflictError(ChainError):
    """意见中仍有未处理的冲突证据。"""
