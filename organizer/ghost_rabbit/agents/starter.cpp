// 《天翼幽灵兔：四重奇点追截战》（ghost_rabbit）起步模板（agents/starter.cpp）。
//
// 演示内容：
//   1. 阶段一声明精确有理速率 P/Q（本模板使用 P=7, Q=4，即 q=1.75，Raw=20.00, Norm=9.09）
//   2. 艾森斯坦蜂窝图 H_omega 的合法格点校验、O(1) 六边形距离、精确艾森斯坦素数判定
//   3. 局部等距无三连共线（Local 3-AP-Free）合法性检验与精确有理漏桶代币管理
//   4. 轻量精确信念集维护与实时声呐探测（每轮 std::cout << ... << '\n' << std::flush）
//
// 提示：将本文件复制或修改后保存到 ../submissions/ghost_rabbit.cpp 即可参与统一评测。

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <numeric>
#include <unordered_set>
#include <utility>
#include <vector>

struct Pt {
    int a = 0, b = 0;
    bool operator==(const Pt& o) const { return a == o.a && b == o.b; }
};

static inline uint64_t pack_pt(Pt p) {
    return (uint64_t(uint32_t(p.a + 2000000)) << 32) | uint64_t(uint32_t(p.b + 2000000));
}

static inline int mod3(int x) {
    int r = x % 3;
    return r < 0 ? r + 3 : r;
}

static inline bool valid_grid(Pt p) {
    return mod3(p.a + p.b) != 0 && std::abs(p.a) <= 100000 && std::abs(p.b) <= 100000;
}

static inline std::array<int, 3> to_c3(Pt p) {
    int d = mod3(p.a + p.b);
    return {(2 * p.a - p.b + d) / 3, (2 * p.b - p.a + d) / 3, (-p.a - p.b + d) / 3};
}

static inline int hex_dist(Pt u, Pt v) {
    auto cu = to_c3(u), cv = to_c3(v);
    return std::abs(cu[0] - cv[0]) + std::abs(cu[1] - cv[1]) + std::abs(cu[2] - cv[2]);
}

static bool is_prime_u64(uint64_t n) {
    if (n < 2) return false;
    for (uint64_t p : {2ULL, 3ULL, 5ULL, 7ULL, 11ULL, 13ULL, 17ULL, 19ULL, 23ULL, 29ULL, 31ULL, 37ULL}) {
        if (n == p) return true;
        if (n % p == 0) return false;
    }
    uint64_t d = n - 1;
    int s = 0;
    while ((d & 1ULL) == 0) { d >>= 1; ++s; }
    auto mul = [](uint64_t a, uint64_t b, uint64_t m) -> uint64_t {
        return static_cast<uint64_t>((__uint128_t(a) * b) % m);
    };
    auto powm = [&](uint64_t a, uint64_t e, uint64_t m) -> uint64_t {
        uint64_t r = 1;
        while (e) {
            if (e & 1ULL) r = mul(r, a, m);
            a = mul(a, a, m);
            e >>= 1;
        }
        return r;
    };
    for (uint64_t a : {2ULL, 325ULL, 9375ULL, 28178ULL, 450775ULL, 9780504ULL, 1795265022ULL}) {
        if (a % n == 0) continue;
        uint64_t x = powm(a, d, n);
        if (x == 1 || x == n - 1) continue;
        bool ok = false;
        for (int i = 1; i < s; ++i) {
            x = mul(x, x, n);
            if (x == n - 1) { ok = true; break; }
        }
        if (!ok) return false;
    }
    return true;
}

static bool is_eisenstein_prime(Pt p) {
    int64_t a = p.a, b = p.b;
    if (a == 0) { int64_t r = std::abs(b); return r % 3 == 2 && is_prime_u64(uint64_t(r)); }
    if (b == 0) { int64_t r = std::abs(a); return r % 3 == 2 && is_prime_u64(uint64_t(r)); }
    if (a == b) { int64_t r = std::abs(a); return r % 3 == 2 && is_prime_u64(uint64_t(r)); }
    uint64_t norm = uint64_t(a * a - a * b + b * b);
    return norm % 3 == 1 && is_prime_u64(norm);
}

static constexpr Pt N1_ODD[3]  = {{0, 1}, {1, 0}, {-1, -1}};
static constexpr Pt N1_EVEN[3] = {{0, -1}, {-1, 0}, {1, 1}};
static constexpr Pt N2_DIRS[6] = {{1, -1}, {-1, 1}, {2, 1}, {-2, -1}, {1, 2}, {-1, -2}};
static constexpr Pt HALF_DIRS[3] = {{1, -1}, {2, 1}, {1, 2}};

static void get_moves(Pt u, const std::unordered_set<uint64_t>& traps, std::vector<Pt>& out) {
    out.clear();
    const Pt* dirs = (mod3(u.a + u.b) == 1) ? N1_ODD : N1_EVEN;
    for (int i = 0; i < 3; ++i) {
        Pt v{u.a + dirs[i].a, u.b + dirs[i].b};
        if (!traps.count(pack_pt(v))) out.push_back(v);
    }
    if (is_eisenstein_prime(u)) {
        for (int i = 0; i < 6; ++i) {
            Pt v{u.a + N2_DIRS[i].a, u.b + N2_DIRS[i].b};
            if (!traps.count(pack_pt(v))) out.push_back(v);
        }
    }
}

static bool can_place_3ap_free(Pt p, const std::unordered_set<uint64_t>& traps, const std::vector<Pt>& pending) {
    if (!valid_grid(p)) return false;
    uint64_t kp = pack_pt(p);
    if (traps.count(kp)) return false;
    for (Pt q : pending) if (q == p) return false;
    auto has = [&](int a, int b) -> bool {
        uint64_t k = pack_pt({a, b});
        if (traps.count(k)) return true;
        for (Pt q : pending) if (q.a == a && q.b == b) return true;
        return false;
    };
    for (const Pt& d : HALF_DIRS) {
        bool m1 = has(p.a - d.a, p.b - d.b);
        bool p1 = has(p.a + d.a, p.b + d.b);
        if (m1 && p1) return false;
        if (m1 && has(p.a - 2 * d.a, p.b - 2 * d.b)) return false;
        if (p1 && has(p.a + 2 * d.a, p.b + 2 * d.b)) return false;
    }
    return true;
}

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);

    // 阶段一：声明速率 P=7, Q=4 (q = 1.75)
    const int64_t P = 7, Q = 4;
    const int64_t g = std::gcd(P, Q);
    const int64_t Pp = P / g, Qp = Q / g;
    std::cout << P << ' ' << Q << '\n' << std::flush;

    int64_t tokens = 0;
    const int64_t cap = 2 * Qp;
    std::unordered_set<uint64_t> traps;
    std::vector<Pt> belief = {{1, 0}};
    std::vector<Pt> mvs;

    for (int turn = 1; turn <= 5000; ++turn) {
        tokens = std::min(tokens + Pp, cap);
        int max_k = static_cast<int>(std::min<int64_t>(2, tokens / Qp));
        int budget_traps = 4000 - static_cast<int>(traps.size());
        max_k = std::min(max_k, std::max(0, budget_traps));

        // 收集当前信念集全部活出口，并按被覆盖频次 + 是否为艾森斯坦素数排序
        std::vector<std::pair<int, Pt>> cands;
        std::unordered_set<uint64_t> seen_cand;
        std::unordered_set<uint64_t> belief_set;
        for (Pt u : belief) belief_set.insert(pack_pt(u));

        std::vector<Pt> nm;
        for (Pt u : belief) {
            get_moves(u, traps, mvs);
            int urg = (mvs.size() <= 2) ? 200 : 60;
            for (Pt v : mvs) {
                uint64_t kv = pack_pt(v);
                if (seen_cand.insert(kv).second) {
                    get_moves(v, traps, nm);
                    // 优先封锁素数跳板与出度大的逃逸口，迫使兔子进入狭小口袋
                    int score = urg + (is_eisenstein_prime(v) ? 140 : 20)
                                + static_cast<int>(nm.size()) * 15
                                - (belief_set.count(kv) ? 120 : 0);
                    cands.push_back({score, v});
                }
            }
        }
        // 按优先级与到信念中心距离排序，优先封锁紧贴兔子的活出口
        double bx = 0.0, by = 0.0;
        for (Pt u : belief) { bx += u.a; by += u.b; }
        if (!belief.empty()) { bx /= belief.size(); by /= belief.size(); }
        std::sort(cands.begin(), cands.end(), [&](const auto& x, const auto& y) {
            if (x.first != y.first) return x.first > y.first;
            double dx = (x.second.a - bx) * (x.second.a - bx) + (x.second.b - by) * (x.second.b - by);
            double dy = (y.second.a - bx) * (y.second.a - bx) + (y.second.b - by) * (y.second.b - by);
            return dx < dy;
        });

        std::vector<Pt> chosen;
        for (const auto& [sc, p] : cands) {
            if (static_cast<int>(chosen.size()) >= max_k) break;
            if (can_place_3ap_free(p, traps, chosen)) {
                chosen.push_back(p);
            }
        }

        // 轮换三个远场轴向投影点（可将不同位置的邻点拉开到不同距离）
        Pt sonar{100000, 0};
        if (turn % 3 == 1) sonar = {0, 100000};
        else if (turn % 3 == 2) sonar = {100000, 0};
        else sonar = {-100000, -100000};

        // 发送本轮决策并刷新缓冲
        std::cout << chosen.size();
        for (Pt p : chosen) std::cout << ' ' << p.a << ' ' << p.b;
        std::cout << ' ' << sonar.a << ' ' << sonar.b << '\n' << std::flush;
        tokens -= static_cast<int64_t>(chosen.size()) * Qp;

        int status = 0, dist = 0, flash = 0, bounce_idx = 0;
        if (!(std::cin >> status >> dist >> flash >> bounce_idx)) break;
        if (status == 1) break;  // 捕获成功，立刻正常退出

        // 更新落地陷阱
        for (size_t j = 0; j < chosen.size(); ++j) {
            if (static_cast<int>(j + 1) != bounce_idx) {
                traps.insert(pack_pt(chosen[j]));
            }
        }

        // 精确更新信念集
        std::vector<Pt> next_belief;
        std::unordered_set<uint64_t> nb_seen;
        auto expand = [&](Pt u) {
            if ((is_eisenstein_prime(u) ? 1 : 0) != flash) return;
            get_moves(u, traps, mvs);
            for (Pt v : mvs) {
                if (hex_dist(sonar, v) == dist) {
                    if (nb_seen.insert(pack_pt(v)).second) next_belief.push_back(v);
                }
            }
        };
        if (bounce_idx > 0 && bounce_idx <= static_cast<int>(chosen.size())) {
            expand(chosen[static_cast<size_t>(bounce_idx - 1)]);
        } else {
            for (Pt u : belief) {
                bool hit = false;
                for (Pt c : chosen) if (c == u) { hit = true; break; }
                if (!hit) expand(u);
            }
        }
        if (next_belief.size() > 4000) next_belief.resize(4000);
        if (!next_belief.empty()) belief = std::move(next_belief);
    }
    return 0;
}
