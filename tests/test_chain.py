"""证据链业务规则测试（标准库 unittest，亦可被 pytest 直接收集）。"""

import unittest
from datetime import datetime, timedelta

from domain.contracts import TestKind

from chain.errors import (
    DuplicateRawResult,
    DutySeparationError,
    InvalidMethodVersion,
    IssuanceBlocked,
    MassConservationError,
    OpenConflictError,
    OpinionStateError,
    SealIntegrityError,
    UnlinkedResultError,
    UnknownReferenceLot,
)
from chain.journal import GENESIS_HASH
from chain.opinions import OpinionState
from chain.registry import Registry, Role
from chain.replay import replay, trace_final
from chain.service import EvidenceChainService

T = datetime(2026, 9, 1, 9, 0)


def build_registry() -> Registry:
    reg = Registry()
    reg.register_staff("ident", "鉴定师甲", {Role.IDENTIFIER})
    reg.register_staff("rev", "复核人乙", {Role.REVIEWER})
    reg.register_staff("sign", "签发人丙", {Role.SIGNER})
    reg.register_staff("multi", "多职责丁", {Role.IDENTIFIER, Role.REVIEWER, Role.SIGNER})
    for kind, ver in (
        (TestKind.MACROSCOPIC, "M-1"),
        (TestKind.MICROSCOPIC, "MIC-1"),
        (TestKind.TLC, "TLC-1"),
        (TestKind.DNA, "DNA-1"),
    ):
        reg.register_method(kind, ver, T - timedelta(days=30))
    reg.register_reference("REF-1", "川贝母对照药材", T + timedelta(days=100))
    return reg


def fresh_service() -> EvidenceChainService:
    return EvidenceChainService(build_registry())


def receive_and_split(service: EvidenceChainService) -> None:
    service.receive_submission("batch-1", "川贝母", "root", 100, "S-1", T)
    service.split_specimen("s-micro", "root", 8, T + timedelta(hours=1), "ident", "显微")
    service.split_specimen("s-dna", "root", 5, T + timedelta(hours=1), "ident", "DNA")


def upload(
    service: EvidenceChainService,
    result_id: str = "r-1",
    specimen: str = "s-micro",
    kind: TestKind = TestKind.MICROSCOPIC,
    version: str = "MIC-1",
    observed_at: datetime | None = None,
    digest: str = "sha256:raw-1",
    reference: str = "REF-1",
    findings: dict | None = None,
) :
    return service.upload_result(
        result_id, specimen, kind, version, reference,
        observed_at or T + timedelta(hours=3), digest,
        findings or {"v": "川贝母特征"}, actor="ident",
    )


def full_opinion(
    service: EvidenceChainService,
    opinion_id: str = "OP-1",
    result_ids=("r-1",),
    signer: str = "sign",
    at: datetime | None = None,
) :
    base = at or T + timedelta(days=1)
    service.draft_opinion(opinion_id, result_ids, "符合规定", "ident", base)
    service.review_opinion(opinion_id, "rev", base + timedelta(hours=1))
    return service.sign_opinion(opinion_id, signer, base + timedelta(hours=2))


class SpecimenAndConservationTests(unittest.TestCase):
    def setUp(self):
        self.service = fresh_service()
        self.service.receive_submission("b", "川贝母", "root", 20, "S-1", T)

    def test_split_within_mass_succeeds_and_tracks_lineage(self):
        self.service.split_specimen("a", "root", 8, T, "ident")
        self.service.split_specimen("b", "root", 5, T, "ident")
        lineage = [s.specimen_id for s in self.service.tree.lineage("b")]
        self.assertEqual(lineage, ["root", "b"])

    def test_split_exact_mass_balance_succeeds(self):
        self.service.split_specimen("a", "root", 12, T, "ident")
        self.service.split_specimen("b", "root", 8, T, "ident")
        self.assertEqual(len(self.service.tree.specimens), 3)

    def test_split_exceeding_parent_mass_rejected(self):
        self.service.split_specimen("a", "root", 15, T, "ident")
        with self.assertRaises(MassConservationError):
            self.service.split_specimen("b", "root", 6, T, "ident")

    def test_split_from_unknown_parent_rejected(self):
        with self.assertRaises(Exception):
            self.service.split_specimen("a", "ghost", 1, T, "ident")

    def test_child_split_counts_against_child_only(self):
        self.service.split_specimen("a", "root", 5, T, "ident")
        self.service.split_specimen("a1", "a", 4, T, "ident")
        with self.assertRaises(MassConservationError):
            self.service.split_specimen("a2", "a", 2, T, "ident")


class ResultRulesTests(unittest.TestCase):
    def setUp(self):
        self.service = fresh_service()
        receive_and_split(self.service)

    def test_unlinked_result_rejected_and_logged(self):
        with self.assertRaises(UnlinkedResultError):
            upload(self.service, result_id="rx", specimen="")
        actions = [e.action for e in self.service.journal.entries]
        self.assertIn("evidence-rejected", actions)
        self.assertNotIn("rx", self.service._results)

    def test_duplicate_raw_result_cannot_create_new_test(self):
        upload(self.service, result_id="r-1", digest="sha256:same")
        with self.assertRaises(DuplicateRawResult):
            upload(self.service, result_id="r-2", digest="sha256:same")
        self.assertEqual(len(self.service._results), 1)

    def test_method_must_be_valid_at_observation_time(self):
        reg = self.service.registry
        reg.register_method(TestKind.DNA, "DNA-FUTURE", T + timedelta(days=10))
        with self.assertRaises(InvalidMethodVersion):
            upload(
                self.service, result_id="rf", specimen="s-dna",
                kind=TestKind.DNA, version="DNA-FUTURE",
                observed_at=T + timedelta(days=1), digest="sha256:f",
            )
        reg.register_method(
            TestKind.TLC, "TLC-OLD",
            T - timedelta(days=60), T - timedelta(days=1),
        )
        with self.assertRaises(InvalidMethodVersion):
            upload(
                self.service, result_id="ro", specimen="s-micro",
                kind=TestKind.TLC, version="TLC-OLD",
                observed_at=T + timedelta(days=1), digest="sha256:o",
            )

    def test_unknown_reference_lot_rejected(self):
        with self.assertRaises(UnknownReferenceLot):
            upload(self.service, result_id="rr", reference="REF-GHOST",
                   digest="sha256:g")


class IssuanceGuardTests(unittest.TestCase):
    def setUp(self):
        self.service = fresh_service()
        receive_and_split(self.service)
        upload(self.service)

    def _draft_review(self):
        self.service.draft_opinion("OP-1", ("r-1",), "结论", "ident", T + timedelta(days=1))
        self.service.review_opinion("OP-1", "rev", T + timedelta(days=1, hours=1))

    def test_broken_seal_blocks_issuance(self):
        self.service.report_seal_dispute("root", T + timedelta(hours=4), "rev", "封签断裂")
        self._draft_review()
        with self.assertRaises(IssuanceBlocked) as ctx:
            self.service.sign_opinion("OP-1", "sign", T + timedelta(days=2))
        self.assertTrue(any("封签" in b for b in ctx.exception.blockers))

    def test_cleared_dispute_allows_issuance_and_leaves_trace(self):
        self.service.report_seal_dispute("root", T + timedelta(hours=4), "rev", "换标争议")
        self.service.clear_seal_dispute(
            "root", T + timedelta(days=1, hours=5), "sign", "核查监控后换新封签"
        )
        self._draft_review()
        op = self.service.sign_opinion("OP-1", "sign", T + timedelta(days=2))
        self.assertEqual(op.state, OpinionState.SIGNED)

    def test_clearing_nonexistent_dispute_rejected(self):
        with self.assertRaises(SealIntegrityError):
            self.service.clear_seal_dispute("root", T, "sign", "无中生有")

    def test_expired_reference_blocks_issuance(self):
        self.service.registry.register_reference(
            "REF-OLD", "过期对照药材", T - timedelta(days=1)
        )
        upload(
            self.service, result_id="r-old", reference="REF-OLD",
            observed_at=T + timedelta(hours=3), digest="sha256:old",
        )
        self.service.draft_opinion("OP-X", ("r-old",), "结论", "ident", T + timedelta(days=1))
        self.service.review_opinion("OP-X", "rev", T + timedelta(days=1, hours=1))
        with self.assertRaises(IssuanceBlocked) as ctx:
            self.service.sign_opinion("OP-X", "sign", T + timedelta(days=2))
        self.assertTrue(any("过期" in b for b in ctx.exception.blockers))

    def test_unresolved_conflict_blocks_review(self):
        self.service.draft_opinion("OP-1", ("r-1",), "结论", "ident", T + timedelta(days=1))
        self.service.raise_conflict(
            "OP-1", "CF-1", "显微与证书矛盾", ("r-1",), "矛盾说明",
            raised_by="ident", at=T + timedelta(days=1),
        )
        with self.assertRaises(OpenConflictError):
            self.service.review_opinion("OP-1", "rev", T + timedelta(days=1, hours=1))
        self.service.resolve_conflict(
            "OP-1", "CF-1", "以显微为准", "ident", T + timedelta(days=1, hours=2)
        )
        self.service.review_opinion("OP-1", "rev", T + timedelta(days=1, hours=3))

    def test_all_blockers_collected_at_once(self):
        # 封签争议 + 过期对照同时存在时，blockers 必须全部列出
        self.service.report_seal_dispute("root", T, "rev", "断裂")
        self.service.registry.register_reference("REF-OLD", "过期", T - timedelta(days=1))
        upload(self.service, result_id="r-old", reference="REF-OLD", digest="sha256:old")
        self.service.draft_opinion(
            "OP-1", ("r-1", "r-old"), "结论", "ident", T + timedelta(days=1)
        )
        self.service.review_opinion("OP-1", "rev", T + timedelta(days=1, hours=1))
        with self.assertRaises(IssuanceBlocked) as ctx:
            self.service.sign_opinion("OP-1", "sign", T + timedelta(days=2))
        self.assertGreaterEqual(len(ctx.exception.blockers), 2)


class DutySeparationTests(unittest.TestCase):
    def setUp(self):
        self.service = fresh_service()
        receive_and_split(self.service)
        upload(self.service)

    def test_reviewer_cannot_be_identifier(self):
        # multi 同时具备鉴定与复核职责，但同一份意见仍不能自鉴定自复核
        self.service.draft_opinion("OP-1", ("r-1",), "x", "multi", T)
        with self.assertRaises(DutySeparationError):
            self.service.review_opinion("OP-1", "multi", T + timedelta(hours=1))

    def test_signer_cannot_be_reviewer_or_identifier(self):
        self.service.draft_opinion("OP-1", ("r-1",), "x", "multi", T)
        self.service.review_opinion("OP-1", "rev", T + timedelta(hours=1))
        with self.assertRaises(DutySeparationError):
            # multi 是鉴定师，不能兼任本意见签发人
            self.service.sign_opinion("OP-1", "multi", T + timedelta(hours=2))
        self.service.draft_opinion("OP-2", ("r-1",), "x", "ident", T + timedelta(hours=3))
        self.service.review_opinion("OP-2", "multi", T + timedelta(hours=4))
        with self.assertRaises(DutySeparationError):
            # multi 是复核人，不能兼任本意见签发人
            self.service.sign_opinion("OP-2", "multi", T + timedelta(hours=5))

    def test_role_required_even_if_person_has_multiple_roles(self):
        # 多职责人员可在不同意见上承担不同职责，但同一意见仍须分离
        self.service.draft_opinion("OP-1", ("r-1",), "x", "multi", T)
        with self.assertRaises(DutySeparationError):
            self.service.review_opinion("OP-1", "multi", T + timedelta(hours=1))

    def test_signing_requires_reviewed_state(self):
        self.service.draft_opinion("OP-1", ("r-1",), "x", "ident", T)
        with self.assertRaises(OpinionStateError):
            self.service.sign_opinion("OP-1", "sign", T + timedelta(hours=1))


class FreezeSupplementRevokeTests(unittest.TestCase):
    def setUp(self):
        self.service = fresh_service()
        receive_and_split(self.service)
        upload(self.service)
        full_opinion(self.service)

    def test_signed_opinion_is_frozen(self):
        with self.assertRaises(OpinionStateError):
            self.service.review_opinion("OP-1", "rev", T + timedelta(days=5))
        with self.assertRaises(OpinionStateError):
            self.service.sign_opinion("OP-1", "sign", T + timedelta(days=5))

    def test_late_result_must_become_supplement_not_rewrite(self):
        upload(
            self.service, result_id="r-dna", specimen="s-dna",
            kind=TestKind.DNA, version="DNA-1",
            observed_at=T + timedelta(days=4), digest="sha256:late",
        )
        # 不能把迟到结果塞进已签发意见（没有修改接口，尝试复用编号也会失败）
        with self.assertRaises(Exception):
            self.service.draft_opinion(
                "OP-1", ("r-dna",), "改写", "ident", T + timedelta(days=5)
            )
        # 只能形成补充意见
        self.service.draft_opinion(
            "OP-2", ("r-dna",), "补充意见", "ident", T + timedelta(days=5),
            supplements_opinion_id="OP-1", supplement_reason="DNA 结果迟到",
        )
        self.service.review_opinion("OP-2", "rev", T + timedelta(days=5, hours=1))
        op2 = self.service.sign_opinion("OP-2", "sign", T + timedelta(days=5, hours=2))
        self.assertEqual(op2.supplements_opinion_id, "OP-1")
        # 原意见原文不动
        op1 = self.service.opinion("OP-1")
        self.assertEqual(op1.state, OpinionState.SIGNED)
        self.assertEqual(op1.result_ids, ("r-1",))

    def test_supplement_requires_frozen_base_and_reason(self):
        upload(self.service, result_id="r-dna", specimen="s-dna",
               kind=TestKind.DNA, version="DNA-1", digest="sha256:late2")
        self.service.draft_opinion("OP-D", ("r-dna",), "草稿", "ident", T + timedelta(days=5))
        with self.assertRaises(OpinionStateError):
            self.service.draft_opinion(
                "OP-X", ("r-dna",), "x", "ident", T + timedelta(days=5, hours=1),
                supplements_opinion_id="OP-D", supplement_reason="未冻结",
            )
        with self.assertRaises(OpinionStateError):
            self.service.draft_opinion(
                "OP-Y", ("r-dna",), "x", "ident", T + timedelta(days=5, hours=1),
                supplements_opinion_id="OP-1", supplement_reason="  ",
            )

    def test_revoke_requires_reason_and_keeps_record(self):
        with self.assertRaises(OpinionStateError):
            self.service.revoke_signature("OP-1", "sign", T + timedelta(days=3), "  ")
        with self.assertRaises(DutySeparationError):
            self.service.revoke_signature("OP-1", "rev", T + timedelta(days=3), "越权")
        op = self.service.revoke_signature(
            "OP-1", "sign", T + timedelta(days=3), "复检发现留样污染"
        )
        self.assertEqual(op.state, OpinionState.SIGNATURE_REVOKED)
        self.assertEqual(op.revoked_reason, "复检发现留样污染")
        self.assertIsNotNone(op.signed_at)  # 原签发事实保留


class JournalTests(unittest.TestCase):
    def test_chain_tampering_is_detected(self):
        service = fresh_service()
        receive_and_split(service)
        upload(service)
        self.assertTrue(service.verify_journal())
        first = service.journal.entries[0]
        object.__setattr__(first, "payload", {**first.payload, "seal": "FORGED"})
        self.assertFalse(service.verify_journal())

    def test_genesis_linking(self):
        service = fresh_service()
        receive_and_split(service)
        entries = service.journal.entries
        self.assertEqual(entries[0].prev_hash, GENESIS_HASH)
        self.assertEqual(entries[1].prev_hash, entries[0].entry_hash)


class ReplayFixtureTests(unittest.TestCase):
    def test_replay_dispute_scenario_end_to_end(self):
        service, fixture, notes = replay()

        # 守恒：root 240g，分出 8g 与 5g
        self.assertEqual(fixture["submission"], "herb-2026-091")
        root = service.tree.get("sealed-a")
        self.assertEqual(root.sealed_mass_grams, 240)
        self.assertEqual(
            service.tree.lineage("dna-a")[0].specimen_id, "sealed-a"
        )

        # 无留样 DNA 报告与重复上传被拒
        self.assertIn("未注明", notes["unlinked_dna_rejected"])
        self.assertIn("重复上传", notes["duplicate_rejected"])

        # 首次签发因封签争议被阻断
        self.assertTrue(
            any("封签" in b for b in notes["first_issuance_blocked"])
        )

        # OP-1 已签发且未被迟到结果改写；OP-2 是补充意见
        op1 = service.opinion("OP-1")
        op2 = service.opinion("OP-2")
        self.assertEqual(op1.state, OpinionState.SIGNED)
        self.assertEqual(op1.result_ids, ("r-macro", "r-micro"))
        self.assertEqual(op2.state, OpinionState.SIGNED)
        self.assertEqual(op2.supplements_opinion_id, "OP-1")
        self.assertEqual(op2.result_ids, ("r-dna",))
        # 冲突证据有明确处理结论
        self.assertTrue(op1.conflicts[0].resolved)
        self.assertTrue(op1.conflicts[0].resolution)

        # 哈希链完整
        self.assertTrue(service.verify_journal())

        # 溯源：从最终意见追到原始留样，无遗留问题
        trace = trace_final(service)
        lineages = {
            rt.result.result_id: [s.specimen_id for s in rt.lineage]
            for rt in trace.results
        }
        self.assertEqual(lineages["r-dna"], ["sealed-a", "dna-a"])
        for rt in trace.results:
            self.assertTrue(rt.method_valid_at_observation)
            self.assertTrue(rt.reference_fresh_at_observation)
        self.assertEqual(trace.supplement_chain[0].opinion_id, "OP-1")
        self.assertEqual(trace.problems, [])

        # 审计日志覆盖关键动作
        actions = [e.action for e in service.journal.entries]
        for expected in (
            "submission-received", "specimen-split", "evidence-rejected",
            "conflict-raised", "conflict-resolved", "seal-dispute-reported",
            "issuance-blocked", "seal-dispute-cleared", "opinion-signed",
            "signature-revoked",
        ):
            if expected == "signature-revoked":
                continue  # 样例场景未撤销签名
            self.assertIn(expected, actions, f"缺少审计动作 {expected}")

    def test_replay_signed_contract_view(self):
        service, _, _ = replay()
        view = service.signed_view("OP-2")
        self.assertEqual(view.author_id, "u-li")
        self.assertEqual(view.reviewer_id, "u-wang")
        self.assertEqual(view.signer_id, "u-zhao")
        self.assertEqual(view.supplements_opinion_id, "OP-1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
