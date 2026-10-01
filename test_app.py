import base64
import tempfile
import unittest
from pathlib import Path

from app import BusinessError, ProvenanceStore


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ProvenanceStore(Path(self.tmp.name) / "test.db")
        self.store.seed()

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_provenance_and_return_review_flow(self):
        source = self.store.add_source("staff", "馆藏购藏档案", "archive", "ACC-1999-7")
        obj = self.store.create_object("staff", "M-1999-7", "青铜器", "礼器", "市博物馆", "1999年入藏，来源待持续核验。")
        event = self.store.add_event("staff", obj["id"], "acquisition", "1999-07-01", "", "本市", "从私人藏家购入", source["id"], "public")
        evidence = self.store.upload_evidence("staff", obj["id"], "purchase.pdf", base64.b64encode(b"purchase record").decode(), "internal", event["id"])
        self.assertEqual(len(evidence["sha256"]), 64)
        updated = self.store.update_object("staff", obj["id"], {"public_summary": "已完成首轮来源整理。"})
        self.assertEqual(updated["version"], 3)
        claim = self.store.create_claim("claimant1", obj["id"], "王氏家族", "返还藏品")
        self.store.transition_claim("reviewer1", claim["id"], "under_review", "材料齐全，进入调查。")
        self.store.transition_claim("reviewer1", claim["id"], "negotiating", "双方开始协商返还安排。")
        self.store.transition_claim("reviewer1", claim["id"], "resolved_return", "签署返还协议。")
        public_view = self.store.get_object("public", obj["id"])
        self.assertNotIn("current_holder", public_view)
        self.assertEqual(len(public_view["events"]), 1)
        self.assertEqual(public_view["claims"][0]["status"], "resolved_return")
        claimant_view = self.store.get_object("claimant1", obj["id"])
        self.assertEqual(len(claimant_view["claims"]), 1)
        self.assertGreaterEqual(len(self.store.object_history("reviewer1", obj["id"])), 6)

    def test_visibility_and_claim_stage_invariants(self):
        obj = self.store.create_object("staff", "M-2001-2", "手稿", "纸质", "资料室", "公开简介。")
        claim = self.store.create_claim("claimant1", obj["id"], "捐赠人后代", "归还手稿")
        with self.assertRaises(BusinessError) as ctx:
            self.store.transition_claim("reviewer1", claim["id"], "resolved_return", "直接结束。")
        self.assertEqual(ctx.exception.code, "invalid_transition")
        self.assertNotIn("claimant_id", self.store.get_object("public", obj["id"])["claims"][0])
        with self.assertRaises(BusinessError) as ctx:
            self.store.add_event("public", obj["id"], "note", "2020-01-01", "", "馆内", "未授权事件", None, "public")
        self.assertEqual(ctx.exception.status, 403)

    def test_acceptance_freezes_conclusion_and_event_requires_base_version(self):
        source = self.store.add_source("staff", "购藏档案", "archive", "ACC-1980-1")
        obj = self.store.create_object("staff", "M-2010-1", "陶罐", "陶器", "库房", "简介。")
        ev_full = self.store.add_event("staff", obj["id"], "acquisition", "1980-01-01", "", "甲地", "有来源有证据", source["id"], "public")
        self.store.upload_evidence("staff", obj["id"], "a.pdf", base64.b64encode(b"a").decode(), "internal", ev_full["id"])
        self.store.add_event("staff", obj["id"], "transfer", "1990-01-01", "", "乙地", "无来源无证据", None, "internal")
        claim = self.store.create_claim("claimant1", obj["id"], "后人", "返还陶罐")
        with self.assertRaises(BusinessError) as ctx:
            self.store.get_latest_conclusion("staff", claim["id"])
        self.assertEqual(ctx.exception.code, "conclusion_not_found")
        accepted = self.store.transition_claim("reviewer1", claim["id"], "under_review", "受理并冻结结论。")
        version = accepted["object_version"]
        conclusion = self.store.get_latest_conclusion("reviewer1", claim["id"])
        self.assertEqual(conclusion["status"], "active")
        self.assertEqual(conclusion["object_version"], version)
        # 冻结内容：当时藏品版本、每段事件的来源与证据、还缺的环节。
        payload = conclusion["payload"]
        self.assertEqual(payload["object_version"], version)
        self.assertEqual(payload["events"][0]["source"]["id"], source["id"])
        self.assertEqual(len(payload["events"][0]["evidence"]), 1)
        self.assertIsNone(payload["events"][1]["source"])
        self.assertEqual(sorted(g["type"] for g in payload["gaps"]), ["missing_evidence", "missing_source"])
        # 研究员补事件必须携带受理版本。
        with self.assertRaises(BusinessError) as ctx:
            self.store.add_event("staff", obj["id"], "transfer", "1995-01-01", "", "丙地", "补充一段")
        self.assertEqual(ctx.exception.code, "base_version_required")
        with self.assertRaises(BusinessError) as ctx:
            self.store.add_event("staff", obj["id"], "transfer", "1995-01-01", "", "丙地", "补充一段", None, "internal", version + 5)
        self.assertEqual(ctx.exception.code, "version_conflict")
        added = self.store.add_event("staff", obj["id"], "transfer", "1995-01-01", "", "丙地", "补充一段", None, "internal", version)
        self.assertEqual(added["object_version"], version + 1)
        # 版本一变，旧结论作废。
        self.assertEqual(self.store.get_latest_conclusion("reviewer1", claim["id"])["status"], "superseded")

    def test_gaps_block_leaving_acceptance_without_reviewer_exception(self):
        obj = self.store.create_object("staff", "M-2011-2", "画卷", "书画", "库房", "简介。")
        self.store.add_event("staff", obj["id"], "acquisition", "1970-01-01", "", "甲地", "无来源无证据")
        claim = self.store.create_claim("claimant1", obj["id"], "后人", "返还画卷")
        self.store.transition_claim("reviewer1", claim["id"], "under_review", "受理并冻结结论。")
        # 缺口没补齐也没有例外说明：主张留在受理阶段。
        with self.assertRaises(BusinessError) as ctx:
            self.store.transition_claim("reviewer1", claim["id"], "negotiating", "尝试推进协商。")
        self.assertEqual(ctx.exception.code, "unresolved_gaps")
        claims = self.store.get_object("staff", obj["id"])["claims"]
        self.assertEqual(claims[0]["status"], "under_review")
        # 审查员例外说明后放行，并记录在审查轨迹里。
        self.store.transition_claim("reviewer1", claim["id"], "negotiating", "带例外推进协商。", "档案灭失，依据馆藏记录例外放行。")
        claims = self.store.get_object("staff", obj["id"])["claims"]
        self.assertEqual(claims[0]["status"], "negotiating")
        self.assertEqual(claims[0]["reviews"][-1]["exception_note"], "档案灭失，依据馆藏记录例外放行。")

    def test_stale_conclusion_must_be_refrozen_before_leaving_acceptance(self):
        source = self.store.add_source("staff", "出土记录", "archive", "EXC-1965-3")
        obj = self.store.create_object("staff", "M-2012-3", "石碑", "石刻", "库房", "简介。")
        self.store.add_event("staff", obj["id"], "acquisition", "1960-01-01", "", "甲地", "最初记录")
        claim = self.store.create_claim("claimant1", obj["id"], "后人", "返还石碑")
        accepted = self.store.transition_claim("reviewer1", claim["id"], "under_review", "受理并冻结结论。")
        # 研究员按受理版本补了一段事件，版本变化，旧结论作废。
        self.store.add_event("staff", obj["id"], "transfer", "1965-01-01", "", "乙地", "补充流转", source["id"], "internal", accepted["object_version"])
        with self.assertRaises(BusinessError) as ctx:
            self.store.transition_claim("reviewer1", claim["id"], "negotiating", "尝试推进协商。")
        self.assertEqual(ctx.exception.code, "stale_conclusion")
        # 审查员按当前版本重新冻结，新结论反映最新缺口。
        fresh = self.store.refreeze_conclusion("reviewer1", claim["id"])
        self.assertEqual(fresh["status"], "active")
        self.assertEqual(fresh["object_version"], accepted["object_version"] + 1)
        self.assertEqual(len(fresh["payload"]["events"]), 2)
        self.assertTrue(fresh["payload"]["gaps"])
        with self.assertRaises(BusinessError) as ctx:
            self.store.transition_claim("reviewer1", claim["id"], "negotiating", "无例外推进协商。")
        self.assertEqual(ctx.exception.code, "unresolved_gaps")
        self.store.transition_claim("reviewer1", claim["id"], "negotiating", "带例外推进协商。", "证据灭失，例外处理。")

    def test_conclusion_hidden_from_public_and_claimant(self):
        obj = self.store.create_object("staff", "M-2013-4", "玉器", "玉石", "库房", "简介。")
        self.store.add_event("staff", obj["id"], "acquisition", "1950-01-01", "", "甲地", "旧记录")
        claim = self.store.create_claim("claimant1", obj["id"], "后人", "返还玉器")
        self.store.transition_claim("reviewer1", claim["id"], "under_review", "受理并冻结结论。")
        for outsider in ("claimant1", "public"):
            with self.assertRaises(BusinessError) as ctx:
                self.store.get_latest_conclusion(outsider, claim["id"])
            self.assertEqual(ctx.exception.status, 403)
        with self.assertRaises(BusinessError) as ctx:
            self.store.refreeze_conclusion("staff", claim["id"])
        self.assertEqual(ctx.exception.status, 403)

    def test_legacy_claims_backfilled_with_conclusion_on_upgrade(self):
        obj = self.store.create_object("staff", "M-2014-5", "铜镜", "金属器", "库房", "简介。")
        self.store.add_event("staff", obj["id"], "acquisition", "1949-01-01", "", "甲地", "旧记录")
        claim = self.store.create_claim("claimant1", obj["id"], "后人", "返还铜镜")
        # 模拟升级前的旧数据：主张已在审查中，但没有任何结论。
        with self.store.connect() as conn:
            conn.execute("UPDATE claims SET status='under_review' WHERE id=?", (claim["id"],))
        self.store.init_schema()
        conclusion = self.store.get_latest_conclusion("reviewer1", claim["id"])
        self.assertEqual(conclusion["status"], "active")
        self.assertEqual(conclusion["created_by"], "system")
        current_version = self.store.get_object("staff", obj["id"])["version"]
        self.assertEqual(conclusion["object_version"], current_version)
        self.assertTrue(conclusion["payload"]["gaps"])
        # 迁移幂等：重复初始化不会重复回填。
        self.store.init_schema()
        with self.store.connect() as conn:
            count = conn.execute("SELECT COUNT(*) c FROM claim_conclusions WHERE claim_id=?", (claim["id"],)).fetchone()["c"]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
