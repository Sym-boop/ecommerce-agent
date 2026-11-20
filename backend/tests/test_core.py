"""单元测试：违禁词检测 / 风控状态机 / 原子落盘 / 数据飞轮 / 版本 / RBAC。

运行（在 backend 目录下）:
    python -m unittest tests.test_core -v
或全部:
    python -m unittest discover -s tests -v

这些测试重点覆盖**出错代价最大**的三处：
    1. 违禁词检测（误报会让审批人失去信任 → 风控形同虚设）
    2. 风控状态机（漏放违规内容不可逆）
    3. JSON 原子落盘（写坏文件会丢全部数据）
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path

# 让 `app.*` 可导入，并把工作目录固定到仓库根
_BACKEND = Path(__file__).resolve().parents[1]
_ROOT = _BACKEND.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
os.chdir(_ROOT)

# ---------------------------------------------------------------------------
# 关键：整套测试的数据目录重定向到临时目录
# ---------------------------------------------------------------------------
# 背景（实测踩过，且是数据丢失级）：store 模块在 import 时载入内存、每次变更
# 全量落盘。测试若直接改 `ad_store._ADS` 再触发 `_persist()`，就会把测试数据
# 写进仓库里**真实的 data/*.json**（演示数据被清空）。
#
# 只做"逐个文档内存覆盖"不够——只要有一个 store 的 `_persist()` 没被覆盖到，
# 真实文件就会被改写（实测 ads.json / versions.json 就这样被改过）。
# 所以这里采取最彻底的做法：**把 json_store 的数据目录整体指向临时目录**，
# 让所有落盘（无论经过哪条路径）都落在临时目录里。
import tempfile as _tempfile  # noqa: E402

from app.common import json_store as _json_store  # noqa: E402

_ISOLATED_DIR = _tempfile.TemporaryDirectory(prefix="dsh-tests-")
_ISOLATED_DATA = Path(_ISOLATED_DIR.name) / "data"
_ISOLATED_DATA.mkdir(parents=True, exist_ok=True)

# 单测固定用 JSON 后端，并让后端也指向隔离目录
os.environ["STORE_BACKEND"] = "json"
os.environ["JOB_BACKEND"] = "threadpool"
_json_store.DATA_DIR = _ISOLATED_DATA

from app.common import store_backend as _store_backend  # noqa: E402

_store_backend.get_backend()._dir = _ISOLATED_DATA

from app.common import audit as _audit  # noqa: E402
from app.common import knowledge_feedback as _feedback  # noqa: E402

_audit._AUDIT_REL = Path(_ISOLATED_DIR.name) / "audit.jsonl"
_feedback._QUEUE_REL = Path(_ISOLATED_DIR.name) / "knowledge_feedback.jsonl"


class TestBannedWords(unittest.TestCase):
    """违禁词检测：``app.common.banned_words``"""

    @classmethod
    def setUpClass(cls):
        from app.common import banned_words
        cls.bw = banned_words

    def test_single_char_word_does_not_match_inside_longer_word(self):
        """「最近」「最后」不应因词表里的单字「最」被判命中（原实测误报）。"""
        for text in ["最近入手了这件卫衣", "最后一天优惠", "他最好朋友"]:
            hits = self.bw.check(text)
            words = {h["word"] for h in hits}
            self.assertNotIn("最", words, f"单字「最」不应命中: {text}")

    def test_multi_char_words_still_detected(self):
        """多字违禁词仍要能检出。"""
        hits = self.bw.check("这是全网最好的选择，第一品牌")
        words = {h["word"] for h in hits}
        self.assertIn("最好", words)
        self.assertIn("第一品牌", words)

    def test_no_redundant_overlapping_hits(self):
        """「最」⊂「最好」不应重复上报（最长优先 + 区间去重）。"""
        hits = self.bw.check("全网最好的选择")
        words = [h["word"] for h in hits]
        self.assertEqual(words.count("最好"), 1)
        self.assertNotIn("最", words)

    def test_clean_text_has_no_hits(self):
        hits = self.bw.check("夏天穿这件很透气，日常通勤都合适")
        self.assertEqual(hits, [])

    def test_replacement_suggestion_filled(self):
        """替换建议必须回填，否则前端「一键替换」会把词删成空。"""
        hits = self.bw.check("全网最好的选择")
        target = [h for h in hits if h["word"] == "最好"]
        self.assertTrue(target, "应命中「最好」")
        self.assertTrue(target[0]["replacement"], "替换建议不应为空")

    def test_medical_words_have_no_suggestion(self):
        """医疗功效类不给替换建议（改写无法规避，必须人工判断）。"""
        hits = self.bw.check("本品可以根治高血压")
        for h in hits:
            if h["word"] in {"根治", "降血压"}:
                self.assertEqual(h["replacement"], "")

    def test_fail_closed_when_wordlist_missing(self):
        """词表缺失必须 fail-closed：抛异常而不是返回空列表。"""
        original = self.bw._WORDS_REL
        try:
            self.bw._WORDS_REL = Path("data/raw/__definitely_missing__.txt")
            self.bw.reload()
            self.assertFalse(self.bw.available())
            with self.assertRaises(self.bw.BannedWordsUnavailable):
                self.bw.check("任意文本")
            # 非严格模式（展示场景）才允许返回空
            self.assertEqual(self.bw.check("任意文本", strict=False), [])
        finally:
            self.bw._WORDS_REL = original
            self.bw.reload()
        self.assertTrue(self.bw.available())

    def test_every_entry_has_category_and_basis(self):
        """词表必须可审计：每条都要有分类和依据。

        回归背景：原来 160 条里只有 13 条有分类、0 条有依据，
        被问「这个词凭什么违禁」答不出条文。现在强制 100% 覆盖。
        """
        from app.common import banned_words as bw

        bw.reload()
        entries = bw._ENTRIES
        self.assertEqual(len(entries), 160)
        no_cat = [e.word for e in entries if e.category == "违禁词"]
        no_basis = [e.word for e in entries if not e.basis]
        self.assertFalse(no_cat, f"缺分类的词: {no_cat[:10]}")
        self.assertFalse(no_basis, f"缺依据的词: {no_basis[:10]}")

    def test_basis_exposed_in_status(self):
        from app.common import banned_words as bw

        bw.reload()
        st = bw.status()
        self.assertEqual(st["categorized_count"], 160)
        self.assertEqual(st["with_basis_count"], 160)

    def test_hit_carries_basis(self):
        from app.common import banned_words as bw

        hits = bw.check("全网最好的水杯")
        self.assertTrue(hits)
        h = hits[0]
        self.assertEqual(h["word"], "最好")
        self.assertEqual(h["category"], "极限词")
        self.assertIn("广告法第九条", h["basis"])

    def test_medical_hit_basis_is_article_17(self):
        from app.common import banned_words as bw

        hits = bw.check("这款茶可以治疗失眠")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["category"], "医疗功效")
        self.assertIn("广告法第十七条", hits[0]["basis"])

    def test_financial_hit_basis_is_article_25(self):
        from app.common import banned_words as bw

        hits = bw.check("投资稳赚不赔")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["category"], "金融承诺")
        self.assertIn("广告法第二十五条", hits[0]["basis"])

    def test_legacy_three_column_format_still_parses(self):
        """旧的三列格式（词|分类|建议）不能因为加了第 4 列而失效。"""
        from app.common import banned_words as bw

        e = bw._parse_line("测试词|极限词|替代")
        self.assertIsNotNone(e)
        self.assertEqual(e.word, "测试词")
        self.assertEqual(e.category, "极限词")
        self.assertEqual(e.replacement, "替代")
        self.assertEqual(e.basis, "")          # 没第 4 列 → 空依据

        e2 = bw._parse_line("测试词2|极限词||广告法第九条(三)")
        self.assertEqual(e2.basis, "广告法第九条(三)")
        self.assertEqual(e2.replacement, "")

    def test_hit_positions_are_reported(self):
        hits = self.bw.check("这是最好的时代")
        self.assertTrue(hits)
        h = hits[0]
        self.assertGreaterEqual(h["start"], 0)
        self.assertEqual(h["end"] - h["start"], len(h["word"]))


class TestPipelineStateMachine(unittest.TestCase):
    """风控状态机：``app.common.pipeline``"""

    @classmethod
    def setUpClass(cls):
        from app.common import ad_store, pipeline

        cls.pipeline = pipeline
        cls.ad_store = ad_store
        cls._original = ad_store._ADS
        ad_store._ADS = []

    @classmethod
    def tearDownClass(cls):
        cls.ad_store._ADS = cls._original

    def setUp(self):
        self.ad_store._ADS = []

    def test_clean_copy_and_normal_budget_go_pending(self):
        ad = self.pipeline.register_ad(ad_type="copy", title="透气T恤", content="清爽好穿", budget=5000)
        self.assertTrue(ad["copy_ok"])
        self.assertTrue(ad["budget_ok"])
        self.assertEqual(ad["stage"], "pending")

    def test_violation_keeps_ad_in_review(self):
        ad = self.pipeline.register_ad(ad_type="copy", title="全网最好", content="绝对正品", budget=5000)
        self.assertFalse(ad["copy_ok"])
        self.assertEqual(ad["stage"], "review")

    def test_budget_out_of_range_keeps_ad_in_review(self):
        low = self.pipeline.register_ad(ad_type="copy", title="正常标题", content="正常内容", budget=100)
        self.assertFalse(low["budget_ok"])
        self.assertEqual(low["stage"], "review")

        high = self.pipeline.register_ad(ad_type="copy", title="正常标题2", content="正常内容2", budget=999999)
        self.assertFalse(high["budget_ok"])
        self.assertEqual(high["stage"], "review")

    def test_budget_none_skips_budget_review(self):
        ok, verdict, _ = self.pipeline.check_budget(None)
        self.assertTrue(ok)
        self.assertEqual(verdict, "normal")

    def test_both_reviews_required_to_advance(self):
        """只有违禁词和预算**都**通过才推进到 pending。"""
        ad = self.pipeline.register_ad(ad_type="copy", title="最好", content="内容", budget=50)
        self.assertFalse(ad["copy_ok"])
        self.assertFalse(ad["budget_ok"])
        self.assertEqual(ad["stage"], "review")

        # 先把违禁词修好 → 仍不能推进（预算还异常）
        updated = self.pipeline.clear_copy(ad["id"], title="不错", content="内容")
        self.assertTrue(updated["copy_ok"])
        self.assertEqual(updated["stage"], "review", "预算未通过不应推进")

        # 再修好预算 → 才推进
        updated = self.pipeline.clear_budget(ad["id"], 5000)
        self.assertEqual(updated["stage"], "pending")

    def test_approve_then_stage_approved(self):
        ad = self.pipeline.register_ad(ad_type="copy", title="标题", content="正文", budget=5000)
        approved = self.pipeline.approve_ad(ad["id"], comment="同意", actor="tester")
        self.assertEqual(approved["stage"], "approved")
        self.assertEqual(approved["handler"], "tester")

    def test_reject_exits_pipeline(self):
        ad = self.pipeline.register_ad(ad_type="copy", title="标题", content="正文", budget=5000)
        rejected = self.pipeline.reject_ad(ad["id"], comment="不合规", actor="tester")
        self.assertEqual(rejected["stage"], "rejected")

    def test_tab_classification(self):
        self.pipeline.register_ad(ad_type="copy", title="全网最好", content="绝对正品", budget=5000)
        self.pipeline.register_ad(ad_type="copy", title="好标题", content="好内容", budget=5000)

        copy_review = self.pipeline.list_tab("copy-review")
        self.assertEqual(len(copy_review), 1)
        self.assertEqual(copy_review[0]["title"], "全网最好")

        pending = self.pipeline.list_tab("pending")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["title"], "好标题")

    def test_stats_shape(self):
        self.pipeline.register_ad(ad_type="copy", title="最好", content="内容", budget=5000)
        stats = self.pipeline.stats()
        for key in ("copy_review", "budget_review", "pending", "approved", "delivered", "rejected"):
            self.assertIn(key, stats)
        self.assertEqual(stats["copy_review"], 1)


class TestDedupe(unittest.TestCase):
    """P0-4 物料去重与幂等。"""

    @classmethod
    def setUpClass(cls):
        from app.common import ad_store

        cls.ad_store = ad_store
        cls._original = ad_store._ADS

    @classmethod
    def tearDownClass(cls):
        cls.ad_store._ADS = cls._original

    def setUp(self):
        self.ad_store._ADS = []

    def test_same_content_not_inserted_twice(self):
        a = self.ad_store.add_ad(ad_type="copy", title="同一标题", content="同一正文")
        b = self.ad_store.add_ad(ad_type="copy", title="同一标题", content="同一正文")
        self.assertEqual(a["id"], b["id"], "重复内容应复用既有记录")
        self.assertTrue(b.get("_deduped"))
        self.assertEqual(len(self.ad_store._ADS), 1)

    def test_different_content_inserted(self):
        self.ad_store.add_ad(ad_type="copy", title="标题A", content="正文A")
        self.ad_store.add_ad(ad_type="copy", title="标题B", content="正文B")
        self.assertEqual(len(self.ad_store._ADS), 2)

    def test_delivered_history_is_preserved(self):
        """已投放的历史记录不参与去重（历史不可改）。"""
        self.ad_store.add_ad(ad_type="copy", title="历史", content="历史内容", stage="delivered")
        self.ad_store.add_ad(ad_type="copy", title="历史", content="历史内容", stage="pending")
        self.assertEqual(len(self.ad_store._ADS), 2)

    def test_fingerprint_is_stable(self):
        fp1 = self.ad_store.content_fingerprint("copy", "t", "c")
        fp2 = self.ad_store.content_fingerprint("copy", "t", "c")
        fp3 = self.ad_store.content_fingerprint("image", "t", "c")
        self.assertEqual(fp1, fp2)
        self.assertNotEqual(fp1, fp3, "类型不同不应视为重复")


class TestJsonStoreAtomicWrite(unittest.TestCase):
    """原子落盘：写坏文件会丢全部数据，必须验证。"""

    def test_write_then_read_roundtrip(self):
        from app.common import json_store

        name = "_test_atomic.json"
        path = json_store.path_of(name)
        try:
            payload = {"a": 1, "中文": "值", "list": [1, 2, 3]}
            json_store.write_json(name, payload)
            self.assertEqual(json_store.read_json(name), payload)
            # 不应留下临时文件
            self.assertFalse(path.with_name(path.name + ".tmp").exists(), "临时文件应已被替换掉")
        finally:
            if path.exists():
                path.unlink()

    def test_read_missing_returns_default(self):
        from app.common import json_store

        self.assertEqual(json_store.read_json("_no_such_file_.json", {"d": 1}), {"d": 1})
        self.assertEqual(json_store.read_list("_no_such_file_.json"), [])

    def test_corrupt_file_returns_default_not_crash(self):
        from app.common import json_store

        name = "_test_corrupt.json"
        path = json_store.path_of(name)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{ 这不是合法 JSON", encoding="utf-8")
            self.assertEqual(json_store.read_json(name, {"fallback": True}), {"fallback": True})
        finally:
            if path.exists():
                path.unlink()


class TestVersions(unittest.TestCase):
    """素材版本链与 diff。"""

    @classmethod
    def setUpClass(cls):
        from app.common import versions

        cls.versions = versions
        cls._original = versions._VERSIONS

    @classmethod
    def tearDownClass(cls):
        cls.versions._VERSIONS = cls._original

    def setUp(self):
        self.versions._VERSIONS = {}

    def test_version_chain_increments(self):
        self.versions.record("AD-1", title="t1", content="c1", actor="u1", note="原始")
        self.versions.record("AD-1", title="t2", content="c2", actor="u2", note="修正")
        chain = self.versions.history("AD-1")
        self.assertEqual(len(chain), 2)
        self.assertEqual([v["version"] for v in chain], [1, 2])
        self.assertEqual(chain[1]["actor"], "u2")

    def test_identical_content_does_not_add_version(self):
        self.versions.record("AD-2", title="t", content="c")
        self.versions.record("AD-2", title="t", content="c")
        self.assertEqual(len(self.versions.history("AD-2")), 1, "内容未变不应新增版本")

    def test_diff_reports_changes(self):
        self.versions.record("AD-3", title="t", content="这是最好的选择")
        self.versions.record("AD-3", title="t", content="这是不错的选择")
        result = self.versions.diff("AD-3")
        self.assertTrue(result["available"])
        self.assertGreater(result["added"], 0)
        self.assertGreater(result["removed"], 0)
        self.assertFalse(result["identical"])

    def test_diff_identical_versions(self):
        self.versions.record("AD-4", title="t", content="内容不变")
        result = self.versions.diff("AD-4")
        self.assertTrue(result["identical"])

    def test_diff_missing_ad(self):
        result = self.versions.diff("AD-not-exist")
        self.assertFalse(result["available"])


class TestRBAC(unittest.TestCase):
    """角色权限与职责分离。"""

    @classmethod
    def setUpClass(cls):
        from app.common import rbac

        cls.rbac = rbac

    def test_role_permissions(self):
        self.assertTrue(self.rbac.has_permission("运营", self.rbac.PERM_GENERATE))
        self.assertFalse(self.rbac.has_permission("运营", self.rbac.PERM_APPROVE))
        self.assertTrue(self.rbac.has_permission("审批", self.rbac.PERM_APPROVE))
        self.assertFalse(self.rbac.has_permission("审批", self.rbac.PERM_REVIEW))
        self.assertTrue(self.rbac.has_permission("管理员", self.rbac.PERM_ADMIN))

    def test_unknown_role_has_no_permission(self):
        self.assertEqual(self.rbac.permissions_for("不存在的角色"), set())

    def test_self_review_blocked(self):
        """禁止审批自己生成的广告。"""
        with self.assertRaises(self.rbac.PermissionDenied):
            self.rbac.require_not_self_review("审批", "alice", "alice", "审批")

    def test_different_person_allowed(self):
        self.rbac.require_not_self_review("审批", "bob", "alice", "审批")

    def test_admin_exempt_from_self_review(self):
        """管理员豁免（单人演示需要），但仍会写审计。"""
        self.rbac.require_not_self_review("管理员", "admin", "admin", "审批")

    def test_require_raises_on_missing_permission(self):
        with self.assertRaises(self.rbac.PermissionDenied):
            self.rbac.require("运营", self.rbac.PERM_APPROVE)


class TestQuality(unittest.TestCase):
    """质量评估客观指标。"""

    @classmethod
    def setUpClass(cls):
        from app.common import quality

        cls.quality = quality

    def test_perfect_copy_scores_high(self):
        result = self.quality.evaluate(
            title="夏天透气T恤",
            content="这件纯棉T恤很透气，" * 8,
            tags=["#夏季穿搭", "#纯棉T恤", "#多色"],
            platform="xiaohongshu",
            sell_points=["纯棉透气", "不易变形", "多色可选"],
            violations=[],
        )
        self.assertGreaterEqual(result["score"], 80)
        self.assertTrue(result["metrics"]["format_ok"])
        self.assertTrue(result["metrics"]["violation_free"])

    def test_violation_heavily_penalized(self):
        clean = self.quality.evaluate(
            title="标题", content="正常内容" * 20, tags=["#a", "#b", "#c"],
            sell_points=["x"],
        )
        dirty = self.quality.evaluate(
            title="标题", content="正常内容" * 20, tags=["#a", "#b", "#c"],
            sell_points=["x"], violations=[{"word": "最"}],
        )
        self.assertLess(dirty["score"], clean["score"])
        self.assertFalse(dirty["metrics"]["violation_free"])

    def test_missing_fields_lose_points(self):
        result = self.quality.evaluate(title="", content="", tags=[], platform="xiaohongshu")
        self.assertFalse(result["metrics"]["format_ok"])
        self.assertLess(result["score"], 80)

    def test_sell_point_coverage(self):
        result = self.quality.evaluate(
            title="t", content="纯棉透气，多色可选", tags=["#a"],
            sell_points=["纯棉透气", "不易变形", "多色可选"],
        )
        self.assertAlmostEqual(result["metrics"]["sell_point_coverage"], 2 / 3, places=2)
        self.assertIn("不易变形", result["metrics"]["sell_points_missing"])

    def test_coverage_one_when_no_sell_points(self):
        result = self.quality.evaluate(title="t", content="c", tags=["#a"], sell_points=[])

        self.assertEqual(result["metrics"]["sell_point_coverage"], 1.0)

    def test_summarize_aggregates(self):
        rows = [
            self.quality.evaluate(title="a", content="x" * 100, tags=["#a", "#b", "#c"], platform="xiaohongshu"),
            self.quality.evaluate(title="b", content="y" * 100, tags=["#a"], platform="tiktok"),
        ]
        summary = self.quality.summarize(rows)
        self.assertEqual(summary["count"], 2)
        self.assertIn("xiaohongshu", summary["by_platform"])
        self.assertIn("tiktok", summary["by_platform"])

    def test_llm_judge_disabled_by_default(self):
        """LLM 评分默认关闭（会产生真实 API 成本）。"""
        os.environ.pop("LLM_JUDGE_ENABLED", None)
        result = self.quality.llm_judge(title="t", content="c")
        self.assertFalse(result["available"])


class TestAudit(unittest.TestCase):
    """审计日志追加写。

    注意：所有用例都把 `_AUDIT_REL` 指到临时目录——审计文件是**追加写**，
    若用仓库里的真实文件，测试记录会混进真实审计日志（观测污染）。
    """

    def setUp(self):
        from app.common import audit

        self.audit = audit
        self._original = audit._AUDIT_REL
        self._tmp = tempfile.TemporaryDirectory()
        audit._AUDIT_REL = Path(self._tmp.name) / "audit.jsonl"

    def tearDown(self):
        self.audit._AUDIT_REL = self._original
        self.audit.set_actor(None)
        self._tmp.cleanup()

    def test_record_and_read(self):
        audit = self.audit
        audit.set_actor("tester", "测试员", "审批")
        audit.record("review.approve", target="AD-1",
                     before={"stage": "pending"}, after={"stage": "approved"})
        rows = audit.read(limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "review.approve")
        self.assertEqual(rows[0]["actor"], "tester")
        self.assertIn("changes", rows[0])
        self.assertEqual(rows[0]["changes"]["stage"]["after"], "approved")

    def test_sensitive_fields_redacted(self):
        self.audit.record("auth.login", detail={"username": "u", "password": "secret123"})
        rows = self.audit.read(limit=1)
        self.assertEqual(rows[0]["detail"]["password"], "***")
        self.assertEqual(rows[0]["detail"]["username"], "u")

    def test_filter_by_action(self):
        self.audit.record("review.approve", target="A")
        self.audit.record("review.reject", target="B")
        self.assertEqual(len(self.audit.read(action="review.approve")), 1)
        self.assertEqual(len(self.audit.read(limit=10)), 2)


class TestKnowledgeFeedback(unittest.TestCase):
    """数据飞轮：人工认可样本回流。

    队列同样是**追加写**，用例必须把它指到临时目录，避免污染真实队列。
    """

    def setUp(self):
        from app.common import knowledge_feedback

        self.fb = knowledge_feedback
        self._original = knowledge_feedback._QUEUE_REL
        self._tmp = tempfile.TemporaryDirectory()
        knowledge_feedback._QUEUE_REL = Path(self._tmp.name) / "feedback.jsonl"

    def tearDown(self):
        self.fb._QUEUE_REL = self._original
        self._tmp.cleanup()

    def test_contribute_queues_sample(self):
        entry = self.fb.contribute(
            ad_id="AD-9", text="人工认可的文案", title="标题",
            platform="xiaohongshu", category="T恤", actor="tester",
        )
        # Milvus 不可用时应入队（不能丢数据）
        self.assertIn(entry["mode"], {"milvus", "queued"})
        self.assertEqual(self.fb.queue_size(), 1)
        rows = self.fb.read_queue()
        self.assertEqual(rows[0]["ad_id"], "AD-9")
        self.assertEqual(rows[0]["category"], "T恤")

    def test_empty_text_skipped(self):
        result = self.fb.contribute(ad_id="AD-x", text="   ")
        self.assertEqual(result["mode"], "skipped")

    def test_queue_size_zero_when_missing(self):
        self.assertEqual(self.fb.queue_size(), 0)


class TestMetrics(unittest.TestCase):
    """可观测性指标。"""

    def test_track_records_success_and_duration(self):
        from app.common import metrics

        metrics.reset()
        with metrics.track("unit_test"):
            pass
        snap = metrics.snapshot()
        cap = snap["capabilities"]["unit_test"]
        self.assertEqual(cap["calls"], 1)
        self.assertEqual(cap["ok"], 1)
        self.assertEqual(cap["failed"], 0)

    def test_track_records_failure_and_reraises(self):
        from app.common import metrics

        metrics.reset()
        with self.assertRaises(ValueError):
            with metrics.track("unit_fail"):
                raise ValueError("boom")
        cap = metrics.snapshot()["capabilities"]["unit_fail"]
        self.assertEqual(cap["failed"], 1)
        self.assertEqual(cap["failure_rate"], 1.0)

    def test_degradation_is_counted(self):
        """降级次数必须可观测——这是"看起来正常其实没调通"的防线。"""
        from app.common import metrics

        metrics.reset()
        with metrics.track("copy_generation") as t:
            t.degraded()
        cap = metrics.snapshot()["capabilities"]["copy_generation"]
        self.assertEqual(cap["degraded"], 1)
        self.assertEqual(cap["degradation_rate"], 1.0)

    def test_token_cost_accumulates(self):
        from app.common import metrics

        metrics.reset()
        with metrics.track("llm") as t:
            t.tokens(input=1000, output=1000)
        snap = metrics.snapshot()
        self.assertEqual(snap["llm_tokens"]["total"], 2000)
        self.assertGreater(snap["cost_estimate"], 0)

    def test_counters(self):
        from app.common import metrics

        metrics.reset()
        metrics.incr("dedupe_hit")
        metrics.incr("dedupe_hit")
        self.assertEqual(metrics.snapshot()["counters"]["dedupe_hit"], 2)


class TestConfigCheck(unittest.TestCase):
    """启动配置自检。"""

    def test_report_shape(self):
        from app.common import config_check

        result = config_check.collect()
        self.assertIn("ok", result)
        self.assertIn("items", result)
        self.assertIsInstance(result["errors"], list)
        self.assertTrue(any(i["name"] == "LLM_MODEL" for i in result["items"]))

    def test_known_bad_model_flagged(self):
        """已知无效模型名必须被标为 error——它会导致静默降级成模板文案。"""
        from app.common import config_check

        original = os.environ.get("LLM_MODEL")
        try:
            os.environ["LLM_MODEL"] = "deepseek-v41-flash"
            result = config_check.collect()
            bad = [i for i in result["items"] if i["name"] == "LLM_MODEL"]
            self.assertEqual(len(bad), 1)
            self.assertEqual(bad[0]["level"], "error")
        finally:
            if original is None:
                os.environ.pop("LLM_MODEL", None)
            else:
                os.environ["LLM_MODEL"] = original

    def test_placeholder_key_flagged(self):
        from app.common import config_check

        self.assertTrue(config_check._is_placeholder(""))
        self.assertTrue(config_check._is_placeholder("***"))
        self.assertTrue(config_check._is_placeholder("sk-xxxx"))
        self.assertFalse(config_check._is_placeholder("sk-real-key-1234567890"))


class TestAuditActorIsolation(unittest.TestCase):
    """审计操作人必须按请求隔离。

    回归测试：原先用 threading.local，FastAPI 把同步端点丢到复用线程池后，
    上一个请求的 actor 会残留在同一线程上——实测「运营发起的生成被记成审查员」。
    改用 ContextVar 后，跨线程读取同一上下文应保持一致。
    """

    def setUp(self):
        from app.common import audit

        self.audit = audit
        audit.set_actor(None)

    def test_actor_is_readable_after_set(self):
        self.audit.set_actor("alice", "爱丽丝", "审查")
        actor = self.audit.current_actor()
        self.assertEqual(actor["username"], "alice")
        self.assertEqual(actor["role"], "审查")

    def test_default_actor_is_anonymous(self):
        self.audit.set_actor(None)
        self.assertEqual(self.audit.current_actor()["username"], "anonymous")

    def test_actor_visible_in_record(self):
        self.audit.set_actor("bob", "鲍勃", "审批")
        entry = self.audit.record("unit.action", target="X")
        self.assertEqual(entry["actor"], "bob")
        self.assertEqual(entry["actor_role"], "审批")

    def test_contextvar_isolated_between_contexts(self):
        """不同上下文（模拟不同请求）之间不应互相污染。"""
        import contextvars

        self.audit.set_actor("outer", "外层", "运营")
        self.assertEqual(self.audit.current_actor()["username"], "outer")

        captured: dict[str, str] = {}

        def run_in_new_context() -> None:
            # 模拟中间件为新请求设置 actor
            self.audit.set_actor("inner", "内层", "审查")
            captured["inner"] = self.audit.current_actor()["username"]

        ctx = contextvars.copy_context()
        ctx.run(run_in_new_context)

        self.assertEqual(captured["inner"], "inner")
        # 外层上下文不受影响
        self.assertEqual(self.audit.current_actor()["username"], "outer")


class TestFlywheelModePersisted(unittest.TestCase):
    """回归测试：回流样本的 mode 必须落进队列。

    原先 `_queue(entry)` 在写入向量库**之前**调用，导致落盘记录缺 mode，
    重启后无法判断样本到底入没入库（可观测性缺失）。
    """

    def test_mode_recorded_in_queue(self):
        from app.common import knowledge_feedback

        original = knowledge_feedback._QUEUE_REL
        with tempfile.TemporaryDirectory() as tmp:
            try:
                knowledge_feedback._QUEUE_REL = Path(tmp) / "fb.jsonl"
                knowledge_feedback.contribute(
                    ad_id="AD-mode", text="内容", title="t",
                    platform="xiaohongshu", category="防晒", actor="u",
                )
                rows = knowledge_feedback.read_queue()
                self.assertTrue(rows)
                self.assertIn(rows[0].get("mode"), {"milvus", "queued"},
                              "队列记录必须带 mode，便于观测是否真正入库")
            finally:
                knowledge_feedback._QUEUE_REL = original


class TestDedupeFlagExposed(unittest.TestCase):
    """回归测试：幂等标记必须能被上层读到。

    原先 `pipeline.register_ad` 里 `ad.pop("_deduped")` 把标记弹掉了，
    调用方（adgen_router）拿不到，导致「重复保存命中幂等」这条链路测不出来。
    """

    @classmethod
    def setUpClass(cls):
        from app.common import ad_store, pipeline

        cls.pipeline = pipeline
        cls.ad_store = ad_store
        cls._original = ad_store._ADS

    @classmethod
    def tearDownClass(cls):
        cls.ad_store._ADS = cls._original

    def setUp(self):
        self.ad_store._ADS = []

    def test_deduped_flag_survives_register_ad(self):
        first = self.pipeline.register_ad(ad_type="copy", title="同标题", content="同正文")
        self.assertFalse(first.get("deduped"), "首次登记不应标记去重")

        second = self.pipeline.register_ad(ad_type="copy", title="同标题", content="同正文")
        self.assertTrue(second.get("deduped"), "重复登记应返回 deduped=True")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.ad_store._ADS), 1)

    def test_register_ad_records_generated_by(self):
        """生成人必须落盘——职责分离依赖它。"""
        ad = self.pipeline.register_ad(ad_type="copy", title="t", content="c", actor="op_user")
        self.assertEqual(ad["generated_by"], "op_user")

    def test_register_ad_records_category_for_flywheel(self):
        ad = self.pipeline.register_ad(
            ad_type="copy", title="t", content="c", category="防晒", session_id="AGEN-1",
        )
        self.assertEqual(ad["category"], "防晒")
        self.assertEqual(ad["session_id"], "AGEN-1")


class TestVersionOnlyOnSuccessfulClear(unittest.TestCase):
    """回归测试：仍命中违禁词的放行尝试不应产生版本记录。

    否则版本链会出现重复的"看起来已修正"的空版本（实测出现过 v2/v3 同备注）。
    """

    @classmethod
    def setUpClass(cls):
        from app.common import ad_store, pipeline, versions

        cls.pipeline = pipeline
        cls.ad_store = ad_store
        cls.versions = versions
        cls._ads = ad_store._ADS
        cls._vers = versions._VERSIONS

    @classmethod
    def tearDownClass(cls):
        cls.ad_store._ADS = cls._ads
        cls.versions._VERSIONS = cls._vers

    def setUp(self):
        self.ad_store._ADS = []
        self.versions._VERSIONS = {}

    def test_failed_clear_does_not_add_version(self):
        ad = self.pipeline.register_ad(ad_type="copy", title="全网最好", content="绝对正品")
        before = len(self.versions.history(ad["id"]))

        # 仍带违禁词 → 不通过
        self.pipeline.clear_copy(ad["id"], title="全网最好", content="绝对正品")
        after = len(self.versions.history(ad["id"]))
        self.assertEqual(after, before, "未通过的修正不应记版本")

    def test_successful_clear_adds_version(self):
        ad = self.pipeline.register_ad(ad_type="copy", title="全网最好", content="绝对正品")
        before = len(self.versions.history(ad["id"]))

        self.pipeline.clear_copy(ad["id"], title="不错的选择", content="正品渠道")
        after = len(self.versions.history(ad["id"]))
        self.assertEqual(after, before + 1, "成功的修正应记一个版本")


class TestStoreBackend(unittest.TestCase):
    """存储后端：JSON 与 SQLite 行为一致，且 SQLite 可从事前 JSON 迁移。"""

    def setUp(self):
        self._backends: list = []
        self._tmpdirs: list[tempfile.TemporaryDirectory] = []

    def tearDown(self):
        # 顺序很重要：Windows 上未关闭的 sqlite 连接会锁住文件，
        # 必须先关连接（含各线程的连接）再删临时目录。
        #
        # 并发写测试中，工作线程结束后其 threading.local 里的连接会先被 GC 回收，
        # CPython 此时会报 ResourceWarning（句柄尚未显式 close）。这只是回收时序，
        # 不影响正确性；真正的清理在下面 close() 里完成，所以这里不把它当噪声报出来。
        import gc
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            for backend in self._backends:
                backend.close()
            self._backends.clear()
            gc.collect()
        for tmp in self._tmpdirs:
            tmp.cleanup()
        self._tmpdirs.clear()

    def _tmp(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self._tmpdirs.append(tmp)
        return Path(tmp.name)

    def _sqlite(self, tmp: Path):
        from app.common import store_backend

        backend = store_backend.SqliteBackend(db_path=tmp / "s.sqlite", data_dir=tmp)
        self._backends.append(backend)
        return backend

    def test_json_backend_roundtrip(self):
        from app.common import store_backend

        tmp = self._tmp()
        backend = store_backend.JsonFileBackend(data_dir=tmp)
        payload = {"a": 1, "中文": [1, 2], "nested": {"k": "v"}}
        backend.write("t.json", payload)
        self.assertEqual(backend.read("t.json"), payload)
        self.assertTrue(backend.exists("t.json"))
        self.assertFalse(backend.exists("nope.json"))
        # 原子写不应留下临时文件
        self.assertFalse((tmp / "t.json.tmp").exists())

    def test_sqlite_backend_roundtrip(self):
        backend = self._sqlite(self._tmp())
        payload = {"x": 1, "list": [1, 2, 3], "中文": "值"}
        backend.write("doc.json", payload)
        self.assertEqual(backend.read("doc.json"), payload)
        self.assertTrue(backend.exists("doc.json"))
        # 覆盖写
        backend.write("doc.json", {"x": 2})
        self.assertEqual(backend.read("doc.json"), {"x": 2})

    def test_sqlite_overwrites_not_appends(self):
        """同一文档重复写应是覆盖，不是追加（避免数据膨胀）。"""
        backend = self._sqlite(self._tmp())
        for i in range(5):
            backend.write("k.json", {"v": i})
        self.assertEqual(backend.read("k.json"), {"v": 4})
        self.assertEqual(backend._count(), 1)

    def test_sqlite_migrates_existing_json(self):
        """首次切到 SQLite 时应自动导入现有 JSON，保证数据不丢。"""
        tmp = self._tmp()
        (tmp / "ads.json").write_text('[{"id": "AD-1"}]', encoding="utf-8")
        (tmp / "broken.json").write_text("{ 非法", encoding="utf-8")

        backend = self._sqlite(tmp)
        result = backend.migrate_from_json()

        self.assertIn("ads.json", result["imported"])
        self.assertEqual(backend.read("ads.json"), [{"id": "AD-1"}])
        # 解析失败的文件应被跳过而不是让迁移崩掉
        self.assertNotIn("broken.json", result["imported"])

    def test_sqlite_migration_does_not_overwrite(self):
        tmp = self._tmp()
        (tmp / "x.json").write_text('{"v": "json"}', encoding="utf-8")
        backend = self._sqlite(tmp)
        backend.write("x.json", {"v": "sqlite"})
        backend.migrate_from_json()
        self.assertEqual(backend.read("x.json"), {"v": "sqlite"}, "已存在的文档不应被覆盖")

    def test_json_store_uses_active_backend(self):
        """json_store 必须走当前后端（这是"切换后端业务零改动"的关键）。"""
        from app.common import json_store, store_backend

        original = store_backend.get_backend()
        backend = self._sqlite(self._tmp())
        try:
            store_backend.reset_backend(backend)
            json_store.write_json("demo.json", {"hello": "世界"})
            self.assertEqual(json_store.read_json("demo.json"), {"hello": "世界"})
            self.assertEqual(json_store.read_list("demo.json", []), [])
            self.assertEqual(json_store.read_dict("demo.json"), {"hello": "世界"})
        finally:
            store_backend.reset_backend(original)

    def test_corrupt_sqlite_document_returns_default(self):
        backend = self._sqlite(self._tmp())
        with backend._conn as conn:
            conn.execute("INSERT INTO documents(name,payload,updated_at) VALUES (?,?,?)",
                         ("bad.json", "{非 JSON", "now"))
            conn.commit()
        self.assertIsNone(backend.read("bad.json"))

    def test_sqlite_supports_concurrent_writers(self):
        """多线程并发写不应损坏文档（JSON 全量重写下会互相覆盖）。"""
        import threading

        backend = self._sqlite(self._tmp())

        def writer(n: int) -> None:
            for i in range(10):
                backend.write(f"doc{n}.json", {"n": n, "i": i})

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for n in range(5):
            doc = backend.read(f"doc{n}.json")
            self.assertEqual(doc["n"], n)
            self.assertEqual(doc["i"], 9)


class TestJobs(unittest.TestCase):
    """异步任务：提交 / 状态流转 / 失败捕获 / 取消。"""

    def setUp(self):
        from app.common import jobs

        self.jobs = jobs
        jobs._JOBS.clear()

    def test_successful_job_lifecycle(self):
        job = self.jobs.submit("unit.ok", _job_ok, 21)
        self.assertTrue(job["job_id"].startswith("JOB-"))
        done = self._wait(job["job_id"])
        self.assertEqual(done["status"], "succeeded")
        self.assertEqual(done["result"], 42)
        self.assertIsNotNone(done["elapsed_ms"])

    def test_failed_job_records_error(self):
        job = self.jobs.submit("unit.fail", _job_fail)
        done = self._wait(job["job_id"])
        self.assertEqual(done["status"], "failed")
        self.assertIn("boom", done["error"])

    def test_job_records_actor_and_kind(self):
        job = self.jobs.submit("unit.meta", _job_ok, 1, actor="tester")
        done = self._wait(job["job_id"])
        self.assertEqual(done["actor"], "tester")
        self.assertEqual(done["kind"], "unit.meta")

    def test_missing_job_returns_none(self):
        self.assertIsNone(self.jobs.get("JOB-doesnotexist"))

    def test_list_and_stats(self):
        self.jobs.submit("unit.a", _job_ok, 1)
        self.jobs.submit("unit.b", _job_fail)
        time.sleep(1.0)
        rows = self.jobs.list_jobs(limit=10)
        self.assertEqual(len(rows), 2)
        stats = self.jobs.stats()
        self.assertEqual(stats["total"], 2)
        self.assertIn(stats["backend"], {"threadpool", "celery"})

    def test_filter_by_status(self):
        self.jobs.submit("unit.ok", _job_ok, 1)
        self.jobs.submit("unit.bad", _job_fail)
        time.sleep(1.0)
        self.assertEqual(len(self.jobs.list_jobs(status="succeeded")), 1)
        self.assertEqual(len(self.jobs.list_jobs(status="failed")), 1)

    def test_backend_info_reports_reason(self):
        info = self.jobs.backend_info()
        self.assertIn(info["name"], {"threadpool", "celery"})
        self.assertTrue(info.get("reason"), "必须说明为什么选了这个后端")

    def test_threadpool_backend_is_default_without_redis(self):
        """Redis 不可达时必须回退线程池，而不是让功能不可用。"""
        info = self.jobs.backend_info()
        self.assertIn(info["name"], {"threadpool", "celery"})
        if info["name"] == "threadpool":
            self.assertFalse(info["distributed"])

    @staticmethod
    def _wait(job_id: str, timeout: float = 10.0) -> dict:
        from app.common import jobs

        deadline = time.time() + timeout
        while time.time() < deadline:
            job = jobs.get(job_id)
            if job and job["status"] in {"succeeded", "failed", "cancelled"}:
                return job
            time.sleep(0.1)
        raise AssertionError(f"任务未在 {timeout}s 内结束: {job_id}")


def _job_ok(value: int) -> int:
    """模块级函数（Celery 后端要求可跨进程序列化）。"""
    return value * 2


def _job_fail() -> None:
    raise RuntimeError("boom")


class TestRevalidateViolations(unittest.TestCase):
    """存量审查结论自愈。

    回归测试：审查结论是登记时算好落盘的；检测规则修正后（如去掉单字「最」的
    子串误报），历史数据仍留着旧误报——表现为"改了 bug 但界面还是显示违规"。
    """

    @classmethod
    def setUpClass(cls):
        from app.common import ad_store

        cls.ad_store = ad_store
        cls._original = ad_store._ADS

    @classmethod
    def tearDownClass(cls):
        cls.ad_store._ADS = cls._original

    def setUp(self):
        self.ad_store._ADS = []

    def test_false_positive_gets_corrected(self):
        """模拟按旧规则产生的误报（「最近」命中「最」），重算后应被修正。"""
        ad = self.ad_store.add_ad(
            ad_type="copy", title="最近入手了这件卫衣", content="穿着很舒服",
            copy_ok=False, stage="review",
            violations=[{"word": "最", "type": "违禁词", "suggestion": ""}],
        )
        result = self.ad_store.revalidate_violations()
        self.assertEqual(result["fixed"], 1)
        self.assertTrue(ad["copy_ok"], "误报应被修正为通过")
        self.assertEqual(ad["violations"], [])

    def test_real_violation_is_preserved(self):
        ad = self.ad_store.add_ad(
            ad_type="copy", title="全网最好的选择", content="绝对正品",
            copy_ok=False, stage="review", violations=[],
        )
        self.ad_store.revalidate_violations()
        self.assertFalse(ad["copy_ok"], "真实违禁词仍应判违规")
        self.assertTrue(ad["violations"])

    def test_terminal_stage_not_modified(self):
        """已投放/已驳回的物料保留当时的事实结论，不被重算改写。"""
        ad = self.ad_store.add_ad(
            ad_type="copy", title="最近入手", content="x",
            copy_ok=False, stage="delivered",
            violations=[{"word": "最", "type": "违禁词", "suggestion": ""}],
        )
        self.ad_store.revalidate_violations()
        self.assertFalse(ad["copy_ok"], "终态物料不应被改写")
        self.assertEqual(len(ad["violations"]), 1)

    def test_no_change_when_already_correct(self):
        clean = self.ad_store.add_ad(ad_type="copy", title="透气T恤", content="清爽好穿",
                                     copy_ok=True, stage="pending")
        result = self.ad_store.revalidate_violations()
        self.assertEqual(result["fixed"], 0)
        self.assertNotIn("review_revalidated_at", clean)


class TestMigrationIsRecursive(unittest.TestCase):
    """回归测试：迁移必须递归子目录。

    历史记录在 `data/history/records.json`，只扫顶层会让切到 SQLite 后
    历史记录变 0（实测踩过）。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._backends: list = []

    def tearDown(self):
        import gc

        for b in self._backends:
            b.close()
        gc.collect()
        self._tmp.cleanup()

    def test_nested_json_is_migrated(self):
        from app.common import store_backend

        tmp = Path(self._tmp.name)
        (tmp / "history").mkdir()
        (tmp / "history" / "records.json").write_text('[{"id": "REC-1"}]', encoding="utf-8")
        (tmp / "stores").mkdir()
        (tmp / "stores" / "qianchuan.json").write_text('{"stores": []}', encoding="utf-8")
        (tmp / "ads.json").write_text('[{"id": "AD-1"}]', encoding="utf-8")

        backend = store_backend.SqliteBackend(db_path=tmp / "s.sqlite", data_dir=tmp)
        self._backends.append(backend)
        result = backend.migrate_from_json()

        self.assertIn("history/records.json", result["imported"], "子目录文档必须被迁移")
        self.assertIn("stores/qianchuan.json", result["imported"])
        self.assertIn("ads.json", result["imported"])
        self.assertEqual(backend.read("history/records.json"), [{"id": "REC-1"}])

    def test_document_names_use_posix_separator(self):
        """文档名必须用正斜杠，才能与 json_store 的 filename 口径一致。"""
        from app.common import store_backend

        tmp = Path(self._tmp.name)
        (tmp / "history").mkdir()
        (tmp / "history" / "records.json").write_text("[]", encoding="utf-8")
        backend = store_backend.SqliteBackend(db_path=tmp / "s.sqlite", data_dir=tmp)
        self._backends.append(backend)
        backend.migrate_from_json()
        names = [r[0] for r in backend._conn.execute("SELECT name FROM documents")]
        self.assertIn("history/records.json", names)
        self.assertNotIn("history\\records.json", names, "不应出现反斜杠形式")


class TestStoresRouteThroughJsonStore(unittest.TestCase):
    """回归测试：store 必须经 json_store 落盘。

    原因：`use_memory()` 隔离与 `STORE_BACKEND=sqlite` 切换都只在 json_store 层生效。
    若某个 store 直接写自己的 `_STORE_PATH`，就会出现：
      - 单测绕过隔离、**覆盖真实数据文件**（实测踩过：演示数据被清空）
      - 切到 SQLite 后该 store 的数据仍在 JSON 里，静默不一致
    """

    def test_history_store_uses_json_store(self):
        from app.common import history_store, json_store

        json_store.use_memory("history/records.json", [])
        try:
            history_store._RECORDS.clear()
            history_store._LOADED = True
            history_store.save_record(product={"name": "测试商品"}, keyword="k",
                                      platform="xiaohongshu", copy={"title": "t", "content": "c"})
            # 应写进内存覆盖，而不是磁盘
            saved = json_store.read_json("history/records.json", [])
            self.assertTrue(saved, "save_record 必须经 json_store 落盘")
        finally:
            json_store.clear_memory()
            history_store._RECORDS.clear()
            history_store._LOADED = False

    def test_product_store_uses_json_store(self):
        from app.common import json_store, product_store

        json_store.use_memory("products.json", [])
        try:
            product_store._PRODUCTS.clear()
            product_store._LOADED = True
            product_store.add_product(name="隔离测试商品", category="测试")
            saved = json_store.read_json("products.json", [])
            self.assertTrue(any(p.get("name") == "隔离测试商品" for p in saved),
                            "add_product 必须经 json_store 落盘")
        finally:
            json_store.clear_memory()
            product_store._PRODUCTS.clear()
            product_store._LOADED = False

    def test_history_store_has_no_direct_path_write(self):
        """源码层面确认：history_store 不再自己拼路径写文件。"""
        import inspect

        from app.common import history_store

        source = inspect.getsource(history_store)
        self.assertNotIn("_STORE_PATH.write_text", source)
        self.assertIn("json_store.write_json", source)


class TestImageCheck(unittest.TestCase):
    """图片一致性校验：客观预检可用，语义校验诚实标注能力边界。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "media" / "images").mkdir(parents=True)
        (self.root / "media" / "uploads").mkdir(parents=True)
        # 清掉可能存在的视觉模型配置，保证用例可重复
        for key in ("VISION_API_KEY", "VISION_MODEL", "VISION_API_BASE"):
            os.environ.pop(key, None)

    def tearDown(self):
        self._tmp.cleanup()

    def _make_image(self, rel: str, size: tuple[int, int]):
        from PIL import Image

        path = self.root / "media" / rel
        Image.new("RGB", size, (200, 200, 200)).save(path)
        return f"/media/{rel}"

    def test_prescreen_reports_resolution_and_ratio(self):
        from app.common import image_check

        result = self._make_image("images/r.png", (1080, 1440))
        check = image_check.prescreen(result_url=result, data_dir=self.root)
        self.assertTrue(check["available"])
        self.assertTrue(check["passed"])
        names = [c["name"] for c in check["checks"]]
        self.assertIn("分辨率", names)

    def test_prescreen_flags_low_resolution(self):
        from app.common import image_check

        result = self._make_image("images/small.png", (200, 200))
        check = image_check.prescreen(result_url=result, data_dir=self.root)
        self.assertFalse(check["passed"])
        self.assertTrue(any("短边" in w for w in check["warnings"]))

    def test_prescreen_flags_aspect_mismatch(self):
        from app.common import image_check

        result = self._make_image("images/out.png", (1280, 720))    # 16:9
        uploaded = self._make_image("uploads/in.png", (720, 1280))  # 9:16
        check = image_check.prescreen(result_url=result, input_image=uploaded,
                                      data_dir=self.root)
        ratio_check = next(c for c in check["checks"] if c["name"] == "宽高比一致性")
        self.assertFalse(ratio_check["passed"])
        self.assertFalse(check["passed"])

    def test_prescreen_missing_file_is_unavailable(self):
        from app.common import image_check

        check = image_check.prescreen(result_url="/media/images/nope.png", data_dir=self.root)
        self.assertFalse(check["available"])
        self.assertIn("不存在", check["reason"])

    def test_vision_disabled_by_default(self):
        """视觉校验默认关闭，且必须说明原因——不能假装校验过。"""
        from app.common import image_check

        self.assertFalse(image_check.vision_enabled())
        result = image_check.vision_check(title="t", content="c", data_dir=self.root)
        self.assertFalse(result["available"])
        self.assertIn("VISION_API_KEY", result["reason"])

    def test_check_asset_separates_layers(self):
        """两层结果必须分开返回，避免把比例检查夸大成图片理解。"""
        from app.common import image_check

        result = self._make_image("images/ok.png", (1080, 1350))
        out = image_check.check_asset(
            result_url=result, title="透气T恤", content="清爽好穿", data_dir=self.root,
        )
        self.assertIn("prescreen", out)
        self.assertIn("vision", out)
        self.assertFalse(out["vision_available"])
        # 视觉不可用时不因此判失败
        self.assertTrue(out["passed"])

    def test_vision_placeholder_key_treated_as_unconfigured(self):
        from app.common import image_check

        os.environ["VISION_API_KEY"] = "***"
        os.environ["VISION_MODEL"] = "some-model"
        try:
            self.assertFalse(image_check.vision_enabled())
        finally:
            os.environ.pop("VISION_API_KEY", None)
            os.environ.pop("VISION_MODEL", None)


class TestAbReport(unittest.TestCase):
    """投放效果对照：分组指标来自真实数据，真实指标缺失时如实标注。"""

    @classmethod
    def setUpClass(cls):
        from app.common import ad_store

        cls.ad_store = ad_store
        cls._original = ad_store._ADS

    @classmethod
    def tearDownClass(cls):
        cls.ad_store._ADS = cls._original

    def setUp(self):
        self.ad_store._ADS = []

    def test_groups_split_ai_and_human(self):
        from app.common import ab_report

        self.ad_store.add_ad(ad_type="copy", title="AI 版", content="内容A",
                             copy_ok=True, stage="delivered", source="ai")
        self.ad_store.add_ad(ad_type="copy", title="人工版", content="内容B",
                             copy_ok=True, stage="delivered", source="human_feedback")
        report = ab_report.build_report()
        self.assertIn("ai", report["groups"])
        self.assertIn("human_edited", report["groups"])
        self.assertEqual(report["groups"]["ai"]["materials"], 1)

    def test_compliance_rate_computed(self):
        from app.common import ab_report

        self.ad_store.add_ad(ad_type="copy", title="合规", content="ok",
                             copy_ok=True, stage="pending", source="ai")
        self.ad_store.add_ad(ad_type="copy", title="违规", content="bad",
                             copy_ok=False, stage="review", source="ai",
                             violations=[{"word": "最"}])
        report = ab_report.build_report()
        g = report["groups"]["ai"]
        self.assertEqual(g["materials"], 2)
        self.assertAlmostEqual(g["compliance_rate"], 0.5, places=4)
        self.assertAlmostEqual(g["avg_violations"], 0.5, places=3)

    def test_real_metrics_flagged_when_absent(self):
        """没有平台回传数据时必须如实说明，不能伪造 CTR/CVR。"""
        from app.common import ab_report

        self.ad_store.add_ad(ad_type="copy", title="t", content="c", stage="delivered")
        report = ab_report.build_report()
        self.assertFalse(report["real_metrics_available"])
        self.assertIn("mock", report["metrics_note"])
        self.assertIsNone(report["groups"]["ai"]["ctr"])

    def test_metrics_recorded_then_ctr_computed(self):
        from app.common import ab_report

        ad = self.ad_store.add_ad(ad_type="copy", title="t", content="c", stage="delivered")
        ab_report.record_metrics(ad["id"], impressions=1000, clicks=50, conversions=5)
        report = ab_report.build_report()
        self.assertTrue(report["real_metrics_available"])
        g = report["groups"]["ai"]
        self.assertAlmostEqual(g["ctr"], 0.05, places=4)
        self.assertAlmostEqual(g["cvr"], 0.1, places=4)
        self.assertEqual(g["impressions"], 1000)

    def test_record_metrics_missing_ad(self):
        from app.common import ab_report

        self.assertIsNone(ab_report.record_metrics("AD-nonexistent", impressions=1))

    def test_zero_impressions_does_not_divide_by_zero(self):
        from app.common import ab_report

        ad = self.ad_store.add_ad(ad_type="copy", title="t", content="c", stage="delivered")
        ab_report.record_metrics(ad["id"], impressions=0, clicks=0, conversions=0)
        report = ab_report.build_report()
        self.assertIsNone(report["groups"]["ai"]["ctr"], "分母为 0 应返回 None 而不是 NaN")

    def test_platform_breakdown(self):
        from app.common import ab_report

        self.ad_store.add_ad(ad_type="copy", title="a", content="x",
                             platform="xiaohongshu", stage="delivered")
        self.ad_store.add_ad(ad_type="copy", title="b", content="y",
                             platform="tiktok", stage="delivered")
        report = ab_report.build_report()
        self.assertIn("xiaohongshu", report["by_platform"])
        self.assertIn("tiktok", report["by_platform"])


class TestAdGraphOrchestration(unittest.TestCase):
    """广告生成主图（LangGraph）的编排正确性。

    用桩替换大模型 / 图文 / 视频调用，不产生真实费用。

    重点回归：**选品失败必须短路**。
    修复前 `select_product → gen_copy` 是无条件边，且 `gen_copy` 不检查 status，
    导致商品名为空时仍调用大模型、并把 status 覆盖成 copy_done，最终报 `done`
    —— 错误被吞掉（实测复现过）。
    """

    PRODUCT = {"name": "测试商品", "category": "测试", "sell_points": ["卖点A"]}

    @classmethod
    def setUpClass(cls):
        from app.agent import ad_graph, copywriter, image_generator, video_generator

        cls.graph = ad_graph
        cls.copywriter = copywriter
        cls.image_generator = image_generator
        cls.video_generator = video_generator
        cls._orig_copy = copywriter.generate_copy
        cls._orig_image = image_generator.generate_image
        cls._orig_video = video_generator.generate_short_video

    @classmethod
    def tearDownClass(cls):
        cls.copywriter.generate_copy = cls._orig_copy
        cls.image_generator.generate_image = cls._orig_image
        cls.video_generator.generate_short_video = cls._orig_video

    def setUp(self):
        self.calls: list[str] = []

        class _Copy:
            title = "标题"
            content = "正文内容"
            tags = ["#a", "#b", "#c"]
            platform = "xiaohongshu"
            language = "zh-CN"
            forbidden_hit: list = []
            reference_snippets: list = []

        def fake_copy(inp):
            self.calls.append(inp.product_name)
            return _Copy()

        self.copywriter.generate_copy = fake_copy
        self.image_generator.generate_image = lambda **kw: "data/media/images/stub.png"
        self.video_generator.generate_short_video = lambda **kw: "data/media/videos/stub.mp4"

    def _thread(self, tag: str) -> str:
        # 每次用独立 thread_id，避免复用检查点造成串场
        return f"test-{tag}-{os.getpid()}-{id(self)}"

    def test_empty_product_short_circuits(self):
        """商品名为空：直接失败，不调用大模型、不产出文案。"""
        result = self.graph.run_ad_generation(
            self._thread("empty"), product={}, keyword="", platform="xiaohongshu",
        )
        self.assertEqual(result.get("status"), "failed")
        self.assertEqual(result.get("error"), "未选择商品")
        self.assertEqual(self.calls, [], "选品失败时不应调用大模型")
        self.assertFalse(result.get("copy"), "选品失败时不应产出文案")

    def test_copy_only_skips_assets(self):
        result = self.graph.run_ad_generation(
            self._thread("copy"), product=self.PRODUCT, keyword="",
            platform="xiaohongshu", want_image=False, want_video=False,
        )
        self.assertEqual(result.get("status"), "done")
        self.assertFalse((result.get("image") or {}).get("url"))
        self.assertFalse((result.get("video") or {}).get("url"))

    def test_image_only(self):
        result = self.graph.run_ad_generation(
            self._thread("image"), product=self.PRODUCT, keyword="",
            platform="xiaohongshu", want_image=True, want_video=False,
        )
        self.assertTrue((result.get("image") or {}).get("url"))
        self.assertFalse((result.get("video") or {}).get("url"))

    def test_video_only_without_image(self):
        """只勾视频、不勾图文：应直接生成视频再收口（这条分支容易写错）。"""
        result = self.graph.run_ad_generation(
            self._thread("video"), product=self.PRODUCT, keyword="",
            platform="xiaohongshu", want_image=False, want_video=True,
        )
        self.assertEqual(result.get("status"), "done")
        self.assertFalse((result.get("image") or {}).get("url"))
        self.assertTrue((result.get("video") or {}).get("url"))

    def test_image_then_video(self):
        result = self.graph.run_ad_generation(
            self._thread("both"), product=self.PRODUCT, keyword="",
            platform="xiaohongshu", want_image=True, want_video=True,
        )
        self.assertTrue((result.get("image") or {}).get("url"))
        self.assertTrue((result.get("video") or {}).get("url"))

    def _stub_violating_copy(self) -> None:
        class _Bad:
            title = "全网最好"
            content = "绝对正品"
            tags = ["#a"]
            platform = "xiaohongshu"
            language = "zh-CN"
            forbidden_hit: list = []
            reference_snippets: list = []

        self.copywriter.generate_copy = lambda inp: _Bad()

    def test_violation_pauses_for_human_review(self):
        """命中违禁词应停在 human_review（is_interrupted=True）。"""
        self._stub_violating_copy()
        thread = self._thread("violation")
        result = self.graph.run_ad_generation(
            thread, product=self.PRODUCT, keyword="", platform="xiaohongshu",
        )
        self.assertTrue(self.graph.is_interrupted(result), "命中违禁词应暂停等待人工")
        words = [v.get("word") for v in (result.get("review") or {}).get("violations", [])]
        self.assertIn("最好", words)

        rejected = self.graph.resume_ad_generation(thread, "reject")
        self.assertEqual(rejected.get("status"), "rejected", "驳回后应退出流水线")

    def test_approve_resumes_and_continues(self):
        """人工放行后应从断点继续，并按勾选生成下游素材。"""
        self._stub_violating_copy()
        thread = self._thread("approve")
        self.graph.run_ad_generation(
            thread, product=self.PRODUCT, keyword="", platform="xiaohongshu",
            want_image=True, want_video=False,
        )
        result = self.graph.resume_ad_generation(thread, "approve")
        self.assertEqual(result.get("status"), "done")
        self.assertTrue((result.get("image") or {}).get("url"), "放行后应继续生成图文")


class TestRetrievalThreshold(unittest.TestCase):
    """RAG 降级检索的相似度阈值。

    回归测试：不足 top_k 时**不能拿无关文档补位**。
    实测踩过：查「蓝牙耳机」会把「保温杯」以 score=0.0 塞进参考结果，
    无关参考污染 Prompt，反而拉低生成质量。
    """

    @classmethod
    def setUpClass(cls):
        from app.common import milvus_cli

        cls.cli = milvus_cli
        cls.wrapper = milvus_cli._get_instance()
        cls._orig_docs = list(cls.wrapper._memory_docs)
        # 造一批类目分明的样本。注意 text 里要包含类目词——
        # 真实数据的 text 形如「类目:蓝牙耳机 平台:xiaohongshu 标题:… 正文:…」，
        # TF-IDF 按 token 匹配，类目词必须出现在文本中才能召回。
        cls.wrapper._memory_docs = [
            {"text": "类目:蓝牙耳机 平台:xiaohongshu 标题:耳机实测 正文:百元耳机卷疯了 音质对标千元",
             "platform": "xiaohongshu", "category": "蓝牙耳机", "original_title": "耳机实测"},
            {"text": "类目:水杯 平台:xiaohongshu 标题:保温杯实测 正文:保温杯界的天花板 24小时还烫嘴",
             "platform": "xiaohongshu", "category": "水杯", "original_title": "保温杯实测"},
            {"text": "类目:口红 平台:xiaohongshu 标题:口红实测 正文:黄皮天菜 涂上白两个度",
             "platform": "xiaohongshu", "category": "口红", "original_title": "口红实测"},
        ]

    @classmethod
    def tearDownClass(cls):
        cls.wrapper._memory_docs = cls._orig_docs

    def test_relevant_query_returns_matches(self):
        hits = self.cli.search(query_text="蓝牙耳机", top_k=3,
                               filters={"platform": "xiaohongshu"})
        self.assertTrue(hits, "相关类目应召回结果")
        self.assertTrue(all(h["score"] > 0.05 for h in hits), "命中项应高于阈值")

    def test_no_irrelevant_padding(self):
        """不足 top_k 时不应补无关文档（这是修掉的缺陷）。"""
        hits = self.cli.search(query_text="蓝牙耳机", top_k=3,
                               filters={"platform": "xiaohongshu"})
        titles = [h.get("original_title") for h in hits]
        self.assertNotIn("保温杯实测", titles, "不相关类目不应被塞进参考")
        self.assertNotIn("口红实测", titles)
        self.assertLessEqual(len(hits), 3)

    def test_unrelated_query_returns_empty(self):
        """完全无关的查询应返回空，而不是硬塞文档。"""
        hits = self.cli.search(query_text="zzz完全不相关的查询xyz", top_k=3,
                               filters={"platform": "xiaohongshu"})
        self.assertEqual(hits, [], "无关查询应返回空列表")

    def test_filter_still_applies(self):
        hits = self.cli.search(query_text="实测", top_k=5, filters={"platform": "tiktok"})
        self.assertTrue(all(h.get("platform") == "tiktok" for h in hits))

    def test_fallback_seeds_local_corpus(self):
        """降级检索必须真有数据。

        回归背景：`_memory_docs` 只在「本进程内调过 insert_data」时才有内容，
        应用启动后它是空的——Milvus 一挂，所谓「本地 TF-IDF 降级」直接返回空参考，
        三层降级名不副实。现在改成首次降级时从 processed 语料惰性载入。
        """
        wrapper = self.wrapper
        saved = list(wrapper._memory_docs)
        try:
            wrapper._memory_docs.clear()
            n = wrapper.seed_memory_from_disk()
            self.assertGreater(n, 0, "从本地语料载入的条数应大于 0")
            self.assertEqual(len(wrapper._memory_docs), n)
            # 已有数据时不应重复载入
            self.assertEqual(wrapper.seed_memory_from_disk(), 0, "重复调用应直接返回 0")
        finally:
            wrapper._memory_docs = saved


class TestMetricsIngest(unittest.TestCase):
    """投放效果回流的校验与算式。

    重点：**脏数据必须整条拒收**。部分写入会造成口径不一致，
    比"没有数据"更难排查。
    """

    @classmethod
    def setUpClass(cls):
        from app.common import json_store, metrics_ingest

        cls.mi = metrics_ingest
        cls._orig_dir = _json_store.DATA_DIR
        cls._tmp = tempfile.mkdtemp(prefix="metrics_test_")
        json_store.DATA_DIR = Path(cls._tmp)
        (Path(cls._tmp) / "metrics").mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        from app.common import json_store

        _json_store.DATA_DIR = cls._orig_dir
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        from app.common import ad_store, json_store

        # 重置素材与指标文件
        json_store.write_json("metrics/daily.json", [])
        ad_store._ADS = []
        self.ad = ad_store.add_ad(
            ad_type="copy", title="测试素材", content="内容",
            platform="xiaohongshu", stage="delivered", copy_ok=True,
        )

    def _payload(self, **kw):
        base = {"ad_id": self.ad["id"], "platform": "xiaohongshu",
                "date": "2026-10-20", "impressions": 1000, "clicks": 100,
                "conversions": 10, "spend": 50.0}
        base.update(kw)
        return base

    def test_valid_record_and_rates(self):
        out = self.mi.record(self._payload())
        self.assertEqual(out["impressions"], 1000)
        self.assertEqual(out["ctr"], 0.1)          # 100/1000
        self.assertEqual(out["cvr"], 0.1)          # 10/100
        self.assertEqual(out["cpa"], 5.0)          # 50/10
        self.assertEqual(out["cpm"], 50.0)         # 50/1000*1000

    def test_same_day_overwrites_not_accumulates(self):
        """同 (ad, platform, date) 重推必须覆盖，否则重试会把曝光翻倍。"""
        self.mi.record(self._payload(impressions=1000, clicks=100))
        out = self.mi.record(self._payload(impressions=1000, clicks=100))
        self.assertTrue(out["replaced"])
        self.assertEqual(out["impressions"], 1000)

    def test_multi_day_accumulates(self):
        self.mi.record(self._payload(date="2026-10-19", impressions=500, clicks=50, conversions=5, spend=20.0))
        out = self.mi.record(self._payload(date="2026-10-20"))
        self.assertEqual(out["impressions"], 1500)
        self.assertEqual(out["days"], 2)

    def test_rejects_clicks_over_impressions(self):
        with self.assertRaises(self.mi.MetricsError):
            self.mi.validate(self._payload(impressions=100, clicks=200))

    def test_rejects_conversions_over_clicks(self):
        with self.assertRaises(self.mi.MetricsError):
            self.mi.validate(self._payload(clicks=100, conversions=200))

    def test_rejects_negative(self):
        with self.assertRaises(self.mi.MetricsError):
            self.mi.validate(self._payload(impressions=-1))

    def test_rejects_fractional_count(self):
        with self.assertRaises(self.mi.MetricsError):
            self.mi.validate(self._payload(impressions=10.5))

    def test_rejects_future_date(self):
        future = (date.today() + timedelta(days=5)).isoformat()
        with self.assertRaises(self.mi.MetricsError):
            self.mi.validate(self._payload(date=future))

    def test_rejects_bad_date_format(self):
        with self.assertRaises(self.mi.MetricsError):
            self.mi.validate(self._payload(date="2026/10/20"))

    def test_rejects_unknown_ad(self):
        with self.assertRaises(self.mi.MetricsError):
            self.mi.record({"ad_id": "NOPE", "impressions": 1})

    def test_batch_rejects_whole_batch_on_one_bad(self):
        good = self._payload()
        bad = self._payload(impressions=10, clicks=99)
        with self.assertRaises(self.mi.MetricsError):
            self.mi.record_batch([good, bad])
        # 整批拒收 → 文件里不应有任何记录
        self.assertEqual(self.mi.list_rows(), [])

    def test_batch_rejects_intra_batch_duplicate(self):
        with self.assertRaises(self.mi.MetricsError):
            self.mi.record_batch([self._payload(), self._payload()])

    def test_sync_writes_back_to_ad(self):
        self.mi.record(self._payload())
        result = self.mi.sync_to_ads()
        self.assertGreaterEqual(result["ads_updated"], 1)
        from app.common import ad_store

        ad = ad_store.get_ad(self.ad["id"])
        self.assertEqual(ad["impressions"], 1000)
        self.assertEqual(ad["clicks"], 100)


class TestDocIngest(unittest.TestCase):
    """文档解析与切块。

    重点：**块不能超过 token 上限**、**相邻块必须有重叠**、**不能有替换字符**。
    这三点任一失效，入库语料就是脏的，且检索质量会静默下降。
    """

    @classmethod
    def setUpClass(cls):
        from app.common import doc_ingest

        cls.di = doc_ingest
        cls._tmp = tempfile.mkdtemp(prefix="docingest_")
        cls.tmp = Path(cls._tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _write(self, name: str, text: str) -> Path:
        p = self.tmp / name
        p.write_text(text, encoding="utf-8")
        return p

    def _long_text(self, sections: int = 12) -> str:
        parts = ["# 运营手册"]
        for i in range(1, sections + 1):
            parts.append(f"## 第 {i} 节\n这一节讲投放要点 {i}。选品看类目竞争度，"
                         f"文案匹配平台调性，预算小步试错。\n注意：不得使用绝对化用语，"
                         f"不得宣称医疗功效，不得伪造资质，违规会被限流。")
        return "\n\n".join(parts)

    def test_parse_txt_with_bom(self):
        """带 BOM 的 UTF-8 不能把 \\ufeff 留在正文里。"""
        p = self.tmp / "bom.txt"
        p.write_bytes("\ufeff第一章 规范\n内容".encode("utf-8"))
        doc = self.di.parse_document(p, prefer_mineru=False)
        self.assertFalse(doc.text.startswith("\ufeff"))
        self.assertIn("第一章", doc.text)

    def test_parse_csv_flattens_rows(self):
        p = self._write("s.csv", "platform,category,title\nxiaohongshu,水杯,杯子实录\n")
        doc = self.di.parse_document(p, prefer_mineru=False)
        self.assertIn("platform:xiaohongshu", doc.text)
        self.assertIn("category:水杯", doc.text)

    def test_parse_docx_includes_table(self):
        import docx

        p = self.tmp / "t.docx"
        d = docx.Document()
        d.add_paragraph("正文段落")
        t = d.add_table(rows=1, cols=2)
        t.cell(0, 0).text = "平台"
        t.cell(0, 1).text = "抖音"
        d.save(p)
        doc = self.di.parse_document(p, prefer_mineru=False)
        self.assertEqual(doc.parser, "python-docx")
        self.assertIn("正文段落", doc.text)
        self.assertIn("平台", doc.text)     # 表格内容不能丢

    def test_unsupported_ext_rejected(self):
        p = self._write("x.exe", "binary")
        with self.assertRaises(self.di.DocParseError):
            self.di.parse_document(p)

    def test_short_text_not_split(self):
        chunks = self.di.chunk_text("一句话文案，不该被切开。", chunk_size=512)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].index, 0)

    def test_chunks_respect_token_limit(self):
        for size in (256, 512):
            chunks = self.di.chunk_text(self._long_text(), chunk_size=size, overlap=size // 10)
            self.assertGreater(len(chunks), 1)
            self.assertTrue(all(c.tokens <= size for c in chunks),
                            f"有块超过 {size} token: {[c.tokens for c in chunks]}")

    def test_chunks_have_overlap(self):
        chunks = self.di.chunk_text(self._long_text(), chunk_size=256, overlap=25)
        self.assertGreater(len(chunks), 2)
        for i in range(len(chunks) - 1):
            a, b = chunks[i].text, chunks[i + 1].text
            best = 0
            for k in range(1, min(120, len(a), len(b)) + 1):
                if a.endswith(b[:k]):
                    best = k
            self.assertGreater(best, 5, f"块{i}→{i+1} 没有重叠")

    def test_no_replacement_chars(self):
        """tiktoken 在 token 边界切多字节汉字会产出 \\ufffd，必须清掉。"""
        chunks = self.di.chunk_text(self._long_text(), chunk_size=256, overlap=25)
        bad = [c.text[:40] for c in chunks if "\ufffd" in c.text]
        self.assertFalse(bad, f"含替换字符的块: {bad}")

    def test_overlap_must_be_less_than_chunk(self):
        with self.assertRaises(ValueError):
            self.di.chunk_text(self._long_text(), chunk_size=100, overlap=100)

    def test_chunk_index_sequential(self):
        chunks = self.di.chunk_text(self._long_text(), chunk_size=256, overlap=25)
        self.assertEqual([c.index for c in chunks], list(range(len(chunks))))
        self.assertTrue(all(c.meta.get("chunk_total") == len(chunks) for c in chunks))

    def test_ingest_dry_run_reports_stats(self):
        p = self._write("long.md", self._long_text())
        r = self.di.ingest_file(p, platform="douyin", category="规范", dry_run=True)
        self.assertEqual(r["chunks"], len(r["preview"]) if r["chunks"] <= 5 else r["chunks"])
        self.assertLessEqual(r["max_chunk_tokens"], r["chunk_size"])
        self.assertEqual(r["parser"], "text")


class TestImageVector(unittest.TestCase):
    """图片向量化与视觉检索。"""

    @classmethod
    def setUpClass(cls):
        from app.common import image_vector

        cls.iv = image_vector
        cls._tmp = tempfile.mkdtemp(prefix="imgvec_")
        cls.tmp = Path(cls._tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _img(self, name: str, color: tuple, stripe: bool = False) -> Path:
        from PIL import Image, ImageDraw

        p = self.tmp / name
        img = Image.new("RGB", (120, 120), color)
        d = ImageDraw.Draw(img)
        if stripe:
            for y in range(0, 120, 15):
                d.rectangle([0, y, 120, y + 7], fill=(255, 255, 255))
        d.ellipse([40, 40, 80, 80], fill=(255, 255, 255))
        img.save(p)
        return p

    def test_dimension_and_norm(self):
        vec, provider = self.iv.visual_vector(self._img("a.png", (200, 30, 30)))
        self.assertEqual(len(vec), self.iv.DIM)
        self.assertAlmostEqual(sum(x * x for x in vec) ** 0.5, 1.0, places=6)
        self.assertIn(provider, ("hist", "clip"))

    def test_same_color_closer_than_different(self):
        red = self.iv.visual_vector(self._img("r.png", (200, 30, 30)))[0]
        red2 = self.iv.visual_vector(self._img("r2.png", (200, 30, 30), stripe=True))[0]
        blue = self.iv.visual_vector(self._img("b.png", (30, 60, 200)))[0]
        sim_same = sum(a * b for a, b in zip(red, red2))
        sim_diff = sum(a * b for a, b in zip(red, blue))
        self.assertGreater(sim_same, sim_diff,
                           f"同色 {sim_same:.3f} 应大于异色 {sim_diff:.3f}")

    def test_dhash_stable_and_distinct(self):
        p1 = self._img("h1.png", (200, 30, 30))
        p2 = self._img("h2.png", (200, 30, 30))          # 同图
        p3 = self._img("h3.png", (200, 30, 30), stripe=True)
        h1 = self.iv._dhash(self.iv._load_image(p1))
        h2 = self.iv._dhash(self.iv._load_image(p2))
        h3 = self.iv._dhash(self.iv._load_image(p3))
        self.assertEqual(h1, h2, "同图哈希应一致")
        self.assertNotEqual(h1, h3, "不同纹理哈希应不同")

    def test_insert_and_search(self):
        for i, color in enumerate([(200, 30, 30), (30, 60, 200), (30, 180, 90)]):
            self.iv.insert_image(self._img(f"i{i}.png", color), platform="xiaohongshu",
                                 category="测试", title=f"i{i}.png")
        res = self.iv.search_similar_image(self._img("q.png", (200, 30, 30)), top_k=3)
        self.assertTrue(res["hits"])
        self.assertIn(res["hits"][0]["title"], ("i0.png", "q.png"))

    def test_reject_unsupported(self):
        p = self.tmp / "x.txt"
        p.write_text("not an image", encoding="utf-8")
        with self.assertRaises(self.iv.ImageVectorError):
            self.iv.visual_vector(p)


class TestTokenUsage(unittest.TestCase):
    """Token 账本：追加写 + 按日/按月聚合。"""

    @classmethod
    def setUpClass(cls):
        from app.common import json_store, token_usage

        cls.tu = token_usage
        cls._orig_dir = _json_store.DATA_DIR
        cls._tmp = tempfile.mkdtemp(prefix="token_")
        json_store.DATA_DIR = Path(cls._tmp)

    @classmethod
    def tearDownClass(cls):
        from app.common import json_store

        _json_store.DATA_DIR = cls._orig_dir
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        self.tu.reset()

    def test_record_and_aggregate(self):
        from datetime import datetime, timedelta

        now = datetime.now()
        self.tu.record(model="m1", prompt_tokens=800, completion_tokens=200, at=now)
        self.tu.record(model="m1", prompt_tokens=400, completion_tokens=100,
                       at=now - timedelta(days=1))
        self.tu.record(model="m2", prompt_tokens=100, completion_tokens=0,
                       at=now - timedelta(days=40))

        today = self.tu.summary("today")
        self.assertEqual(today["overall"]["calls"], 1)
        self.assertEqual(today["overall"]["total_tokens"], 1000)

        month = self.tu.summary("month")
        self.assertGreaterEqual(month["overall"]["calls"], 1)
        self.assertLessEqual(month["overall"]["calls"], 3)

        allp = self.tu.summary("all")
        self.assertEqual(allp["overall"]["calls"], 3)
        # 1000 (800+200) + 500 (400+100) + 100 (100+0)
        self.assertEqual(allp["overall"]["total_tokens"], 1600)
        self.assertGreater(allp["overall"]["cost_estimate"], 0)
        self.assertEqual(len(allp["by_model"]), 2)

    def test_cost_math(self):
        row = self.tu.record(model="m", prompt_tokens=1000, completion_tokens=1000)
        expect = 1000 / 1000 * self.tu.PRICE_INPUT_PER_1K + \
                 1000 / 1000 * self.tu.PRICE_OUTPUT_PER_1K
        self.assertAlmostEqual(row["cost"], round(expect, 6), places=6)

    def test_negative_tokens_clamped(self):
        row = self.tu.record(model="m", prompt_tokens=-5, completion_tokens=-9)
        self.assertEqual(row["prompt_tokens"], 0)
        self.assertEqual(row["completion_tokens"], 0)

    def test_persists_to_disk(self):
        self.tu.record(model="m", prompt_tokens=10, completion_tokens=5)
        self.assertTrue(self.tu._path().exists())
        self.assertEqual(len(self.tu.load_all()), 1)

    def test_bad_lines_skipped(self):
        p = self.tu._path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write("not-json\n")
        self.tu.record(model="m", prompt_tokens=1, completion_tokens=1)
        self.assertEqual(len(self.tu.load_all()), 1)   # 坏行被跳过

    def test_projection_fields(self):
        self.tu.record(model="m", prompt_tokens=500, completion_tokens=500)
        s = self.tu.summary("all")
        self.assertIn("daily_avg_tokens", s)
        self.assertIn("projected_30d_tokens", s)
        self.assertGreaterEqual(s["projected_30d_tokens"], 0)


class TestRerank(unittest.TestCase):
    """Cross-Encoder 精排：失败必须 fail-open，不能把检索拖挂。"""

    def test_fail_open_when_model_missing(self):
        """模型目录不存在时保持召回顺序，而不是抛异常。"""
        from pathlib import Path as _P

        from app.common import rerank as rk

        saved = (rk._MODEL_DIR, rk._load_tried, rk._model, rk._load_error)
        try:
            rk._MODEL_DIR = _P("/nonexistent/rerank-model")
            rk._load_tried = False
            rk._model = None
            rk._load_error = None
            rk._ENABLED = True

            docs = [{"text": "第一条"}, {"text": "第二条"}, {"text": "第三条"}]
            out = rk.rerank("查询", docs, top_k=2)
            self.assertFalse(out["reranked"], "模型缺失时不应声称精排过")
            self.assertEqual(len(out["docs"]), 2)
            self.assertEqual([d["text"] for d in out["docs"]], ["第一条", "第二条"])
        finally:
            rk._MODEL_DIR, rk._load_tried, rk._model, rk._load_error = saved

    def test_empty_docs_short_circuit(self):
        from app.common import rerank as rk

        out = rk.rerank("查询", [], top_k=3)
        self.assertEqual(out["docs"], [])
        self.assertFalse(out["reranked"])

    def test_available_requires_all_files(self):
        from pathlib import Path as _P

        from app.common import rerank as rk

        saved = (rk._MODEL_DIR, rk._ENABLED)
        try:
            rk._ENABLED = True
            rk._MODEL_DIR = _P(self.id())          # 不存在的目录
            self.assertFalse(rk.available())
        finally:
            rk._MODEL_DIR, rk._ENABLED = saved

    def test_status_shape(self):
        from app.common import rerank as rk

        st = rk.status()
        for key in ("enabled", "model_dir", "available", "loaded"):
            self.assertIn(key, st)

    @unittest.skipUnless(
        __import__("app.common.rerank", fromlist=["x"]).available(),
        "Reranker 模型未就绪（需先跑 scripts/download_reranker.py）",
    )
    def test_real_model_puts_relevant_first(self):
        """真模型：同品类文案应排到无关品类前面。"""
        from app.common import rerank as rk

        docs = [
            {"text": "黄皮天菜，涂上立马白两个度", "category": "口红"},
            {"text": "这款水杯保温 12 小时，容量 500ml，办公室必备", "category": "水杯"},
            {"text": "地铁上开降噪，整个世界都安静了", "category": "蓝牙耳机"},
        ]
        out = rk.rerank("水杯", docs, top_k=3)
        self.assertTrue(out["reranked"])
        self.assertEqual(out["docs"][0]["category"], "水杯")
        self.assertGreater(out["docs"][0]["rerank_score"],
                           out["docs"][1]["rerank_score"])


class TestRelevanceJudge(unittest.TestCase):
    """LLM 相关性复核（拒识）。用桩替换 LLM，不产生真实调用。"""

    @classmethod
    def setUpClass(cls):
        from app.agent import copywriter as cw

        cls.cw = cw
        cls._orig = cw._call_llm

    @classmethod
    def tearDownClass(cls):
        cls.cw._call_llm = cls._orig

    def setUp(self):
        self.refs = [
            {"original_title": "杯子", "text": "水杯实测", "category": "水杯", "platform": "x"},
            {"original_title": "耳机", "text": "耳机实测", "category": "耳机", "platform": "x"},
        ]

    def tearDown(self):
        self.cw._call_llm = self._orig
        os.environ.pop("RAG_LLM_JUDGE", None)

    def test_keeps_relevant(self):
        self.cw._call_llm = lambda m, temperature=0.7: '{"relevant": [0]}'
        v = self.cw.judge_relevance("水杯", self.refs)
        self.assertTrue(v["judged"])
        self.assertEqual(len(v["kept"]), 1)
        self.assertEqual(len(v["dropped"]), 1)

    def test_tolerates_markdown_wrapper(self):
        self.cw._call_llm = lambda m, temperature=0.7: '```json\n{"relevant": [0,1]}\n```'
        v = self.cw.judge_relevance("水杯", self.refs)
        self.assertTrue(v["judged"])
        self.assertEqual(len(v["kept"]), 2)

    def test_all_irrelevant_means_reject(self):
        self.cw._call_llm = lambda m, temperature=0.7: '{"relevant": []}'
        v = self.cw.judge_relevance("水杯", self.refs)
        self.assertTrue(v["judged"])
        self.assertEqual(len(v["kept"]), 0)

    def test_llm_failure_keeps_all(self):
        """复核失败不能把正常召回归零。"""

        def boom(m, temperature=0.7):
            raise RuntimeError("boom")

        self.cw._call_llm = boom
        v = self.cw.judge_relevance("水杯", self.refs)
        self.assertFalse(v["judged"])
        self.assertEqual(len(v["kept"]), 2)

    def test_switch_off(self):
        os.environ["RAG_LLM_JUDGE"] = "0"
        self.cw._call_llm = lambda m, temperature=0.7: '{"relevant": []}'
        v = self.cw.judge_relevance("水杯", self.refs)
        self.assertFalse(v["judged"])
        self.assertEqual(len(v["kept"]), 2)

    def test_empty_refs_short_circuit(self):
        v = self.cw.judge_relevance("水杯", [])
        self.assertFalse(v["judged"])
        self.assertEqual(v["kept"], [])


class TestDiagnoseAgent(unittest.TestCase):
    """素材诊断 Agent：LLM 工具调用循环 + 规则兜底。

    这是项目里唯一一处「模型自己决定调哪个工具」的地方，所以重点测三件事：
    工具只读可调用、轮次有上限、LLM 挂了必须退化成规则诊断而不是报错。
    """

    def setUp(self):
        from app.agent import diagnose_agent as da

        self.da = da
        self._orig_llm = da._call_llm_with_tools
        self._orig_detail = da._tool_get_ad_detail
        self._orig_baseline = da._tool_get_platform_baseline
        # 固定素材，避免依赖真实数据
        self.ad = {
            "ad_id": "AD-TEST", "title": "测试素材", "platform": "xiaohongshu",
            "type": "copy", "stage": "review", "status": "published",
            "budget": None, "impressions": 22000, "clicks": 740,
            "conversions": 78, "spend": 378.5, "ctr": 0.0336, "cvr": 0.1054,
            "violations": [], "copy_ok": True, "budget_ok": True,
        }
        da._tool_get_ad_detail = lambda ad_id="": dict(self.ad)

    def tearDown(self):
        self.da._call_llm_with_tools = self._orig_llm
        self.da._tool_get_ad_detail = self._orig_detail
        self.da._tool_get_platform_baseline = self._orig_baseline

    def _baseline(self, ctr=0.05):
        return {
            "basis": "platform", "platform": "xiaohongshu",
            "platform_baseline": {"ctr": ctr, "cvr": 0.1, "cpa": 5.0,
                                  "impressions": 100000, "rows": 20},
            "overall": {"ctr": ctr}, "available_platforms": ["xiaohongshu"],
        }

    # ---------- 规则兜底 ----------

    def test_rule_fallback_low_sample(self):
        self.ad["impressions"] = 500
        self.da._tool_get_platform_baseline = lambda platform="": self._baseline()
        out = self.da.rule_based_diagnosis("AD-TEST")
        self.assertTrue(out["fallback"])
        self.assertIn("样本不足", out["diagnosis"])

    def test_rule_fallback_detects_low_ctr(self):
        self.ad["ctr"] = 0.01          # 1% vs 基线 5% → 明显偏低
        self.da._tool_get_platform_baseline = lambda platform="": self._baseline(0.05)
        out = self.da.rule_based_diagnosis("AD-TEST")
        self.assertIn("偏低", out["diagnosis"])

    def test_rule_fallback_normal(self):
        self.ad["ctr"] = 0.049
        self.da._tool_get_platform_baseline = lambda platform="": self._baseline(0.05)
        out = self.da.rule_based_diagnosis("AD-TEST")
        self.assertIn("正常", out["diagnosis"])

    # ---------- LLM 工具调用循环 ----------

    def test_llm_executes_tool_calls_then_answers(self):
        seq = [
            {"content": "", "tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": "get_ad_detail", "arguments": '{"ad_id": "AD-TEST"}'}}]},
            {"content": "结论：正常", "tool_calls": []},
        ]
        self.da._call_llm_with_tools = lambda m, t: seq.pop(0)
        out = self.da.diagnose("AD-TEST")
        self.assertFalse(out["fallback"])
        self.assertEqual(len(out["tool_calls"]), 1)
        self.assertEqual(out["tool_calls"][0]["name"], "get_ad_detail")
        self.assertIn("正常", out["diagnosis"])

    def test_llm_max_steps_truncates(self):
        """模型一直调工具不答话 → 必须截断，不能无限循环。"""
        self.da._call_llm_with_tools = lambda m, t: {
            "content": "", "tool_calls": [{
                "id": "c", "type": "function",
                "function": {"name": "get_ad_detail", "arguments": '{"ad_id": "AD-TEST"}'}}]}
        out = self.da.diagnose("AD-TEST", max_steps=2)
        self.assertEqual(len(out["tool_calls"]), 2)
        self.assertTrue(out.get("truncated"))

    def test_llm_failure_falls_back_to_rule(self):
        def boom(m, t):
            raise RuntimeError("模拟 LLM 故障")
        self.da._call_llm_with_tools = boom
        self.da._tool_get_platform_baseline = lambda platform="": self._baseline()
        out = self.da.diagnose("AD-TEST")
        self.assertTrue(out["fallback"])
        self.assertIn("error", out)

    def test_unknown_tool_is_recorded_not_fatal(self):
        seq = [
            {"content": "", "tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": "delete_everything", "arguments": "{}"}}]},
            {"content": "结论：无法诊断", "tool_calls": []},
        ]
        self.da._call_llm_with_tools = lambda m, t: seq.pop(0)
        out = self.da.diagnose("AD-TEST")
        self.assertEqual(out["tool_calls"][0]["name"], "delete_everything")
        self.assertFalse(out["tool_calls"][0]["ok"])   # 被拒
        self.assertFalse(out["fallback"])

    def test_missing_ad_short_circuits(self):
        self.da._tool_get_ad_detail = lambda ad_id="": {"error": f"未找到素材 {ad_id}"}
        out = self.da.diagnose("AD-NOT-EXIST")
        self.assertIn("无法诊断", out["diagnosis"])
        self.assertEqual(out["tool_calls"], [])

    def test_empty_ad_id_rejected(self):
        with self.assertRaises(ValueError):
            self.da.diagnose("")

    # ---------- 契约 ----------

    def test_tools_are_read_only(self):
        """所有工具名必须是查询语义，不能出现写操作。"""
        names = [t["function"]["name"] for t in self.da.TOOLS_SCHEMA]
        for n in names:
            self.assertTrue(n.startswith("get_"), f"{n} 不是只读语义")
            for bad in ("create", "update", "delete", "submit", "deliver"):
                self.assertNotIn(bad, n)
        self.assertEqual(len(names), len(set(names)))

    def test_tool_impl_matches_schema(self):
        names = {t["function"]["name"] for t in self.da.TOOLS_SCHEMA}
        self.assertEqual(names, set(self.da._TOOL_IMPL.keys()))


class TestApprovalPersistence(unittest.TestCase):
    """审批单持久化（路径 B：task_engine + Supervisor）。

    回归背景：APPROVALS 原来是纯进程内存，服务一重启待审批单全丢——
    列表变空、点通过返 404。审批是这条链路上唯一需要跨小时等待的状态，
    最不能丢。现在落盘恢复，重启前 pending 的标 orphaned。
    """

    def setUp(self):
        from app.common import json_store, task_engine

        self.js = json_store
        self.te = task_engine
        json_store.use_memory("approvals.json", {})
        self.te.APPROVALS.clear()
        self.te._APPROVALS_LOADED = False

    def tearDown(self):
        from app.common import json_store

        self.te.APPROVALS.clear()
        self.te._APPROVALS_LOADED = False
        json_store.clear_memory()

    def _seed(self, items):
        self.js.write_json("approvals.json", items)
        return self.te.load_approvals(force=True)

    def test_pending_becomes_orphaned(self):
        """重启前 pending → 恢复后 orphaned，并写明原因。"""
        r = self._seed({
            "a1": {"id": "a1", "task_id": "t1", "type": "copy",
                   "status": "pending", "created_at": "2026-11-05T10:00:00",
                   "versions": []},
        })
        self.assertEqual(r["loaded"], 1)
        self.assertEqual(r["orphaned"], 1)
        a = self.te.APPROVALS["a1"]
        self.assertEqual(a["status"], "orphaned")
        self.assertIn("重启", a["orphan_reason"])
        self.assertTrue(a["orphaned_at"])

    def test_processed_status_preserved(self):
        """已处理的不该被改写。"""
        self._seed({
            "a2": {"id": "a2", "task_id": "t2", "type": "ads",
                   "status": "approved", "created_at": "2026-11-05T09:00:00",
                   "versions": []},
        })
        self.assertEqual(self.te.APPROVALS["a2"]["status"], "approved")
        self.assertNotIn("orphan_reason", self.te.APPROVALS["a2"])

    def test_orphaned_approval_returns_409(self):
        """对孤儿单点通过要明确说原因，不能 404 也不能静默成功。"""
        import asyncio

        self._seed({
            "a1": {"id": "a1", "task_id": "t1", "type": "copy",
                   "status": "pending", "created_at": "2026-11-05T10:00:00",
                   "versions": []},
        })
        with self.assertRaises(self.te.EngineError) as ctx:
            asyncio.run(self.te.resolve_approval("a1", "approve"))
        self.assertEqual(ctx.exception.status, 409)
        self.assertIn("重启", ctx.exception.msg)

    def test_missing_approval_still_404(self):
        with self.assertRaises(self.te.EngineError) as ctx:
            self.te.get_approval("nope")
        self.assertEqual(ctx.exception.status, 404)

    def test_list_filters(self):
        self._seed({
            "a1": {"id": "a1", "task_id": "t1", "type": "copy", "status": "pending",
                   "created_at": "2026-11-05T10:00:00", "versions": []},
            "a2": {"id": "a2", "task_id": "t2", "type": "ads", "status": "approved",
                   "created_at": "2026-11-05T09:00:00", "versions": []},
        })
        self.assertEqual(len(self.te.list_approvals(status="pending")), 0)
        self.assertEqual(len(self.te.list_approvals(status="orphaned")), 1)
        # processed 只算真正处理过的，孤儿单不算
        self.assertEqual(len(self.te.list_approvals(status="processed")), 1)
        self.assertEqual(len(self.te.list_approvals()), 2)

    def test_persist_round_trip(self):
        """写入后重新载入，内容应保持一致（不含 pending→orphaned 转换）。"""
        self._seed({
            "a3": {"id": "a3", "task_id": "t3", "type": "copy",
                   "status": "rejected", "created_at": "2026-11-05T08:00:00",
                   "versions": [{"version": 1, "content": "旧"}]},
        })
        r = self.te.load_approvals(force=True)
        self.assertEqual(r["loaded"], 1)
        self.assertEqual(r["orphaned"], 0)
        self.assertEqual(self.te.APPROVALS["a3"]["versions"][0]["content"], "旧")

    def test_orphaned_is_written_back_to_disk(self):
        """转 orphaned 后必须回写，否则下次启动又变回 pending。"""
        self._seed({
            "a1": {"id": "a1", "task_id": "t1", "type": "copy",
                   "status": "pending", "created_at": "2026-11-05T10:00:00",
                   "versions": []},
        })
        on_disk = self.js.read_dict("approvals.json", {})
        self.assertEqual(on_disk["a1"]["status"], "orphaned")


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ---------------------------------------------------------------------------
# 投放：能力标注 / 未接入平台必须拒绝 / 幂等 / 停投
# ---------------------------------------------------------------------------
class TestDeliveryCapability(unittest.TestCase):
    """投放页「不再编数」的回归测试。

    背景：原实现给四个平台统一返回「随机 95% 成功 + 随机触达 2000~50000」的模拟回执，
    既承认是 mock 又编了数据。这里锁死三件事：

        1. 未接入平台（reserved）→ 后端必须 501，不能悄悄返回成功
        2. 千川投放记录里不得出现随机触达数
        3. 同一物料重复投放必须跳过（避免重复建真实计划、重复扣费）
    """

    @classmethod
    def setUpClass(cls):
        import os as _os
        from pathlib import Path as _Path

        cls._orig_dir = _json_store.DATA_DIR
        cls._tmp = tempfile.mkdtemp(prefix="dsh_delivery_")
        _json_store.DATA_DIR = _Path(cls._tmp)
        # token 落盘也指向临时目录，别污染真实 data/
        cls._orig_token_file = _os.environ.get("QIANCHUAN_TOKEN_FILE")
        _os.environ["QIANCHUAN_TOKEN_FILE"] = str(_Path(cls._tmp) / "qianchuan_token.json")

        # mcp_servers 是仓库根下的包，测试只把 backend 加进了 sys.path
        if str(_ROOT) not in sys.path:
            sys.path.insert(0, str(_ROOT))

        from app.api import delivery_router as dr
        from app.common import ad_store, store_store

        cls.dr = dr
        cls.ad_store = ad_store
        cls.store_store = store_store
        # 清空模块级缓存，避免继承别的测试留下的批次
        dr._DELIVERIES.clear()

    @classmethod
    def tearDownClass(cls):
        import os as _os

        _json_store.DATA_DIR = cls._orig_dir
        if cls._orig_token_file is None:
            _os.environ.pop("QIANCHUAN_TOKEN_FILE", None)
        else:
            _os.environ["QIANCHUAN_TOKEN_FILE"] = cls._orig_token_file
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        self.dr._DELIVERIES.clear()
        # 登记一个千川店铺，并准备一条「已审批通过」的物料
        self.store_store.add_store("qianchuan", {
            "shop_id": "S-DEMO-1", "aweme_id": "AW-DEMO-1",
            "advertiser_id": "1800000001",
            "products": [{"product_id": "P-DEMO-1", "name": "测试商品"}],
        })
        self.ad = self.ad_store.add_ad(
            ad_type="copy", title="测试物料-投放", platform="xiaohongshu",
            stage="approved", dedupe=False,
        )

    # ---------- 能力标注 ----------
    def test_reserved_platform_has_capability_flag(self):
        """四个平台必须都带 capability，且只有千川是 real_ready。"""
        caps = {p["value"]: p["capability"] for p in self.dr.DELIVERY_PLATFORMS}
        self.assertEqual(caps["qianchuan"], "real_ready")
        for key in ("juguang", "alimama", "duoduo"):
            self.assertEqual(caps[key], "reserved", f"{key} 应为预留位")

    def test_placeholder_token_is_not_authorized(self):
        """`***` 占位符不能被当成已授权。

        坑：`bool("***")` 是 True，会把「未配置」误报成「已授权」。
        必须用 `is_real()` 判断。
        """
        from app.agent import douyin_delivery_agent as agent

        old = os.environ.get("QIANCHUAN_ACCESS_TOKEN")
        try:
            os.environ["QIANCHUAN_ACCESS_TOKEN"] = "***"
            self.assertFalse(agent.capability()["authorized"])
        finally:
            if old is None:
                os.environ.pop("QIANCHUAN_ACCESS_TOKEN", None)
            else:
                os.environ["QIANCHUAN_ACCESS_TOKEN"] = old

    # ---------- 未接入平台 ----------
    def test_reserved_platform_submit_returns_501(self):
        """投到阿里妈妈必须 501，而不是返回一个假的成功回执。"""
        import asyncio

        resp = asyncio.run(self.dr.submit_delivery({
            "platform": "alimama", "ad_ids": [self.ad["id"]], "budget": 5000,
        }))
        self.assertEqual(resp.status_code, 501)
        self.assertIn("尚未接入", json.loads(resp.body.decode("utf-8"))["msg"])

    # ---------- 千川投放 ----------
    def test_qianchuan_submit_has_no_random_reach(self):
        """投放记录里不得有编造的触达数，且要记下真实模式。"""
        import asyncio

        resp = asyncio.run(self.dr.submit_delivery({
            "platform": "qianchuan", "ad_ids": [self.ad["id"]], "budget": 5000,
        }))
        # 失败路径返回 JSONResponse（有 status_code），成功路径返回 ok() 包装的 dict
        self.assertFalse(hasattr(resp, "status_code"), "正常投放不应走错误分支")
        data = resp["data"]
        self.assertEqual(data["platform"], "qianchuan")
        item = data["items"][0]
        self.assertIn("reach", item)
        self.assertIsNone(item["reach"], "触达数必须为 None，不能是随机数")
        self.assertEqual(item["status"], "success")
        self.assertTrue(item["ad_promotion_id"])
        self.assertIn(data["mode"], ("mock", "real"))

    def test_submit_is_idempotent(self):
        """同一物料重复投放必须跳过，不能建第二个计划。"""
        import asyncio

        first = asyncio.run(self.dr.submit_delivery({
            "platform": "qianchuan", "ad_ids": [self.ad["id"]], "budget": 5000,
        }))
        first_data = first["data"]
        plan_id = first_data["items"][0]["ad_promotion_id"]

        second = asyncio.run(self.dr.submit_delivery({
            "platform": "qianchuan", "ad_ids": [self.ad["id"]], "budget": 5000,
        }))
        second_data = second["data"]
        item = second_data["items"][0]
        self.assertEqual(item["status"], "skipped")
        self.assertEqual(item["ad_promotion_id"], plan_id)
        self.assertEqual(second_data["success_count"], 0)

    # ---------- 停投 ----------
    def test_stop_without_plan_id_returns_400(self):
        """没投过的物料不能关停（没有计划 ID 可停）。"""
        import asyncio

        resp = asyncio.run(self.dr.stop_delivery({"ad_id": self.ad["id"]}))
        self.assertEqual(resp.status_code, 400)

    def test_stop_with_plan_id_succeeds(self):
        """投过的物料可以关停——这是真实投放的安全网。"""
        import asyncio

        asyncio.run(self.dr.submit_delivery({
            "platform": "qianchuan", "ad_ids": [self.ad["id"]], "budget": 5000,
        }))
        resp = asyncio.run(self.dr.stop_delivery({"ad_id": self.ad["id"]}))
        data = resp["data"]
        self.assertTrue(data["stopped"])
        self.assertTrue(data["ad_promotion_id"])


class TestQianchuanTokenStore(unittest.TestCase):
    """token 持久化与续期判定。

    原来 `refresh_access_token()` 拿到新 token 后没人保存，等于续了个寂寞。
    """

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="dsh_qc_token_")
        self._orig = os.environ.get("QIANCHUAN_TOKEN_FILE")
        os.environ["QIANCHUAN_TOKEN_FILE"] = str(Path(self._tmp) / "token.json")
        if str(_ROOT) not in sys.path:
            sys.path.insert(0, str(_ROOT))
        from mcp_servers.douyin import token_store

        self.ts = token_store

    def tearDown(self):
        if self._orig is None:
            os.environ.pop("QIANCHUAN_TOKEN_FILE", None)
        else:
            os.environ["QIANCHUAN_TOKEN_FILE"] = self._orig
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_save_and_load_roundtrip(self):
        self.ts.save("at-123", "rt-456", expires_in=3600, advertiser_id="1800", source="oauth")
        doc = self.ts.load()
        self.assertEqual(doc["access_token"], "at-123")
        self.assertEqual(doc["refresh_token"], "rt-456")
        self.assertEqual(doc["advertiser_id"], "1800")
        self.assertEqual(doc["source"], "oauth")

    def test_needs_refresh_when_almost_expired(self):
        self.ts.save("at-x", "rt-x", expires_in=10)      # 10 秒后过期 < 安全余量
        self.assertTrue(self.ts.needs_refresh())

    def test_no_refresh_when_fresh(self):
        self.ts.save("at-y", "rt-y", expires_in=7200)
        self.assertFalse(self.ts.needs_refresh())

    def test_no_refresh_without_refresh_token(self):
        self.ts.save("at-z", "", expires_in=10)
        self.assertFalse(self.ts.needs_refresh(), "没有 refresh_token 时不该尝试续期")

    def test_status_is_maskd(self):
        self.ts.save("at-secret-value", "rt-secret", expires_in=3600)
        st = self.ts.status()
        self.assertTrue(st["authorized"])
        self.assertNotIn("at-secret-value", json.dumps(st), "status 不能返回 token 明文")

class TestLocalVideoProvider(unittest.TestCase):
    """本地合成视频通道（VIDEO_PROVIDER=local）。

    背景：视频原来只有 kling / aliyun 两条付费通道，没 Key 直接报错，
    演示时这一环完全没法展示。local 通道用 imageio-ffmpeg 内置的 ffmpeg
    把海报合成为 MP4，不需要任何 Key。

    **注意语义**：这是「本地合成」，不是 AI 生成视频。测试只验证
    「能在没有 Key 的情况下产出可播放的 MP4」，不宣称它是模型能力。
    """

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(_ROOT))
        from app.agent import video_generator as vg

        cls.vg = vg
        cls._orig = {k: os.environ.get(k) for k in
                     ("VIDEO_PROVIDER", "VIDEO_API_KEY", "VIDEO_DURATION")}

    @classmethod
    def tearDownClass(cls):
        for k, v in cls._orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def setUp(self):
        if not self.vg.local_available():
            self.skipTest("未安装 imageio-ffmpeg，跳过本地合成测试")
        # 造测试图：**必须有纹理**。
        # 纯色图平移看不出任何像素变化（实测运动检测得到 0.00），
        # 会让「视频是否在动」的断言永远失败或永远通过，失去意义。
        from PIL import Image, ImageDraw

        self._tmp = tempfile.mkdtemp(prefix="dsh_video_")
        self._img = os.path.join(self._tmp, "src.png")
        img = Image.new("RGB", (1080, 1440), (245, 214, 214))
        draw = ImageDraw.Draw(img)
        for i in range(0, 1080, 60):
            draw.rectangle([i, 0, i + 30, 1440], fill=(200, 120, 120))
        for i in range(0, 1440, 90):
            draw.rectangle([0, i, 1080, i + 18], fill=(90, 60, 60))
        img.save(self._img)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_local_provider_needs_no_key(self):
        """local 通道在没有 Key（占位符）时也必须能出片。"""
        os.environ["VIDEO_PROVIDER"] = "local"
        os.environ["VIDEO_API_KEY"] = "***"
        out = self.vg.generate_short_video("测试", self._img, duration=2)
        p = Path(out)
        self.assertTrue(p.exists(), "应产出视频文件")
        self.assertGreater(p.stat().st_size, 1000, "产物不应为空")
        self.assertEqual(p.suffix.lower(), ".mp4")
        p.unlink(missing_ok=True)

    def test_missing_key_error_mentions_local_escape(self):
        """第三方 provider 缺 Key 时，报错必须告诉用户 local 这条出路。"""
        os.environ["VIDEO_PROVIDER"] = "kling"
        os.environ["VIDEO_API_KEY"] = "***"
        with self.assertRaises(self.vg.VideoAPIError) as ctx:
            self.vg.generate_short_video("测试", self._img, duration=2)
        self.assertIn("VIDEO_PROVIDER=local", str(ctx.exception))

    def test_local_video_actually_moves(self):
        """产出的视频**必须真的有画面变化**，不能是一张静图配淡入淡出。

        这条是回归测试：第一版用 `zoompan ... d=1` 做推进，结果画面完全静止
        （实测 1s 与 5s 两帧平均像素差 = 0.00），只有淡入淡出在变亮度。
        「能播放」不等于「有运动」，所以这里直接从成片抽两帧比对像素。
        """
        import subprocess

        from PIL import Image

        import numpy as np

        os.environ["VIDEO_PROVIDER"] = "local"
        os.environ["VIDEO_API_KEY"] = "***"
        out = Path(self.vg.generate_short_video("运动检测", self._img, duration=4))
        self.addCleanup(lambda: out.unlink(missing_ok=True))

        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        frames = {}
        for stamp in (0.8, 3.2):
            f = Path(self._tmp) / f"motion_{stamp}.png"
            subprocess.run([exe, "-y", "-ss", str(stamp), "-i", str(out),
                            "-frames:v", "1", str(f)], capture_output=True)
            if f.exists():
                frames[stamp] = np.asarray(Image.open(f).convert("L"), dtype=np.int16)
        self.assertEqual(len(frames), 2, "抽帧失败，无法判定运动")
        a, b = frames[0.8], frames[3.2]
        mean_diff = float(np.abs(a - b).mean())
        self.assertGreater(
            mean_diff, 2.0,
            f"画面几乎没变化（平均像素差 {mean_diff:.2f}）——疑似又用回了 zoompan d=1",
        )

    def test_local_missing_image_raises(self):
        """素材不存在要报错，而不是产出一个空视频。"""
        os.environ["VIDEO_PROVIDER"] = "local"
        with self.assertRaises(self.vg.VideoAPIError):
            self.vg.generate_short_video("测试", os.path.join(self._tmp, "nope.png"), duration=2)

class TestCopyTemplates(unittest.TestCase):
    """爆款文案模版：平台 × 结构。

    设计意图：平台管语气、模版管结构，两个维度正交。
    这里锁三件事：
        1. 模版按平台过滤正确，平台与模版不匹配时被忽略（不报错）
        2. build_prompt 真的把结构约束注入了 system prompt
        3. 不传模版时，prompt 与改造前一致（不破坏默认行为）
    """

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(_ROOT))
        from app.agent import copywriter
        from app.common import copy_templates

        cls.cw = copywriter
        cls.tpl = copy_templates

    def test_list_by_platform(self):
        xhs = self.tpl.list_templates("xiaohongshu")
        dy = self.tpl.list_templates("tiktok")
        amz = self.tpl.list_templates("amazon")
        self.assertGreaterEqual(len(xhs), 4)
        self.assertGreaterEqual(len(dy), 3)
        self.assertGreaterEqual(len(amz), 3)
        for t in xhs:
            self.assertEqual(t["platform"], "xiaohongshu")
        # 不传平台返回全部
        self.assertEqual(len(self.tpl.list_templates()), len(self.tpl.TEMPLATES))

    def test_cross_platform_template_is_ignored(self):
        """拿小红书模版去生成 Amazon，应被忽略而不是报错。"""
        struct = self.tpl.structure_of("amazon", "xhs_pain")
        self.assertEqual(struct, "")
        # resolve 返回 None，get_template 仍能拿到（用于展示）
        self.assertIsNone(self.tpl.resolve("amazon", "xhs_pain"))
        self.assertIsNotNone(self.tpl.get_template("xhs_pain"))

    def test_unknown_template_falls_back(self):
        self.assertEqual(self.tpl.structure_of("xiaohongshu", "not_exist"), "")

    def _input(self, platform="xiaohongshu", template_id=""):
        return self.cw.CopyWriterInput(
            product_name="测试商品", product_sell_points=["卖点一"],
            product_category="水杯", platform=platform, template_id=template_id,
        )

    def test_prompt_injects_structure(self):
        plain = self.cw.build_prompt(self._input(), [])
        with_tpl = self.cw.build_prompt(self._input(template_id="xhs_pain"), [])
        self.assertNotIn("【结构模版】", plain)
        self.assertIn("【结构模版】", with_tpl)
        self.assertGreater(len(with_tpl), len(plain))

    def test_default_prompt_unchanged_without_template(self):
        """不传模版时必须与平台默认 prompt 一致，避免影响既有行为。"""
        prompt = self.cw.build_prompt(self._input(), [])
        base = self.cw.PLATFORM_PROMPTS["xiaohongshu"]
        self.assertTrue(prompt.startswith(base))
        self.assertNotIn("结构模版", prompt)

