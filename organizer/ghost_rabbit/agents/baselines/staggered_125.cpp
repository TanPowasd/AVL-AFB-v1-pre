// 《天翼幽灵兔：四重奇点追截战》（ghost_rabbit）公开基线 #2：Staggered-125（agents/baselines/staggered_125.cpp）。
//
// 算法思路（P=5, Q=4，q = 1.25，RawScore = 100.00，NormScore = 33.33）：
//   1. 维护与全部声呐、素数闪光、幽灵弹开观测 100% 一致的精确信念集，结合局部极小极大声呐 + 远场三轴投影压低量子云；
//   2. 1-2 步前瞻素数优先封锁 + 增量局部禁手地堡防御（Type-1 相邻双禁手 / Type-2 出口互斥地堡严格零新增）；
//   3. 当兔子陷入 <= 600 格有限空腔后自动切换至腔内过滤模式，结合直接一击捕获与量子云坍缩射击稳定完成零免死区收口。

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <numeric>
#include <unordered_map>
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

static constexpr int SIEVE_N = 250000;
static std::vector<uint8_t> g_comp;

static void init_sieve() {
    g_comp.assign(SIEVE_N + 1, 0);
    g_comp[0] = g_comp[1] = 1;
    for (int i = 2; i * i <= SIEVE_N; ++i) {
        if (!g_comp[i]) {
            for (int j = i * i; j <= SIEVE_N; j += i) g_comp[j] = 1;
        }
    }
}

static bool is_prime_u64(uint64_t n) {
    if (n <= uint64_t(SIEVE_N)) return !g_comp[static_cast<size_t>(n)];
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

static inline bool is_eisenstein_prime(Pt p) {
    int64_t a = p.a, b = p.b;
    if (a == 0) { int64_t r = std::abs(b); return r % 3 == 2 && is_prime_u64(uint64_t(r)); }
    if (b == 0) { int64_t r = std::abs(a); return r % 3 == 2 && is_prime_u64(uint64_t(r)); }
    if (a == b) { int64_t r = std::abs(a); return r % 3 == 2 && is_prime_u64(uint64_t(r)); }
    uint64_t norm = uint64_t(a * a - a * b + b * b);
    return norm % 3 == 1 && is_prime_u64(norm);
}

static constexpr Pt N1_ODD[3]    = {{0, 1}, {1, 0}, {-1, -1}};
static constexpr Pt N1_EVEN[3]   = {{0, -1}, {-1, 0}, {1, 1}};
static constexpr Pt N2_DIRS[6]   = {{1, -1}, {-1, 1}, {2, 1}, {-2, -1}, {1, 2}, {-1, -2}};
static constexpr Pt HALF_DIRS[3] = {{1, -1}, {2, 1}, {1, 2}};
static constexpr Pt FAR_AXIS[3]  = {{100000, 0}, {0, 100000}, {-100000, -100000}};

static inline void get_moves(Pt u, const std::unordered_set<uint64_t>& traps, std::vector<Pt>& out) {
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

static inline bool can1_T(Pt p, const std::unordered_set<uint64_t>& T) {
    if (!valid_grid(p) || T.count(pack_pt(p))) return false;
    for (const Pt& d : HALF_DIRS) {
        bool m1 = T.count(pack_pt({p.a - d.a, p.b - d.b})) != 0;
        bool p1 = T.count(pack_pt({p.a + d.a, p.b + d.b})) != 0;
        if (m1 && p1) return false;
        if (m1 && T.count(pack_pt({p.a - 2 * d.a, p.b - 2 * d.b}))) return false;
        if (p1 && T.count(pack_pt({p.a + 2 * d.a, p.b + 2 * d.b}))) return false;
    }
    return true;
}

static bool can_place_3ap_free(Pt p, const std::unordered_set<uint64_t>& traps, const std::vector<Pt>& pending) {
    if (!valid_grid(p)) return false;
    uint64_t kp = pack_pt(p);
    if (traps.count(kp)) return false;
    for (Pt q : pending) if (q == p) return false;
    auto has = [&](int a, int b) -> bool {
        if (traps.count(pack_pt({a, b}))) return true;
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

static bool can_place_all_T(const Pt* pts, int cnt, std::unordered_set<uint64_t>& T) {
    uint64_t ins[10];
    int n_ins = 0;
    bool ok = true;
    for (int i = 0; i < cnt; ++i) {
        if (!can1_T(pts[i], T)) { ok = false; break; }
        uint64_t k = pack_pt(pts[i]);
        T.insert(k);
        ins[n_ins++] = k;
    }
    for (int i = 0; i < n_ins; ++i) T.erase(ins[i]);
    return ok;
}

static bool cell_is_bunker_T(Pt w, std::unordered_set<uint64_t>& T, const std::unordered_set<uint64_t>* comp_filter, bool check_type2) {
    if (can1_T(w, T)) return false;
    Pt live[10];
    int n_live = 0;
    const Pt* dirs = (mod3(w.a + w.b) == 1) ? N1_ODD : N1_EVEN;
    for (int i = 0; i < 3; ++i) {
        Pt nb{w.a + dirs[i].a, w.b + dirs[i].b};
        uint64_t kn = pack_pt(nb);
        if (T.count(kn)) continue;
        if (comp_filter && !comp_filter->count(kn)) continue;
        if (!can1_T(nb, T)) return true; // Type-1: 相邻双禁手
        live[n_live++] = nb;
    }
    if (is_eisenstein_prime(w)) {
        for (int i = 0; i < 6; ++i) {
            Pt nb{w.a + N2_DIRS[i].a, w.b + N2_DIRS[i].b};
            uint64_t kn = pack_pt(nb);
            if (T.count(kn)) continue;
            if (comp_filter && !comp_filter->count(kn)) continue;
            if (!can1_T(nb, T)) return true; // Type-1: 素数 N2 相邻双禁手
            live[n_live++] = nb;
        }
    }
    if (check_type2 && n_live >= 2 && n_live <= 3 && !can_place_all_T(live, n_live, T)) return true; // Type-2: 出口互斥地堡
    return false;
}

static bool is_bunker_bad(
    Pt v,
    std::unordered_set<uint64_t>& traps,
    const std::vector<Pt>& pending,
    const std::unordered_set<uint64_t>* comp_filter = nullptr,
    bool check_type2 = true
) {
    uint64_t pend_ins[4];
    int n_pend = 0;
    for (Pt q : pending) {
        uint64_t k = pack_pt(q);
        if (traps.insert(k).second) pend_ins[n_pend++] = k;
    }
    uint64_t kv = pack_pt(v);

    Pt check_w[160];
    uint64_t check_k[160];
    int nw = 0;
    auto add_w = [&](Pt p) {
        uint64_t k = pack_pt(p);
        if (!valid_grid(p) || k == kv || traps.count(k)) return;
        if (comp_filter && !comp_filter->count(k)) return;
        for (int i = 0; i < nw; ++i) if (check_k[i] == k) return;
        if (nw < 160) { check_w[nw] = p; check_k[nw] = k; ++nw; }
    };
    Pt direct_w[24];
    int ndw = 0;
    for (const Pt& d : HALF_DIRS) {
        Pt pts[4] = {{v.a - d.a, v.b - d.b}, {v.a + d.a, v.b + d.b},
                     {v.a - 2 * d.a, v.b - 2 * d.b}, {v.a + 2 * d.a, v.b + 2 * d.b}};
        for (Pt w : pts) {
            direct_w[ndw++] = w;
            add_w(w);
        }
    }
    if (check_type2) {
        for (int idx = 0; idx < ndw; ++idx) {
            Pt w = direct_w[idx];
            if (!valid_grid(w)) continue;
            const Pt* dirs = (mod3(w.a + w.b) == 1) ? N1_ODD : N1_EVEN;
            for (int i = 0; i < 3; ++i) add_w({w.a + dirs[i].a, w.b + dirs[i].b});
            for (int i = 0; i < 6; ++i) {
                Pt nb{w.a + N2_DIRS[i].a, w.b + N2_DIRS[i].b};
                if (is_eisenstein_prime(nb)) add_w(nb);
            }
        }
    }

    uint8_t was_bad[160];
    for (int i = 0; i < nw; ++i) {
        was_bad[i] = cell_is_bunker_T(check_w[i], traps, comp_filter, check_type2) ? 1 : 0;
    }

    bool ins_v = traps.insert(kv).second;
    bool bad = false;
    for (int i = 0; i < nw; ++i) {
        if (was_bad[i]) continue;
        if (cell_is_bunker_T(check_w[i], traps, comp_filter, check_type2)) { bad = true; break; }
    }
    if (ins_v) traps.erase(kv);
    for (int i = 0; i < n_pend; ++i) traps.erase(pend_ins[i]);
    return bad;
}

static Pt choose_minimax_sonar(
    const std::vector<Pt>& belief,
    std::unordered_set<uint64_t>& traps,
    const std::vector<Pt>& chosen,
    int turn
) {
    if (belief.empty() || belief.size() > 64) {
        return FAR_AXIS[(turn - 1) % 3];
    }
    uint64_t tmp_ins[4];
    int n_tmp = 0;
    for (Pt p : chosen) {
        uint64_t k = pack_pt(p);
        if (traps.insert(k).second) tmp_ins[n_tmp++] = k;
    }
    std::vector<Pt> next_pts;
    std::unordered_set<uint64_t> seen;
    std::vector<Pt> mvs;
    for (Pt u : belief) {
        bool hit = false;
        for (Pt c : chosen) if (c == u) { hit = true; break; }
        if (hit) continue;
        get_moves(u, traps, mvs);
        for (Pt v : mvs) {
            if (seen.insert(pack_pt(v)).second) {
                next_pts.push_back(v);
                if (next_pts.size() >= 140) break;
            }
        }
        if (next_pts.size() >= 140) break;
    }
    for (int i = 0; i < n_tmp; ++i) traps.erase(tmp_ins[i]);

    if (next_pts.size() <= 1) {
        return next_pts.empty() ? FAR_AXIS[(turn - 1) % 3] : next_pts[0];
    }

    std::vector<Pt> probes = {FAR_AXIS[(turn - 1) % 3], FAR_AXIS[turn % 3], FAR_AXIS[(turn + 1) % 3]};
    if (next_pts.size() <= 72) {
        Pt anchor = next_pts[0];
        for (int da = -2; da <= 2; ++da) {
            for (int db = -2; db <= 2; ++db) {
                Pt q{anchor.a + da, anchor.b + db};
                if (valid_grid(q)) probes.push_back(q);
            }
        }
        for (size_t i = 0; i < std::min<size_t>(next_pts.size(), 10); ++i) probes.push_back(next_pts[i]);
    }

    Pt best_probe = FAR_AXIS[(turn - 1) % 3];
    int best_worst = 1e9;
    double best_sum_sq = 1e18;
    std::unordered_map<int, int> cnt;
    for (Pt q : probes) {
        cnt.clear();
        int worst = 0;
        double sum_sq = 0.0;
        for (Pt v : next_pts) {
            int c = ++cnt[hex_dist(q, v)];
            if (c > worst) worst = c;
        }
        for (const auto& [d, c] : cnt) sum_sq += double(c) * double(c);
        if (worst < best_worst || (worst == best_worst && sum_sq < best_sum_sq)) {
            best_worst = worst;
            best_sum_sq = sum_sq;
            best_probe = q;
        }
    }
    return best_probe;
}

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    init_sieve();

    // 声明速率 P=5, Q=4 (q = 1.25 -> RawScore=100.00, NormScore=33.33)
    const int64_t P = 5, Q = 4;
    const int64_t g = std::gcd(P, Q);
    const int64_t Pp = P / g, Qp = Q / g;
    std::cout << P << ' ' << Q << '\n' << std::flush;

    int64_t tokens = 0;
    const int64_t cap = 2 * Qp;
    std::unordered_set<uint64_t> traps;
    traps.reserve(8192);
    std::vector<Pt> belief = {{1, 0}};
    std::vector<Pt> mvs, nm;

    std::unordered_set<uint64_t> comp;
    std::vector<Pt> clist;
    bool is_enclosed = false;
    int last_bfs_turn = -100;
    int stall_turns = 0;
    double prev_a = 1.0, prev_b = 0.0;
    double vel_a = 0.0, vel_b = 0.0;

    auto refresh_cavity = [&](int limit) -> bool {
        comp.clear();
        clist.clear();
        for (Pt u : belief) {
            if (comp.insert(pack_pt(u)).second) clist.push_back(u);
        }
        for (size_t hd = 0; hd < clist.size(); ++hd) {
            get_moves(clist[hd], traps, mvs);
            for (Pt v : mvs) {
                if (comp.insert(pack_pt(v)).second) {
                    clist.push_back(v);
                    if (static_cast<int>(clist.size()) > limit) return false;
                }
            }
        }
        return true;
    };

    for (int turn = 1; turn <= 5000; ++turn) {
        tokens = std::min(tokens + Pp, cap);
        int max_k = static_cast<int>(std::min<int64_t>(2, tokens / Qp));
        max_k = std::min(max_k, std::max(0, 4000 - static_cast<int>(traps.size())));

        std::unordered_set<uint64_t> belief_set;
        for (Pt u : belief) belief_set.insert(pack_pt(u));

        std::vector<Pt> chosen;

        // 0. 直接一击捕获判定
        if (max_k > 0 && !belief.empty()) {
            std::unordered_set<uint64_t> exs;
            std::vector<Pt> exl;
            for (Pt u : belief) {
                get_moves(u, traps, mvs);
                for (Pt v : mvs) if (exs.insert(pack_pt(v)).second) exl.push_back(v);
            }
            if (!exl.empty() && static_cast<int>(exl.size()) <= max_k) {
                bool no_b = true;
                for (Pt e : exl) if (belief_set.count(pack_pt(e))) { no_b = false; break; }
                if (no_b) {
                    std::vector<Pt> cand_k;
                    bool all_ok = true;
                    for (Pt e : exl) {
                        if (can_place_3ap_free(e, traps, cand_k)) cand_k.push_back(e);
                        else { all_ok = false; break; }
                    }
                    if (all_ok) chosen = std::move(cand_k);
                }
            }
        }

        // 1. 定期或在小腔内刷新有限空腔判定（严格控制 BFS 上限，确保单局 CPU < 300ms）
        if (chosen.empty() && traps.size() >= 4) {
            bool b_out = false;
            if (is_enclosed) {
                for (Pt u : belief) if (!comp.count(pack_pt(u))) { b_out = true; break; }
            }
            int period = is_enclosed ? (clist.size() <= 60 ? 1 : 6) : 16;
            if (b_out || (turn - last_bfs_turn >= period)) {
                is_enclosed = refresh_cavity(450);
                last_bfs_turn = turn;
            }
        }
        const std::unordered_set<uint64_t>* cfilt = is_enclosed ? &comp : nullptr;

        // 追踪兔子重心平滑速度向量（截断切向逃逸）
        double cur_a = 0.0, cur_b = 0.0;
        if (!belief.empty()) {
            for (Pt u : belief) { cur_a += u.a; cur_b += u.b; }
            cur_a /= double(belief.size());
            cur_b /= double(belief.size());
            if (turn > 1) {
                vel_a = vel_a * 0.65 + (cur_a - prev_a) * 0.35;
                vel_b = vel_b * 0.65 + (cur_b - prev_b) * 0.35;
            }
            prev_a = cur_a;
            prev_b = cur_b;
        }
        double vnorm = std::hypot(vel_a, vel_b);
        double ux = (vnorm > 1e-6) ? (vel_a / vnorm) : 0.0;
        double uy = (vnorm > 1e-6) ? (vel_b / vnorm) : 0.0;

        if (chosen.empty() && max_k > 0) {
            // 统计 1 步与 2 步活出口威胁权值：优先封堵“兔子行进正前方 + 高出度 + 素数跳板”，将其逼回已有陷阱群！
            std::unordered_map<uint64_t, std::pair<double, Pt>> cand_map;
            for (Pt u : belief) {
                bool up = is_eisenstein_prime(u);
                get_moves(u, traps, mvs);
                double urgency = (mvs.size() <= 2) ? 200.0 : (mvs.size() == 3 ? 65.0 : 20.0);
                if (up) urgency *= 1.4;
                for (Pt v : mvs) {
                    uint64_t kv = pack_pt(v);
                    auto it = cand_map.find(kv);
                    if (it == cand_map.end()) {
                        double base = (is_eisenstein_prime(v) ? 260.0 : 25.0);
                        if (belief_set.count(kv)) base -= 180.0;
                        get_moves(v, traps, nm);
                        base += nm.size() * 22.0;
                        double fwd = (v.a - cur_a) * ux + (v.b - cur_b) * uy;
                        base += fwd * 45.0 + hex_dist({1, 0}, v) * 4.0;
                        cand_map[kv] = {base + urgency, v};
                    } else {
                        it->second.first += urgency;
                    }
                }
            }
            std::vector<Pt> hop1;
            hop1.reserve(cand_map.size());
            for (const auto& [k, pr] : cand_map) hop1.push_back(pr.second);
            for (Pt v : hop1) {
                get_moves(v, traps, nm);
                for (Pt w : nm) {
                    uint64_t kw = pack_pt(w);
                    if (!cand_map.count(kw) && !(belief.size() == 1 && belief_set.count(kw))) {
                        double fwd = (w.a - cur_a) * ux + (w.b - cur_b) * uy;
                        double base = (is_eisenstein_prime(w) ? 75.0 : 10.0) + fwd * 28.0 + hex_dist({1, 0}, w) * 2.5;
                        cand_map[kw] = {base, w};
                    }
                }
            }

            std::vector<std::pair<double, Pt>> cands;
            cands.reserve(cand_map.size());
            for (const auto& [k, pr] : cand_map) cands.push_back(pr);
            std::sort(cands.begin(), cands.end(), [](const auto& x, const auto& y) { return x.first > y.first; });

            for (const auto& [sc, p] : cands) {
                if (static_cast<int>(chosen.size()) >= max_k) break;
                if (belief.size() == 1 && belief_set.count(pack_pt(p))) continue;
                if (can_place_3ap_free(p, traps, chosen) && !is_bunker_bad(p, traps, chosen, cfilt)) {
                    chosen.push_back(p);
                }
            }

            // 若 1/2 步出口全被地堡防线过滤，则低开销刷新有限空腔，在严格防地堡前提下顺延外环
            if (chosen.empty()) {
                if (turn != last_bfs_turn) {
                    is_enclosed = refresh_cavity(450);
                    last_bfs_turn = turn;
                    cfilt = is_enclosed ? &comp : nullptr;
                }
                size_t scan_lim = is_enclosed ? clist.size() : std::min<size_t>(clist.size(), 90);
                std::vector<std::pair<double, Pt>> ec;
                for (size_t idx = 0; idx < scan_lim; ++idx) {
                    Pt v = clist[idx];
                    if (belief.size() == 1 && belief_set.count(pack_pt(v))) continue;
                    if (can_place_3ap_free(v, traps, chosen) && !is_bunker_bad(v, traps, chosen, cfilt, true)) {
                        int md = 1e9;
                        for (size_t bi = 0; bi < std::min<size_t>(belief.size(), 16); ++bi) {
                            md = std::min(md, hex_dist(belief[bi], v));
                        }
                        double fwd = (v.a - cur_a) * ux + (v.b - cur_b) * uy;
                        double sc = (is_eisenstein_prime(v) ? 500.0 : 0.0) + fwd * 30.0 - double(md) * 20.0
                                    - (belief_set.count(pack_pt(v)) ? 100.0 : 0.0);
                        ec.push_back({sc, v});
                        if (!is_enclosed && ec.size() >= 10) break;
                    }
                }
                std::sort(ec.begin(), ec.end(), [](const auto& x, const auto& y) { return x.first > y.first; });
                for (const auto& [sc, p] : ec) {
                    if (static_cast<int>(chosen.size()) >= max_k) break;
                    if (can_place_3ap_free(p, traps, chosen) && !is_bunker_bad(p, traps, chosen, cfilt, true)) {
                        chosen.push_back(p);
                    }
                }
            }

            // 量子云主动坍缩（|belief| >= 2 时严格防地堡前提下向信念格试射：弹开即坍缩为单点，落地即直接缩腔/绝杀）
            if (chosen.empty() && belief.size() >= 2) {
                for (Pt u : belief) {
                    if (can_place_3ap_free(u, traps, chosen) && !is_bunker_bad(u, traps, chosen, cfilt, true)) {
                        chosen.push_back(u);
                        break;
                    }
                }
            }
            // 若在严格防地堡下仍无解（仅在极小闭腔终局或双格对称云出现），且信念集合 >= 2，允许击穿单个信念格坍缩
            if (chosen.empty()) {
                ++stall_turns;
                if (stall_turns >= 4 && belief.size() >= 2) {
                    for (Pt u : belief) {
                        if (can_place_3ap_free(u, traps, chosen)) {
                            chosen.push_back(u);
                            break;
                        }
                    }
                    stall_turns = 0;
                }
            } else {
                stall_turns = 0;
            }
        }

        Pt sonar = choose_minimax_sonar(belief, traps, chosen, turn);

        std::cout << chosen.size();
        for (Pt p : chosen) std::cout << ' ' << p.a << ' ' << p.b;
        std::cout << ' ' << sonar.a << ' ' << sonar.b << '\n' << std::flush;
        tokens -= static_cast<int64_t>(chosen.size()) * Qp;

        int status = 0, dist = 0, flash = 0, bounce_idx = 0;
        if (!(std::cin >> status >> dist >> flash >> bounce_idx)) break;
        if (status == 1) break;

        for (size_t j = 0; j < chosen.size(); ++j) {
            if (static_cast<int>(j + 1) != bounce_idx) traps.insert(pack_pt(chosen[j]));
        }

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
        if (next_belief.size() > 6000) {
            std::sort(next_belief.begin(), next_belief.end(), [](Pt a, Pt b) {
                return hex_dist({1, 0}, a) < hex_dist({1, 0}, b);
            });
            next_belief.resize(6000);
        }
        if (!next_belief.empty()) belief = std::move(next_belief);
    }
    return 0;
}
