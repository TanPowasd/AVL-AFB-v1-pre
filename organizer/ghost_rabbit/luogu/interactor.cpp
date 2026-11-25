// 《天翼幽灵兔：四重奇点追截战》（The Winged Ghost Rabbit）
// 洛谷在线交互题评测器（testlib.h，严格兼容 C++14 / -fno-asm / -O2）。
//
// 输入文件 (.in) 格式：4 个整数
//   <family_id> <seed> <max_turns> <max_traps>
// 其中 family_id ∈ {1, 2, 3, 4} 对应 4 类互补强交互器家族，测试点编号已打乱交错排布。
// 交互成功时通过 quitp(NormScore / 100.0, ...) 返回连续部分分并在评语中报告精确 RawScore。

#include "testlib.h"

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <random>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace {

static const int COORD_LIMIT = 100000;
static const int COORD_OFFSET = 200000;
static const int64_t MAX_Q_DENOM = 1000000LL;
static const int SIEVE_MAX = 2000000;

struct Pt {
    int a, b;
    Pt() : a(0), b(0) {}
    Pt(int aa, int bb) : a(aa), b(bb) {}
    bool operator==(const Pt& o) const { return a == o.a && b == o.b; }
    bool operator!=(const Pt& o) const { return !(*this == o); }
    bool operator<(const Pt& o) const { return a != o.a ? a < o.a : b < o.b; }
};

inline uint64_t pack_pt(Pt p) {
    return (uint64_t(uint32_t(p.a + COORD_OFFSET)) << 20) | uint32_t(p.b + COORD_OFFSET);
}

inline int delta3(int a, int b) {
    int r = (a + b) % 3;
    return r < 0 ? r + 3 : r;
}

inline bool in_bounds(Pt p) {
    return p.a >= -COORD_LIMIT && p.a <= COORD_LIMIT && p.b >= -COORD_LIMIT && p.b <= COORD_LIMIT;
}

inline bool valid_grid(Pt p) {
    return in_bounds(p) && delta3(p.a, p.b) != 0;
}

struct C3 {
    int c1, c2, c3;
};

inline C3 to_c3(Pt p) {
    int d = delta3(p.a, p.b);
    C3 r;
    r.c1 = (2 * p.a - p.b + d) / 3;
    r.c2 = (2 * p.b - p.a + d) / 3;
    r.c3 = (-p.a - p.b + d) / 3;
    return r;
}

inline int hex_dist(Pt u, Pt v) {
    C3 cu = to_c3(u);
    C3 cv = to_c3(v);
    return std::abs(cu.c1 - cv.c1) + std::abs(cu.c2 - cv.c2) + std::abs(cu.c3 - cv.c3);
}

inline int64_t my_gcd(int64_t a, int64_t b) {
    if (a < 0) a = -a;
    if (b < 0) b = -b;
    while (b != 0) {
        int64_t t = a % b;
        a = b;
        b = t;
    }
    return a;
}

static const Pt N1_ODD[3]    = {Pt(1, 0), Pt(0, 1), Pt(-1, -1)};
static const Pt N1_EVEN[3]   = {Pt(-1, 0), Pt(0, -1), Pt(1, 1)};
static const Pt N2_OFF[6]    = {Pt(1, -1), Pt(-1, 1), Pt(2, 1), Pt(-2, -1), Pt(1, 2), Pt(-1, -2)};
static const Pt N2_AXES[3]   = {Pt(1, -1), Pt(2, 1), Pt(1, 2)};

static std::vector<uint8_t> g_is_comp;

void init_sieve() {
    g_is_comp.assign(SIEVE_MAX + 1, 0);
    g_is_comp[0] = 1;
    g_is_comp[1] = 1;
    for (int i = 2; 1LL * i * i <= SIEVE_MAX; ++i) {
        if (!g_is_comp[i]) {
            for (int j = i * i; j <= SIEVE_MAX; j += i) g_is_comp[j] = 1;
        }
    }
}

inline uint64_t mul_mod(uint64_t a, uint64_t b, uint64_t m) {
#ifdef __SIZEOF_INT128__
    return static_cast<uint64_t>((static_cast<unsigned __int128>(a) * b) % m);
#else
    uint64_t r = 0;
    a %= m;
    while (b > 0) {
        if (b & 1ULL) {
            r = (r + a >= m) ? (r + a - m) : (r + a);
        }
        a = (a + a >= m) ? (a + a - m) : (a + a);
        b >>= 1;
    }
    return r;
#endif
}

inline uint64_t pow_mod(uint64_t a, uint64_t e, uint64_t m) {
    uint64_t r = 1;
    while (e > 0) {
        if (e & 1ULL) r = mul_mod(r, a, m);
        a = mul_mod(a, a, m);
        e >>= 1;
    }
    return r;
}

bool is_prime_u64(uint64_t n) {
    if (n <= static_cast<uint64_t>(SIEVE_MAX)) return !g_is_comp[static_cast<size_t>(n)];
    if (n % 2 == 0 || n % 3 == 0 || n % 5 == 0) return false;
    uint64_t d = n - 1;
    int s = 0;
    while ((d & 1ULL) == 0) {
        d >>= 1;
        ++s;
    }
    static const uint64_t bases[7] = {2ULL, 325ULL, 9375ULL, 28178ULL, 450775ULL, 9780504ULL, 1795265022ULL};
    for (int bi = 0; bi < 7; ++bi) {
        uint64_t a = bases[bi];
        if (a % n == 0) continue;
        uint64_t x = pow_mod(a, d, n);
        if (x == 1 || x == n - 1) continue;
        bool ok = false;
        for (int r = 1; r < s; ++r) {
            x = mul_mod(x, x, n);
            if (x == n - 1) {
                ok = true;
                break;
            }
        }
        if (!ok) return false;
    }
    return true;
}

bool is_eisenstein_prime(Pt p) {
    if (delta3(p.a, p.b) == 0) return false;
    if (p.a == 0 || p.b == 0 || p.a == p.b) {
        int64_t r = std::max(std::abs(int64_t(p.a)), std::abs(int64_t(p.b)));
        return (r % 3 == 2) && is_prime_u64(static_cast<uint64_t>(r));
    }
    int64_t norm = int64_t(p.a) * p.a - int64_t(p.a) * p.b + int64_t(p.b) * p.b;
    return (norm > 0) && (norm % 3 == 1) && is_prime_u64(static_cast<uint64_t>(norm));
}

struct TrapSet {
    std::vector<Pt> pts;
    std::unordered_set<uint64_t> pt_set;

    TrapSet() {
        pts.reserve(4096);
        pt_set.reserve(8192);
    }

    bool contains(Pt p) const { return pt_set.find(pack_pt(p)) != pt_set.end(); }
    int size() const { return static_cast<int>(pts.size()); }

    bool violates_local_3ap(Pt p, bool has_extra, Pt extra) const {
        for (int i = 0; i < 3; ++i) {
            Pt d = N2_AXES[i];
            Pt m1(p.a - d.a, p.b - d.b);
            Pt p1(p.a + d.a, p.b + d.b);
            Pt m2(p.a - 2 * d.a, p.b - 2 * d.b);
            Pt p2(p.a + 2 * d.a, p.b + 2 * d.b);
            bool hm1 = (has_extra && m1 == extra) || contains(m1);
            bool hp1 = (has_extra && p1 == extra) || contains(p1);
            bool hm2 = (has_extra && m2 == extra) || contains(m2);
            bool hp2 = (has_extra && p2 == extra) || contains(p2);
            if (hm1 && hp1) return true;
            if (hm1 && hm2) return true;
            if (hp1 && hp2) return true;
        }
        return false;
    }

    bool validate_round_public(const std::vector<Pt>& placed, std::string& err) const {
        if (placed.size() > 2) {
            err = "k_t must be in {0, 1, 2}";
            return false;
        }
        for (size_t i = 0; i < placed.size(); ++i) {
            if (!in_bounds(placed[i])) {
                err = "trap coordinate out of [-100000, 100000]";
                return false;
            }
            if (delta3(placed[i].a, placed[i].b) == 0) {
                err = "trap coordinate not in H_omega";
                return false;
            }
            if (contains(placed[i])) {
                err = "duplicate trap on already locked vertex";
                return false;
            }
        }
        if (placed.size() == 2 && placed[0] == placed[1]) {
            err = "two traps in same turn have identical coordinates";
            return false;
        }
        if (placed.size() >= 1 && violates_local_3ap(placed[0], false, Pt())) {
            err = "trap #1 violates Local 3-AP-Free constraint";
            return false;
        }
        if (placed.size() == 2 && violates_local_3ap(placed[1], true, placed[0])) {
            err = "trap #2 violates Local 3-AP-Free constraint";
            return false;
        }
        return true;
    }

    void add(Pt p) {
        if (pt_set.insert(pack_pt(p)).second) pts.push_back(p);
    }
};

struct ExtraBlocked {
    const std::unordered_set<uint64_t>* base;
    uint64_t extra[2];
    int n_extra;

    ExtraBlocked(const std::unordered_set<uint64_t>* b) : base(b), n_extra(0) {
        extra[0] = 0;
        extra[1] = 0;
    }

    bool has(uint64_t k) const {
        for (int i = 0; i < n_extra; ++i) {
            if (extra[i] == k) return true;
        }
        return base->find(k) != base->end();
    }
};

void get_legal_moves_ex(Pt u, const ExtraBlocked& blk, std::vector<Pt>& out) {
    out.clear();
    const Pt* dirs = (delta3(u.a, u.b) == 1) ? N1_ODD : N1_EVEN;
    for (int i = 0; i < 3; ++i) {
        Pt v(u.a + dirs[i].a, u.b + dirs[i].b);
        if (!blk.has(pack_pt(v))) out.push_back(v);
    }
    if (is_eisenstein_prime(u)) {
        for (int i = 0; i < 6; ++i) {
            Pt v(u.a + N2_OFF[i].a, u.b + N2_OFF[i].b);
            if (!blk.has(pack_pt(v))) out.push_back(v);
        }
    }
}

void get_legal_moves(Pt u, const std::unordered_set<uint64_t>& traps, std::vector<Pt>& out) {
    ExtraBlocked eb(&traps);
    get_legal_moves_ex(u, eb, out);
}

struct EscapeEval {
    bool escaped;
    int reached;
    int prime_count;
    double best_dir;
    EscapeEval() : escaped(false), reached(0), prime_count(0), best_dir(-1e18) {}
};

inline double pt_x(Pt p) { return p.a - 0.5 * p.b; }
inline double pt_y(Pt p) { return 0.8660254037844386 * p.b; }

EscapeEval eval_escape_bfs(
    Pt start,
    const ExtraBlocked& blk,
    int max_depth,
    int max_nodes,
    double pref_dx,
    double pref_dy
) {
    EscapeEval res;
    if (blk.has(pack_pt(start))) return res;
    static const int VSIZE = 512;
    static const int VMASK = VSIZE - 1;
    static uint64_t vkey[VSIZE];
    static uint32_t vgen[VSIZE];
    static uint32_t cur_gen = 1;
    static std::vector<std::pair<Pt, int> > q;
    q.clear();
    if (++cur_gen == 0) {
        for (int i = 0; i < VSIZE; ++i) vgen[i] = 0;
        cur_gen = 1;
    }
    int cap = std::min(max_nodes, 256);
    auto ins = [&](uint64_t k) -> bool {
        uint32_t h = static_cast<uint32_t>((k * 11400714819323198485ULL) >> 55) & VMASK;
        while (vgen[h] == cur_gen) {
            if (vkey[h] == k) return false;
            h = (h + 1) & VMASK;
        }
        vgen[h] = cur_gen;
        vkey[h] = k;
        return true;
    };
    auto note = [&](Pt p) {
        res.reached++;
        if (is_eisenstein_prime(p)) res.prime_count++;
        double sc = pt_x(p) * pref_dx + pt_y(p) * pref_dy;
        if (sc > res.best_dir) res.best_dir = sc;
    };
    q.push_back(std::make_pair(start, 0));
    ins(pack_pt(start));
    note(start);
    int cnt = 1;
    std::vector<Pt> mvs;
    mvs.reserve(9);
    for (int head = 0; head < static_cast<int>(q.size()) && cnt < cap; ++head) {
        Pt u = q[head].first;
        int d = q[head].second;
        if (d >= max_depth) {
            res.escaped = true;
            continue;
        }
        get_legal_moves_ex(u, blk, mvs);
        for (size_t i = 0; i < mvs.size(); ++i) {
            Pt v = mvs[i];
            if (ins(pack_pt(v))) {
                q.push_back(std::make_pair(v, d + 1));
                note(v);
                if (++cnt >= cap) {
                    res.escaped = true;
                    break;
                }
            }
        }
    }
    return res;
}

struct DeepPlan {
    bool escaped;
    int total_reached;
    int max_dist;
    int best_move_idx;
    double best_proj_delta;
    std::vector<double> move_score;
    std::vector<bool> move_escaped;
    double gap_hx, gap_hy;
    bool has_gap;
    DeepPlan()
        : escaped(false), total_reached(0), max_dist(0), best_move_idx(0),
          best_proj_delta(-1e18), gap_hx(1.0), gap_hy(0.0), has_gap(false) {}
};

DeepPlan run_deep_escape_plan(
    Pt start,
    const std::vector<Pt>& mvs,
    const TrapSet& traps,
    double hx,
    double hy,
    int hug_side,
    double perp_bias,
    int max_depth,
    int max_nodes
) {
    DeepPlan plan;
    const int k = static_cast<int>(mvs.size());
    plan.move_score.assign(static_cast<size_t>(k), -1e18);
    plan.move_escaped.assign(static_cast<size_t>(k), false);
    if (k == 0) return plan;

    static const int VSIZE = 2048;
    static const int VMASK = VSIZE - 1;
    static uint64_t vkey[VSIZE];
    static uint32_t vgen[VSIZE];
    static uint32_t cur_gen = 1;
    if (++cur_gen == 0) {
        for (int i = 0; i < VSIZE; ++i) vgen[i] = 0;
        cur_gen = 1;
    }
    auto ins = [&](uint64_t key) -> bool {
        uint32_t h = static_cast<uint32_t>((key * 11400714819323198485ULL) >> 53) & VMASK;
        while (vgen[h] == cur_gen) {
            if (vkey[h] == key) return false;
            h = (h + 1) & VMASK;
        }
        vgen[h] = cur_gen;
        vkey[h] = key;
        return true;
    };

    struct Node { Pt pt; int dist, first_idx, primes; };
    static std::vector<Node> q;
    q.clear();
    const int cap = std::min(max_nodes, 640);
    ins(pack_pt(start));
    Node root_n = {start, 0, -1, 0};
    q.push_back(root_n);

    double cx = pt_x(start), cy = pt_y(start);
    double px = -hy * hug_side, py = hx * hug_side;

    std::vector<int> order(static_cast<size_t>(k));
    for (int i = 0; i < k; ++i) order[static_cast<size_t>(i)] = i;
    std::sort(order.begin(), order.end(), [&](int i, int j) {
        auto s0 = [&](Pt v) {
            double dx = pt_x(v) - cx, dy = pt_y(v) - cy;
            return (dx * hx + dy * hy) * 2.0 + (dx * px + dy * py) * perp_bias
                   + (is_eisenstein_prime(v) ? 0.35 : 0.0);
        };
        return s0(mvs[static_cast<size_t>(i)]) > s0(mvs[static_cast<size_t>(j)]);
    });

    for (size_t oi = 0; oi < order.size(); ++oi) {
        int idx = order[oi];
        Pt v = mvs[static_cast<size_t>(idx)];
        if (ins(pack_pt(v))) {
            Node n1 = {v, 1, idx, is_eisenstein_prime(v) ? 1 : 0};
            q.push_back(n1);
        }
    }

    std::vector<Pt> nxt;
    nxt.reserve(9);
    for (int head = 1; head < static_cast<int>(q.size()) && static_cast<int>(q.size()) < cap; ++head) {
        Node cur = q[static_cast<size_t>(head)];
        if (cur.dist > plan.max_dist) plan.max_dist = cur.dist;
        if (cur.dist >= max_depth) {
            plan.escaped = true;
            if (cur.first_idx >= 0) plan.move_escaped[static_cast<size_t>(cur.first_idx)] = true;
            continue;
        }
        get_legal_moves(cur.pt, traps.pt_set, nxt);
        for (size_t ni = 0; ni < nxt.size(); ++ni) {
            Pt v = nxt[ni];
            if (ins(pack_pt(v))) {
                int nd = cur.dist + 1;
                Node nn = {v, nd, cur.first_idx, cur.primes + (is_eisenstein_prime(v) ? 1 : 0)};
                q.push_back(nn);
                if (nd > plan.max_dist) plan.max_dist = nd;
                if (nd >= max_depth) {
                    plan.escaped = true;
                    if (cur.first_idx >= 0) plan.move_escaped[static_cast<size_t>(cur.first_idx)] = true;
                }
                if (static_cast<int>(q.size()) >= cap) {
                    plan.escaped = true;
                    break;
                }
            }
        }
    }

    plan.total_reached = static_cast<int>(q.size());
    if (plan.total_reached >= cap) plan.escaped = true;

    static const int NSEC = 12;
    double sec_front[NSEC];
    double sec_maxr[NSEC];
    for (int s = 0; s < NSEC; ++s) { sec_front[s] = 0.0; sec_maxr[s] = 0.0; }
    const int frontier_d = std::max(1, plan.max_dist - 2);
    double global_best = -1e18;

    for (size_t qi = 1; qi < q.size(); ++qi) {
        const Node& nd = q[qi];
        if (nd.first_idx < 0) continue;
        double dx = pt_x(nd.pt) - cx;
        double dy = pt_y(nd.pt) - cy;
        double r = std::hypot(dx, dy);
        bool is_outer = (!plan.escaped) || (nd.dist >= frontier_d) || (qi * 2 >= q.size());
        if (is_outer && r > 1.0) {
            double ang = std::atan2(dy, dx);
            if (ang < 0.0) ang += 6.283185307179586;
            int s = static_cast<int>(ang / 6.283185307179586 * NSEC) % NSEC;
            sec_front[s] += 1.0 + 0.15 * r;
            if (r > sec_maxr[s]) sec_maxr[s] = r;
        }
        if (plan.escaped && !is_outer) continue;
        if (plan.escaped && (nd.dist >= max_depth || static_cast<int>(qi) * 3 >= static_cast<int>(q.size()) * 2)) {
            plan.move_escaped[static_cast<size_t>(nd.first_idx)] = true;
        }
        double proj = dx * hx + dy * hy;
        double perp = dx * px + dy * py;
        double speed = proj / double(std::max(1, nd.dist));
        double perp_speed = perp / double(std::max(1, nd.dist));
        double sc = speed * 130.0 + proj * 15.0 + perp_speed * (perp_bias * 65.0)
                    + (double(nd.primes) / double(std::max(1, nd.dist))) * 18.0;
        if (sc > plan.move_score[static_cast<size_t>(nd.first_idx)]) {
            plan.move_score[static_cast<size_t>(nd.first_idx)] = sc;
        }
        if (proj > plan.best_proj_delta) plan.best_proj_delta = proj;
        if (sc > global_best) {
            global_best = sc;
            plan.best_move_idx = nd.first_idx;
        }
    }

    double best_gap_sc = -1e18;
    int best_s = -1;
    for (int s = 0; s < NSEC; ++s) {
        int sl = (s + NSEC - 1) % NSEC;
        int sr = (s + 1) % NSEC;
        double reach_w = sec_front[s] + 0.45 * (sec_front[sl] + sec_front[sr]);
        double maxr_w = sec_maxr[s];
        if (reach_w < 1.0 && maxr_w < 2.0) continue;
        double ang = (s + 0.5) * (6.283185307179586 / NSEC);
        double dx = std::cos(ang), dy = std::sin(ang);
        double trap_pen = 0.0;
        for (size_t i = 0; i < traps.pts.size(); ++i) {
            double fx = pt_x(traps.pts[i]) - cx, fy = pt_y(traps.pts[i]) - cy;
            double d2 = fx * fx + fy * fy;
            if (d2 < 0.25 || d2 > 6400.0) continue;
            double d = std::sqrt(d2);
            double dot = (fx * dx + fy * dy) / d;
            if (dot > 0.55) trap_pen += (dot - 0.45) * 45.0 / (d + 4.0);
        }
        double cur_dot = dx * hx + dy * hy;
        double side_dot = dx * px + dy * py;
        double gsc = reach_w * 3.5 + maxr_w * 18.0 - trap_pen * 8.0
                     + side_dot * 12.0 - (cur_dot > 0.80 ? 45.0 : 0.0);
        if (gsc > best_gap_sc) {
            best_gap_sc = gsc;
            best_s = s;
        }
    }
    if (best_s >= 0) {
        double ang = (best_s + 0.5) * (6.283185307179586 / NSEC);
        plan.gap_hx = std::cos(ang);
        plan.gap_hy = std::sin(ang);
        plan.has_gap = true;
    }
    return plan;
}

std::pair<double, double> find_open_gap_direction(
    Pt start,
    const TrapSet& traps,
    double cur_hx,
    double cur_hy,
    int hug_side
) {
    std::vector<Pt> mvs;
    get_legal_moves(start, traps.pt_set, mvs);
    if (mvs.empty()) return std::make_pair(cur_hx, cur_hy);
    DeepPlan dp = run_deep_escape_plan(start, mvs, traps, cur_hx, cur_hy, hug_side, 0.25, 16, 420);
    if (dp.has_gap) return std::make_pair(dp.gap_hx, dp.gap_hy);
    double ang = hug_side * 1.2566370614;
    double c = std::cos(ang), s = std::sin(ang);
    return std::make_pair(cur_hx * c - cur_hy * s, cur_hx * s + cur_hy * c);
}

struct StepOut {
    int status, dist, flash, bounce_idx;
    StepOut() : status(0), dist(0), flash(0), bounce_idx(0) {}
};

class RabbitBase {
public:
    virtual ~RabbitBase() {}
    virtual bool step(
        const TrapSet& prev_traps,
        const std::vector<Pt>& placed,
        Pt sonar,
        int max_traps,
        TrapSet& next_traps,
        StepOut& out,
        std::string& err
    ) = 0;
};

class ConcreteRabbit : public RabbitBase {
public:
    ConcreteRabbit(int mode, uint64_t seed)
        : mode_(mode), rng_(seed), rpos_(1, 0), hx_(1.0), hy_(0.0), hug_side_(1),
          turn_(0), stalled_turns_(0), commit_until_(0), best_proj_(0.0) {
        double ang = std::uniform_real_distribution<double>(0.0, 6.283185307179586)(rng_);
        hx_ = std::cos(ang);
        hy_ = std::sin(ang);
        hug_side_ = (rng_() & 1ULL) ? 1 : -1;
        vcnt_[pack_pt(rpos_)] = 1;
        best_proj_ = pt_x(rpos_) * hx_ + pt_y(rpos_) * hy_;
    }

    bool step(
        const TrapSet& prev_traps,
        const std::vector<Pt>& placed,
        Pt sonar,
        int max_traps,
        TrapSet& next_traps,
        StepOut& out,
        std::string& err
    ) {
        out = StepOut();
        int bounce = 0;
        std::vector<Pt> landed;
        for (size_t j = 0; j < placed.size(); ++j) {
            if (placed[j] == rpos_) bounce = static_cast<int>(j + 1);
            else landed.push_back(placed[j]);
        }
        if (prev_traps.size() + static_cast<int>(landed.size()) > max_traps) {
            err = "total locked traps exceeded limit";
            return false;
        }
        for (size_t i = 0; i < landed.size(); ++i) next_traps.add(landed[i]);
        out.bounce_idx = bounce;
        out.flash = is_eisenstein_prime(rpos_) ? 1 : 0;

        std::vector<Pt> mvs;
        get_legal_moves(rpos_, next_traps.pt_set, mvs);
        if (mvs.empty()) {
            out.status = 1;
            return true;
        }
        turn_++;
        rpos_ = choose_move(mvs, next_traps);
        vcnt_[pack_pt(rpos_)]++;
        out.status = 0;
        out.dist = hex_dist(sonar, rpos_);
        return true;
    }

private:
    int count_near_traps(Pt p, const TrapSet& traps, int radius) const {
        int cnt = 0;
        for (size_t i = 0; i < traps.pts.size(); ++i) {
            if (hex_dist(p, traps.pts[i]) <= radius) cnt++;
        }
        return cnt;
    }

    bool wall_ahead_close(Pt cur, const TrapSet& traps) const {
        double cx = pt_x(cur), cy = pt_y(cur);
        int close_front = 0;
        for (size_t i = 0; i < traps.pts.size(); ++i) {
            double fx = pt_x(traps.pts[i]) - cx, fy = pt_y(traps.pts[i]) - cy;
            double d2 = fx * fx + fy * fy;
            if (d2 > 49.0 || d2 < 0.25) continue;
            double d = std::sqrt(d2);
            if ((fx * hx_ + fy * hy_) / d > 0.55) close_front++;
        }
        return close_front >= 3;
    }

    void lock_heading(double nx, double ny, int commit_turns) {
        double nrm = std::hypot(nx, ny);
        if (nrm > 1e-9) {
            hx_ = nx / nrm;
            hy_ = ny / nrm;
        }
        best_proj_ = pt_x(rpos_) * hx_ + pt_y(rpos_) * hy_;
        stalled_turns_ = 0;
        commit_until_ = turn_ + commit_turns;
    }

    Pt choose_move(const std::vector<Pt>& mvs, const TrapSet& traps) {
        if (mvs.size() == 1) return mvs[0];

        double cur_proj = pt_x(rpos_) * hx_ + pt_y(rpos_) * hy_;
        if (cur_proj > best_proj_ + 1.0) {
            best_proj_ = cur_proj;
            stalled_turns_ = 0;
        } else {
            stalled_turns_++;
        }

        if (mode_ == 4 && stalled_turns_ == 0 && turn_ % 36 == 0 && turn_ >= commit_until_) {
            double jitter = std::uniform_real_distribution<double>(-0.31, 0.31)(rng_);
            double c = std::cos(jitter), s = std::sin(jitter);
            lock_heading(hx_ * c - hy_ * s, hx_ * s + hy_ * c, 14);
        }

        int plan_depth = (mode_ == 2) ? 20 : 18;
        int plan_cap = (mode_ == 2) ? 580 : 540;
        double perp_bias = (mode_ == 2) ? 0.42 : 0.20;

        DeepPlan plan = run_deep_escape_plan(
            rpos_, mvs, traps, hx_, hy_, hug_side_, perp_bias, plan_depth, plan_cap
        );

        bool need_pivot = plan.escaped && turn_ >= commit_until_ && (
            plan.best_proj_delta < 3.0
            || stalled_turns_ >= 10
            || (mode_ == 2 && wall_ahead_close(rpos_, traps) && plan.best_proj_delta < 7.0)
        );
        if (need_pivot && plan.has_gap) {
            lock_heading(plan.gap_hx, plan.gap_hy, 18);
            plan = run_deep_escape_plan(
                rpos_, mvs, traps, hx_, hy_, hug_side_, perp_bias, plan_depth, plan_cap
            );
        }

        double cx = pt_x(rpos_), cy = pt_y(rpos_);
        ExtraBlocked eb(&traps.pt_set);
        std::vector<double> scores(mvs.size(), -1e18);
        std::vector<Pt> nm;
        for (size_t i = 0; i < mvs.size(); ++i) {
            Pt v = mvs[i];
            get_legal_moves(v, traps.pt_set, nm);
            bool vp = is_eisenstein_prime(v);
            std::unordered_map<uint64_t, int>::const_iterator vit = vcnt_.find(pack_pt(v));
            int vc = (vit != vcnt_.end()) ? vit->second : 0;

            double safety = 120.0;
            if (nm.empty()) safety = -100000.0;
            else if (nm.size() == 1) safety = -35000.0;
            else if (nm.size() == 2 && !vp) safety = -600.0;

            if (plan.escaped) {
                double dx = pt_x(v) - cx, dy = pt_y(v) - cy;
                double step_proj = dx * hx_ + dy * hy_;
                bool jumped = (hex_dist(rpos_, v) == 2);
                double sc = safety
                            + (plan.move_escaped[i] ? 20000.0 : 0.0)
                            + (static_cast<int>(i) == plan.best_move_idx ? 95.0 : 0.0)
                            + plan.move_score[i]
                            + step_proj * 36.0
                            + (jumped ? 24.0 : 0.0)
                            + (vp ? 18.0 : 0.0)
                            - double(count_near_traps(v, traps, 2)) * 18.0
                            - double(vc) * 36.0;
                if (mode_ == 1) {
                    sc += step_proj * 14.0 + (vp ? 14.0 : 0.0);
                } else if (mode_ == 2) {
                    sc += (jumped ? 22.0 : 0.0);
                }
                sc += std::uniform_real_distribution<double>(-0.35, 0.35)(rng_);
                scores[i] = sc;
            } else {
                EscapeEval ev = eval_escape_bfs(v, eb, 10, 220, hx_, hy_);
                double sc = safety
                            + (ev.escaped ? 15000.0 : 0.0)
                            + ev.reached * 45.0
                            + ev.prime_count * 18.0
                            + double(nm.size()) * 25.0
                            + (vp ? 65.0 : 0.0)
                            - double(vc) * 18.0
                            + std::uniform_real_distribution<double>(-0.8, 0.8)(rng_);
                scores[i] = sc;
            }
        }
        size_t best_i = static_cast<size_t>(std::max_element(scores.begin(), scores.end()) - scores.begin());
        if (mode_ == 4) {
            double mx = scores[best_i];
            std::vector<double> w(mvs.size(), 0.0);
            double sw = 0.0;
            for (size_t i = 0; i < mvs.size(); ++i) {
                double diff = scores[i] - mx;
                w[i] = (diff < -40.0) ? 0.0 : std::exp(diff / 9.0);
                sw += w[i];
            }
            if (sw > 0.0) {
                double r = std::uniform_real_distribution<double>(0.0, sw)(rng_);
                for (size_t i = 0; i < mvs.size(); ++i) {
                    r -= w[i];
                    if (r <= 0.0) { best_i = i; break; }
                }
            }
        }
        return mvs[best_i];
    }

    int mode_;
    std::mt19937_64 rng_;
    Pt rpos_;
    double hx_, hy_;
    int hug_side_;
    int turn_, stalled_turns_, commit_until_;
    double best_proj_;
    std::unordered_map<uint64_t, int> vcnt_;
};

class BeliefAdvRabbit : public RabbitBase {
public:
    explicit BeliefAdvRabbit(uint64_t seed)
        : rng_(seed), hx_(1.0), hy_(0.0), hug_side_(1),
          turn_(0), stalled_turns_(0), commit_until_(0), best_proj_(0.0) {
        double ang = std::uniform_real_distribution<double>(0.0, 6.283185307179586)(rng_);
        hx_ = std::cos(ang);
        hy_ = std::sin(ang);
        hug_side_ = (rng_() & 1ULL) ? 1 : -1;
        belief_.push_back(Pt(1, 0));
        best_proj_ = pt_x(Pt(1, 0)) * hx_ + pt_y(Pt(1, 0)) * hy_;
    }

    bool step(
        const TrapSet& prev_traps,
        const std::vector<Pt>& placed,
        Pt sonar,
        int max_traps,
        TrapSet& next_traps,
        StepOut& out,
        std::string& err
    ) {
        out = StepOut();
        turn_++;
        struct Bucket {
            int bounce, flash, dist;
            std::vector<Pt> states;
            std::unordered_set<uint64_t> seen;
            Bucket() : bounce(0), flash(0), dist(0) {}
        };
        std::unordered_map<uint64_t, Bucket> buckets;

        ExtraBlocked blk0(&prev_traps.pt_set);
        blk0.n_extra = static_cast<int>(placed.size());
        for (size_t i = 0; i < placed.size(); ++i) blk0.extra[i] = pack_pt(placed[i]);

        if (prev_traps.size() + static_cast<int>(placed.size()) <= max_traps) {
            std::vector<Pt> mvs;
            for (size_t ui = 0; ui < belief_.size(); ++ui) {
                Pt u = belief_[ui];
                bool hit = false;
                for (size_t j = 0; j < placed.size(); ++j) if (placed[j] == u) { hit = true; break; }
                if (hit) continue;
                int fl = is_eisenstein_prime(u) ? 1 : 0;
                get_legal_moves_ex(u, blk0, mvs);
                for (size_t vi = 0; vi < mvs.size(); ++vi) {
                    Pt v = mvs[vi];
                    int d = hex_dist(sonar, v);
                    uint64_t bk = (uint64_t(0) << 22) | (uint64_t(fl) << 21) | uint64_t(d);
                    Bucket& b = buckets[bk];
                    if (b.states.empty()) { b.bounce = 0; b.flash = fl; b.dist = d; }
                    if (b.seen.insert(pack_pt(v)).second) b.states.push_back(v);
                }
            }
        }
        for (size_t j = 0; j < placed.size(); ++j) {
            Pt target = placed[j];
            bool in_b = false;
            for (size_t ui = 0; ui < belief_.size(); ++ui) if (belief_[ui] == target) { in_b = true; break; }
            if (!in_b) continue;
            if (prev_traps.size() + static_cast<int>(placed.size()) - 1 > max_traps) continue;
            ExtraBlocked blkj(&prev_traps.pt_set);
            for (size_t i = 0; i < placed.size(); ++i) {
                if (i != j) blkj.extra[blkj.n_extra++] = pack_pt(placed[i]);
            }
            int fl = is_eisenstein_prime(target) ? 1 : 0;
            std::vector<Pt> mvs;
            get_legal_moves_ex(target, blkj, mvs);
            for (size_t vi = 0; vi < mvs.size(); ++vi) {
                Pt v = mvs[vi];
                int d = hex_dist(sonar, v);
                uint64_t bk = (uint64_t(j + 1) << 22) | (uint64_t(fl) << 21) | uint64_t(d);
                Bucket& b = buckets[bk];
                if (b.states.empty()) { b.bounce = static_cast<int>(j + 1); b.flash = fl; b.dist = d; }
                if (b.seen.insert(pack_pt(v)).second) b.states.push_back(v);
            }
        }

        if (buckets.empty()) {
            bool found_cap = false;
            int cap_bounce = 0;
            if (prev_traps.size() + static_cast<int>(placed.size()) <= max_traps) {
                for (size_t ui = 0; ui < belief_.size(); ++ui) {
                    bool hit = false;
                    for (size_t j = 0; j < placed.size(); ++j) if (placed[j] == belief_[ui]) { hit = true; break; }
                    if (!hit) { found_cap = true; cap_bounce = 0; break; }
                }
            }
            if (!found_cap && prev_traps.size() + static_cast<int>(placed.size()) - 1 <= max_traps) {
                for (size_t j = 0; j < placed.size() && !found_cap; ++j) {
                    for (size_t ui = 0; ui < belief_.size(); ++ui) {
                        if (belief_[ui] == placed[j]) { found_cap = true; cap_bounce = static_cast<int>(j + 1); break; }
                    }
                }
            }
            if (!found_cap) {
                err = "total locked traps exceeded limit";
                return false;
            }
            for (size_t i = 0; i < placed.size(); ++i) {
                if (static_cast<int>(i) != cap_bounce - 1) next_traps.add(placed[i]);
            }
            out.status = 1;
            belief_.clear();
            return true;
        }

        double px = -hy_ * hug_side_;
        double py = hx_ * hug_side_;
        auto srank = [&](Pt v) -> double {
            double vx = pt_x(v), vy = pt_y(v);
            double proj = vx * hx_ + vy * hy_;
            double perp = vx * px + vy * py;
            return proj * 3.6 + perp * 0.40 + std::hypot(vx, vy) * 0.65
                   + (is_eisenstein_prime(v) ? 0.85 : 0.0);
        };

        double best_sc = -1e18;
        uint64_t best_bk = 0;
        std::vector<Pt> nm;
        for (std::unordered_map<uint64_t, Bucket>::iterator it = buckets.begin(); it != buckets.end(); ++it) {
            uint64_t bk = it->first;
            Bucket& b = it->second;
            ExtraBlocked blk = blk0;
            if (b.bounce > 0) {
                blk.n_extra = 0;
                for (size_t i = 0; i < placed.size(); ++i) {
                    if (static_cast<int>(i) != b.bounce - 1) blk.extra[blk.n_extra++] = pack_pt(placed[i]);
                }
            }
            int top_k = std::min<int>(static_cast<int>(b.states.size()), 6);
            if (static_cast<int>(b.states.size()) > top_k) {
                std::partial_sort(b.states.begin(), b.states.begin() + top_k, b.states.end(),
                                  [&](Pt x, Pt y) { return srank(x) > srank(y); });
            } else {
                std::sort(b.states.begin(), b.states.end(),
                          [&](Pt x, Pt y) { return srank(x) > srank(y); });
            }
            double best_ev = -1e18;
            int sample_n = std::min<int>(static_cast<int>(b.states.size()), 3);
            for (int i = 0; i < sample_n; ++i) {
                Pt v = b.states[static_cast<size_t>(i)];
                EscapeEval ev = eval_escape_bfs(v, blk, 12, 160, hx_, hy_);
                get_legal_moves_ex(v, blk, nm);
                double safe = (nm.size() <= 1) ? -25000.0
                              : ((nm.size() == 2 && !is_eisenstein_prime(v)) ? -400.0 : 120.0);
                double ev_sc = safe
                               + (ev.escaped ? 18000.0 : 0.0)
                               + ev.reached * 32.0
                               + (ev.escaped ? ev.best_dir * 36.0 : ev.prime_count * 16.0)
                               + srank(v) * 14.0;
                if (ev_sc > best_ev) best_ev = ev_sc;
            }
            double sc = best_ev
                        + 12.0 * std::log(1.0 + double(b.states.size()))
                        - (b.bounce > 0 ? 55.0 : 0.0)
                        + std::uniform_real_distribution<double>(-0.4, 0.4)(rng_);
            if (sc > best_sc) { best_sc = sc; best_bk = bk; }
        }

        Bucket& ch = buckets[best_bk];
        for (size_t i = 0; i < placed.size(); ++i) {
            if (static_cast<int>(i) != ch.bounce - 1) next_traps.add(placed[i]);
        }
        if (ch.states.size() > 512) {
            std::partial_sort(ch.states.begin(), ch.states.begin() + 512, ch.states.end(),
                              [&](Pt a, Pt b) { return srank(a) > srank(b); });
            ch.states.resize(512);
        }
        belief_ = std::move(ch.states);

        double lead_proj = pt_x(belief_[0]) * hx_ + pt_y(belief_[0]) * hy_;
        if (lead_proj > best_proj_ + 1.2) {
            best_proj_ = lead_proj;
            stalled_turns_ = 0;
        } else {
            stalled_turns_++;
            if (stalled_turns_ >= 8 && turn_ >= commit_until_) {
                std::pair<double, double> gd = find_open_gap_direction(belief_[0], next_traps, hx_, hy_, hug_side_);
                double nrm = std::hypot(gd.first, gd.second);
                if (nrm > 1e-9) {
                    hx_ = gd.first / nrm;
                    hy_ = gd.second / nrm;
                }
                best_proj_ = pt_x(belief_[0]) * hx_ + pt_y(belief_[0]) * hy_;
                stalled_turns_ = 0;
                commit_until_ = turn_ + 18;
            }
        }

        out.status = 0;
        out.dist = ch.dist;
        out.flash = ch.flash;
        out.bounce_idx = ch.bounce;
        return true;
    }

private:
    std::mt19937_64 rng_;
    double hx_, hy_;
    int hug_side_;
    int turn_, stalled_turns_, commit_until_;
    double best_proj_;
    std::vector<Pt> belief_;
};

}  // namespace

int main(int argc, char* argv[]) {
    registerInteraction(argc, argv);
    init_sieve();

    int family_id = inf.readInt(1, 4, "family_id");
    uint64_t seed = static_cast<uint64_t>(inf.readLong(1, 9000000000000000000LL, "seed"));
    int max_turns = inf.readInt(1, 50000, "max_turns");
    int max_traps = inf.readInt(1, 50000, "max_traps");

    std::unique_ptr<RabbitBase> strat;
    if (family_id == 3) strat.reset(new BeliefAdvRabbit(seed));
    else strat.reset(new ConcreteRabbit(family_id, seed));

    // 阶段一：读入速率声明 P Q
    int64_t P = ouf.readLong(1, 2LL * MAX_Q_DENOM, "P");
    int64_t Q = ouf.readLong(1, MAX_Q_DENOM, "Q");
    if (P > 2LL * Q) {
        quitf(_wa, "Declared rate P=%lld > 2*Q=%lld", (long long)P, (long long)(2LL * Q));
    }

    int64_t g = my_gcd(P, Q);
    int64_t Pp = P / g;
    int64_t Qp = Q / g;
    int64_t tokens = 0;
    int64_t cap = 2LL * Qp;

    TrapSet traps;
    auto t_start = std::chrono::steady_clock::now();

    for (int t = 1; t <= max_turns; ++t) {
        double elapsed_s = std::chrono::duration<double>(std::chrono::steady_clock::now() - t_start).count();
        if (elapsed_s > 8.5) {
            quitf(_wa, "Interaction wall watchdog triggered on turn %d (%.2fs)", t, elapsed_s);
        }

        tokens = std::min(tokens + Pp, cap);
        int kt = ouf.readInt(0, 2, "k_t");
        if (int64_t(kt) * Qp > tokens) {
            quitf(_wa, "Turn %d: insufficient leaky-bucket tokens (%lld < %lld)",
                  t, (long long)tokens, (long long)(int64_t(kt) * Qp));
        }

        std::vector<Pt> placed;
        placed.reserve(static_cast<size_t>(kt));
        for (int j = 0; j < kt; ++j) {
            int a = ouf.readInt(-COORD_LIMIT, COORD_LIMIT, "trap_a");
            int b = ouf.readInt(-COORD_LIMIT, COORD_LIMIT, "trap_b");
            placed.push_back(Pt(a, b));
        }
        int pa = ouf.readInt(-COORD_LIMIT, COORD_LIMIT, "sonar_a");
        int pb = ouf.readInt(-COORD_LIMIT, COORD_LIMIT, "sonar_b");
        Pt sonar(pa, pb);
        if (!valid_grid(sonar)) {
            quitf(_wa, "Turn %d: sonar (%d, %d) is not in H_omega", t, pa, pb);
        }

        std::string err;
        if (!traps.validate_round_public(placed, err)) {
            quitf(_wa, "Turn %d: %s", t, err.c_str());
        }

        tokens -= int64_t(kt) * Qp;

        StepOut out;
        if (!strat->step(traps, placed, sonar, max_traps, traps, out, err)) {
            quitf(_wa, "Turn %d: %s", t, err.c_str());
        }

        if (out.status == 1) {
            std::cout << "1 0 0 0" << std::endl;
            if (!ouf.seekEof()) {
                quitf(_pe, "Extra output emitted after status=1 capture signal");
            }
            if (Pp >= 2 * Qp) {
                quitf(_wa, "Captured on turn %d (F=%d), but q=%lld/%lld >= 2 gives 0 points",
                      t, traps.size(), (long long)Pp, (long long)Qp);
            }
            if (2 * Pp <= Qp) {
                quitf(_ok, "Captured on turn %d (F=%d)! q=%lld/%lld <= 1/2 -> RawScore=SINGULARITY(+inf), Norm=100.00",
                      t, traps.size(), (long long)Pp, (long long)Qp);
            }
            double raw_sc = 100.0 * double(4 * Qp - 2 * Pp) / double(2 * Pp - Qp);
            double norm_sc = 100.0 * double(2 * Qp - Pp) / double(Pp + Qp);
            double ratio = std::max(0.0, std::min(1.0, norm_sc / 100.0));
            if (ratio >= 0.999999) {
                quitf(_ok, "Captured on turn %d (F=%d)! q=%lld/%lld, RawScore=%.2f, Norm=100.00",
                      t, traps.size(), (long long)Pp, (long long)Qp, raw_sc);
            }
            quitp(ratio, "Captured on turn %d (F=%d)! q=%lld/%lld, RawScore=%.2f, Norm=%.2f/100",
                  t, traps.size(), (long long)Pp, (long long)Qp, raw_sc, norm_sc);
        }

        std::cout << "0 " << out.dist << ' ' << out.flash << ' ' << out.bounce_idx << std::endl;
    }

    quitf(_wa, "Rabbit escaped after %d turns (locked traps=%d, q=%lld/%lld)",
          max_turns, traps.size(), (long long)Pp, (long long)Qp);
    return 0;
}
