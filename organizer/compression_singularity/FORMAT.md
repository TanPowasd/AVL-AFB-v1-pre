# 接口与协议规范（FORMAT）· 压缩奇点

## 1. 标准输入输出

- **stdin**：
  - 第 1 行：正整数 `L`（正式评测 `100000 <= L <= 1000000`）
  - 第 2 行：长度为 `L` 的字符串 `S`，仅含 `'0'` 和 `'1'`
- **stdout**：
  - 单行字符串 `P`，仅含 6 种字符 `> < + . [ ]`，长度满足 `1 <= |P| <= 2 * L + 100`
  - 仅允许末尾存在单个 `\n` 或 `\r\n`；禁止内部空格、注释或多余空行

## 2. 虚拟机指令表

| 指令 | 语义 | 原始步数 |
|---|---|---|
| `>` | `p += 1; ip += 1`（若 `p > 10^7` 立即 `TAPE_OUT_OF_BOUNDS`） | `+1` |
| `<` | `p -= 1; ip += 1`（若 `p < -10^7` 立即 `TAPE_OUT_OF_BOUNDS`） | `+1` |
| `+` | `T[p] ^= 1; ip += 1` | `+1` |
| `.` | 输出 `T[p]` 并与 `S[out_pos]` 比对；`ip += 1` | `+1` |
| `[` | 若 `T[p] == 0` 则 `ip = match[ip] + 1`，否则 `ip += 1` | `+1` |
| `]` | 若 `T[p] == 1` 则 `ip = match[ip] + 1`，否则 `ip += 1` | `+1` |

## 3. 判定状态码

- `AC`：正常停机（`ip == |P|`）且输出流与 `S` 逐位一致
- `CE` / `RE` / `TLE` / `MLE` / `OLE`：求解器编译或运行失败
- `SYNTAX_ERROR`：空程序、非法字符或括号不匹配
- `PROGRAM_TOO_LONG`：`|P| > 2 * L + 100`
- `STEP_LIMIT_EXCEEDED`：在 `ip < |P|` 时达到 $t = 3 \times 10^{12}$ 步
- `TAPE_OUT_OF_BOUNDS`：读写头超出 $[-10^7, 10^7]$
- `OUTPUT_MISMATCH`：输出比特与 `S` 不符，或停机时输出长度不等于 `L`
- `VM_TIMEOUT`：虚拟机物理校验耗时超过 $600\text{ s}$

## 4. 评分公式速查

- **基准信息量 $B_{\text{ref}}$**：将目标串 $S$ 按高位优先（MSB-first）打包为 $\lceil L/8 \rceil$ 字节（末字节低位补 `0`），使用 `lzma.compress(raw_bytes, format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA2, "preset": 9}])` 压缩后取 `max(8, len(payload) * 8)` 比特。
- **单点原分（不封顶）**：通过全部合法性与输出校验（`AC`）时，
  $$\operatorname{RawScore}(P) = 100 \cdot \frac{B_{\text{ref}}}{|P| \cdot \log_2 6}$$
  任意非 `AC` 状态（`CE / RE / TLE / MLE / OLE / SYNTAX_ERROR / PROGRAM_TOO_LONG / STEP_LIMIT_EXCEEDED / TAPE_OUT_OF_BOUNDS / OUTPUT_MISMATCH / VM_TIMEOUT`）计 `0`。
- **单点百分制折算分**：
  $$\operatorname{NormScore}(r) = 100 \cdot \frac{r^{0.75}}{r^{0.75} + 1000^{0.75}} \in [0, 100]$$
  （$r = 1000$ 对应 `50.00` 分，$r > 10\,000$ 后强饱和）。
- **正式评测汇总**：动态生成 `512` 个隐藏测试点（`8` 隐藏家族 × `[32 × 10^5 + 32 × 10^6]`），**正式总分（排序与套题计分主分）为 512 测 `NormScore` 的等权算术平均**；同时附报 512 测 `RawScore` 的等权算术平均作为不封顶诊断指标。

## 5. 离线回放 JSON 结构（`compression_singularity_replay/1`）

由 `scripts/record_replay.py` 或 `scripts/verify_program.py --trace` 生成，包含：
- `summary`：`ac_count`、`total_cases`、`raw_score`、`score`、`by_family`
- `cases[]`：各点 `verdict`、`prog_len`、`b_ref_bits`、`steps`、`p_min`、`p_max`、`op_counts`、`raw_score`、`norm_score` 与 `trace[]`（`step`, `p`, `p_min`, `p_max`, `out_pos`, `ones`, `win`）
