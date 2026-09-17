"""证据链服务的规则测试。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from domain import errors
from domain.contracts import Specimen
from domain.contracts import TestKind as Kind
from domain.ledger import EvidenceChain
from domain.registry import MethodRegistry, MethodVersion, ReferenceLot, ReferenceRegistry
from domain.trace import find_conflicts, render_trace


def ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)


def make_registries() -> tuple[MethodRegistry, ReferenceRegistry]:
    methods = MethodRegistry()
    methods.register(
        MethodVersion("macro-2025", Kind.MACROSCOPIC, ts("2026-01-01 00:00"), None)
    )
    methods.register(
        MethodVersion("micro-2020", Kind.MICROSCOPIC, ts("2021-01-01 00:00"), ts("2025-12-31 23:59"))
    )
    methods.register(
        MethodVersion("micro-2025", Kind.MICROSCOPIC, ts("2026-01-01 00:00"), None)
    )
    methods.register(MethodVersion("tlc-2025", Kind.TLC, ts("2026-01-01 00:00"), None))
    methods.register(MethodVersion("dna-its2-2024", Kind.DNA, ts("2024-06-01 00:00"), None))
    references = ReferenceRegistry()
    references.register(ReferenceLot("RS-MAC-2026", ts("2026-12-31 23:59")))
    references.register(ReferenceLot("RS-MIC-2026", ts("2026-12-31 23:59")))
    references.register(ReferenceLot("RS-TLC-2026", ts("2027-03-31 23:59")))
    references.register(ReferenceLot("RS-TLC-2024", ts("2025-12-31 23:59")))
    references.register(ReferenceLot("RS-DNA-2026", ts("2026-11-30 23:59")))
    return methods, references


def make_chain() -> EvidenceChain:
    methods, references = make_registries()
    chain = EvidenceChain(methods, references)
    chain.declare_submission("herb-2026-091", declared_name="川贝母")
    chain.receive_specimen(
        Specimen("sealed-a", None, 240.0, "S-881", ts("2026-09-01 09:00")),
        submission_id="herb-2026-091",
    )
    chain.split_specimen(
        parent_id="sealed-a", child_id="micro-a", mass_grams=8.0,
        seal_code="A-101", at=ts("2026-09-02 10:00"), custodian="形态室",
    )
    chain.split_specimen(
        parent_id="sealed-a", child_id="dna-a", mass_grams=5.0,
        seal_code="A-102", at=ts("2026-09-02 10:30"), custodian="分子室",
    )
    return chain


def record_micro(chain: EvidenceChain, **overrides):
    params = dict(
        result_id="R-MIC-1", specimen_id="micro-a", kind=Kind.MICROSCOPIC,
        method_version="micro-2025", reference_lot="RS-MIC-2026",
        observed_at=ts("2026-09-04 10:00"), recorded_at=ts("2026-09-04 11:00"),
        source_digest="sha256:micro-001",
        findings={"consistent_with_declared": "no", "starch": "淀粉粒脐点形态与川贝母不符"},
    )
    params.update(overrides)
    return chain.record_result(**params)


def record_tlc(chain: EvidenceChain, **overrides):
    params = dict(
        result_id="R-TLC-1", specimen_id="micro-a", kind=Kind.TLC,
        method_version="tlc-2025", reference_lot="RS-TLC-2026",
        observed_at=ts("2026-09-05 10:00"), recorded_at=ts("2026-09-05 11:00"),
        source_digest="sha256:tlc-001",
        findings={"matches_reference": "yes"},
    )
    params.update(overrides)
    return chain.record_result(**params)


def sign_o1(chain: EvidenceChain):
    return chain.sign_opinion(
        opinion_id="O1", result_ids=["R-MIC-1", "R-TLC-1"],
        author_id="appraiser-wang", reviewer_id="reviewer-chen", signer_id="director-liu",
        signed_at=ts("2026-09-10 15:00"),
        conclusion="显微特征与标示品名不符",
        handling={"R-MIC-1": "以显微特征为准，待分子结果复核"},
    )


def record_late_dna(chain: EvidenceChain, **overrides):
    params = dict(
        result_id="R-DNA-1", specimen_id="dna-a", kind=Kind.DNA,
        method_version="dna-its2-2024", reference_lot="RS-DNA-2026",
        observed_at=ts("2026-09-11 09:00"), recorded_at=ts("2026-09-12 10:00"),
        source_digest="sha256:dna-001",
        findings={"consistent_with_declared": "no", "closest_match": "平贝母"},
    )
    params.update(overrides)
    return chain.record_result(**params)


def make_signed_chain() -> EvidenceChain:
    chain = make_chain()
    record_micro(chain)
    record_tlc(chain)
    sign_o1(chain)
    return chain


# ----------------------------------------------------------------------
# 分样守恒与来源
# ----------------------------------------------------------------------

def test_split_conserves_mass():
    chain = make_chain()
    assert chain.remaining_mass("sealed-a") == pytest.approx(227.0)
    with pytest.raises(errors.MassConservationError):
        chain.split_specimen(
            parent_id="sealed-a", child_id="too-much", mass_grams=300.0,
            seal_code="X-1", at=ts("2026-09-02 11:00"),
        )
    with pytest.raises(errors.MassConservationError):
        chain.split_specimen(
            parent_id="sealed-a", child_id="zero", mass_grams=0.0,
            seal_code="X-2", at=ts("2026-09-02 11:00"),
        )
    chain.split_specimen(
        parent_id="sealed-a", child_id="rest", mass_grams=227.0,
        seal_code="A-103", at=ts("2026-09-02 11:00"),
    )
    assert chain.remaining_mass("sealed-a") == pytest.approx(0.0)


def test_split_preserves_provenance():
    chain = make_chain()
    child = chain.specimen("micro-a")
    assert child.parent_id == "sealed-a"
    assert chain.root_of("micro-a") == "sealed-a"
    assert chain.lineage_ids("micro-a") == ["micro-a", "sealed-a"]
    with pytest.raises(errors.UnknownSpecimen):
        chain.split_specimen(
            parent_id="ghost", child_id="x", mass_grams=1.0,
            seal_code="X", at=ts("2026-09-02 11:00"),
        )


def test_split_records_handover_event():
    chain = make_chain()
    splits = [e for e in chain.custody_events() if e.kind == "split"]
    assert len(splits) == 2
    assert splits[0].detail["parent"] == "sealed-a"
    assert splits[0].detail["custodian"] == "形态室"


# ----------------------------------------------------------------------
# 重复上传去重
# ----------------------------------------------------------------------

def test_duplicate_upload_does_not_add_test():
    chain = make_chain()
    first = record_micro(chain)
    assert first.created
    again = chain.record_result(
        result_id="R-MIC-1-REUPLOAD", specimen_id="micro-a", kind=Kind.MICROSCOPIC,
        method_version="micro-2025", reference_lot="RS-MIC-2026",
        observed_at=ts("2026-09-04 10:00"), recorded_at=ts("2026-09-06 09:00"),
        source_digest="sha256:micro-001",
        findings={"consistent_with_declared": "no"},
    )
    assert not again.created
    assert again.result.result_id == "R-MIC-1"
    assert chain.result_count == 1
    duplicates = [e for e in chain.custody_events() if e.kind == "result-duplicate"]
    assert len(duplicates) == 1


def test_same_digest_on_other_specimen_is_new_test():
    chain = make_chain()
    record_micro(chain)
    other = chain.record_result(
        result_id="R-MIC-2", specimen_id="dna-a", kind=Kind.MICROSCOPIC,
        method_version="micro-2025", reference_lot="RS-MIC-2026",
        observed_at=ts("2026-09-04 10:00"), source_digest="sha256:micro-001",
    )
    assert other.created
    assert chain.result_count == 2


# ----------------------------------------------------------------------
# 方法版本与对照材料时效
# ----------------------------------------------------------------------

def test_method_version_must_be_valid_at_observation():
    chain = make_chain()
    record_micro(chain, method_version="micro-2020")  # 2026 年观察时 2020 版已废止
    record = chain.record_of_result("R-MIC-1")
    assert not record.citable
    assert record.method_issue is not None
    record_tlc(chain)
    with pytest.raises(errors.InvalidResultCitation, match="micro-2020"):
        sign_o1(chain)


def test_unknown_method_version_blocks_citation():
    chain = make_chain()
    record_micro(chain, method_version="micro-1999")
    record_tlc(chain)
    with pytest.raises(errors.InvalidResultCitation, match="未知方法版本"):
        sign_o1(chain)


def test_expired_reference_blocks_signing():
    chain = make_chain()
    record_micro(chain)
    record_tlc(chain, reference_lot="RS-TLC-2024")  # 已过期
    with pytest.raises(errors.InvalidResultCitation, match="RS-TLC-2024"):
        sign_o1(chain)


def test_valid_observation_remains_citable_after_lot_expiry():
    """观察时对照有效即始终可引用，不因之后过期而失效。"""
    methods, references = make_registries()
    references.register(ReferenceLot("RS-SHORT", ts("2026-09-05 00:00")))
    chain = EvidenceChain(methods, references)
    chain.receive_specimen(Specimen("root", None, 100.0, "S-1", ts("2026-09-01 09:00")))
    chain.record_result(
        result_id="R-1", specimen_id="root", kind=Kind.TLC,
        method_version="tlc-2025", reference_lot="RS-SHORT",
        observed_at=ts("2026-09-03 10:00"), recorded_at=ts("2026-09-03 11:00"),
        source_digest="sha256:x",
    )
    opinion = chain.sign_opinion(
        opinion_id="O1", result_ids=["R-1"],
        author_id="a", reviewer_id="b", signer_id="c",
        signed_at=ts("2026-09-10 15:00"),
    )
    assert opinion.opinion_id == "O1"


# ----------------------------------------------------------------------
# 职责分离
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "author,reviewer,signer",
    [
        ("wang", "wang", "liu"),
        ("wang", "chen", "wang"),
        ("wang", "chen", "chen"),
        ("wang", "wang", "wang"),
    ],
)
def test_duty_separation_required(author, reviewer, signer):
    chain = make_chain()
    record_micro(chain)
    with pytest.raises(errors.DutySeparationError):
        chain.sign_opinion(
            opinion_id="O1", result_ids=["R-MIC-1"],
            author_id=author, reviewer_id=reviewer, signer_id=signer,
            signed_at=ts("2026-09-10 15:00"),
        )


def test_sign_happy_path():
    chain = make_chain()
    record_micro(chain)
    record_tlc(chain)
    opinion = sign_o1(chain)
    assert opinion.result_ids == ("R-MIC-1", "R-TLC-1")
    assert opinion.supplements_opinion_id is None
    assert chain.opinion_record("O1").revocation is None


# ----------------------------------------------------------------------
# 迟到结果与补充意见
# ----------------------------------------------------------------------

def test_late_result_cannot_rewrite_signed_opinion():
    chain = make_signed_chain()
    record_late_dna(chain)
    assert chain.record_of_result("R-DNA-1").late_after == ("O1",)
    with pytest.raises(errors.LateResultRequiresSupplement, match="R-DNA-1"):
        chain.sign_opinion(
            opinion_id="O-REWRITE", result_ids=["R-MIC-1", "R-TLC-1", "R-DNA-1"],
            author_id="a", reviewer_id="b", signer_id="c",
            signed_at=ts("2026-09-12 15:00"),
        )
    # 原意见保持不动
    assert chain.opinion_record("O1").opinion.result_ids == ("R-MIC-1", "R-TLC-1")


def test_late_result_forms_supplement():
    chain = make_signed_chain()
    record_late_dna(chain)
    supplement = chain.sign_opinion(
        opinion_id="O2", result_ids=["R-DNA-1"],
        author_id="a", reviewer_id="b", signer_id="c",
        signed_at=ts("2026-09-12 16:00"), supplements_opinion_id="O1",
    )
    assert supplement.supplements_opinion_id == "O1"
    assert chain.opinion_count == 2


def test_signed_opinion_id_cannot_be_reused():
    chain = make_signed_chain()
    with pytest.raises(errors.OpinionAlreadyExists):
        sign_o1(chain)


def test_supplement_target_must_exist_and_be_effective():
    chain = make_signed_chain()
    record_late_dna(chain)
    with pytest.raises(errors.UnknownOpinion):
        chain.sign_opinion(
            opinion_id="O2", result_ids=["R-DNA-1"],
            author_id="a", reviewer_id="b", signer_id="c",
            signed_at=ts("2026-09-12 16:00"), supplements_opinion_id="O-NOPE",
        )
    chain.revoke_opinion("O1", reason="封签争议待查", revoked_by="quality-li")
    with pytest.raises(errors.SupplementTargetError):
        chain.sign_opinion(
            opinion_id="O2", result_ids=["R-DNA-1"],
            author_id="a", reviewer_id="b", signer_id="c",
            signed_at=ts("2026-09-12 16:00"), supplements_opinion_id="O1",
        )


# ----------------------------------------------------------------------
# 封签争议
# ----------------------------------------------------------------------

def test_seal_dispute_blocks_signing():
    chain = make_chain()
    record_micro(chain)
    record_tlc(chain)
    chain.record_seal_dispute("sealed-a", at=ts("2026-09-08 09:00"), note="换标争议")
    with pytest.raises(errors.SealCompromised, match="sealed-a"):
        sign_o1(chain)


def test_seal_dispute_only_blocks_affected_lineage():
    chain = make_chain()
    record_micro(chain)
    record_tlc(chain)
    record_late_dna(chain, recorded_at=ts("2026-09-06 10:00"))
    chain.record_seal_dispute("dna-a", at=ts("2026-09-08 09:00"), note="封签断裂")
    # micro-a 链路未受影响，可以签发
    sign_o1(chain)
    # 但引用 dna-a 上的结果会被拦截
    with pytest.raises(errors.SealCompromised):
        chain.sign_opinion(
            opinion_id="O2", result_ids=["R-DNA-1"],
            author_id="a", reviewer_id="b", signer_id="c",
            signed_at=ts("2026-09-09 15:00"),
        )


# ----------------------------------------------------------------------
# 撤销留痕
# ----------------------------------------------------------------------

def test_revocation_requires_reason():
    chain = make_signed_chain()
    with pytest.raises(errors.RevocationReasonRequired):
        chain.revoke_opinion("O1", reason="  ", revoked_by="quality-li")


def test_revocation_is_recorded_with_reason():
    chain = make_signed_chain()
    revocation = chain.revoke_opinion(
        "O1", reason="封签争议待查，结论暂停使用",
        revoked_by="quality-li", at=ts("2026-09-15 09:00"),
    )
    record = chain.opinion_record("O1")
    assert record.revocation == revocation
    assert record.revocation.reason == "封签争议待查，结论暂停使用"
    assert record.opinion.result_ids == ("R-MIC-1", "R-TLC-1")  # 原意见保留
    with pytest.raises(errors.AlreadyRevoked):
        chain.revoke_opinion("O1", reason="再次撤销", revoked_by="quality-li")


def test_revoke_unknown_opinion():
    chain = make_chain()
    with pytest.raises(errors.UnknownOpinion):
        chain.revoke_opinion("O-NOPE", reason="x", revoked_by="quality-li")


# ----------------------------------------------------------------------
# 批次边界
# ----------------------------------------------------------------------

def test_opinion_cannot_span_submissions():
    chain = make_chain()
    record_micro(chain)
    chain.receive_specimen(
        Specimen("sealed-b", None, 100.0, "S-882", ts("2026-09-01 10:00")),
        submission_id="herb-2026-092",
    )
    chain.record_result(
        result_id="R-OTHER", specimen_id="sealed-b", kind=Kind.TLC,
        method_version="tlc-2025", reference_lot="RS-TLC-2026",
        observed_at=ts("2026-09-05 10:00"), source_digest="sha256:other",
    )
    with pytest.raises(errors.MixedSubmissionError):
        chain.sign_opinion(
            opinion_id="O1", result_ids=["R-MIC-1", "R-OTHER"],
            author_id="a", reviewer_id="b", signer_id="c",
            signed_at=ts("2026-09-10 15:00"),
        )


def test_unknown_result_citation():
    chain = make_chain()
    with pytest.raises(errors.UnknownResult):
        chain.sign_opinion(
            opinion_id="O1", result_ids=["R-GHOST"],
            author_id="a", reviewer_id="b", signer_id="c",
            signed_at=ts("2026-09-10 15:00"),
        )


# ----------------------------------------------------------------------
# 回放与冲突处理
# ----------------------------------------------------------------------

def make_story_chain() -> EvidenceChain:
    chain = make_signed_chain()
    record_late_dna(chain)
    chain.sign_opinion(
        opinion_id="O2", result_ids=["R-DNA-1"],
        author_id="appraiser-wang", reviewer_id="reviewer-chen", signer_id="director-liu",
        signed_at=ts("2026-09-12 16:00"), supplements_opinion_id="O1",
        conclusion="DNA 与平贝母参考序列一致，维持原判",
        handling={"R-DNA-1": "分子证据与显微一致"},
    )
    chain.record_seal_dispute("sealed-a", at=ts("2026-09-14 09:00"), note="换标争议")
    chain.revoke_opinion("O1", reason="封签争议待查", revoked_by="quality-li",
                         at=ts("2026-09-15 09:00"))
    chain.revoke_opinion("O2", reason="所补充的 O1 已撤销", revoked_by="quality-li",
                         at=ts("2026-09-15 09:05"))
    return chain


def test_find_conflicts_links_handling():
    chain = make_story_chain()
    conflicts = find_conflicts(chain, "sealed-a")
    by_result = {note.result_id: note for note in conflicts}
    assert set(by_result) == {"R-MIC-1", "R-DNA-1"}
    assert any("O1" in handling for handling in by_result["R-MIC-1"].handling)
    assert any("O2" in handling for handling in by_result["R-DNA-1"].handling)


def test_trace_renders_full_chain():
    chain = make_story_chain()
    report = render_trace(chain, "O2")
    for marker in [
        "herb-2026-091", "川贝母", "sealed-a", "S-881", "micro-2025",
        "形态室", "分子室", "227.0", "换标争议", "撤销", "补充 O1",
        "R-DNA-1", "consistent_with_declared", "迟到", "封签争议待查",
    ]:
        assert marker in report, marker


def test_replay_script_runs_and_preserves_chain():
    import replay

    chain = replay.main()
    assert chain.opinion_record("O2").revocation is not None
    assert chain.record_of_result("R-DNA-1").late_after == ("O1",)
    # R-MAC-1、R-MIC-1、R-TLC-1、R-TLC-2、R-DNA-1；重复上传未新增
    assert chain.result_count == 5
    assert chain.remaining_mass("sealed-a") == pytest.approx(227.0)
