# -*- coding: utf-8 -*-
"""mem_recall 评测器、反泄漏匿名化、防死锁/防 OOM、冻结配置与沙箱端到端测试。"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ORG_ROOT = ROOT.parent
REPO_ROOT = ORG_ROOT.parent
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ORG_ROOT))

import runner  # noqa: E402
import score_all  # noqa: E402
import submission_sandbox  # noqa: E402


def _mini_ds(group="mem", metric="r@10", split="dev", docs=None, questions=None, name="mini"):
    return {
        "name": name,
        "group": group,
        "metric": metric,
        "split": split,
        "official": split == "test",
        "practice_only": False,
        "banks": [{"bank_id": "raw_bank_secret", "docs": docs or [
            {"id": "P:secret_1", "text": "alpha apple", "date": "2026-01-01"},
            {"id": "D:secret_2", "text": "beta banana", "date": "2026-01-02"},
        ]}],
        "questions": questions if questions is not None else [
            {"qid": "alice#profile#0", "bank_id": "raw_bank_secret", "question": "apple",
             "category": "profile", "evidence_groups": [["P:secret_1"]]},
        ],
    }


class TestMemRecallMetricsAndProtocol(unittest.TestCase):
    def test_ndcg10_and_scored_edge_cases(self):
        # 过滤 rel <= 0，得分严格在 [0, 1]
        self.assertAlmostEqual(runner.ndcg10(["d1"], {"d1": 1, "d2": -1, "d3": 0}), 1.0)
        self.assertAlmostEqual(runner.ndcg10(["d2"], {"d1": 1, "d2": -1}), 0.0)
        self.assertAlmostEqual(runner.ndcg10(["d1"], {"d1": 2000}), 1.0)  # 无 OverflowError
        self.assertFalse(runner.scored({"qrels": {"d1": 0}}, "ndcg@10"))
        self.assertFalse(runner.scored({"qrels": {"d1": 1}}, "r@10"))
        self.assertFalse(runner.scored({"evidence_groups": [[]]}, "r@10"))

    def test_zero_scored_questions_and_duplicate_qid_across_banks(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            echo = Path(tmp) / "echo.py"
            echo.write_text(textwrap.dedent("""
                import json, sys
                ids = []
                for line in sys.stdin:
                    m = json.loads(line)
                    if m["type"] == "memory":
                        ids = [d["id"] for d in m["docs"]]
                        print("{}", flush=True)
                    elif m["type"] == "query":
                        print(json.dumps({"ranking": ids}), flush=True)
                    else:
                        break
            """), encoding="utf-8")

            # 0 计分题不得 KeyError
            ds0 = _mini_ds(questions=[{"qid": "q0", "bank_id": "raw_bank_secret", "question": "none"}])
            r0 = runner.run_set(str(echo), ds0)
            self.assertEqual(r0["ALL"]["n"], 0)
            self.assertEqual(r0["ALL"]["r@10"], 0.0)

            # 跨 bank 同 qid 不得互相覆盖
            ds_dup = {
                "name": "dup",
                "group": "mem",
                "metric": "r@10",
                "split": "dev",
                "banks": [
                    {"bank_id": "B1", "docs": [{"id": "x1", "text": "one"}]},
                    {"bank_id": "B2", "docs": [{"id": "x2", "text": "two"}]},
                ],
                "questions": [
                    {"qid": "same", "bank_id": "B1", "question": "q1", "evidence_groups": [["x1"]]},
                    {"qid": "same", "bank_id": "B2", "question": "q2", "evidence_groups": [["x2"]]},
                ],
            }
            r_dup = runner.run_set(str(echo), ds_dup)
            self.assertEqual(r_dup["ALL"]["r@10"], 1.0)
            self.assertEqual(r_dup["_meta"]["answered"], 2)

    def test_id_anonymization_and_retrieval_shuffle(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            log_path = Path(tmp) / "seen.jsonl"
            spy = Path(tmp) / "spy.py"
            spy.write_text(textwrap.dedent(f"""
                import json, sys
                log = open({str(log_path)!r}, "w", encoding="utf-8")
                ids = []
                for line in sys.stdin:
                    m = json.loads(line)
                    if m["type"] == "memory":
                        log.write(json.dumps({{"bank_id": m["bank_id"], "doc_ids": [d["id"] for d in m["docs"]], "texts": [d["text"] for d in m["docs"]]}}) + "\\n")
                        ids = [d["id"] for d in m["docs"]]
                        print("{{}}", flush=True)
                    elif m["type"] == "query":
                        log.write(json.dumps({{"qid": m["qid"], "bank_id": m["bank_id"]}}) + "\\n")
                        print(json.dumps({{"ranking": ids}}), flush=True)
                    else:
                        break
                log.close()
            """), encoding="utf-8")

            # 1. mem 集：匿名化 ID，且保留文档原始对话时序
            docs = [{"id": f"P:orig_{i}", "text": f"turn_{i}"} for i in range(10)]
            ds_mem = _mini_ds(group="mem", metric="r@10", docs=docs, questions=[
                {"qid": "cat#leak#1", "bank_id": "raw_bank_secret", "question": "q", "evidence_groups": [["P:orig_0"]]}
            ])
            res_mem = runner.run_set(str(spy), ds_mem, seed=42)
            self.assertEqual(res_mem["ALL"]["r@10"], 1.0)
            events = [json.loads(x) for x in log_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events[0]["bank_id"], "b_0001")
            self.assertEqual(events[0]["doc_ids"][0], "d_000001")
            self.assertEqual(events[0]["texts"], [f"turn_{i}" for i in range(10)])
            self.assertEqual(events[1]["qid"], "q_00001")

            # 2. zh/en 检索集：匿名化 ID 且置换文档顺序（不同 seed 产生不同顺序）
            docs_ret = [{"id": str(30000000 + i), "text": f"doc_{i}"} for i in range(30)]
            ds_ret = _mini_ds(group="zh", metric="ndcg@10", docs=docs_ret, questions=[
                {"qid": "1", "bank_id": "raw_bank_secret", "question": "q", "qrels": {"30000001": 1}}
            ])
            runner.run_set(str(spy), ds_ret, seed=101)
            order1 = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])["texts"]
            runner.run_set(str(spy), ds_ret, seed=202)
            order2 = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])["texts"]
            self.assertNotEqual(order1, [f"doc_{i}" for i in range(30)])
            self.assertNotEqual(order1, order2)

    def test_stdin_deadlock_stdout_flood_null_json_and_desync(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            # 1. 多 MB 载荷下完全不读 stdin 的 bot 不得死锁（受限时退出）
            big_docs = [{"id": f"id_{i}", "text": "x" * 2000} for i in range(250)]  # ~500 KB > 4 KB Win pipe
            ds_big = _mini_ds(docs=big_docs, questions=[
                {"qid": "q1", "bank_id": "raw_bank_secret", "question": "x", "evidence_groups": [["id_0"]]}
            ])
            hang = Path(tmp) / "hang.py"
            hang.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
            with mock.patch.object(runner, "mem_limit", return_value=0.6), \
                 mock.patch.object(runner, "set_limit", return_value=2.0):
                t0 = time.monotonic()
                res_hang = runner.run_set(str(hang), ds_big)
                elapsed = time.monotonic() - t0
            self.assertLess(elapsed, 4.0, f"stdin write deadlocked! elapsed={elapsed:.2f}s")
            self.assertEqual(res_hang["_meta"]["timeouts_or_crashes"], 1)

            # 2. 超大单行输出 (> 256 KiB) 必须立即终止而非 OOM
            flood = Path(tmp) / "flood.py"
            flood.write_text(textwrap.dedent("""
                import sys
                sys.stdin.readline()
                print('{"pad":"' + ('A' * 400000) + '"}', flush=True)
            """), encoding="utf-8")
            res_flood = runner.run_set(str(flood), _mini_ds())
            self.assertEqual(res_flood["_meta"]["timeouts_or_crashes"], 1)

            # 3. 合法 JSON `null` 在 memory 阶段被接受，在 query 阶段计犯规而非崩溃
            null_bot = Path(tmp) / "null_bot.py"
            null_bot.write_text(textwrap.dedent("""
                import json, sys
                for line in sys.stdin:
                    m = json.loads(line)
                    if m["type"] == "end":
                        break
                    print("null", flush=True)
            """), encoding="utf-8")
            res_null = runner.run_set(str(null_bot), _mini_ds())
            self.assertEqual(res_null["_meta"]["timeouts_or_crashes"], 0)
            self.assertEqual(res_null["_meta"]["fouls"], 1)

            # 4. 多行抢跑失步必须拦截判崩溃
            desync = Path(tmp) / "desync.py"
            desync.write_text(textwrap.dedent("""
                import json, sys, time
                for line in sys.stdin:
                    m = json.loads(line)
                    if m["type"] == "memory":
                        print('{}\\n{"ranking":["d_000001"]}\\n{"ranking":["d_000001"]}', flush=True)
                    elif m["type"] == "query":
                        time.sleep(0.05)
                        print('{"ranking":[]}', flush=True)
                    else:
                        break
            """), encoding="utf-8")
            ds_two = _mini_ds(questions=[
                {"qid": "q1", "bank_id": "raw_bank_secret", "question": "apple", "evidence_groups": [["P:secret_1"]]},
                {"qid": "q2", "bank_id": "raw_bank_secret", "question": "banana", "evidence_groups": [["D:secret_2"]]},
                {"qid": "q3", "bank_id": "raw_bank_secret", "question": "apple", "evidence_groups": [["P:secret_1"]]},
            ])
            res_desync = runner.run_set(str(desync), ds_two)
            self.assertEqual(res_desync["_meta"]["timeouts_or_crashes"], 1)


class TestMemRecallDataIntegrityAndScoring(unittest.TestCase):
    def test_zero_exact_kmb_overlap_between_dev_and_hidden(self):
        dev_kmb = REPO_ROOT / "contestant/mem_recall/data/dev/kmb.json.gz"
        hid_kmb = ROOT / "hidden/kmb.json.gz"
        if not dev_kmb.is_file() or not hid_kmb.is_file():
            self.skipTest("kmb dev/hidden files not present")
        d = json.load(gzip.open(dev_kmb, "rt", encoding="utf-8"))
        h = json.load(gzip.open(hid_kmb, "rt", encoding="utf-8"))
        overlap = {(q["bank_id"], q["question"].strip()) for q in d["questions"]} & {
            (q["bank_id"], q["question"].strip()) for q in h["questions"]
        }
        self.assertEqual(len(overlap), 0, f"kmb 存在 dev-hidden 原题泄漏：{len(overlap)}")

    def test_score_py_protects_leaderboard_and_verifies_frozen_profile(self):
        lb_path = ROOT / "results/leaderboard.json"
        # 1. 非正式 --sets 试图覆写 leaderboard.json 必须拒绝
        r1 = subprocess.run(
            [sys.executable, str(ROOT / "scripts/score.py"),
             "--bot", str(ROOT / "agents/baselines/bm25.py"),
             "--sets", "cpzh", "--out", str(lb_path)],
            capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
        self.assertEqual(r1.returncode, 2)
        self.assertIn("禁止覆写正式榜单", r1.stderr)

        # 2. 空提交目录必须失败退出而非清空输出文件
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            empty_dir = Path(tmp) / "empty_subs"
            empty_dir.mkdir()
            out_file = Path(tmp) / "out.json"
            r2 = subprocess.run(
                [sys.executable, str(ROOT / "scripts/score.py"),
                 "--submissions", str(empty_dir), "--sets", "cpzh", "--out", str(out_file)],
                capture_output=True, text=True, encoding="utf-8", timeout=20,
            )
            self.assertNotEqual(r2.returncode, 0)
            self.assertFalse(out_file.exists())

    def test_score_all_extract_enforces_official_v2_integrity(self):
        prof = json.loads((ROOT / "config/scoring_v2.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp) / "mem_recall"
            (base / "config").mkdir(parents=True)
            (base / "results").mkdir(parents=True)
            (base / "agents/submissions").mkdir(parents=True)
            (base / "config/scoring_v2.json").write_text(json.dumps(prof), encoding="utf-8")
            cand = base / "agents/submissions/model.py"
            cand.write_text("print(1)\n", encoding="utf-8")
            cand_sha = hashlib.sha256(cand.read_bytes()).hexdigest()
            lb = base / "results/leaderboard.json"

            with mock.patch.object(score_all, "ORGANIZER_ROOT", Path(tmp)):
                # 1. 旧版 / dev 成绩被拒
                lb.write_text(json.dumps({"model": {"_macro_dev": {"score": 0.6, "n_sets": 18}}}), encoding="utf-8")
                self.assertEqual(score_all.extract_problem_metric("mem_recall", "model")["status"], "incompatible_scoring")

                # 2. 不完整集数 (1/18) 被拒
                lb.write_text(json.dumps({
                    "_scoring": {"scoring_version": "mem_recall_v2", "official": True, "profile_revision": prof["profile_revision"]},
                    "model": {"_submission_sha256": cand_sha, "_macro_official": {"score": 0.9, "n_sets": 1}},
                }), encoding="utf-8")
                self.assertEqual(score_all.extract_problem_metric("mem_recall", "model")["status"], "invalid_result")

                # 3. 合法 18 集正式成绩通过
                lb.write_text(json.dumps({
                    "_scoring": {"scoring_version": "mem_recall_v2", "official": True, "profile_revision": prof["profile_revision"]},
                    "model": {"_submission_sha256": cand_sha, "_macro_official": {"score": 0.6123, "n_sets": 18, "groups": {}}},
                }), encoding="utf-8")
                ok = score_all.extract_problem_metric("mem_recall", "model")
                self.assertEqual(ok["status"], "OK")
                self.assertAlmostEqual(ok["score"], 0.6123)

                # 4. 修改源码后判 stale_submission
                cand.write_text("print(2)\n", encoding="utf-8")
                self.assertEqual(score_all.extract_problem_metric("mem_recall", "model")["status"], "stale_submission")

    @unittest.skipUnless(submission_sandbox.available(), "沙箱不可用")
    def test_sandboxed_chinese_bm25_and_hidden_block(self):
        # 核心回归：验证 Windows AppContainer 沙箱内非可信中文提交不再因 -I 崩溃或 GBK 乱码，且与沙箱外一致
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            sub = Path(tmp) / "untrusted_bm25.py"
            sub.write_bytes((ROOT / "agents/baselines/bm25.py").read_bytes())
            out_sbx = Path(tmp) / "sbx.json"
            out_raw = Path(tmp) / "raw.json"

            # 刻意移除父环境 PYTHONUTF8，验证沙箱和 runner 自身强制 UTF-8
            clean_env = {k: v for k, v in os.environ.items() if k.upper() not in ("PYTHONUTF8", "PYTHONIOENCODING")}

            r_sbx = subprocess.run(
                [sys.executable, str(ROOT / "scripts/score.py"),
                 "--bot", str(sub), "--sets", "cpzh,hive,scifact", "--seed", "777", "--jobs", "2", "--out", str(out_sbx)],
                cwd=str(ROOT), env=clean_env, capture_output=True, text=True, encoding="utf-8", timeout=120,
            )
            self.assertEqual(r_sbx.returncode, 0, f"sandbox score failed:\n{r_sbx.stdout}\n{r_sbx.stderr}")

            r_raw = subprocess.run(
                [sys.executable, str(ROOT / "engine/runner.py"),
                 "--bot", str(sub), "--data", str(ROOT / "hidden"), "--sets", "cpzh,hive,scifact",
                 "--seed", "777", "--jobs", "2", "--out", str(out_raw)],
                cwd=str(ROOT), env=clean_env, capture_output=True, text=True, encoding="utf-8", timeout=120,
            )
            self.assertEqual(r_raw.returncode, 0, r_raw.stderr)

            d_sbx = json.loads(out_sbx.read_text(encoding="utf-8"))["untrusted_bm25"]
            d_raw = json.loads(out_raw.read_text(encoding="utf-8"))["untrusted_bm25"]
            for sname in ("cpzh", "hive", "scifact"):
                metric = d_sbx[sname]["_meta"]["metric"]
                self.assertEqual(d_sbx[sname]["_meta"]["timeouts_or_crashes"], 0, sname)
                self.assertAlmostEqual(d_sbx[sname]["ALL"][metric], d_raw[sname]["ALL"][metric], places=4, msg=sname)
            self.assertGreater(d_sbx["cpzh"]["ALL"]["r@10"], 0.55)
            self.assertGreater(d_sbx["hive"]["ALL"]["r@10"], 0.18)

            # 窃取隐藏文件提交必须在沙箱内被拦截
            thief = Path(tmp) / "thief.py"
            thief.write_text(textwrap.dedent(f"""
                import json, sys
                secret = {str(ROOT / "hidden/cpzh.json")!r}
                for line in sys.stdin:
                    m = json.loads(line)
                    if m["type"] == "memory":
                        try:
                            open(secret, encoding="utf-8").read(10)
                            raise RuntimeError("UNEXPECTED_ACCESS")
                        except PermissionError:
                            pass
                        print("{{}}", flush=True)
                    elif m["type"] == "query":
                        print('{{"ranking":[]}}', flush=True)
                    else:
                        break
            """), encoding="utf-8")
            out_thief = Path(tmp) / "thief.json"
            r_thief = subprocess.run(
                [sys.executable, str(ROOT / "scripts/score.py"),
                 "--bot", str(thief), "--sets", "cpzh", "--seed", "777", "--out", str(out_thief)],
                cwd=str(ROOT), env=clean_env, capture_output=True, text=True, encoding="utf-8", timeout=60,
            )
            self.assertEqual(r_thief.returncode, 0, r_thief.stderr)
            d_thief = json.loads(out_thief.read_text(encoding="utf-8"))["thief"]
            self.assertEqual(d_thief["cpzh"]["_meta"]["timeouts_or_crashes"], 0)
            self.assertEqual(d_thief["cpzh"]["ALL"]["r@10"], 0.0)


if __name__ == "__main__":
    unittest.main()
