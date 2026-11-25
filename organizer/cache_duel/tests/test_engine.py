# -*- coding: utf-8 -*-
"""cache_duel 引擎、协议边界、犯规判负与沙箱清理回归测试。"""
from __future__ import annotations

import functools
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
ORG_ROOT = ROOT.parent
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ORG_ROOT))

from engine import belady_misses, play  # noqa: E402
import strategy_scoring as scoring  # noqa: E402
import submission_sandbox  # noqa: E402


def _brute(trace, cap):
    @functools.lru_cache(None)
    def f(i, cache):
        if i == len(trace):
            return 0
        k = trace[i]
        if k in cache:
            return f(i + 1, cache)
        if len(cache) < cap:
            return 1 + f(i + 1, cache | {k})
        return 1 + min(f(i + 1, (cache - {e}) | {k}) for e in cache)
    return f(0, frozenset())


class CacheDuelEngineTests(unittest.TestCase):
    def test_belady_exact_and_cap_zero(self):
        self.assertEqual(belady_misses([], 8), 0)
        self.assertEqual(belady_misses([0, 1, 2], 0), 3)
        rng = random.Random(0)
        for _ in range(300):
            cap = rng.randint(1, 3)
            tr = [rng.randrange(5) for _ in range(rng.randint(1, 12))]
            self.assertEqual(belady_misses(tr, cap), _brute(tr, cap), (tr, cap))

    def test_lru_match(self):
        lru = str(ROOT / "agents/baselines/lru.py")
        r = play(lru, lru, 8, 64, 500, 1)
        self.assertEqual(r.faults["attacker"], 0)
        self.assertEqual(r.faults["defender"], 0)
        self.assertFalse(r.faults["attacker_dead"])
        self.assertFalse(r.faults["defender_dead"])
        self.assertEqual(r.def_misses, 500)
        self.assertTrue(0 < r.def_score < 0.2)

    def test_reference_bots_full_c64(self):
        lru = str(ROOT / "agents/baselines/lru.py")
        for name in ("adaptive_strong.py", "strong.py", "hark_strong.py"):
            bot = str(ROOT / "agents/reference" / name)
            r1 = play(bot, lru, 64, 4096, 600, 7)
            self.assertEqual(r1.faults["attacker"], 0, name)
            self.assertFalse(r1.faults["attacker_dead"], name)
            r2 = play(lru, bot, 64, 4096, 600, 7)
            self.assertEqual(r2.faults["defender"], 0, name)
            self.assertFalse(r2.faults["defender_dead"], name)
            # 修复后 adaptive_strong 在 C=64 对 lru 循环扫描不再崩盘
            if name == "adaptive_strong.py":
                self.assertGreater(r2.def_score, 0.85, f"adaptive_strong C=64 defense regression: {r2.def_score}")

    def test_zero_byte_and_empty_json_forfeit_to_zero(self):
        cfg = scoring.load_profile("cache_duel")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            blank = Path(tmp) / "blank.py"
            blank.write_text("", encoding="utf-8")
            empty_json = Path(tmp) / "empty_json.py"
            empty_json.write_text(
                "import sys\nfor line in sys.stdin:\n    print('{}', flush=True)\n",
                encoding="utf-8",
            )
            for cand in (blank, empty_json):
                jobs = scoring.make_jobs("cache_duel", str(cand), cfg, [7], ticks=60)
                # 抽查攻防两个席位
                for seat in (0, 1):
                    job = next(j for j in jobs if j["seat"] == seat and j["opponent"] == "lru")
                    res = scoring.execute({**job, "resource_policy": None})
                    self.assertEqual(res["score"], 0.0, f"{cand.name} seat={seat} should forfeit to 0.0")
                    self.assertTrue(res["candidate_fault"])

    def test_protocol_hardening_oversize_utf8_desync_and_no_end_reply(self):
        lru = str(ROOT / "agents/baselines/lru.py")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            # 1. 超大行输出（> 64 KiB）必须被立即拦截判死
            oversize = Path(tmp) / "oversize.py"
            oversize.write_text(textwrap.dedent("""
                import sys
                sys.stdin.readline()
                print("{}", flush=True)
                sys.stdin.readline()
                print('{"key":0,"pad":"' + ('A' * 100000) + '"}', flush=True)
            """), encoding="utf-8")
            r_over = play(str(oversize), lru, 8, 64, 20, 1)
            self.assertTrue(r_over.faults["attacker_dead"])
            self.assertGreater(r_over.faults["attacker"], 0)
            # 攻击方犯规退化为常量 0（OPT=1, M=1, def_score=1.0）
            self.assertEqual(r_over.def_score, 1.0)

            # 2. 非法 UTF-8 字节不会卡死 6s，立即判死
            bad_utf8 = Path(tmp) / "bad_utf8.py"
            bad_utf8.write_text(textwrap.dedent("""
                import sys
                sys.stdin.readline()
                print("{}", flush=True)
                sys.stdin.readline()
                sys.stdout.buffer.write(b"\\xff\\xfe\\n")
                sys.stdout.buffer.flush()
            """), encoding="utf-8")
            r_utf = play(str(bad_utf8), lru, 8, 64, 20, 1)
            self.assertTrue(r_utf.faults["attacker_dead"])

            # 3. 提前多行抢跑失步必须判死
            desync = Path(tmp) / "desync.py"
            desync.write_text(textwrap.dedent("""
                import sys
                sys.stdin.readline()
                print("{}", flush=True)
                sys.stdin.readline()
                print('{"key":0}\\n{"key":1}', flush=True)
                for line in sys.stdin:
                    if '"end"' in line: break
                    print('{"key":0}', flush=True)
            """), encoding="utf-8")
            r_desync = play(str(desync), lru, 8, 64, 20, 1)
            self.assertTrue(r_desync.faults["attacker_dead"])

            # 4. 收到 end 直接退出不打印 {} 不应误标 dead
            no_end = Path(tmp) / "no_end.py"
            no_end.write_text(textwrap.dedent("""
                import json, sys
                init = json.loads(sys.stdin.readline())
                print("{}", flush=True)
                for line in sys.stdin:
                    m = json.loads(line)
                    if m.get("end"):
                        break
                    print('{"key":0}', flush=True)
            """), encoding="utf-8")
            r_end = play(str(no_end), lru, 8, 64, 20, 1)
            self.assertFalse(r_end.faults["attacker_dead"])
            self.assertEqual(r_end.faults["attacker"], 0)

    def test_run_match_and_record_replay_self_flag(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r1 = subprocess.run(
                [sys.executable, str(ROOT / "scripts/run_match.py"),
                 "--a", "lru", "--self", "--swap", "--config-idx", "0", "--seeds", "11", "--out", str(Path(tmp) / "m")],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(r1.returncode, 0, r1.stderr)
            r2 = subprocess.run(
                [sys.executable, str(ROOT / "scripts/record_replay.py"),
                 "--a", "lru", "--b", "self", "--seed", "11", "--length", "40", "--out", str(Path(tmp) / "r")],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(r2.returncode, 0, r2.stderr)

    @unittest.skipUnless(sys.platform == "win32" and submission_sandbox.available(), "Windows 沙箱回归")
    def test_windows_appcontainer_no_profile_or_temp_leak(self):
        if submission_sandbox.probe_windows_sandbox() != "windows_appcontainer_job":
            self.skipTest("当前环境未启用 Windows AppContainer")
        import winreg

        def _count_profiles() -> int:
            key_path = r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppContainer\Mappings"
            cnt = 0
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as k:
                    i = 0
                    while True:
                        try:
                            sub = winreg.EnumKey(k, i)
                            i += 1
                            with winreg.OpenKey(k, sub) as sk:
                                name, _ = winreg.QueryValueEx(sk, "Moniker")
                                if str(name).startswith("avl_sbx_"):
                                    cnt += 1
                        except OSError:
                            break
            except OSError:
                pass
            return cnt

        def _count_sbx_dirs() -> int:
            td = Path(tempfile.gettempdir())
            return sum(1 for p in td.iterdir() if p.is_dir() and p.name.startswith("avl_win_sbx_"))

        before_prof = _count_profiles()
        before_dirs = _count_sbx_dirs()
        cfg = scoring.load_profile("cache_duel")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            cand = Path(tmp) / "cand_lru.py"
            cand.write_bytes((ROOT / "agents/baselines/lru.py").read_bytes())
            prev_sbx = scoring.SANDBOX
            scoring.SANDBOX = True
            try:
                jobs = scoring.make_jobs("cache_duel", str(cand), cfg, [7], ticks=80)
                for seat in (0, 1):
                    job = next(j for j in jobs if j["seat"] == seat and j["opponent"] == "fifo")
                    res = scoring.execute({**job, "resource_policy": None})
                    self.assertFalse(res["candidate_fault"], res["faults"])
            finally:
                scoring.SANDBOX = prev_sbx

        self.assertLessEqual(_count_profiles(), before_prof, "Windows AppContainer Profile 发生泄漏")
        self.assertLessEqual(_count_sbx_dirs(), before_dirs, "Windows 沙箱临时目录发生泄漏")


if __name__ == "__main__":
    unittest.main()
