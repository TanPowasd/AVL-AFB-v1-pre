# -*- coding: utf-8 -*-
"""《压缩奇点》隐藏动态测试生成器（八大纯计算机制家族 F1 ~ F8）。

本文件属于保密评测生成器，绝不得导出至选手工作区。
每个测试点均由单一 64 位种子完全确定性地生成，并经过严格非退化与可压缩性守卫校验。
"""
from __future__ import annotations

import itertools
import random
from typing import Any

# 经数学验证的 GF(2) 本原多项式（每个元组表示 feedback taps，对应 x^d + sum_{t in taps} x^(d-t) + 1）
# 使用标准右移 Galois/Fibonacci 形式：new_bit = xor_{t in taps} ((state >> (d - t)) & 1)
# 其中 taps 均包含 d（即常数项 1），保证周期为 2^d - 1（最大长度序列 m-sequence）。
PRIMITIVE_POLYS: dict[int, list[tuple[int, ...]]] = {
    7:  [(7, 1), (7, 3), (7, 4), (7, 6)],
    8:  [(8, 7, 6, 1), (8, 6, 3, 2), (8, 7, 2, 1), (8, 6, 5, 2)],
    9:  [(9, 4), (9, 5), (9, 7, 4, 2), (9, 7, 5, 1)],
    10: [(10, 3), (10, 7), (10, 9, 7, 3), (10, 5, 3, 2)],
    11: [(11, 2), (11, 9), (11, 6, 5, 1), (11, 4, 2, 1)],
    12: [(12, 10, 9, 3), (12, 6, 4, 1), (12, 8, 5, 1), (12, 8, 2, 1)],
    13: [(13, 10, 4, 1), (13, 12, 8, 2), (13, 11, 8, 3), (13, 12, 7, 5)],
    14: [(14, 12, 11, 1), (14, 13, 4, 2), (14, 5, 4, 3), (14, 7, 5, 3)],
    15: [(15, 1), (15, 4), (15, 7), (15, 8)],
    16: [(16, 12, 9, 7), (16, 12, 10, 3), (16, 11, 6, 5), (16, 11, 9, 7)],
    17: [(17, 3), (17, 5), (17, 6), (17, 11)],
    18: [(18, 7), (18, 11), (18, 14, 11, 10), (18, 10, 5, 4)],
    19: [(19, 13, 10, 1), (19, 17, 16, 6), (19, 8, 7, 5), (19, 16, 15, 6)],
    20: [(20, 3), (20, 17), (20, 19, 9, 8), (20, 14, 13, 1)],
    21: [(21, 2), (21, 19), (21, 12, 10, 1), (21, 14, 5, 3)],
    22: [(22, 1), (22, 21), (22, 15, 12, 9), (22, 20, 11, 9)],
    23: [(23, 5), (23, 9), (23, 14), (23, 18)],
    24: [(24, 23, 10, 8), (24, 16, 9, 7), (24, 20, 19, 17), (24, 10, 6, 1)],
}

# 1D 初等元胞自动机中具备强混沌/复杂动力学的规则号（剔除易被字典压平的简单分形规则）
RICH_ECA_RULES = (
    30, 45, 73, 75, 86, 89, 101, 105, 106, 110,
    120, 135, 137, 149, 150, 169, 193, 225,
)


def _gen_lfsr_bits(degree: int, taps: tuple[int, ...], init_state: int, length: int) -> str:
    """高速精确生成最大周期 Fibonacci LFSR 比特流（等价于逐位递推，利用字节/块展开加速）。

    递推定义（对 k >= degree）：
        b[k] = xor_{t in taps} b[k - t]
    初始前 degree 位为 init_state 的高位优先展开（b[0] = (init_state >> (degree - 1)) & 1）。
    """
    if length <= 0:
        return ""
    if init_state == 0:
        init_state = 1
    mask = (1 << degree) - 1
    init_state &= mask
    # 利用 Python 大整数移位向量化：每次可同时推进 step = min(taps) 位！
    step = min(taps)
    # 预先把初值位按 MSB-first 打包进 big int：高 bit 是早期输出，低 bit 是最新输出
    # 递推时若 cur 已含 M 位（最高位是 b[0]，最低位是 b[M-1]），
    # 下一批最多 step 位：对 0 <= j < step，b[M + j] = xor_{t in taps} b[M + j - t]。
    # b[M + j - t] 对应 cur 右移 (t - step) 位后的低 step 位（若 cur 仅截取所需窗口则 O(1) 位操作！）
    chunks: list[str] = []
    # 先用精确窗口滑动生成（每次把 cur 保持在最多 degree + 4096 位以内，避免大整数变慢）
    buf = init_state
    buf_len = degree
    produced = 0
    FLUSH_BITS = 2048
    step_mask = (1 << step) - 1

    while produced + buf_len < length:
        # 一次性并行计算接下来的 step 位
        # 当前 buf 最低位是 b[n-1]，因此 b[n - t] 在 bit 位置 (t - 1)
        # 要计算的 step 位依次为 j=0..step-1（j=0 放高位 step-1，j=step-1 放低位 0）：
        # b[n + j - t] 位于原 buf 的 bit 位置 (t - 1 - j)，
        # 因此将 (buf >> (t - step)) & step_mask 直接按位异或即可得到这 step 位！
        nxt = 0
        for t in taps:
            nxt ^= (buf >> (t - step)) & step_mask
        buf = (buf << step) | nxt
        buf_len += step
        if buf_len >= degree + FLUSH_BITS:
            emit_n = buf_len - degree
            emit_val = buf >> degree
            chunks.append(f"{emit_val:0{emit_n}b}")
            buf &= mask
            buf_len = degree
            produced += emit_n

    if buf_len > 0:
        chunks.append(f"{buf:0{buf_len}b}")
    out = "".join(chunks)
    return out[:length]


def _xor_bitstrings(a: str, b: str) -> str:
    n = len(a)
    assert len(b) == n
    # 分块转 int 异或，避免单次百万位大整数十进制/二进制转换开销
    CHUNK = 16384
    parts: list[str] = []
    for i in range(0, n, CHUNK):
        ca = a[i:i + CHUNK]
        cb = b[i:i + CHUNK]
        m = len(ca)
        xv = int(ca, 2) ^ int(cb, 2)
        parts.append(f"{xv:0{m}b}")
    return "".join(parts)


def _is_trivially_periodic(s: str, max_p: int = 64) -> bool:
    sample = s[:min(len(s), 4096)]
    n = len(sample)
    for p in range(1, min(max_p, n // 4) + 1):
        if sample[:-p] == sample[p:]:
            return True
    return False


def _ones_ratio(s: str) -> float:
    return s.count("1") / max(1, len(s))


def _has_high_block_diversity(s: str, min_unique_32: int = 120) -> bool:
    """检查前段与尾段 8192 位中 32 位非重叠块的多样性，快速拦截中后期周期塌缩与低熵退化。"""
    if len(s) < 16384:
        return True
    for seg in (s[:8192], s[-8192:]):
        if len({seg[i:i + 32] for i in range(0, 8192, 32)}) < min_unique_32:
            return False
    return True


def _is_prime(n: int) -> bool:
    """确定性 Miller-Rabin 素性检验（对所有 64 位以内整数精确）。"""
    if n < 2 or n % 2 == 0:
        return n == 2
    d = n - 1
    r = 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for a in (2, 3, 5, 7, 11, 13, 17, 19, 23):
        if a >= n:
            continue
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(r - 1):
            x = (x * x) % n
            if x == n - 1:
                break
        else:
            return False
    return True


def _generate_f1(length: int, rng: random.Random) -> tuple[str, dict[str, Any]]:
    """F1（递归语法流）：随机 L-System 态射展开或扭转平衡 SLP 语法树。"""
    for _attempt in range(32):
        sub_mode = rng.choice(["lsystem", "twisted_slp"])
        if sub_mode == "lsystem":
            k = rng.randint(3, 5)
            syms = [chr(ord("A") + i) for i in range(k)]
            # 构造强连通且严格增长的态射规则（每个符号展开为 2..4 个符号）
            rules: dict[str, str] = {}
            for i, sym in enumerate(syms):
                rhs_len = rng.randint(2, 4)
                # 保证包含下一个环状符号以强连通，防止退化到单符号子集
                rhs_chars = [syms[(i + 1) % k]]
                while len(rhs_chars) < rhs_len:
                    rhs_chars.append(rng.choice(syms))
                rng.shuffle(rhs_chars)
                rules[sym] = "".join(rhs_chars)
            # 随机非退化终结比特投影（长度 1..3，确保含 0 与 1）
            proj: dict[str, str] = {}
            for sym in syms:
                plen = rng.randint(1, 3)
                proj[sym] = "".join(rng.choice("01") for _ in range(plen))
            if set("".join(proj.values())) != {"0", "1"}:
                proj[syms[0]] = "01"
                proj[syms[1]] = "10"

            axiom = rng.choice(syms)
            cur = axiom
            depth = 0
            # 展开直到总比特长度 >= length
            while len(cur) < length and depth < 40:
                nxt_parts: list[str] = []
                acc_len = 0
                for ch in cur:
                    rep = rules[ch]
                    nxt_parts.append(rep)
                    acc_len += len(rep)
                    if acc_len >= length + 64:
                        break
                cur = "".join(nxt_parts)
                depth += 1

            bits_parts: list[str] = []
            blen = 0
            for ch in cur:
                b = proj[ch]
                bits_parts.append(b)
                blen += len(b)
                if blen >= length:
                    break
            s = "".join(bits_parts)[:length]
            meta = {"family": "F1", "submode": "lsystem", "k": k, "depth": depth, "rules": rules, "proj": proj}
        else:
            # 广义扭转平衡 SLP（类似 Thue-Morse / Rudin-Shapiro 广义递归）
            k = rng.randint(2, 4)
            base_len = rng.randint(3, 7)
            leaves = ["".join(rng.choice("01") for _ in range(base_len)) for _ in range(k)]
            leaves[0] = "0" + leaves[0][1:]
            leaves[1] = "1" + leaves[1][1:]
            # 每层每个非终结符由 2..3 个下层非终结符（可带按位取反标志）拼接而成
            level_rules: list[list[tuple[int, bool]]] = []
            for i in range(k):
                arity = rng.randint(2, 3)
                parts = [((i + 1) % k, bool(rng.randint(0, 1)))]
                for _ in range(arity - 1):
                    parts.append((rng.randrange(k), bool(rng.randint(0, 1))))
                rng.shuffle(parts)
                level_rules.append(parts)

            trans = str.maketrans("01", "10")
            cur_vals = list(leaves)
            depth = 0
            while min(len(v) for v in cur_vals) < length and depth < 24:
                nxt_vals: list[str] = []
                for parts in level_rules:
                    segs: list[str] = []
                    acc = 0
                    for idx, inv in parts:
                        piece = cur_vals[idx].translate(trans) if inv else cur_vals[idx]
                        segs.append(piece)
                        acc += len(piece)
                        if acc >= length + 64:
                            break
                    nxt_vals.append("".join(segs)[:length + 64])
                cur_vals = nxt_vals
                depth += 1
            s = cur_vals[0][:length]
            meta = {"family": "F1", "submode": "twisted_slp", "k": k, "depth": depth}

        ratio = _ones_ratio(s)
        if len(s) == length and 0.15 <= ratio <= 0.85 and not _is_trivially_periodic(s):
            return s, meta
    raise RuntimeError("Failed to generate non-degenerate F1 stream")


def _generate_f2(length: int, rng: random.Random) -> tuple[str, dict[str, Any]]:
    """F2（黑盒协议流）：最大周期 LFSR、交错帧协议或双 LFSR 耦合状态机。"""
    for _attempt in range(32):
        sub_mode = rng.choice(["primitive_lfsr", "framed_protocol", "coupled_lfsr"])
        if sub_mode == "primitive_lfsr":
            # 保证周期 2^d - 1 > length，避免在 LZMA 字典窗口内出现整周期重复
            min_deg = min(23, max(14, length.bit_length() + 1))
            deg = rng.randint(min_deg, 24)
            taps = rng.choice(PRIMITIVE_POLYS[deg])
            init_state = rng.randrange(1, 1 << deg)
            decim = rng.choice([1, 1, 2, 3])
            raw = _gen_lfsr_bits(deg, taps, init_state, length * decim)
            s = raw[::decim][:length] if decim > 1 else raw
            meta = {"family": "F2", "submode": "primitive_lfsr", "degree": deg, "taps": list(taps), "decim": decim}
        elif sub_mode == "framed_protocol":
            sync_len = rng.randint(4, 7)
            sync = "".join(rng.choice("01") for _ in range(sync_len))
            if set(sync) != {"0", "1"}:
                sync = "10" + sync[2:]
            ctr_bits = rng.randint(8, 12)
            ctr_start = rng.randrange(0, 1 << ctr_bits)
            ctr_step = rng.choice([1, 3, 5, 7])
            payload_bits = rng.randint(8, 18)
            lfsr_deg = rng.randint(8, 13)
            lfsr_taps = rng.choice(PRIMITIVE_POLYS[lfsr_deg])
            lfsr_init = rng.randrange(1, 1 << lfsr_deg)
            frame_len = sync_len + ctr_bits + payload_bits + 2  # +2 parity bits (even/odd subsets)
            n_frames = (length + frame_len - 1) // frame_len
            payload_stream = _gen_lfsr_bits(lfsr_deg, lfsr_taps, lfsr_init, n_frames * payload_bits)
            ctr_mask = (1 << ctr_bits) - 1
            frames: list[str] = []
            ctr = ctr_start
            for fi in range(n_frames):
                c_str = f"{ctr:0{ctr_bits}b}"
                p_str = payload_stream[fi * payload_bits:(fi + 1) * payload_bits]
                body = c_str + p_str
                p0 = str(body.count("1") & 1)
                p1 = str(body[::2].count("1") & 1)
                frames.append(sync + body + p0 + p1)
                ctr = (ctr + ctr_step) & ctr_mask
            s = "".join(frames)[:length]
            meta = {
                "family": "F2", "submode": "framed_protocol", "frame_len": frame_len,
                "sync_len": sync_len, "ctr_bits": ctr_bits, "payload_bits": payload_bits, "lfsr_deg": lfsr_deg,
            }
        else:
            # 双短阶 LFSR 组合（XOR 或非线性 AND/MUX 混合，保持低线性复杂度可逆向且总周期 > length）
            d1 = rng.randint(10, 13)
            d2 = rng.randint(11, 14)
            if d2 == d1:
                d2 = d1 + 1
            t1 = rng.choice(PRIMITIVE_POLYS[d1])
            t2 = rng.choice(PRIMITIVE_POLYS[d2])
            s1 = _gen_lfsr_bits(d1, t1, rng.randrange(1, 1 << d1), length)
            s2 = _gen_lfsr_bits(d2, t2, rng.randrange(1, 1 << d2), length)
            comb = rng.choice(["xor", "and_xor"])
            if comb == "xor":
                s = _xor_bitstrings(s1, s2)
            else:
                # s1 ^ (s2 & s3) 其中 s3 为 5..7 位短周期，保持线性复杂度可控
                d3 = rng.randint(7, 9)
                t3 = rng.choice(PRIMITIVE_POLYS[d3])
                s3 = _gen_lfsr_bits(d3, t3, rng.randrange(1, 1 << d3), length)
                CHUNK = 16384
                parts: list[str] = []
                for i in range(0, length, CHUNK):
                    a = s1[i:i + CHUNK]
                    b = s2[i:i + CHUNK]
                    c = s3[i:i + CHUNK]
                    m = len(a)
                    val = int(a, 2) ^ (int(b, 2) & int(c, 2))
                    parts.append(f"{val:0{m}b}")
                s = "".join(parts)
            meta = {"family": "F2", "submode": f"coupled_{comb}", "d1": d1, "d2": d2}

        ratio = _ones_ratio(s)
        if len(s) == length and 0.25 <= ratio <= 0.75 and not _is_trivially_periodic(s):
            return s, meta
    raise RuntimeError("Failed to generate non-degenerate F2 stream")


def _eca_step_bitmask(row: int, width: int, rule: int, cyclic: bool) -> int:
    """用 Python 整数按位并行一步推进整行 1D 初等元胞自动机（MSB=x=0, LSB=x=width-1）。"""
    mask = (1 << width) - 1
    if cyclic:
        # x 的左邻 x-1 在更高一位，故 row >> 1；x=0 的左邻是 x=width-1（最低位）
        left = (row >> 1) | ((row & 1) << (width - 1))
        # x 的右邻 x+1 在更低一位，故 row << 1；x=width-1 的右邻是 x=0（最高位）
        right = ((row << 1) & mask) | (row >> (width - 1))
    else:
        left = row >> 1
        right = (row << 1) & mask
    center = row
    nl = mask ^ left
    nc = mask ^ center
    nr = mask ^ right

    nxt = 0
    for code in range(8):
        if (rule >> code) & 1:
            a = left if (code & 4) else nl
            b = center if (code & 2) else nc
            c = right if (code & 1) else nr
            nxt |= (a & b & c)
    return nxt & mask


def _generate_f3(length: int, rng: random.Random) -> tuple[str, dict[str, Any]]:
    """F3（空间演化切片）：1D 元胞自动机、二阶可逆元胞自动机或流式 2D 因果切片/迷宫。"""
    for _attempt in range(32):
        sub_mode = rng.choice(["eca_1d", "reversible_2row_ca", "causal_2d_maze"])
        if sub_mode == "eca_1d":
            width = rng.randint(64, 480)
            rule = rng.choice(RICH_ECA_RULES)
            cyclic = bool(rng.randint(0, 1))
            n_seeds = rng.randint(1, 6)
            row = 0
            for _ in range(n_seeds):
                row |= 1 << rng.randrange(width)
            n_rows = (length + width - 1) // width
            rows: list[str] = []
            seen_recent: set[int] = set()
            collapsed = False
            for ri in range(n_rows):
                rows.append(f"{row:0{width}b}")
                if ri < 64:
                    seen_recent.add(row)
                row = _eca_step_bitmask(row, width, rule, cyclic)
                if row == 0 or row == (1 << width) - 1:
                    collapsed = True
                    break
            if collapsed or len(seen_recent) < 12:
                continue
            s = "".join(rows)[:length]
            meta = {"family": "F3", "submode": "eca_1d", "width": width, "rule": rule, "cyclic": cyclic}
        elif sub_mode == "reversible_2row_ca":
            width = rng.randint(48, 320)
            rule = rng.choice(RICH_ECA_RULES)
            cyclic = True
            prev = 0
            cur = 0
            for _ in range(rng.randint(1, 5)):
                prev |= 1 << rng.randrange(width)
            for _ in range(rng.randint(1, 5)):
                cur |= 1 << rng.randrange(width)
            n_rows = (length + width - 1) // width
            rows = []
            for _ in range(n_rows):
                rows.append(f"{cur:0{width}b}")
                nxt = _eca_step_bitmask(cur, width, rule, cyclic) ^ prev
                prev, cur = cur, nxt
            s = "".join(rows)[:length]
            meta = {"family": "F3", "submode": "reversible_2row_ca", "width": width, "rule": rule}
        else:
            # 流式 2D 因果迷宫/通道网格：奇数行走廊与偶数列墙体由短阶 LFSR 控制的 Sidewinder/Binary-Tree 生成
            # 宽度 W = 2*cols + 1 <= 257，严格仅需 O(W) 行缓冲即可在单带 VM 原地生成！
            cols = rng.randint(24, 96)
            width = 2 * cols + 1
            deg = rng.randint(9, 14)
            taps = rng.choice(PRIMITIVE_POLYS[deg])
            init_state = rng.randrange(1, 1 << deg)
            n_row_pairs = (length + 2 * width - 1) // (2 * width)
            decisions = _gen_lfsr_bits(deg, taps, init_state, n_row_pairs * cols)

            rows = []
            d_idx = 0
            # 每组输出两行（房间/水平连通行 + 垂直连通行），确定性流式 Binary-Tree 迷宫展开
            for _ in range(n_row_pairs):
                # 房间行：1=通道, 0=墙；偶数列(2c+1)为房间，中间(2c+2)根据 decision 决定向东打通
                room_row = ["0"] * width
                wall_row = ["0"] * width
                for c in range(cols):
                    bit = decisions[d_idx]
                    d_idx += 1
                    room_row[2 * c + 1] = "1"
                    if c == cols - 1:
                        # 最右列只能向下打通
                        wall_row[2 * c + 1] = "1"
                    elif bit == "1":
                        # 向右打通水平通道
                        room_row[2 * c + 2] = "1"
                    else:
                        # 向下打通垂直通道
                        wall_row[2 * c + 1] = "1"
                rows.append("".join(room_row))
                rows.append("".join(wall_row))
            s = "".join(rows)[:length]
            meta = {"family": "F3", "submode": "streaming_maze", "width": width, "cols": cols, "lfsr_deg": deg}

        ratio = _ones_ratio(s)
        if len(s) == length and 0.15 <= ratio <= 0.85 and not _is_trivially_periodic(s):
            return s, meta
    raise RuntimeError("Failed to generate non-degenerate F3 stream")


def _generate_f4(length: int, rng: random.Random) -> tuple[str, dict[str, Any]]:
    """F4（可解耦混淆流）：低阶线性/周期/稀疏掩码与 F1/F2/F3 主流按位异或（XOR）叠加。"""
    for _attempt in range(32):
        base_fam = rng.choice(["F1", "F2", "F3"])
        if base_fam == "F1":
            base_s, base_meta = _generate_f1(length, rng)
        elif base_fam == "F2":
            base_s, base_meta = _generate_f2(length, rng)
        else:
            base_s, base_meta = _generate_f3(length, rng)

        mask_kind = rng.choice(["short_lfsr", "periodic_mask", "sparse_flip"])
        if mask_kind == "short_lfsr":
            m_deg = rng.randint(7, 12)
            # 避免与 F2 同阶同多项式恰好完全抵消
            if base_meta.get("degree") == m_deg:
                m_deg = 7 if m_deg != 7 else 9
            m_taps = rng.choice(PRIMITIVE_POLYS[m_deg])
            m_init = rng.randrange(1, 1 << m_deg)
            mask_s = _gen_lfsr_bits(m_deg, m_taps, m_init, length)
            mask_meta = {"mask": "short_lfsr", "degree": m_deg, "taps": list(m_taps)}
        elif mask_kind == "periodic_mask":
            period = rng.choice([7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47])
            pat = "".join(rng.choice("01") for _ in range(period))
            if pat.count("1") < 2 or pat.count("0") < 2:
                pat = ("10110" * ((period // 5) + 1))[:period]
            mask_s = (pat * ((length // period) + 2))[:length]
            mask_meta = {"mask": "periodic_mask", "period": period}
        else:
            # 稀疏周期翻转：两个互质小周期的乘积/相交位置翻转
            p1 = rng.choice([5, 7, 11, 13])
            p2 = rng.choice([17, 19, 23, 29])
            r1 = rng.randrange(p1)
            r2 = rng.randrange(p2)
            pat_len = p1 * p2
            buf = ["0"] * pat_len
            for i in range(pat_len):
                if i % p1 == r1 or i % p2 == r2:
                    buf[i] = "1"
            pat = "".join(buf)
            mask_s = (pat * ((length // pat_len) + 2))[:length]
            mask_meta = {"mask": "sparse_flip", "p1": p1, "p2": p2}

        s = _xor_bitstrings(base_s, mask_s)
        ratio = _ones_ratio(s)
        if len(s) == length and 0.25 <= ratio <= 0.75 and not _is_trivially_periodic(s):
            return s, {"family": "F4", "base": base_meta, **mask_meta}
    raise RuntimeError("Failed to generate non-degenerate F4 stream")


def _generate_f5(length: int, rng: random.Random) -> tuple[str, dict[str, Any]]:
    """F5（2-进数与算术动力学流）：FCSR 2-进有理数展开（p/q）或二次 Weyl 离散相位流。"""
    for _attempt in range(32):
        sub_mode = rng.choice(["fcsr_2adic", "quadratic_weyl"])
        if sub_mode == "fcsr_2adic":
            # 选取素数 q ∈ [2^20, 2^22]，确保 ord_q(2) > length 且非二次剩余避免短周期
            q = 0
            for _ in range(256):
                cand = rng.randrange(1 << 20, 1 << 22) | 1
                if _is_prime(cand) and pow(2, (cand - 1) // 2, cand) != 1:
                    # 排除极少数 ord_q(2) 偏小的情况（检查常见小因子）
                    if pow(2, (cand - 1) // 3 if (cand - 1) % 3 == 0 else 1, cand) != 1:
                        q = cand
                        break
            if q == 0:
                continue
            p = rng.randrange(1, q)
            CHUNK = 16384
            mask = (1 << CHUNK) - 1
            inv_q = pow(q, -1, 1 << CHUNK)
            r = p
            parts: list[str] = []
            rem = length
            while rem > 0:
                take = min(CHUNK, rem)
                blk = ((-r) * inv_q) & mask
                r = (r + q * blk) >> CHUNK
                bs = f"{blk:0{CHUNK}b}"[::-1]
                parts.append(bs[:take])
                rem -= take
            s = "".join(parts)
            meta = {"family": "F5", "submode": "fcsr_2adic", "q": q, "p": p}
        else:
            # 二次 Weyl 离散相位流：u_k = (A k^2 + B k + C) mod 2^m，b_k = [u_k >= 2^(m-1)]
            m = rng.randint(21, 24)
            M = 1 << m
            mask = M - 1
            A = rng.randrange(1, M) | 1
            B = rng.randrange(1, M) | 1
            C = rng.randrange(0, M)
            half = M >> 1
            u = C
            delta = (A + B) & mask
            step2 = (2 * A) & mask
            out = bytearray(length)
            for i in range(length):
                out[i] = 49 if u >= half else 48
                u = (u + delta) & mask
                delta = (delta + step2) & mask
            s = out.decode("ascii")
            meta = {"family": "F5", "submode": "quadratic_weyl", "m": m, "A": A, "B": B}

        ratio = _ones_ratio(s)
        if len(s) == length and 0.25 <= ratio <= 0.75 and not _is_trivially_periodic(s) and _has_high_block_diversity(s, 140):
            return s, meta
    raise RuntimeError("Failed to generate non-degenerate F5 stream")


def _generate_f6(length: int, rng: random.Random) -> tuple[str, dict[str, Any]]:
    """F6（非线性与停走时钟控制流）：可逆二次 NLFSR、缩减生成器（Shrinking）或交替步进生成器。"""
    for _attempt in range(32):
        sub_mode = rng.choice(["quadratic_nlfsr", "shrinking_lfsr", "alt_step_lfsr"])
        if sub_mode == "quadratic_nlfsr":
            d = rng.randint(21, 23)
            step = 4
            n_lin = rng.randint(2, 4)
            lin = sorted(set([d] + [rng.randint(step, d - 1) for _ in range(n_lin)]))
            q1 = (rng.randint(step, d - 1), rng.randint(step, d - 1))
            while q1[0] == q1[1]:
                q1 = (rng.randint(step, d - 1), rng.randint(step, d - 1))
            q2 = (rng.randint(step, d - 1), rng.randint(step, d - 1))
            while q2[0] == q2[1] or set(q2) == set(q1):
                q2 = (rng.randint(step, d - 1), rng.randint(step, d - 1))
            buf = rng.randrange(1, 1 << d)
            mask = (1 << d) - 1
            smask = (1 << step) - 1
            buf_len = d
            produced = 0
            chunks: list[str] = []
            a1, b1 = q1[0] - step, q1[1] - step
            a2, b2 = q2[0] - step, q2[1] - step
            lin_sh = [t - step for t in lin]
            while produced + buf_len < length:
                nxt = ((buf >> a1) & (buf >> b1)) ^ ((buf >> a2) & (buf >> b2))
                for sh in lin_sh:
                    nxt ^= (buf >> sh)
                nxt &= smask
                buf = (buf << step) | nxt
                buf_len += step
                if buf_len >= d + 2048:
                    en = buf_len - d
                    chunks.append(f"{buf >> d:0{en}b}")
                    buf &= mask
                    buf_len = d
                    produced += en
            chunks.append(f"{buf:0{buf_len}b}")
            s = "".join(chunks)[:length]
            meta = {"family": "F6", "submode": "quadratic_nlfsr", "d": d, "lin": lin, "q1": list(q1), "q2": list(q2)}
        elif sub_mode == "shrinking_lfsr":
            # 控时 LFSR 限定可逆向小阶数 d1 ∈ [7, 10]，数据 LFSR d2 ∈ [12, 16]
            d1 = rng.randint(7, 10)
            d2 = rng.randint(12, 16)
            t1 = rng.choice(PRIMITIVE_POLYS[d1])
            t2 = rng.choice(PRIMITIVE_POLYS[d2])
            raw_len = int(length * 2.15) + 512
            s1 = _gen_lfsr_bits(d1, t1, rng.randrange(1, 1 << d1), raw_len)
            s2 = _gen_lfsr_bits(d2, t2, rng.randrange(1, 1 << d2), raw_len)
            s = "".join(itertools.compress(s2, (c == "1" for c in s1)))[:length]
            meta = {"family": "F6", "submode": "shrinking_lfsr", "d1": d1, "d2": d2}
        else:
            # 交替步进生成器（Alternating-Step）：控时 d1 ∈ [7, 10]，双数据 LFSR d2, d3 ∈ [11, 14]
            d1 = rng.randint(7, 10)
            d2 = rng.randint(11, 14)
            d3 = rng.randint(11, 14)
            if d3 == d2:
                d3 = d2 + 1
            t1 = rng.choice(PRIMITIVE_POLYS[d1])
            t2 = rng.choice(PRIMITIVE_POLYS[d2])
            t3 = rng.choice(PRIMITIVE_POLYS[d3])
            s1 = _gen_lfsr_bits(d1, t1, rng.randrange(1, 1 << d1), length)
            s2 = _gen_lfsr_bits(d2, t2, rng.randrange(1, 1 << d2), length + 4)
            s3 = _gen_lfsr_bits(d3, t3, rng.randrange(1, 1 << d3), length + 4)
            i2 = 0
            i3 = 0
            b2 = s2[0]
            b3 = s3[0]
            out = bytearray(length)
            for i, c in enumerate(s1):
                if c == "1":
                    i2 += 1
                    b2 = s2[i2]
                else:
                    i3 += 1
                    b3 = s3[i3]
                out[i] = 48 if b2 == b3 else 49
            s = out.decode("ascii")
            meta = {"family": "F6", "submode": "alt_step_lfsr", "d1": d1, "d2": d2, "d3": d3}

        ratio = _ones_ratio(s)
        if len(s) == length and 0.25 <= ratio <= 0.75 and not _is_trivially_periodic(s) and _has_high_block_diversity(s, 140):
            return s, meta
    raise RuntimeError("Failed to generate non-degenerate F6 stream")


def _generate_f7(length: int, rng: random.Random) -> tuple[str, dict[str, Any]]:
    """F7（k-自动序列与块坐标置换流）：广义 k-Automatic DFA 或块内位反转/格雷码/矩阵转置置换。"""
    for _attempt in range(64):
        sub_mode = rng.choice(["k_automatic_dfa", "permuted_block"])
        if sub_mode == "k_automatic_dfa":
            k = rng.choice([2, 3, 4, 5])
            Q = rng.randint(8, 12)
            delta = [[0] * k for _ in range(Q)]
            for q in range(Q):
                delta[q][0] = (q + 1) % Q
                for d in range(1, k):
                    delta[q][d] = rng.randrange(Q)
                rng.shuffle(delta[q])
            out_map = [rng.randint(0, 1) for _ in range(Q)]
            if sum(out_map) < 3 or sum(out_map) > Q - 3:
                out_map = [i & 1 for i in range(Q)]
            offset = rng.randint(0, 511)
            N = length + offset
            st = bytearray(N)
            st[0] = 0
            for n in range(1, N):
                st[n] = delta[st[n // k]][n % k]
            tbl = bytes(48 + out_map[q] for q in range(Q))
            s = bytes(tbl[x] for x in st[offset:N]).decode("ascii")
            # 严筛高复杂度 k-自动序列（拒绝低熵塌缩 DFA）
            if not _has_high_block_diversity(s, 64):
                continue
            meta = {"family": "F7", "submode": "k_automatic_dfa", "k": k, "Q": Q, "offset": offset}
        else:
            base_s, base_meta = _generate_f2(length, rng) if rng.random() < 0.6 else _generate_f3(length, rng)
            pmode = rng.choice(["bitrev", "gray", "transpose"])
            if pmode == "bitrev":
                m = rng.randint(10, 13)
                B = 1 << m
                perm = [int(f"{i:0{m}b}"[::-1], 2) for i in range(B)]
                pmeta: dict[str, Any] = {"perm": "bitrev", "m": m, "block": B}
            elif pmode == "gray":
                m = rng.randint(10, 13)
                B = 1 << m
                perm = [i ^ (i >> 1) for i in range(B)]
                pmeta = {"perm": "gray", "m": m, "block": B}
            else:
                R = rng.randint(32, 128)
                C = rng.randint(32, 128)
                B = R * C
                perm = [r * C + c for c in range(C) for r in range(R)]
                pmeta = {"perm": "transpose", "R": R, "C": C, "block": B}
            parts = []
            for off in range(0, length, B):
                blk = base_s[off:off + B]
                if len(blk) == B:
                    parts.append("".join(blk[p] for p in perm))
                else:
                    parts.append(blk)
            s = "".join(parts)[:length]
            if not _has_high_block_diversity(s, 140):
                continue
            meta = {"family": "F7", "submode": "permuted_block", "base": base_meta, **pmeta}

        ratio = _ones_ratio(s)
        if len(s) == length and 0.20 <= ratio <= 0.80 and not _is_trivially_periodic(s):
            return s, meta
    raise RuntimeError("Failed to generate non-degenerate F7 stream")


def _generate_f8(length: int, rng: random.Random) -> tuple[str, dict[str, Any]]:
    """F8（自指游程与队列计算流）：广义自指 Kolakoski、Post 循环标签系统或慢控双引擎切换流。"""
    for _attempt in range(96):
        sub_mode = rng.choice(["generalized_kolakoski", "cyclic_tag_system", "mux_switched_dual"])
        if sub_mode == "generalized_kolakoski":
            table = [rng.randint(1, 4) for _ in range(4)]
            if len(set(table)) < 3:
                table = [1, 2, 3, rng.choice([2, 4])]
                rng.shuffle(table)
            seed_len = rng.randint(6, 10)
            out = bytearray(48 + rng.randint(0, 1) for _ in range(seed_len))
            cur_bit = 48 if out[-1] == 49 else 49
            r = 0
            ok = True
            while len(out) < length:
                if r + 1 >= len(out):
                    ok = False
                    break
                v = ((out[r] - 48) << 1) | (out[r + 1] - 48)
                r += 2
                out.extend([cur_bit] * table[v])
                cur_bit = 97 - cur_bit
            if not ok:
                continue
            s = out[:length].decode("ascii")
            meta: dict[str, Any] = {"family": "F8", "submode": "generalized_kolakoski", "table": table, "seed_len": seed_len}
        elif sub_mode == "cyclic_tag_system":
            P = rng.randint(3, 6)
            apps = [bytes(48 + rng.randint(0, 1) for _ in range(rng.randint(1, 4))) for _ in range(P)]
            if sum(len(a) for a in apps) < 2 * P + 1:
                apps[0] = b"101"
            seed_len = rng.randint(8, 16)
            q = bytearray(48 + rng.randint(0, 1) for _ in range(seed_len))
            q[0] = 49
            head = 0
            out = bytearray(length)
            ok = True
            for i in range(length):
                if head >= len(q) or (len(q) - head) > 300_000:
                    ok = False
                    break
                b = q[head]
                head += 1
                out[i] = b
                if b == 49:
                    q.extend(apps[i % P])
            if not ok:
                continue
            s = out.decode("ascii")
            meta = {"family": "F8", "submode": "cyclic_tag_system", "P": P, "seed_len": seed_len}
        else:
            p = rng.choice([5, 7, 9, 11, 13])
            pat = [rng.randint(0, 1) for _ in range(p)]
            if sum(pat) < 2 or sum(pat) > p - 2:
                pat = [i & 1 for i in range(p)]
            n0 = sum(1 for x in pat if x == 0) * ((length // p) + 2)
            n1 = sum(1 for x in pat if x == 1) * ((length // p) + 2)
            s0, m0 = _generate_f2(n0, rng)
            s1, m1 = _generate_f3(n1, rng) if rng.random() < 0.5 else _generate_f5(n1, rng)
            out = bytearray(length)
            i0 = i1 = 0
            for i in range(length):
                if pat[i % p] == 0:
                    out[i] = ord(s0[i0])
                    i0 += 1
                else:
                    out[i] = ord(s1[i1])
                    i1 += 1
            s = out.decode("ascii")
            meta = {"family": "F8", "submode": "mux_switched_dual", "period": p, "g0": m0.get("family"), "g1": m1.get("family")}

        ratio = _ones_ratio(s)
        if len(s) == length and 0.20 <= ratio <= 0.80 and not _is_trivially_periodic(s) and _has_high_block_diversity(s, 160):
            return s, meta
    raise RuntimeError("Failed to generate non-degenerate F8 stream")


def generate_case(family: str, length: int, seed: int) -> tuple[str, dict[str, Any]]:
    """按家族（F1..F8）、长度与 64 位种子确定性生成目标二进制串 S 及元数据。"""
    rng = random.Random(int(seed))
    fam = family.upper()
    if fam == "F1":
        s, meta = _generate_f1(length, rng)
    elif fam == "F2":
        s, meta = _generate_f2(length, rng)
    elif fam == "F3":
        s, meta = _generate_f3(length, rng)
    elif fam == "F4":
        s, meta = _generate_f4(length, rng)
    elif fam == "F5":
        s, meta = _generate_f5(length, rng)
    elif fam == "F6":
        s, meta = _generate_f6(length, rng)
    elif fam == "F7":
        s, meta = _generate_f7(length, rng)
    elif fam == "F8":
        s, meta = _generate_f8(length, rng)
    else:
        raise ValueError(f"Unknown family: {family!r} (expected F1..F8)")
    meta["seed"] = int(seed)
    meta["length"] = int(length)
    return s, meta


ALL_FAMILIES: tuple[str, ...] = ("F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8")

# 公开匿名标本固定种子（与正式动态 CSPRNG 种子严格隔离；导出时已打乱顺序并脱敏为 specimen_1..8）
PUBLIC_SPECIMEN_SPECS: tuple[tuple[str, str, int, int], ...] = (
    ("specimen_1", "F3", 100_000, 2026112103),
    ("specimen_2", "F7", 100_000, 2026112107),
    ("specimen_3", "F1", 100_000, 2026112105),
    ("specimen_4", "F5", 100_000, 2026112111),
    ("specimen_5", "F4", 100_000, 2026112105),
    ("specimen_6", "F8", 100_000, 2026112113),
    ("specimen_7", "F2", 100_000, 2026112103),
    ("specimen_8", "F6", 100_000, 2026112117),
)
PUBLIC_SPECIMEN_SEEDS: frozenset[int] = frozenset(s for _, _, _, s in PUBLIC_SPECIMEN_SPECS)

# 阶段二暗箱泛化压测正式编排：512 个动态测试点 = 8 隐藏家族 × (32 组 10^5 + 32 组 10^6)
OFFICIAL_SUITE_SPEC: tuple[tuple[str, str, int], ...] = tuple(
    [(f"{fam}_100k_{idx:02d}", fam, 100_000) for idx in range(1, 33) for fam in ALL_FAMILIES]
    + [(f"{fam}_1M_{idx:02d}", fam, 1_000_000) for idx in range(1, 33) for fam in ALL_FAMILIES]
)

QUICK_SUITE_SPEC: tuple[tuple[str, str, int], ...] = tuple(
    (f"{fam}_10k", fam, 10_000) for fam in ALL_FAMILIES
)
