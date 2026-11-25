// 《压缩奇点》公开基线：嵌套循环游程编码器（RLE + Loop Synthesis Baseline）。
// 展示如何在 6 指令单带比特机上构造计数循环：
//   - 短游程直接输出连续 '.'
//   - 长游程使用单层或双层循环把连续 k 个相同比特压缩至 O(sqrt(k)) 指令。
//   内存布局（局部工作区）：
//     cell 0: 当前输出比特值（始终保持为 0 或 1，用于 '.'）
//     cell 1..a: 单层/外层计数器（a 个连续 1，归零循环返回 cell 0）

#include <cmath>
#include <iostream>
#include <string>

namespace {

// 在 cell 0 为当前输出比特 cur、右侧全为 0 的前提下，连续输出 cnt 个 cur，结束后回到 cell 0 且右侧恢复为 0。
void emit_run_bit(std::string& prog, char cur, std::size_t cnt) {
    // 比较直接输出与单层循环的长度：
    // cur == '0': cell 0 == 0，右侧全 0。循环法输出 a*b 个 '0' 成本 = 2*a + b + 4 + (cnt - a*b)。
    // cur == '1': 先 '+' 变 0，跑循环（先输出 '.' 再 '+' 清零），再 '+' 变回 1，总成本 = 2*a + b + 6 + (cnt - a*b)。
    const std::size_t overhead = (cur == '0') ? 4 : 6;
    std::size_t best_cost = cnt;
    std::size_t best_a = 0, best_b = 0;

    if (cnt >= 12) {
        // 枚举合理乘数 a（限制最大 512 避免不必要展开），也可嵌套处理极大游程
        std::size_t max_a = std::min<std::size_t>(cnt, static_cast<std::size_t>(std::sqrt(static_cast<double>(cnt))) + 8);
        for (std::size_t a = 2; a <= max_a; ++a) {
            std::size_t b = cnt / a;
            std::size_t r = cnt % a;
            if (b == 0) continue;
            std::size_t cost = 2 * a + b + overhead + r;
            if (cost < best_cost) {
                best_cost = cost;
                best_a = a;
                best_b = b;
            }
        }
    }

    if (best_a == 0) {
        prog.append(cnt, '.');
        return;
    }

    std::size_t rem = cnt - best_a * best_b;
    if (cur == '0') {
        for (std::size_t i = 0; i < best_a; ++i) prog += ">+";
        prog.push_back('[');
        prog.push_back('+');
        prog.append(best_b, '.');
        prog.push_back('<');
        prog.push_back(']');
        prog.append(rem, '.');
    } else {
        prog.push_back('+');  // cell 0: 1 -> 0 (sentinel)
        for (std::size_t i = 0; i < best_a; ++i) prog += ">+";
        prog.push_back('[');
        prog.append(best_b, '.');
        prog.push_back('+');
        prog.push_back('<');
        prog.push_back(']');
        prog.push_back('+');  // restore cell 0: 0 -> 1
        prog.append(rem, '.');
    }
}

}  // namespace

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);

    std::size_t L = 0;
    std::string S;
    if (!(std::cin >> L >> S)) return 0;

    std::string prog;
    prog.reserve(S.size() + 64);

    char cur = '0';
    std::size_t i = 0;
    const std::size_t n = S.size();
    while (i < n) {
        std::size_t j = i + 1;
        while (j < n && S[j] == S[i]) ++j;
        if (S[i] != cur) {
            prog.push_back('+');
            cur = S[i];
        }
        emit_run_bit(prog, cur, j - i);
        i = j;
    }

    std::cout << prog << '\n';
    return 0;
}
