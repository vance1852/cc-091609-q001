"""证据链服务的领域异常。"""


class ChainError(Exception):
    """证据链操作的基础异常。"""


class DuplicateSpecimen(ChainError):
    """标本编号重复登记。"""


class UnknownSpecimen(ChainError):
    """标本不存在。"""


class MassConservationError(ChainError):
    """分样未保持来源与数量守恒。"""


class UnknownResult(ChainError):
    """检验结果不存在。"""


class UnknownOpinion(ChainError):
    """鉴定意见不存在。"""


class MixedSubmissionError(ChainError):
    """一份意见跨了多个送检批次。"""


class InvalidResultCitation(ChainError):
    """引用的结果在观察时不具备有效的方法版本或对照材料。"""


class LateResultRequiresSupplement(ChainError):
    """迟到结果须以补充意见收录，不得改写已签发结论。"""


class SealCompromised(ChainError):
    """封签断裂或存在争议，禁止签发。"""


class DutySeparationError(ChainError):
    """鉴定、复核、签发未做到职责分离。"""


class OpinionAlreadyExists(ChainError):
    """意见编号已签发，结论不可改写。"""


class SupplementTargetError(ChainError):
    """补充意见的对象不合法。"""


class AlreadyRevoked(ChainError):
    """意见已撤销。"""


class RevocationReasonRequired(ChainError):
    """撤销签名必须留下原因。"""
