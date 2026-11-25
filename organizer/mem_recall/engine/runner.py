# -*- coding: utf-8 -*-
"""mem_recall v2 评测器：按 bank 把记忆喂给 bot，逐题要排序，记忆集算 r@10、检索集算 nDCG@10，再做宏平均。

用法：
  python engine/runner.py --bot agents/baselines/bm25.py --data data/dev
  python engine/runner.py --bots agents/baselines --data data/dev --sets cpzh,hive --out results/dev.json
  python scripts/spar.py agents/submissions/my_agent.py --sets cpzh,hive,scifact

协议（每行一个 UTF-8 JSON，见 README.md）：
  对每个 bank：先发 {"type":"memory","set","bank_id","docs":[{id,text,date?,speaker?,...}]}，bot 回一行合法 JSON（如 {}）；
  再发该 bank 的每道题 {"type":"query","qid","bank_id","question"}，bot 回 {"ranking":[doc id, ...]}（最多 50 个）；
  全部 bank 结束后发 {"type":"end"}。

安全与反泄漏加固：
  1. 协议边界统一匿名化 bank_id (`b_0001`)、doc.id (`d_000001`) 与 qid (`q_00001`)，回传排序经逆映射还原后计分；
  2. 对无序检索集（group in {"en","zh"}）随机置换文档与问题顺序（记忆对话集保留原始文档时序，仅置换问题顺序）；
  3. 独立写线程限流写入多 MB stdin 载荷，彻底消除管道死锁；
  4. 二进制按行限长读取（上限 256 KiB）与有界队列，防止恶意超大输出 OOM 击垮裁判；
  5. 区分合法 JSON `null` 与超时/崩溃，使用单调时钟与优雅退出以杜绝僵尸进程及沙箱 Profile 泄漏。
"""
from __future__ import annotations

import argparse
import collections
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import json
import math
import os
import queue
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

STEP_LIMIT = 10.0
TOPK = 50
MAX_RESTARTS = 3
MAX_REPLY_BYTES = 262144   # 单行回复上限 256 KiB（合法 50 条匿名 id 不足 1 KiB）
MAX_RANK_SCAN = 2000       # 防止提交返回百万级列表在计时外阻塞裁判
CLOSE_WAIT_S = 2.0
DEFAULT_SEED = 20261123


def mem_limit(chars: int) -> float:
    return max(10.0, chars / 10000.0)


def set_limit(chars: int, nq: int) -> float:
    return 120.0 + chars / 10000.0 + nq * 1.0


def _natural_key(path: str) -> list[Any]:
    return [int(tok) if tok.isdigit() else tok.lower() for tok in re.split(r"(\d+)", os.path.basename(path))]


class Bot:
    def __init__(self, path: str):
        self._cwd = tempfile.mkdtemp(prefix="avl_mr_cwd_")
        env = {
            **os.environ,
            "PYTHONHASHSEED": "0",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
        self.p = subprocess.Popen(
            [sys.executable, "-u", "-X", "utf8", os.path.abspath(path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            env=env,
            cwd=self._cwd,
        )
        self.q: queue.Queue = queue.Queue(maxsize=4)
        self._reader_thread = threading.Thread(target=self._read, daemon=True)
        self._reader_thread.start()
        self.dead = False

    def _put_event(self, kind: str, payload: Any) -> bool:
        try:
            self.q.put((kind, payload), timeout=0.5)
            return True
        except queue.Full:
            return False

    def _read(self) -> None:
        stdout = self.p.stdout
        if stdout is None:
            self._put_event("eof", None)
            return
        try:
            while True:
                raw = stdout.readline(MAX_REPLY_BYTES + 1)
                if not raw:
                    break
                if len(raw) > MAX_REPLY_BYTES and not raw.endswith(b"\n"):
                    self._put_event("oversize", None)
                    return
                try:
                    line = raw.decode("utf-8")
                except UnicodeDecodeError:
                    self._put_event("bad_utf8", None)
                    return
                if not self._put_event("line", line):
                    return
        except Exception:
            pass
        self._put_event("eof", None)

    def _write_with_timeout(self, payload: bytes, timeout_s: float) -> bool:
        """在独立线程中写入 stdin 并受时限约束，避免多 MB memory 载荷导致主线程永久管道死锁。"""
        stdin = self.p.stdin
        if stdin is None:
            return False
        err: list[BaseException] = []

        def _writer() -> None:
            try:
                stdin.write(payload)
                stdin.flush()
            except BaseException as exc:
                err.append(exc)

        th = threading.Thread(target=_writer, daemon=True)
        th.start()
        th.join(timeout=max(timeout_s, 0.001))
        if th.is_alive():
            self.kill()
            th.join(timeout=1.0)
            return False
        if err:
            return False
        return True

    def ask(self, obj: dict[str, Any], limit: float) -> tuple[bool, Any, float]:
        """→ (ok, 解析结果, 用时)。ok=False 表示超时 / 崩溃 / 非 JSON / 越限 / 抢跑，bot 随即作废。"""
        if self.dead:
            return False, None, 0.0
        # 发消息前若有未请求的滞留输出，说明子进程多行抢跑失步，立即终止
        if not self.q.empty():
            self.kill()
            return False, None, 0.0
        t0 = time.monotonic()
        payload = (json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if not self._write_with_timeout(payload, limit):
            self.kill()
            return False, None, time.monotonic() - t0
        rem = limit - (time.monotonic() - t0)
        try:
            kind, line = self.q.get(timeout=max(rem, 0.001))
        except queue.Empty:
            self.kill()
            return False, None, time.monotonic() - t0
        dt = time.monotonic() - t0
        if kind != "line" or not line:
            self.kill()
            return False, None, dt
        try:
            return True, json.loads(line), dt
        except Exception:
            self.kill()
            return False, None, dt

    def _cleanup_resources(self) -> None:
        for stream in (self.p.stdin, self.p.stdout):
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
        if self._cwd and os.path.isdir(self._cwd):
            shutil.rmtree(self._cwd, ignore_errors=True)

    def kill(self) -> None:
        self.dead = True
        try:
            if self.p.poll() is None:
                self.p.kill()
        except Exception:
            pass
        try:
            self.p.wait(timeout=CLOSE_WAIT_S)
        except Exception:
            pass
        self._cleanup_resources()

    def close(self) -> None:
        try:
            if not self.dead and self.p.stdin is not None:
                self._write_with_timeout(b'{"type":"end"}\n', CLOSE_WAIT_S)
                try:
                    self.p.stdin.close()
                except Exception:
                    pass
                try:
                    self.p.wait(timeout=CLOSE_WAIT_S)
                    return
                except subprocess.TimeoutExpired:
                    pass
            self.kill()
        finally:
            self._cleanup_resources()


# ------------------------------------------------------------------ 指标
def recall_groups(rank: list[str], groups: list[list[str]], k: int) -> tuple[float, float]:
    valid_groups = [g for g in groups if g]
    if not valid_groups:
        return 0.0, 0.0
    top = set(rank[:k])
    cov = [bool(top & set(g)) for g in valid_groups]
    return sum(cov) / len(cov), float(all(cov))


def ndcg10(rank: list[str], rel: dict[str, int | float]) -> float:
    """与 UMB / xbench bench_ir.metrics 相同：gain = 2^rel - 1（仅计正相关 rel > 0），log2(i+2) 折扣。"""
    pos_rel = {d: min(float(g), 30.0) for d, g in rel.items() if isinstance(g, (int, float)) and not isinstance(g, bool) and g > 0}
    if not pos_rel:
        return 0.0
    dcg = sum((2.0 ** pos_rel[d] - 1.0) / math.log2(i + 2) for i, d in enumerate(rank[:10]) if d in pos_rel)
    ideal = sorted(pos_rel.values(), reverse=True)[:10]
    idcg = sum((2.0 ** g - 1.0) / math.log2(i + 2) for i, g in enumerate(ideal))
    return (dcg / idcg) if idcg > 0.0 else 0.0


def score_q(q: dict[str, Any], rank: list[str], metric: str) -> dict[str, float]:
    if metric == "ndcg@10":
        rel = q.get("qrels") or {}
        pos_keys = {d for d, g in rel.items() if isinstance(g, (int, float)) and not isinstance(g, bool) and g > 0}
        r10 = (len(set(rank[:10]) & pos_keys) / len(pos_keys)) if pos_keys else 0.0
        return {"ndcg@10": ndcg10(rank, rel), "r@10": r10}
    groups = q.get("evidence_groups") or []
    r5, a5 = recall_groups(rank, groups, 5)
    r10, a10 = recall_groups(rank, groups, 10)
    return {"r@10": r10, "r@5": r5, "all@10": a10}


def scored(q: dict[str, Any], metric: str | None = None) -> bool:
    if metric == "ndcg@10":
        rel = q.get("qrels") or {}
        return any(isinstance(g, (int, float)) and not isinstance(g, bool) and g > 0 for g in rel.values())
    if metric == "r@10":
        groups = q.get("evidence_groups") or []
        return any(bool(g) for g in groups)
    return bool(q.get("evidence_groups") or q.get("qrels"))


# ------------------------------------------------------------------ 数据
def read_file(p: str) -> dict[str, Any]:
    if p.endswith(".gz"):
        with gzip.open(p, "rt", encoding="utf-8") as f:
            return json.load(f)
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def list_files(path: str) -> list[str]:
    if os.path.isfile(path):
        return [path]
    return sorted(
        (os.path.join(path, f) for f in os.listdir(path) if f.endswith(".json") or f.endswith(".json.gz")),
        key=_natural_key,
    )


def load_sets(path: str, only: set[str] | None = None) -> dict[str, dict[str, Any]]:
    """→ {集名: 合并后的集}（partK 按自然数序合并）。--sets 含未知集名时立即报错。"""
    by: dict[str, list[str]] = collections.defaultdict(list)
    all_bases: set[str] = set()
    for f in list_files(path):
        base = os.path.basename(f).split(".")[0]
        all_bases.add(base)
        if only and base not in only:
            continue
        by[base].append(f)
    if only:
        missing = sorted(only - all_bases)
        if missing:
            raise ValueError(f"未知评测集名称：{', '.join(missing)}（可选：{', '.join(sorted(all_bases))}）")
    out: dict[str, dict[str, Any]] = {}
    for name, fs in sorted(by.items()):
        merged = None
        for f in sorted(fs, key=_natural_key):
            d = read_file(f)
            if "banks" not in d:
                print(f"  跳过非 v0.2 文件 {f}", file=sys.stderr)
                continue
            if merged is None:
                merged = d
            else:
                merged["banks"] += d["banks"]
                merged["questions"] += d["questions"]
        if merged:
            merged.pop("part", None)
            out[merged["name"]] = merged
    return out


# ------------------------------------------------------------------ 跑一集
def _prepare_bank_view(
    ds_name: str,
    group: str,
    bank_idx: int,
    bank: dict[str, Any],
    qs: list[dict[str, Any]],
    rng: random.Random,
) -> tuple[str, list[dict[str, Any]], dict[str, str], list[tuple[str, dict[str, Any]]]]:
    """构造协议边界匿名视图：
    - 所有集合均匿名化 bank_id / doc.id / qid，彻底阻断 qid->doc_id 与 category 泄漏；
    - 无序检索集（en/zh）随机置换 docs 与 questions 顺序，阻断语料位置聚集作弊；
    - 记忆对话集（mem）保留原始 docs 顺序（合法对话时序），仅随机置换 questions 顺序。
    """
    pub_bank_id = f"b_{bank_idx:04d}"
    docs_seq = list(bank["docs"])
    if group in ("en", "zh"):
        rng.shuffle(docs_seq)

    pub_docs: list[dict[str, Any]] = []
    pub_to_orig: dict[str, str] = {}
    for di, orig_doc in enumerate(docs_seq, 1):
        pub_doc_id = f"d_{di:06d}"
        pub_to_orig[pub_doc_id] = orig_doc["id"]
        pub_doc = {k: v for k, v in orig_doc.items() if k != "id"}
        pub_doc["id"] = pub_doc_id
        pub_docs.append(pub_doc)

    qs_seq = list(qs)
    rng.shuffle(qs_seq)
    pub_qs = [(f"q_{qi:05d}", q) for qi, q in enumerate(qs_seq, 1)]
    return pub_bank_id, pub_docs, pub_to_orig, pub_qs


def run_set(bot_path: str, ds: dict[str, Any], per_q: bool = False, seed: int = DEFAULT_SEED) -> dict[str, Any]:
    metric = ds["metric"]
    group = ds["group"]
    rng = random.Random(hashlib.sha256(f"{seed}|{ds['name']}".encode("utf-8")).digest())

    qby: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for q in ds["questions"]:
        qby[q["bank_id"]].append(q)
    chars_of = {b["bank_id"]: sum(len(d["text"]) for d in b["docs"]) for b in ds["banks"]}
    budget = set_limit(sum(chars_of.values()), len(ds["questions"]))
    used, restarts, fouls, timeouts = 0.0, 0, 0, 0
    pred: dict[tuple[str, str], list[str]] = {}
    bot: Bot | None = None
    t_index = 0.0

    try:
        bank_idx = 0
        for b in ds["banks"]:
            qs = qby.get(b["bank_id"], [])
            if not qs:
                continue
            if used >= budget:
                break
            bank_idx += 1
            if bot is None or bot.dead:
                if bot is not None:
                    if restarts >= MAX_RESTARTS:
                        break
                    restarts += 1
                bot = Bot(bot_path)

            pub_bank_id, pub_docs, pub_to_orig, pub_qs = _prepare_bank_view(
                ds["name"], group, bank_idx, b, qs, rng
            )
            ok, _r, dt = bot.ask(
                {"type": "memory", "set": ds["name"], "bank_id": pub_bank_id, "docs": pub_docs},
                min(mem_limit(chars_of[b["bank_id"]]), budget - used),
            )
            used += dt
            t_index += dt
            if not ok:
                timeouts += 1
                continue

            for pub_qid, q in pub_qs:
                if used >= budget:
                    break
                ok, r, dt = bot.ask(
                    {"type": "query", "qid": pub_qid, "bank_id": pub_bank_id, "question": q["question"]},
                    min(STEP_LIMIT, budget - used),
                )
                used += dt
                if not ok:
                    timeouts += 1
                    break
                rk = r.get("ranking") if isinstance(r, dict) else None
                if not isinstance(rk, list):
                    fouls += 1
                    rk = []
                ex = set(q.get("exclude") or [])
                seen: set[str] = set()
                clean: list[str] = []
                for x in rk[:MAX_RANK_SCAN]:
                    if not isinstance(x, str):
                        continue
                    orig_id = pub_to_orig.get(x)
                    if orig_id is not None and orig_id not in seen and orig_id not in ex:
                        seen.add(orig_id)
                        clean.append(orig_id)
                        if len(clean) >= TOPK:
                            break
                pred[(b["bank_id"], q["qid"])] = clean
    finally:
        if bot is not None:
            bot.close()

    rows: dict[str, dict[str, list[float]]] = collections.defaultdict(lambda: collections.defaultdict(list))
    perq: dict[str, float] = {}
    for q in ds["questions"]:
        if not scored(q, metric):
            continue
        s = score_q(q, pred.get((q["bank_id"], q["qid"]), []), metric)
        perq[q["qid"]] = round(s[metric], 4)
        for c in {q.get("category") or "-", "ALL"}:
            for m, v in s.items():
                rows[c][m].append(v)

    if not rows:
        res: dict[str, Any] = {"ALL": {metric: 0.0, "n": 0}}
    else:
        res = {
            c: {m: round(sum(v) / len(v), 4) for m, v in d.items()} | {"n": len(d[metric])}
            for c, d in rows.items()
        }
    res["_meta"] = {
        "metric": metric,
        "group": group,
        "split": ds.get("split"),
        "official": ds.get("official", False),
        "practice_only": ds.get("practice_only", False),
        "seconds": round(used, 2),
        "seconds_memory": round(t_index, 2),
        "time_budget": round(budget, 1),
        "fouls": fouls,
        "timeouts_or_crashes": timeouts,
        "restarts": restarts,
        "answered": len(pred),
        "n_questions": len(ds["questions"]),
    }
    if per_q:
        res["_per_q"] = perq
    return res


def macro(res: dict[str, Any], sets: dict[str, dict[str, Any]], pred_fn) -> dict[str, Any] | None:
    ks = [n for n in sets if pred_fn(sets[n])]
    if not ks:
        return None
    main_score = sum(res[n]["ALL"][sets[n]["metric"]] for n in ks) / len(ks)
    grp: dict[str, float] = {}
    for g in ("mem", "en", "zh"):
        kg = [n for n in ks if sets[n]["group"] == g]
        if kg:
            grp[g] = round(sum(res[n]["ALL"][sets[n]["metric"]] for n in kg) / len(kg), 4)
    return {"score": round(main_score, 4), "n_sets": len(ks), "sets": ks, "groups": grp}


def _atomic_write_json(path: str, payload: dict[str, Any]) -> None:
    abs_path = os.path.abspath(path)
    parent = os.path.dirname(abs_path)
    os.makedirs(parent, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=parent, delete=False, newline="\n") as fh:
        tmp_path = fh.name
        fh.write(text)
    try:
        os.replace(tmp_path, abs_path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--bot")
    g.add_argument("--bots")
    ap.add_argument("--data", required=True)
    ap.add_argument("--sets", default="")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--per-q", action="store_true")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    if a.jobs < 1:
        sys.exit("--jobs 必须为正整数")

    bots = (
        [a.bot]
        if a.bot
        else sorted(
            os.path.join(a.bots, f)
            for f in os.listdir(a.bots)
            if f.endswith(".py") and not f.startswith("_")
        )
    )
    if not bots:
        sys.exit("未找到任何可评测的 .py 提交文件")

    only = {s.strip() for s in a.sets.split(",") if s.strip()} if a.sets else None
    try:
        sets = load_sets(a.data, only)
    except ValueError as exc:
        sys.exit(str(exc))
    if not sets:
        sys.exit("没有可跑的集")

    order = sorted(
        sets,
        key=lambda n: ({"mem": 0, "en": 1, "zh": 2}[sets[n]["group"]], sets[n].get("practice_only", False), n),
    )
    out: dict[str, Any] = {}
    for b in bots:
        name = os.path.splitext(os.path.basename(b))[0]
        t0 = time.monotonic()
        with ThreadPoolExecutor(max(1, a.jobs)) as ex:
            futs = {n: ex.submit(run_set, b, sets[n], a.per_q, a.seed) for n in order}
            res = {n: f.result() for n, f in futs.items()}
        is_test = lambda s: s.get("split") == "test" and not s.get("practice_only")
        is_dev = lambda s: s.get("split") == "dev" and not s.get("practice_only")
        res["_macro_official"] = macro(res, sets, is_test)
        res["_macro_dev"] = macro(res, sets, is_dev)
        res["_wall_seconds"] = round(time.monotonic() - t0, 1)
        out[name] = res
        print(f"\n== {name}   ({res['_wall_seconds']}s, seed={a.seed})")
        print(f"{'集':18s} {'组':3s} {'指标':8s} {'题数':>5s} {'分数':>7s}  备注")
        for n in order:
            r, m = res[n], res[n]["_meta"]
            flag = "练习" if m["practice_only"] else ""
            bad = f" 犯规{m['fouls']}" if m["fouls"] else ""
            bad += (
                f" 超时/崩溃{m['timeouts_or_crashes']} 未答{m['n_questions'] - m['answered']}"
                if m["timeouts_or_crashes"] or m["answered"] < m["n_questions"]
                else ""
            )
            print(f"{n:18s} {m['group']:3s} {m['metric']:8s} {r['ALL']['n']:5d} {r['ALL'][m['metric']]:7.4f}  {flag}{bad} {m['seconds']:.0f}s")
        for k in ("_macro_official", "_macro_dev"):
            if res[k]:
                print(
                    f"{k[1:]}: {res[k]['score']:.4f}（{res[k]['n_sets']} 集）  "
                    + "  ".join(f"{g} {v:.4f}" for g, v in res[k]["groups"].items())
                )
    if a.out:
        _atomic_write_json(a.out, out)


if __name__ == "__main__":
    main()
