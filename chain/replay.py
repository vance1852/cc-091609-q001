"""回放 fixtures/specimen_chain.json：一次复检与换标争议。

场景（标签：川贝母，送检批次 herb-2026-091）：
1. 240g 原始封签样 sealed-a（封签 S-881）收样，分出 8g 显微样 micro-a、5g DNA 样 dna-a，守恒。
2. 性状与显微观察与供应商证书矛盾：显微特征不支持川贝母，登记为冲突证据。
3. 补来的 DNA 条形码报告未注明取自哪份留样 -> 拒绝进入证据链（留痕）。
4. 封签争议被报告：此时首次签发被阻断（封签断裂/争议未排除）。
5. 争议澄清系运输中换标、核查后换新封签并排除争议；意见同时写明冲突如何处理，
   复核、签发形成首份意见 OP-1。
6. DNA 仪器结果迟到：不得改写已签发的 OP-1，只能形成补充意见 OP-2。
7. 补充意见复核签发；尝试重复上传同一 DNA 原始结果 -> 拒绝，不能新增检验。
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

from domain.contracts import TestKind

from chain.errors import (
    DuplicateRawResult,
    IssuanceBlocked,
    UnlinkedResultError,
)
from chain.registry import Registry, Role
from chain.service import EvidenceChainService
from chain.tracer import OpinionTrace, trace_opinion

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "specimen_chain.json"

T0 = datetime(2026, 9, 1, 9, 0)


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def build_registry() -> Registry:
    reg = Registry()
    reg.register_staff("u-li", "李鉴定师", {Role.IDENTIFIER})
    reg.register_staff("u-wang", "王复核人", {Role.REVIEWER})
    reg.register_staff("u-zhao", "赵签发人", {Role.SIGNER})
    # 同一人可有多职责，但同一份意见上三职责必须分离
    reg.register_staff("u-chen", "陈备份", {Role.IDENTIFIER, Role.REVIEWER, Role.SIGNER})

    reg.register_method(TestKind.MACROSCOPIC, "CHP-MAC-2020", T0 - timedelta(days=400))
    reg.register_method(TestKind.MICROSCOPIC, "CHP-MIC-2020", T0 - timedelta(days=400))
    reg.register_method(TestKind.TLC, "CHP-TLC-2020", T0 - timedelta(days=400))
    reg.register_method(TestKind.DNA, "DNA-BC-2023", T0 - timedelta(days=200))
    # 旧版 DNA 方法在 2026-06-01 废止，用于验证“只能引用当时有效版本”
    reg.register_method(
        TestKind.DNA, "DNA-BC-2019",
        T0 - timedelta(days=2000), T0 - timedelta(days=92),
    )

    reg.register_reference("REF-CB-01", "川贝母对照药材", T0 + timedelta(days=300))
    return reg


def replay() -> tuple[EvidenceChainService, dict, dict]:
    """构建并回放整条争议链，返回服务、fixture 数据和关键节点记录。"""
    fixture = load_fixture()
    service = EvidenceChainService(build_registry())
    notes: dict[str, object] = {}

    root = fixture["specimens"][0]
    service.receive_submission(
        fixture["submission"], fixture["declaredName"],
        root["id"], root["massGrams"], root["seal"],
        T0, actor="intake-zhang",
    )
    micro_spec = fixture["specimens"][1]
    dna_spec = fixture["specimens"][2]
    service.split_specimen(
        micro_spec["id"], micro_spec["parent"], micro_spec["massGrams"],
        T0 + timedelta(hours=2), by="u-li", purpose="显微鉴别",
    )
    service.split_specimen(
        dna_spec["id"], dna_spec["parent"], dna_spec["massGrams"],
        T0 + timedelta(hours=2, minutes=10), by="u-li", purpose="DNA 条形码留样",
    )

    # 性状观察（原始封签样）与显微观察（micro-a）
    service.upload_result(
        "r-macro", "sealed-a", TestKind.MACROSCOPIC, "CHP-MAC-2020", "REF-CB-01",
        T0 + timedelta(hours=3), "sha256:macro-0001",
        {"shape": "类圆锥形", "color": "表面类白色",
         "remark": "外观与供应商证书描述一致"},
        actor="u-li",
    )
    service.upload_result(
        "r-micro", "micro-a", TestKind.MICROSCOPIC, "CHP-MIC-2020", "REF-CB-01",
        T0 + timedelta(hours=5), "sha256:micro-0001",
        {"starch": "未见川贝母特征性淀粉粒形态",
         "needle": "未见气孔式不定式排列",
         "verdict": "显微特征不支持标签川贝母"},
        actor="u-li",
    )

    # 补来的 DNA 报告没有注明留样 -> 拒绝
    try:
        service.upload_result(
            "r-dna-unlinked", "", TestKind.DNA, "DNA-BC-2023", "REF-CB-01",
            T0 + timedelta(days=1), "sha256:dna-unlinked",
            {"match": "Fritillaria cirrhosa 99.6%"},
            actor="u-li",
        )
    except UnlinkedResultError as exc:
        notes["unlinked_dna_rejected"] = str(exc)

    # 起草首份意见，显微与证书矛盾登记为冲突
    op1 = service.draft_opinion(
        "OP-1", ("r-macro", "r-micro"),
        "标签川贝母；显微特征与供应商证书矛盾，暂不能出具符合性结论。",
        "u-li", T0 + timedelta(days=1, hours=2),
    )
    service.raise_conflict(
        "OP-1", "CF-1",
        "性状/供应商证书 与 显微观察矛盾",
        ("r-macro", "r-micro"),
        "证书称川贝母合格，但显微未见川贝母特征性淀粉粒。",
        raised_by="u-li", at=T0 + timedelta(days=1, hours=2),
    )

    # 运输环节发现封签异常（换标争议）
    service.report_seal_dispute(
        "sealed-a", T0 + timedelta(days=1, hours=4),
        reported_by="intake-zhang",
        note="封签 S-881 在复检核对时发现断裂痕迹，供应商要求换标",
    )

    # 冲突处理完成但封签争议未排除 -> 复核可过，签发必须被阻断
    service.resolve_conflict(
        "OP-1", "CF-1",
        "以显微鉴别为准，外观性状受产地加工影响不作为唯一依据；"
        "待封签争议排除并补充 DNA 条形码后再行综合判定。",
        resolved_by="u-li", at=T0 + timedelta(days=1, hours=5),
    )
    service.review_opinion("OP-1", "u-wang", T0 + timedelta(days=1, hours=6))
    try:
        service.sign_opinion("OP-1", "u-zhao", T0 + timedelta(days=1, hours=7))
    except IssuanceBlocked as exc:
        notes["first_issuance_blocked"] = exc.blockers

    # 争议澄清：核查运输记录确认系外包换标，原签重封并排除争议
    service.clear_seal_dispute(
        "sealed-a", T0 + timedelta(days=2, hours=2),
        cleared_by="u-zhao",
        note="核查运输监控与供应商批次记录，确认外包换标、内封样完好；换发新封签 S-881-R",
    )
    # 签发首份意见（结论仍保留冲突处理过程，不覆盖原始记录）
    service.sign_opinion("OP-1", "u-zhao", T0 + timedelta(days=2, hours=3))

    # DNA 仪器结果迟到（明确取自 dna-a 留样后才被接受）
    service.upload_result(
        "r-dna", "dna-a", TestKind.DNA, "DNA-BC-2023", "REF-CB-01",
        T0 + timedelta(days=6), "sha256:dna-0001",
        {"match": "Fritillaria cirrhosa 99.6%", "marker": "ITS2",
         "note": "仪器排队，结果迟到；留样取自 dna-a（sealed-a 5g 分样）"},
        actor="u-li",
    )
    # 重复上传同一原始结果 -> 拒绝，不能新增检验
    try:
        service.upload_result(
            "r-dna-dup", "dna-a", TestKind.DNA, "DNA-BC-2023", "REF-CB-01",
            T0 + timedelta(days=6, hours=1), "sha256:dna-0001",
            {"match": "Fritillaria cirrhosa 99.6%"},
            actor="u-li",
        )
    except DuplicateRawResult as exc:
        notes["duplicate_rejected"] = str(exc)

    # 迟到结果不得改写 OP-1，只能形成补充意见
    op2 = service.draft_opinion(
        "OP-2", ("r-dna",),
        "补充意见：DNA 条形码 ITS2 与川贝母 Fritillaria cirrhosa 匹配 99.6%，"
        "结合封签争议排除记录，支持该内封留样为川贝母；本意见不替代 OP-1。",
        "u-li", T0 + timedelta(days=7),
        supplements_opinion_id="OP-1",
        supplement_reason="DNA 仪器结果在 OP-1 签发后迟到，按规定形成补充意见而非改写原意见",
    )
    service.review_opinion("OP-2", "u-wang", T0 + timedelta(days=7, hours=2))
    service.sign_opinion("OP-2", "u-zhao", T0 + timedelta(days=7, hours=3))

    notes["op1_signed_at"] = op1.signed_at
    notes["op2"] = op2
    return service, fixture, notes


def trace_final(service: EvidenceChainService) -> OpinionTrace:
    return trace_opinion(service, "OP-2")
