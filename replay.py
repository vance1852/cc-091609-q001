"""回放 fixtures/specimen_chain.json：一次川贝母复检与换标争议。

fixture 记录了送检批次、分样结构与两类事件（封签争议、迟到结果）；
本脚本为各环节补上时间线，驱动 domain 中的证据链服务重建全过程，
最后从最终意见回放到原始留样，展示冲突证据如何被处理。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from domain import errors
from domain.contracts import Specimen, TestKind
from domain.ledger import EvidenceChain
from domain.registry import MethodRegistry, MethodVersion, ReferenceLot, ReferenceRegistry
from domain.trace import render_trace

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "specimen_chain.json"


def ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)


def build_registries() -> tuple[MethodRegistry, ReferenceRegistry]:
    """登记方法版本与对照批次；2020 版方法 2025 年底起被 2025 版取代。"""
    methods = MethodRegistry()
    methods.register(
        MethodVersion("macro-2020", TestKind.MACROSCOPIC, ts("2021-01-01 00:00"), ts("2025-12-31 23:59"))
    )
    methods.register(
        MethodVersion("macro-2025", TestKind.MACROSCOPIC, ts("2026-01-01 00:00"), None)
    )
    methods.register(
        MethodVersion("micro-2020", TestKind.MICROSCOPIC, ts("2021-01-01 00:00"), ts("2025-12-31 23:59"))
    )
    methods.register(
        MethodVersion("micro-2025", TestKind.MICROSCOPIC, ts("2026-01-01 00:00"), None)
    )
    methods.register(MethodVersion("tlc-2025", TestKind.TLC, ts("2026-01-01 00:00"), None))
    methods.register(MethodVersion("dna-its2-2024", TestKind.DNA, ts("2024-06-01 00:00"), None))
    references = ReferenceRegistry()
    references.register(ReferenceLot("RS-MAC-2026", ts("2026-12-31 23:59")))
    references.register(ReferenceLot("RS-MIC-2026", ts("2026-12-31 23:59")))
    references.register(ReferenceLot("RS-TLC-2026", ts("2027-03-31 23:59")))
    references.register(ReferenceLot("RS-TLC-2024", ts("2025-12-31 23:59")))  # 已过期
    references.register(ReferenceLot("RS-DNA-2026", ts("2026-11-30 23:59")))
    return methods, references


def expect_blocked(label: str, exc_type: type[errors.ChainError], action) -> None:
    """执行一个应当被拦截的签发动作并打印拦截原因。"""
    try:
        action()
    except exc_type as exc:
        print(f"  ✗ {label}被拦截: {exc}")
    else:  # pragma: no cover - 回放脚本的保护
        raise AssertionError(f"{label}未被拦截")


def main() -> EvidenceChain:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    methods, references = build_registries()
    chain = EvidenceChain(methods, references)

    submission = fixture["submission"]
    declared = fixture["declaredName"]
    print(f"送检批次 {submission}｜标示品名 {declared}")
    chain.declare_submission(submission, declared_name=declared)

    print("\n[1] 收样与分样（来源与数量守恒）")
    custodians = {"micro-a": "形态室", "dna-a": "分子室"}
    child_seals = {"micro-a": "A-101", "dna-a": "A-102"}
    split_times = {"micro-a": ts("2026-09-02 10:00"), "dna-a": ts("2026-09-02 10:30")}
    for item in fixture["specimens"]:
        if "parent" not in item:
            chain.receive_specimen(
                Specimen(item["id"], None, float(item["massGrams"]), item["seal"], ts("2026-09-01 09:00")),
                submission_id=submission,
            )
            print(f"  收样 {item['id']}：{item['massGrams']}g，封签 {item['seal']}")
    for item in fixture["specimens"]:
        if "parent" in item:
            chain.split_specimen(
                parent_id=item["parent"],
                child_id=item["id"],
                mass_grams=float(item["massGrams"]),
                seal_code=child_seals[item["id"]],
                at=split_times[item["id"]],
                custodian=custodians[item["id"]],
            )
            print(f"  分样 {item['parent']} → {item['id']}：{item['massGrams']}g，交接{custodians[item['id']]}")
    print(f"  留样 sealed-a 现存 {chain.remaining_mass('sealed-a'):.1f}g")

    print("\n[2] 性状、显微与薄层检验")
    chain.record_result(
        result_id="R-MAC-1", specimen_id="micro-a", kind=TestKind.MACROSCOPIC,
        method_version="macro-2025", reference_lot="RS-MAC-2026",
        observed_at=ts("2026-09-03 10:00"), recorded_at=ts("2026-09-03 11:00"),
        source_digest="sha256:macro-raw-001",
        findings={"shape": "类圆锥形, 外层鳞叶2瓣大小悬殊", "consistent_with_declared": "yes"},
    )
    print("  R-MAC-1 性状鉴别：与标示品名相符")
    chain.record_result(
        result_id="R-MIC-1", specimen_id="micro-a", kind=TestKind.MICROSCOPIC,
        method_version="micro-2025", reference_lot="RS-MIC-2026",
        observed_at=ts("2026-09-04 10:00"), recorded_at=ts("2026-09-04 11:00"),
        source_digest="sha256:micro-raw-001",
        findings={
            "starch": "淀粉粒脐点人字状, 与川贝母标准不符, 近似平贝母",
            "consistent_with_declared": "no",
        },
    )
    print("  R-MIC-1 显微鉴别：与供应商证书的标示品名矛盾（冲突证据）")
    chain.record_result(
        result_id="R-TLC-1", specimen_id="micro-a", kind=TestKind.TLC,
        method_version="tlc-2025", reference_lot="RS-TLC-2026",
        observed_at=ts("2026-09-05 10:00"), recorded_at=ts("2026-09-05 11:00"),
        source_digest="sha256:tlc-raw-001",
        findings={"matches_reference": "yes", "note": "与川贝母对照药材相应位置显相同斑点"},
    )
    print("  R-TLC-1 薄层色谱：与对照药材一致")

    duplicate = chain.record_result(
        result_id="R-TLC-1-REUPLOAD", specimen_id="micro-a", kind=TestKind.TLC,
        method_version="tlc-2025", reference_lot="RS-TLC-2026",
        observed_at=ts("2026-09-05 10:00"), recorded_at=ts("2026-09-05 12:00"),
        source_digest="sha256:tlc-raw-001",
        findings={"matches_reference": "yes"},
    )
    print(f"  重复上传同一薄层原始数据 → 未新增检验，仍指向 {duplicate.result.result_id}")

    chain.record_result(
        result_id="R-TLC-2", specimen_id="micro-a", kind=TestKind.TLC,
        method_version="tlc-2025", reference_lot="RS-TLC-2024",
        observed_at=ts("2026-09-06 10:00"), recorded_at=ts("2026-09-06 11:00"),
        source_digest="sha256:tlc-raw-002",
        findings={"matches_reference": "yes"},
    )
    print("  R-TLC-2 使用了已过期对照批次 RS-TLC-2024，入档但不得引用")

    print("\n[3] 签发首份意见")
    expect_blocked(
        "鉴定人与复核人为同一人",
        errors.DutySeparationError,
        lambda: chain.sign_opinion(
            opinion_id="O-BAD-DUTY", result_ids=["R-MAC-1"],
            author_id="appraiser-wang", reviewer_id="appraiser-wang", signer_id="director-liu",
            signed_at=ts("2026-09-10 14:00"),
        ),
    )
    expect_blocked(
        "引用过期对照批次的结果",
        errors.InvalidResultCitation,
        lambda: chain.sign_opinion(
            opinion_id="O-INVALID", result_ids=["R-MAC-1", "R-MIC-1", "R-TLC-2"],
            author_id="appraiser-wang", reviewer_id="reviewer-chen", signer_id="director-liu",
            signed_at=ts("2026-09-10 14:30"),
        ),
    )
    chain.sign_opinion(
        opinion_id="O1",
        result_ids=["R-MAC-1", "R-MIC-1", "R-TLC-1"],
        author_id="appraiser-wang", reviewer_id="reviewer-chen", signer_id="director-liu",
        signed_at=ts("2026-09-10 15:00"),
        conclusion="显微特征与标示品名川贝母不符，薄层色谱未见矛盾；综合判定本批样品与标示不符",
        handling={"R-MIC-1": "显微与薄层结果不一致，以显微特征为准，待分子结果复核"},
    )
    print("  ✓ O1 签发（鉴定/复核/签发三人分离）")

    print("\n[4] 处理 fixture 事件")
    # fixture 是脱敏记录，事件数组不代表时序；回放时为每类事件补上时间线后按时间重放
    event_times = {"late-result": ts("2026-09-12 10:00"), "seal-dispute": ts("2026-09-14 09:00")}
    ordered_events = sorted(fixture["events"], key=lambda e: event_times[e["kind"]])
    dna_specimen = next(s["id"] for s in fixture["specimens"] if "dna" in s["id"])
    for event in ordered_events:
        if event["kind"] == "late-result":
            assert event["test"] == TestKind.DNA, "fixture 的迟到结果应为 DNA 检验"
            chain.record_result(
                result_id="R-DNA-1", specimen_id=dna_specimen, kind=TestKind.DNA,
                method_version="dna-its2-2024", reference_lot="RS-DNA-2026",
                observed_at=ts("2026-09-11 09:00"), recorded_at=ts("2026-09-12 10:00"),
                source_digest="sha256:dna-raw-001",
                findings={
                    "closest_match": "平贝母 Fritillaria ussuriensis (ITS2 相似度 99.8%)",
                    "consistent_with_declared": "no",
                },
            )
            print("  DNA 条形码报告迟到（O1 签发后才记录，且已指明取自哪份留样）")
            expect_blocked(
                "用迟到结果改写已签发结论",
                errors.LateResultRequiresSupplement,
                lambda: chain.sign_opinion(
                    opinion_id="O-REWRITE",
                    result_ids=["R-MAC-1", "R-MIC-1", "R-DNA-1"],
                    author_id="appraiser-wang", reviewer_id="reviewer-chen",
                    signer_id="director-liu", signed_at=ts("2026-09-12 15:00"),
                ),
            )
            chain.sign_opinion(
                opinion_id="O2", result_ids=["R-DNA-1"],
                author_id="appraiser-wang", reviewer_id="reviewer-chen",
                signer_id="director-liu", signed_at=ts("2026-09-12 16:00"),
                supplements_opinion_id="O1",
                conclusion="DNA 条形码与平贝母参考序列一致，支持 O1 判定：样品非标示的川贝母",
                handling={"R-DNA-1": "分子证据与显微一致，维持原判"},
            )
            print("  ✓ 迟到结果形成补充意见 O2（补充 O1，不改写原结论）")
        elif event["kind"] == "seal-dispute":
            chain.record_seal_dispute(
                event["specimen"], at=ts("2026-09-14 09:00"),
                note="换标争议：供应商质疑留样封签 S-881 与送检实物不符，监管链完整性待查",
            )
            print(f"  封签争议登记于 {event['specimen']}（换标争议）")
            expect_blocked(
                "封签争议期间重新签发",
                errors.SealCompromised,
                lambda: chain.sign_opinion(
                    opinion_id="O3", result_ids=["R-MAC-1", "R-MIC-1", "R-TLC-1"],
                    author_id="appraiser-wang", reviewer_id="reviewer-chen",
                    signer_id="director-liu", signed_at=ts("2026-09-14 10:00"),
                ),
            )
            expect_blocked(
                "不留原因撤销签名",
                errors.RevocationReasonRequired,
                lambda: chain.revoke_opinion("O1", reason="", revoked_by="quality-li"),
            )
            chain.revoke_opinion(
                "O1", reason="封签争议待查，监管链完整性存疑，结论暂停用于争议处理",
                revoked_by="quality-li", at=ts("2026-09-15 09:00"),
            )
            chain.revoke_opinion(
                "O2", reason="所补充的 O1 已撤销，一并暂停使用",
                revoked_by="quality-li", at=ts("2026-09-15 09:05"),
            )
            print("  O1、O2 已撤销并留有原因（结论脱离取样过程即不得用于争议处理）")

    print()
    print(render_trace(chain, "O2"))
    return chain


if __name__ == "__main__":
    main()
