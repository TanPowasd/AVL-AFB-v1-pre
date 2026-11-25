# -*- coding: utf-8 -*-
"""前沿实验室回放、离线播放器、选手包纯净导出、冻结配置与 PvP/ELO 自动化测试。"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
ORGANIZER_ROOT = ROOT.parent

sys.path.insert(0, str(ORGANIZER_ROOT))
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "score"))

import elo  # noqa: E402
import engine  # noqa: E402
import replay as replay_mod  # noqa: E402
import strategy_scoring  # noqa: E402


def _load_local_pack_contestant():
    spec = importlib.util.spec_from_file_location(
        "ai_training_pack_contestant",
        str(ROOT / "scripts" / "pack_contestant.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pack_contestant = _load_local_pack_contestant()

FORBIDDEN_WORDS = re.compile(r"organizer|组委会|OPEN_ISSUES|HEAVY_TESTS|TanPowasd|Powasd|/data/ws|/data/home")
TEXT_SUFFIXES = {".py", ".md", ".json", ".txt", ".html", ".js", ".css"}


class TestReplayAndCLI(unittest.TestCase):
    def test_sample_replay_exists_and_schema_valid(self):
        sample = ROOT / "replays" / "replay_seed1_fw.json"
        self.assertTrue(sample.is_file(), f"缺少样例回放：{sample}")
        data = json.loads(sample.read_text(encoding="utf-8"))
        self.assertEqual(data["format"], replay_mod.FORMAT)
        self.assertEqual(data["meta"]["problem"], "ai_training")
        self.assertEqual(data["meta"]["mode"], "adv")
        self.assertEqual(data["meta"]["seed"], 1)
        self.assertEqual(data["meta"]["ticks"], 4380)
        self.assertGreater(len(data["days"]), 100)
        self.assertIsInstance(data["events"], list)
        # 复核 meta 与末帧一致
        ok, msg = replay_mod.verify_against_engine(
            data,
            {
                "seed": data["meta"]["seed"],
                "ticks": data["meta"]["ticks"],
                "value": data["meta"]["final_value"],
                "C": data["meta"]["final_C"],
            },
        )
        self.assertTrue(ok, msg)

    def test_record_replay_cli_adv_and_determinism(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            out_dir = Path(tmp) / "replays"
            cmd = [
                sys.executable, "-X", "utf8",
                str(ROOT / "scripts" / "record_replay.py"),
                "--a", "scaling",
                "--b", "blank",
                "--seed", "2",
                "--ticks", "240",
                "--every", "2",
                "--swap",
                "--out", str(out_dir),
            ]
            run = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
            self.assertEqual(run.returncode, 0, f"stdout:\n{run.stdout}\nstderr:\n{run.stderr}")
            self.assertIn("复算校验：一致", run.stdout)

            fw = out_dir / "replay_seed2_fw.json"
            sw = out_dir / "replay_seed2_sw.json"
            self.assertTrue(fw.is_file())
            self.assertTrue(sw.is_file())

            d_fw = json.loads(fw.read_text(encoding="utf-8"))
            d_sw = json.loads(sw.read_text(encoding="utf-8"))
            self.assertEqual(d_fw["meta"]["sample_every"], 2)
            # 交换座位完全对称
            self.assertEqual(d_fw["meta"]["final_value"], d_sw["meta"]["final_value"][::-1])
            self.assertEqual(d_fw["meta"]["final_C"], d_sw["meta"]["final_C"][::-1])

            # 篡改末帧估值必须触发校验失败
            tampered = json.loads(fw.read_text(encoding="utf-8"))
            tampered["days"][-1]["A"]["value"] += 123.45
            ok, msg = replay_mod.verify_against_engine(
                tampered,
                {"seed": 2, "ticks": 240, "value": d_fw["meta"]["final_value"], "C": d_fw["meta"]["final_C"]},
            )
            self.assertFalse(ok)
            self.assertIn("末帧估值", msg)

    def test_record_replay_cli_solo(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            out_dir = Path(tmp) / "replays"
            cmd = [
                sys.executable, "-X", "utf8",
                str(ROOT / "scripts" / "record_replay.py"),
                "--a", "blank",
                "--solo",
                "--seed", "3",
                "--ticks", "192",
                "--out", str(out_dir),
            ]
            run = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
            self.assertEqual(run.returncode, 0, f"stdout:\n{run.stdout}\nstderr:\n{run.stderr}")
            out_file = out_dir / "replay_solo_seed3.json"
            self.assertTrue(out_file.is_file())
            data = json.loads(out_file.read_text(encoding="utf-8"))
            self.assertEqual(data["meta"]["mode"], "solo")
            self.assertEqual(data["meta"]["winner"], "solo")
            self.assertEqual(data["meta"]["seats"][1]["label"], "SOLO_NPC")

            # 直接调用引擎验证确定性 frame_digest
            res = engine.play(str(ROOT / "agents/baselines/blank.py"), engine.SOLO_NPC, 3, ticks=192, inproc_b=True)
            rebuilt = replay_mod.build_replay(
                res,
                seats=data["meta"]["seats"],
                seed=3,
                ticks=192,
                mode="solo",
                engine_sha=data["meta"]["engine_sha"],
            )
            self.assertEqual(replay_mod.frame_digest(rebuilt), replay_mod.frame_digest(data))
            ok, msg = replay_mod.verify_against_engine(data, res)
            self.assertTrue(ok, msg)


class TestVisualizerOffline(unittest.TestCase):
    def test_player_html_offline_and_complete(self):
        player = ROOT / "viz" / "player.html"
        self.assertTrue(player.is_file())
        html = player.read_text(encoding="utf-8")
        # 禁止任何外部网络/CDN依赖
        self.assertIsNone(re.search(r"https?://", html, re.IGNORECASE), "viz/player.html 不得包含外链 URL")
        self.assertIsNone(re.search(r"<script[^>]+src=", html, re.IGNORECASE), "viz/player.html 不得包含外部 script")
        self.assertIsNone(re.search(r"<link[^>]+href=", html, re.IGNORECASE), "viz/player.html 不得包含外部 link")
        # 关键元素与格式检查
        self.assertIn("afb-ai-training-replay", html)
        for elem_id in ("cvVal", "cvCap", "cvComp", "cvShare", "evList", "seek", "theme", "cardA", "cardB"):
            self.assertIn(f'id="{elem_id}"', html)
        # P1-8 可视化修复校验：破产表项、dead 徽章、tickEnd 截断、单日回放渲染
        self.assertIn("bkA ? 0 :", html)
        self.assertIn("进程崩溃 dead", html)
        self.assertIn("Math.min(d.day * 24 + 23", html)
        self.assertIn("days.length === 1", html)
        # 纯净性检查
        self.assertIsNone(FORBIDDEN_WORDS.search(html))
        # JS 语法检查（若系统安装 node）
        import shutil
        if shutil.which("node"):
            m = re.search(r"<script>(.*)</script>", html, re.S)
            self.assertIsNotNone(m)
            with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as fh:
                fh.write(m.group(1))
                js_path = fh.name
            try:
                chk = subprocess.run(["node", "--check", js_path], capture_output=True, text=True, timeout=15)
                self.assertEqual(chk.returncode, 0, chk.stderr)
            finally:
                Path(js_path).unlink(missing_ok=True)


class TestContestantKitExportAndPurity(unittest.TestCase):
    def test_export_public_files_purity_and_zero_leakage(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            dest = Path(tmp) / "ai_training_kit"
            pack_contestant.export_kit(dest, root=ROOT, make_zip=False, force=True)

            expected_public = (
                "README.md",
                "AGENTS.md",
                "AI_README.md",
                "FORMAT.md",
                "handbook/contest-rules.md",
                "handbook/player-guide.md",
                "handbook/game-facts.md",
                "prompts/task.txt",
                "agents/submissions/README.md",
                "agents/template/main.py",
                "agents/template/planner.py",
                "agents/template/params.json",
                "engine/archive.py",
                "engine/pkg_entry.py",
                "engine/engine.py",
                "engine/world.py",
                "engine/solo_npc.py",
                "engine/policy.py",
                "engine/replay.py",
                "scripts/make_submission.py",
                "scripts/spar.py",
                "scripts/run_match.py",
                "scripts/record_replay.py",
                "viz/player.html",
                "config/format.json",
                "config/workspace.json",
                "manifest.json",
            )
            for rel in expected_public:
                self.assertTrue((dest / rel).is_file(), f"选手包缺少公开文件：{rel}")

            forbidden_private = (
                "scripts/duel.py",
                "scripts/run_league.py",
                "scripts/pack_prompt.py",
                "scripts/pack_contestant.py",
                "scripts/score.py",
                "scripts/recalibrate.py",
                "score/elo.py",
                "score/scorer.py",
                "agents/reference",
                "agents/reference/ref.py",
                "agents/reference/hark_strong.py",
                "agents/reference/distill_strong.py",
                "agents/reference/scaling_hegemon.py",
                "agents/reference/capital_arbitrageur.py",
                "agents/reference/adaptive_tactician.py",
                "config/scoring_v2.json",
                "config/calibration_v2.json",
                "replays/replay_seed1_fw.json",
            )
            for rel in forbidden_private:
                self.assertFalse((dest / rel).exists(), f"选手包泄露了私有文件：{rel}")

            # 校验 manifest.json 哈希完整匹配及 .zip 提交路径
            inv = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(inv["unified_submission"], "../submissions/ai_training.zip")
            actual = {
                p.relative_to(dest).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in dest.rglob("*")
                if p.is_file() and p != dest / "manifest.json"
            }
            self.assertEqual(inv["files"], actual)

            # 校验全部导出文本文件不含禁用词与隐藏参考对手名
            hidden_leak = re.compile(r"scaling_hegemon|capital_arbitrageur|adaptive_tactician|hark_strong|distill_strong")
            for p in sorted(dest.rglob("*")):
                if p.is_file() and p.suffix in TEXT_SUFFIXES:
                    txt = p.read_text(encoding="utf-8", errors="replace")
                    m = FORBIDDEN_WORDS.search(txt)
                    self.assertIsNone(m, f"导出文件含禁用词 {m.group(0) if m else ''}: {p.relative_to(dest)}")
                    hm = hidden_leak.search(txt)
                    self.assertIsNone(hm, f"导出文件泄露隐藏对手名 {hm.group(0) if hm else ''}: {p.relative_to(dest)}")

            # 校验选手工作区内 make_submission.py 打包、冒烟与边界守卫
            mk_bad = subprocess.run(
                [sys.executable, "-X", "utf8", "scripts/make_submission.py",
                 "agents/template", "--out", str(Path(tmp) / "outside.zip")],
                cwd=dest, capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(mk_bad.returncode, 2, mk_bad.stdout + mk_bad.stderr)

            mk_ok = subprocess.run(
                [sys.executable, "-X", "utf8", "scripts/make_submission.py",
                 "agents/template", "--out", "agents/submissions/tpl.zip"],
                cwd=dest, capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(mk_ok.returncode, 0, mk_ok.stdout + mk_ok.stderr)
            self.assertIn("冒烟通过", mk_ok.stdout)
            self.assertTrue((dest / "agents/submissions/tpl.zip").is_file())

            # 校验选手工作区内 spar.py 支持 .zip 且记录 faults
            sp_ok = subprocess.run(
                [sys.executable, "-X", "utf8", "scripts/spar.py",
                 "agents/submissions/tpl.zip", "blank", "--seeds", "1", "--ticks", "48"],
                cwd=dest, capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(sp_ok.returncode, 0, sp_ok.stdout + sp_ok.stderr)
            self.assertIn("faults(", sp_ok.stdout)
            spar_files = list((dest / "results").rglob("spar.json"))
            self.assertTrue(spar_files)
            sp_json = json.loads(spar_files[0].read_text(encoding="utf-8"))
            self.assertIn("faults", sp_json["rows"][0])

            # 校验选手工作区内 record_replay.py 的红线守卫
            for blocked_args in (
                ("--a", "blank", "--b", "merchant", "--ticks", "48", "--serve"),
                ("--a", "blank", "--b", "merchant", "--ticks", "48", "--unrestricted"),
                ("--a", "blank", "--b", "merchant", "--ticks", "48", "--out", str(Path(tmp) / "outside")),
            ):
                res = subprocess.run(
                    [sys.executable, "-X", "utf8", "scripts/record_replay.py", *blocked_args],
                    cwd=dest, capture_output=True, text=True, encoding="utf-8", timeout=30,
                )
                self.assertEqual(res.returncode, 2, f"应被选手守卫拒绝：{blocked_args}\n{res.stdout}\n{res.stderr}")

            # 选手工作区用 .zip 正常录制到 replays/ 应成功
            ok_run = subprocess.run(
                [sys.executable, "-X", "utf8", "scripts/record_replay.py",
                 "--a", "agents/submissions/tpl.zip", "--b", "blank", "--seed", "7", "--ticks", "48", "--no-verify"],
                cwd=dest, capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(ok_run.returncode, 0, ok_run.stdout + ok_run.stderr)
            self.assertTrue((dest / "replays" / "replay_seed7_fw.json").is_file())


class TestFrozenProfileAndElo(unittest.TestCase):
    def test_load_profile_passes(self):
        cfg = strategy_scoring.load_profile("ai_training")
        self.assertEqual(cfg["problem"], "ai_training")
        self.assertEqual(cfg["version"], "scoring_v2")
        self.assertEqual(cfg.get("submission_ext"), ".zip")
        self.assertIn("engine/archive.py", cfg["frozen_files"])
        self.assertIn("engine/pkg_entry.py", cfg["frozen_files"])
        self.assertAlmostEqual(cfg["margin_scale"], engine.VALUE_SCALE)

    def test_elo_unit_functions(self):
        self.assertAlmostEqual(elo.expected(1500, 1500), 0.5)
        self.assertGreater(elo.expected(1700, 1500), 0.75)

        ra, rb = elo.update(1500.0, 1500.0, 1.0, margin=1.5, k=24.0)
        self.assertAlmostEqual(ra, 1518.0)
        self.assertAlmostEqual(rb, 1482.0)

        self.assertAlmostEqual(elo.margin_factor(500.0, 500.0), 1.0)
        mf1 = elo.margin_factor(500.0 + engine.VALUE_SCALE, 500.0, scale=engine.VALUE_SCALE, cap=2.0)
        self.assertGreater(mf1, 1.7)
        self.assertLessEqual(mf1, 2.0)
        self.assertAlmostEqual(elo.margin_factor(1e6, 0.0, cap=1.8), 1.8)

        self.assertTrue(elo.is_half_score(4, 0, 8))
        self.assertTrue(elo.is_half_score(2, 4, 8))
        self.assertFalse(elo.is_half_score(5, 0, 8))

        tb = elo.tiebreak_seeds({}, used_seeds=[1, 173], count=3)
        self.assertEqual(tb, [181, 191, 193])

        self.assertEqual(elo.judge_game(1000.0, 1005.0, draw_margin_pct=1.0), "draw")
        self.assertEqual(elo.judge_game(1200.0, 1000.0, draw_margin_pct=1.0), "A")
        self.assertEqual(elo.judge_game(800.0, 1000.0, draw_margin_pct=1.0), "B")

        rows = [
            {"name": "alpha", "elo": 1532.4, "w": 3, "d": 1, "l": 0, "avg_value": 812.5, "avg_C": 0.6123, "avg_adv_score": 68.5},
            {"name": "beta", "elo": 1467.6, "w": 0, "d": 1, "l": 3, "avg_value": 410.0, "avg_C": 0.4010, "avg_adv_score": 31.5},
        ]
        md = elo.render_table(rows)
        self.assertIn("| 排名 | 选手 | ELO |", md)
        self.assertIn("| 1 | alpha | 1532 | 3 | 1 | 0 | 812.5 | 0.6123 | 68.50 |", md)

    def test_duel_and_league_smoke(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            duel_out = Path(tmp) / "duel.json"
            duel_rep = Path(tmp) / "replays"
            r_duel = subprocess.run(
                [
                    sys.executable, "-X", "utf8", str(ROOT / "scripts" / "duel.py"),
                    str(ROOT / "agents/baselines/blank.py"),
                    str(ROOT / "agents/baselines/merchant.py"),
                    "--seeds", "1", "--ticks", "120", "--no-tiebreak",
                    "--record-replay", "--replay-dir", str(duel_rep),
                    "--out", str(duel_out), "--inproc",
                ],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60,
            )
            self.assertEqual(r_duel.returncode, 0, r_duel.stdout + r_duel.stderr)
            self.assertTrue(duel_out.is_file())
            self.assertTrue(any(duel_rep.glob("*.json")))

            league_out = Path(tmp) / "league"
            r_lg = subprocess.run(
                [
                    sys.executable, "-X", "utf8", str(ROOT / "scripts" / "run_league.py"),
                    str(ROOT / "agents/baselines/blank.py"),
                    str(ROOT / "agents/baselines/scaling.py"),
                    str(ROOT / "agents/baselines/merchant.py"),
                    "--seed-count", "1", "--ticks", "120",
                    "--out", str(league_out), "--inproc",
                ],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60,
            )
            self.assertEqual(r_lg.returncode, 0, r_lg.stdout + r_lg.stderr)
            self.assertTrue((league_out / "leaderboard.md").is_file())
            self.assertTrue((league_out / "league.json").is_file())
            lg_data = json.loads((league_out / "league.json").read_text(encoding="utf-8"))
            self.assertEqual(len(lg_data["rows"]), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
