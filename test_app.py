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

    def _setup_accepted_claim(self):
        src = self.store.add_source("staff", "馆藏档案", "archive", "ACC-1999-7")
        obj = self.store.create_object("staff", "M-1999-7", "青铜器", "礼器", "市博物馆", "1999年入藏。")
        self.store.add_event("staff", obj["id"], "acquisition", "1999-07-01", "", "本市", "从私人藏家购入", src["id"], "public")
        claim = self.store.create_claim("claimant1", obj["id"], "王氏家族", "返还藏品")
        self.store.transition_claim("reviewer1", claim["id"], "under_review", "材料齐全，进入调查。")
        return obj, claim

    def test_acceptance_freezes_conclusion_with_version_sources_and_gaps(self):
        obj, claim = self._setup_accepted_claim()
        concl = self.store.get_conclusion("reviewer1", claim["id"])
        self.assertEqual(concl["status"], "active")
        self.assertEqual(concl["object_version"], self.store.get_object("staff", obj["id"])["version"])
        block = concl["snapshot"]["events"][0]
        self.assertIsNotNone(block["source"])
        self.assertEqual(block["source"]["id"], block["event"]["source_id"])
        # 取得事件有来源但无证据 -> 缺口
        self.assertTrue(any(g["type"] == "missing_evidence" for g in concl["gaps"]))

    def test_adding_event_requires_accepted_version_and_invalidates_conclusion(self):
        obj, claim = self._setup_accepted_claim()
        accepted = self.store.get_conclusion("reviewer1", claim["id"])["object_version"]
        # 不带受理版本
        with self.assertRaises(BusinessError) as ctx:
            self.store.add_event("staff", obj["id"], "note", "2000-01-01", "", "馆内", "补记", None, "internal")
        self.assertEqual(ctx.exception.code, "accepted_version_required")
        # 带错受理版本
        with self.assertRaises(BusinessError) as ctx:
            self.store.add_event("staff", obj["id"], "note", "2000-01-01", "", "馆内", "补记", None, "internal", accepted - 1)
        self.assertEqual(ctx.exception.code, "conclusion_stale")
        # 带正确受理版本 -> 成功，旧结论作废
        res = self.store.add_event("staff", obj["id"], "note", "2000-01-01", "", "馆内", "补记", None, "internal", accepted)
        self.assertEqual(res["object_version"], accepted + 1)
        self.assertEqual(self.store.get_conclusion("reviewer1", claim["id"])["status"], "stale")

    def test_stale_conclusion_blocks_transition_until_refreeze(self):
        obj, claim = self._setup_accepted_claim()
        accepted = self.store.get_conclusion("reviewer1", claim["id"])["object_version"]
        self.store.add_event("staff", obj["id"], "note", "2000-01-01", "", "馆内", "补记", None, "internal", accepted)
        # 结论已作废，推进被拒
        with self.assertRaises(BusinessError) as ctx:
            self.store.transition_claim("reviewer1", claim["id"], "negotiating", "开始协商返还。")
        self.assertEqual(ctx.exception.code, "conclusion_stale")
        # 重来：重新冻结
        concl = self.store.refreeze_conclusion("reviewer1", claim["id"], "重新冻结结论。")
        self.assertEqual(concl["status"], "active")
        self.assertEqual(concl["object_version"], self.store.get_object("staff", obj["id"])["version"])

    def test_unresolved_gaps_block_transition_without_exception_note(self):
        obj, claim = self._setup_accepted_claim()
        # 缺口仍在，无例外说明 -> 留在受理阶段
        with self.assertRaises(BusinessError) as ctx:
            self.store.transition_claim("reviewer1", claim["id"], "negotiating", "继续推进协商。")
        self.assertEqual(ctx.exception.code, "gap_not_resolved")
        self.assertEqual(self.store.get_object("reviewer1", obj["id"])["claims"][0]["status"], "under_review")
        # 含“例外”说明 -> 可推进
        self.store.transition_claim("reviewer1", claim["id"], "negotiating", "例外说明：证据暂缺，先推进协商。")
        self.assertEqual(self.store.get_object("reviewer1", obj["id"])["claims"][0]["status"], "negotiating")

    def test_gaps_and_conclusion_staff_only(self):
        obj, claim = self._setup_accepted_claim()
        for role in ("public", "claimant1"):
            with self.assertRaises(BusinessError) as ctx:
                self.store.get_gaps(role, obj["id"])
            self.assertEqual(ctx.exception.status, 403)
            with self.assertRaises(BusinessError) as ctx:
                self.store.get_conclusion(role, claim["id"])
            self.assertEqual(ctx.exception.status, 403)
        self.store.get_gaps("reviewer1", obj["id"])
        self.store.get_conclusion("reviewer1", claim["id"])

    def test_backfill_conclusion_for_legacy_claim(self):
        obj, claim = self._setup_accepted_claim()
        # 模拟旧数据：删掉结论
        with self.store.connect() as conn:
            conn.execute("DELETE FROM claim_conclusions WHERE claim_id=?", (claim["id"],))
        with self.assertRaises(BusinessError) as ctx:
            self.store.get_conclusion("reviewer1", claim["id"])
        self.assertEqual(ctx.exception.code, "conclusion_not_found")
        # 升级回填
        with self.store.connect() as conn:
            self.store._backfill_conclusions(conn)
        concl = self.store.get_conclusion("reviewer1", claim["id"])
        self.assertIsNotNone(concl)
        self.assertEqual(concl["note"], "升级回填结论")
        self.assertEqual(concl["object_version"], self.store.get_object("staff", obj["id"])["version"])


if __name__ == "__main__":
    unittest.main()
