# -*- coding: utf-8 -*-
"""前沿实验室对局回放构建、存储与校验（afb-ai-training-replay/1）。

只依赖 Python 标准库，基于 engine.play() 返回的结果构建结构化、确定性的回放 JSON，
并提供文件哈希、帧摘要与针对引擎终局结算的一致性校验。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import archive as _archive  # noqa: E402

FORMAT = "afb-ai-training-replay/1"
PROBLEM = "ai_training"
DAY = 24
VALUE_SCALE = 12710.23

REQUIRED_LAB_FIELDS = ("value", "cash", "C", "compute", "staff", "eq", "rep", "runs")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    return _archive.sha256_path(path)


def sha256_path(path: str) -> str:
    return _archive.sha256_path(path)


def _norm_seats(seats: list[dict[str, Any]] | None, res: dict[str, Any]) -> list[dict[str, Any]]:
    fallback_labels = [str(res.get("A", "A")), str(res.get("B", "B"))]
    out = []
    for i in range(2):
        src = seats[i] if seats and i < len(seats) and isinstance(seats[i], dict) else {}
        out.append({
            "seat": int(src.get("seat", i)),
            "label": str(src.get("label") or fallback_labels[i]),
            "file": str(src.get("file") or ""),
            "file_sha256": str(src.get("file_sha256") or ""),
        })
    return out


def build_replay(
    res: dict[str, Any],
    seats: list[dict[str, Any]] | None,
    seed: int,
    ticks: int,
    mode: str = "adv",
    engine_sha: str = "",
    every: int = 1,
) -> dict[str, Any]:
    """根据 engine.play() 的返回值构建标准回放 JSON 字典。"""
    mode = "solo" if mode == "solo" else "adv"
    every = max(1, int(every))
    raw_replay = res.get("replay") or {}
    raw_days = raw_replay.get("days") or []
    raw_events = raw_replay.get("events") or []

    # 保持事件按 tick 稳定排序，并补充 day 字段与结构化 details
    indexed_events = sorted(enumerate(raw_events), key=lambda pair: (int(pair[1].get("t", 0)), pair[0]))
    events: list[dict[str, Any]] = []
    bankrupt_day: list[int | None] = [None, None]
    for _, ev in indexed_events:
        t_val = int(ev.get("t", 0))
        d_val = t_val // DAY
        ev_type = str(ev.get("type", "unknown"))
        details = {k: v for k, v in ev.items() if k not in ("t", "day", "type")}
        if ev_type == "bankrupt":
            lab = details.get("lab")
            if lab == "A" and bankrupt_day[0] is None:
                bankrupt_day[0] = d_val
            elif lab == "B" and bankrupt_day[1] is None:
                bankrupt_day[1] = d_val
        events.append({
            "t": t_val,
            "day": d_val,
            "type": ev_type,
            "details": details,
        })

    # 按 every 采样每日快照，始终保留首帧与终局帧
    days: list[dict[str, Any]] = []
    n_days = len(raw_days)
    for idx, d in enumerate(raw_days):
        if every > 1 and idx != 0 and idx != n_days - 1 and (idx % every != 0):
            continue
        day_num = int(d.get("day", idx))
        shares = {str(k): round(float(v), 4) for k, v in (d.get("shares") or {}).items()}
        row: dict[str, Any] = {"day": day_num, "shares": shares}
        for seat_i, lab in enumerate(("A", "B")):
            src = d.get(lab) or {}
            row[lab] = {
                "value": round(float(src.get("value", 0.0)), 2),
                "cash": round(float(src.get("cash", 0.0)), 2),
                "C": round(float(src.get("C", 0.0)), 4),
                "compute": int(round(float(src.get("compute", 0)))),
                "staff": int(src.get("staff", 0)),
                "eq": round(float(src.get("eq", 1.0)), 4),
                "rep": round(float(src.get("rep", 0.0)), 3),
                "runs": int(src.get("runs", 0)),
                "bk": bool(bankrupt_day[seat_i] is not None and day_num >= bankrupt_day[seat_i]),
            }
        days.append(row)

    norm_seats = _norm_seats(seats, res)
    vals = [round(float(x), 2) for x in (res.get("value") or [0.0, 0.0])[:2]]
    cs = [round(float(x), 5) for x in (res.get("C") or [0.0, 0.0])[:2]]
    vdetail = res.get("value_detail") or [{}, {}]
    equities = [round(float((vdetail[i] if i < len(vdetail) else {}).get("equity", 1.0)), 5) for i in range(2)]
    cashes = [round(float((vdetail[i] if i < len(vdetail) else {}).get("cash", 0.0)), 2) for i in range(2)]
    bankrupt = [bankrupt_day[0] is not None, bankrupt_day[1] is not None]

    if mode == "solo":
        winner = "solo"
    elif abs(vals[0] - vals[1]) < 1e-6:
        winner = "draw"
    else:
        winner = "A" if vals[0] > vals[1] else "B"

    adv_a = round(50.0 + 50.0 * math.tanh((vals[0] - vals[1]) / VALUE_SCALE), 2)
    adv_b = round(50.0 + 50.0 * math.tanh((vals[1] - vals[0]) / VALUE_SCALE), 2)

    meta = {
        "format": FORMAT,
        "problem": PROBLEM,
        "mode": mode,
        "seed": int(seed),
        "ticks": int(ticks),
        "sample_every": every,
        "value_scale": VALUE_SCALE,
        "engine_sha": str(engine_sha or ""),
        "seats": norm_seats,
        "labels": [s["label"] for s in norm_seats],
        "winner": winner,
        "final_value": vals,
        "final_C": cs,
        "final_equity": equities,
        "final_cash": cashes,
        "bankrupt": bankrupt,
        "adv_score": [adv_a, adv_b],
        "solo_score": round(100.0 * cs[0], 2),
        "faults": res.get("faults") or [],
        "value_detail": vdetail,
        "models": res.get("models") or [[], []],
        "tech": res.get("tech") or [[], []],
        "totals": res.get("totals") or [{}, {}],
    }
    return {
        "format": FORMAT,
        "meta": meta,
        "days": days,
        "events": events,
    }


def save_replay(path: str, data: dict[str, Any]) -> str:
    """将回放 JSON 以紧凑 UTF-8 写入目标路径。"""
    abs_path = os.path.abspath(path)
    parent = os.path.dirname(abs_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(abs_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    return abs_path


def frame_digest(data: dict[str, Any]) -> str:
    """计算回放确定性内容摘要（覆盖 days 与 events，不受耗时统计影响）。"""
    payload = {"days": data.get("days") or [], "events": data.get("events") or []}
    return sha256_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def verify_against_engine(data: dict[str, Any], res: dict[str, Any]) -> tuple[bool, str]:
    """校验回放结构、帧完整性及终局估值/能力 C 与引擎结果一致。"""
    if not isinstance(data, dict):
        return False, "回放顶层必须是 dict"
    if data.get("format") != FORMAT:
        return False, f"回放格式不匹配：{data.get('format')!r} != {FORMAT!r}"
    meta = data.get("meta")
    if not isinstance(meta, dict):
        return False, "缺少 meta 字段"
    if meta.get("format") != FORMAT or meta.get("problem") != PROBLEM:
        return False, "meta.format 或 meta.problem 不匹配"
    if meta.get("mode") not in ("adv", "solo"):
        return False, f"未知赛制 mode：{meta.get('mode')!r}"
    seats = meta.get("seats")
    if not isinstance(seats, list) or len(seats) != 2:
        return False, "meta.seats 必须包含 2 个座位"

    days = data.get("days")
    if not isinstance(days, list) or not days:
        return False, "days 为空或类型错误"
    prev_day = -1
    for i, d in enumerate(days):
        if not isinstance(d, dict) or "day" not in d or "shares" not in d:
            return False, f"第 {i} 帧缺少基础字段"
        day_num = int(d["day"])
        if day_num <= prev_day:
            return False, f"days 时间序异常：第 {i} 帧 day={day_num} <= {prev_day}"
        prev_day = day_num
        if not isinstance(d["shares"], dict):
            return False, f"第 {i} 帧 shares 必须是 dict"
        for lab in ("A", "B"):
            side = d.get(lab)
            if not isinstance(side, dict):
                return False, f"第 {i} 帧缺少 {lab} 数据"
            for field in REQUIRED_LAB_FIELDS:
                if field not in side or not isinstance(side[field], (int, float)) or not math.isfinite(float(side[field])):
                    return False, f"第 {i} 帧 {lab}.{field} 缺失或非有限数"

    events = data.get("events")
    if not isinstance(events, list):
        return False, "events 必须是列表"
    prev_t = -1
    for i, ev in enumerate(events):
        if not isinstance(ev, dict):
            return False, f"第 {i} 条事件不是 dict"
        for key in ("t", "day", "type", "details"):
            if key not in ev:
                return False, f"第 {i} 条事件缺少 {key}"
        t_val = int(ev["t"])
        if t_val < prev_t:
            return False, f"事件时间倒流：第 {i} 条 t={t_val} < {prev_t}"
        if int(ev["day"]) != t_val // DAY:
            return False, f"第 {i} 条事件 day={ev['day']} 与 t={t_val} 不一致"
        if not isinstance(ev["details"], dict):
            return False, f"第 {i} 条事件 details 必须是 dict"
        prev_t = t_val

    if "seed" in res and int(meta.get("seed", -1)) != int(res["seed"]):
        return False, f"seed 不一致：{meta.get('seed')} != {res['seed']}"
    if "ticks" in res and int(meta.get("ticks", -1)) != int(res["ticks"]):
        return False, f"ticks 不一致：{meta.get('ticks')} != {res['ticks']}"

    want_val = [round(float(x), 2) for x in res["value"]]
    want_c = [round(float(x), 5) for x in res["C"]]
    got_meta_val = [round(float(x), 2) for x in (meta.get("final_value") or [])]
    got_meta_c = [round(float(x), 5) for x in (meta.get("final_C") or [])]
    if got_meta_val != want_val:
        return False, f"meta.final_value {got_meta_val} != 引擎结算 {want_val}"
    if got_meta_c != want_c:
        return False, f"meta.final_C {got_meta_c} != 引擎结算 {want_c}"

    last = days[-1]
    last_val = [round(float(last["A"]["value"]), 2), round(float(last["B"]["value"]), 2)]
    last_c = [round(float(last["A"]["C"]), 4), round(float(last["B"]["C"]), 4)]
    for idx in range(2):
        if abs(last_val[idx] - want_val[idx]) > 0.02:
            return False, f"末帧估值 {last_val} != 引擎结算 {want_val}"
        if abs(last_c[idx] - want_c[idx]) > 1e-4 + 1e-9:
            return False, f"末帧能力 C {last_c} != 引擎结算 {want_c}"

    return True, f"校验通过（终局估值 {want_val}，能力 C {want_c}，共 {len(days)} 天 / {len(events)} 事件）"
