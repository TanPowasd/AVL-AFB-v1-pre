"""回放编码器单测（纯 stdlib，跑一局短对局）。

运行：
    python tests/test_replay.py
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
import arena  # noqa: E402
from loader import load_agent  # noqa: E402
import replay as R  # noqa: E402

CFG = {"episodeSteps": 96, "boardSize": 10, "turnsPerDay": 24, "startingMoney": 3000,
       "actTimeoutS": 1.0, "overageBudgetS": 60.0, "errorBudget": 30, "drawMarginPct": 1.0}
SEED = 11


def policies():
    a = load_agent(os.path.join(ROOT, "agents", "baselines", "greedy_farm.py"))
    b = load_agent(os.path.join(ROOT, "agents", "baselines", "passive.py"))
    return [a, b], [a[0], b[0]]


class ReplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agents, cls.labels = policies()
        cls.engine_sha = R.sha256_file(os.path.join(ROOT, "engine", "rules.py"))

    def record(self, **kw):
        rec = R.ReplayRecorder(CFG, [{"seat": 0}, {"seat": 1}], SEED, engine_sha=self.engine_sha,
                               labels=self.labels, **kw)
        res = arena.play_game([self.agents[0][1], self.agents[1][1]], SEED, CFG, recorder=rec)
        return rec, res

    def test_内部字段不进回放(self):
        """瓦片白名单：引擎内部键不得出现在 extra 里。"""
        rec, _ = self.record()
        cell = {"kind": "PLANT", "crop": "WHEAT", "planted_day": 3, "watered_today": True,
                "yield_units": 2, "consecutive_unwatered": 0, "fertilized_until_day": 0,
                "pending_care_bonus": 99, "max_lifespan_step": 240}
        code, extra = rec._cell(cell)
        self.assertEqual(extra, [R.EX_PLANT, code, 3, 1, 2, 0, 0])
        self.assertNotIn("pending_care_bonus", str(extra))
        self.assertNotIn("max_lifespan_step", str(extra))

    def test_帧数与终局现金一致(self):
        rec, res = self.record()
        data = rec.build(res)
        self.assertEqual(len(data["frames"]), CFG["episodeSteps"] - 1)
        self.assertEqual(data["meta"]["steps_done"], data["frames"][-1]["t"])
        ok, msg = R.verify_against_engine(data, res["cash"])
        self.assertTrue(ok, msg)

    def test_解码后每帧瓦片完整(self):
        rec, res = self.record()
        frames = R.decode_frames(rec.build(res))
        self.assertTrue(frames)
        for f in frames:
            for p in f["p"]:
                self.assertEqual(len(p["tiles"]), 100)
            self.assertEqual(len(f["mkt"]["prices"]), 9)
        # day/hour 由步号推导，且与引擎一致
        self.assertEqual((frames[5]["day"], frames[5]["hour"]),
                         divmod(frames[5]["t"], 24))

    def test_增量帧与全量帧等价(self):
        """同一局的 delta 与 full 录制，解码后必须逐帧等价。"""
        rec_full, res = self.record(delta=False)
        rec_delta, _ = self.record(delta=True)
        full = R.decode_frames(rec_full.build(res))
        delta = R.decode_frames(rec_delta.build(res))
        self.assertEqual(len(full), len(delta))
        for i, (a, b) in enumerate(zip(full, delta)):
            self.assertEqual(a["p"][0]["tiles"], b["p"][0]["tiles"], "第 %d 帧瓦片不一致" % i)
            self.assertEqual(a["p"][1]["tiles"], b["p"][1]["tiles"])
            self.assertEqual(a["p"][0]["extra"], b["p"][0]["extra"])
            self.assertEqual(a["mkt"], b["mkt"])
            self.assertEqual(a["town"], b["town"])
            self.assertEqual(a["act"], b["act"])

    def test_增量更省体积(self):
        rec_full, res = self.record(delta=False)
        rec_delta, _ = self.record(delta=True)
        import json
        a = len(json.dumps(rec_full.build(res), separators=(",", ":")))
        b = len(json.dumps(rec_delta.build(res), separators=(",", ":")))
        self.assertLess(b, a)

    def test_确定性(self):
        """同种子同双方重跑：帧摘要完全一致。"""
        rec1, res1 = self.record()
        rec2, res2 = self.record()
        self.assertEqual(R.frame_digest(rec1.build(res1)), R.frame_digest(rec2.build(res2)))

    def test_图例为_code到名称(self):
        rec, res = self.record()
        legend = rec.build(res)["legend"]
        for code, name in legend["crops"].items():
            self.assertRegex(code, r"^[0-9A-F]$")
            self.assertIn(name, ("WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
