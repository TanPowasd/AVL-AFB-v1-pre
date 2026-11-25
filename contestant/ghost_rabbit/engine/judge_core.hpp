#pragma once
// 《天翼幽灵兔：四重奇点追截战》（The Winged Ghost Rabbit / ghost_rabbit）
// 权威 C++20 规则核心头文件（engine/judge_core.hpp）。
//
// 职责：
//   - 艾森斯坦蜂巢网格 H_ω 的合法性、邻域 N_1 / N_2 与 O(1) 三维等距嵌入精确图距离
//   - 艾森斯坦素数洞穴集合 P_ω 的确定性精确判定（2e6 线性筛 + 64-bit 确定性 Miller-Rabin）
//   - 精确有理漏桶（最简分数 P'/Q'，tokens 上限 2Q'，初始 0）
//   - 局部等距无三连共线约束 Local 3-AP-Free（禁止存在 v-d, v, v+d 同时在 F 中，d ∈ N_2(0,0)；
//     严格等价于图距离 <= 6 内无等差三点共线 u + w = 2v）
//   - 公开状态优先的严格单轮合法性检验 + 幽灵弹开 + 素数 p=2 康威天使翼判定
//   - 双轨计分公式（单点原分 RawScore + 奇点标记 + 0~100 饱和折算分 NormScore）
//   - 离线回放 JSON 序列化

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <numeric>
#include <queue>
#include <sstream>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace ghost_rabbit {

static constexpr int COORD_LIMIT = 100000;
static constexpr int COORD_OFFSET = 200000;
static constexpr int MAX_Q_DENOM = 1000000;
static constexpr int DEFAULT_MAX_TURNS = 5000;
static constexpr int DEFAULT_MAX_TRAPS = 4000;
static constexpr int64_t MAX_LINE_BYTES = 1024;

struct Pt {
    int a = 0;
    int b = 0;
    bool operator==(const Pt& o) const noexcept { return a == o.a && b == o.b; }
    bool operator!=(const Pt& o) const noexcept { return !(*this == o); }
    bool operator<(const Pt& o) const noexcept { return a != o.a ? a < o.a : b < o.b; }
};

inline uint64_t pack_pt(Pt p) noexcept {
    return (uint64_t(uint32_t(p.a + COORD_OFFSET)) << 20) | uint32_t(p.b + COORD_OFFSET);
}

inline Pt unpack_pt(uint64_t k) noexcept {
    return {int(k >> 20) - COORD_OFFSET, int(k & ((1ULL << 20) - 1)) - COORD_OFFSET};
}

inline int delta3(int a, int b) noexcept {
    int r = (a + b) % 3;
    return r < 0 ? r + 3 : r;
}

inline bool in_bounds(Pt p) noexcept {
    return p.a >= -COORD_LIMIT && p.a <= COORD_LIMIT && p.b >= -COORD_LIMIT && p.b <= COORD_LIMIT;
}

inline bool valid_grid(Pt p) noexcept {
    return in_bounds(p) && delta3(p.a, p.b) != 0;
}

struct C3 {
    int c1, c2, c3;
};

inline C3 to_c3(Pt p) noexcept {
    int d = delta3(p.a, p.b);
    return {(2 * p.a - p.b + d) / 3, (2 * p.b - p.a + d) / 3, (-p.a - p.b + d) / 3};
}

inline Pt from_c3(int c1, int c2, int c3) noexcept {
    return {c1 - c3, c2 - c3};
}

inline int hex_dist(Pt u, Pt v) noexcept {
    C3 cu = to_c3(u);
    C3 cv = to_c3(v);
    return std::abs(cu.c1 - cv.c1) + std::abs(cu.c2 - cv.c2) + std::abs(cu.c3 - cv.c3);
}

inline std::array<Pt, 3> get_n1(Pt u) noexcept {
    if (delta3(u.a, u.b) == 1) {
        return {{{u.a + 1, u.b}, {u.a, u.b + 1}, {u.a - 1, u.b - 1}}};
    }
    return {{{u.a - 1, u.b}, {u.a, u.b - 1}, {u.a + 1, u.b + 1}}};
}

static constexpr std::array<Pt, 6> N2_OFF = {{{1, -1}, {-1, 1}, {2, 1}, {-2, -1}, {1, 2}, {-1, -2}}};
static constexpr std::array<Pt, 3> N2_AXES = {{{1, -1}, {2, 1}, {1, 2}}};

// ---------------------------------------------------------------------------
// 确定性艾森斯坦素数判定（2e6 筛表 + 64-bit 确定性 Miller-Rabin）
// ---------------------------------------------------------------------------
class PrimeOracle {
public:
    static constexpr int SIEVE_MAX = 2000000;

    PrimeOracle() {
        is_comp_.assign(SIEVE_MAX + 1, 0);
        is_comp_[0] = is_comp_[1] = 1;
        for (int i = 2; 1LL * i * i <= SIEVE_MAX; ++i) {
            if (!is_comp_[i]) {
                for (int j = i * i; j <= SIEVE_MAX; j += i) is_comp_[j] = 1;
            }
        }
    }

    static const PrimeOracle& instance() {
        static const PrimeOracle inst;
        return inst;
    }

    bool is_prime_u64(uint64_t n) const noexcept {
        if (n <= SIEVE_MAX) return !is_comp_[static_cast<size_t>(n)];
        if (n % 2 == 0 || n % 3 == 0 || n % 5 == 0) return false;
        uint64_t d = n - 1;
        int s = 0;
        while ((d & 1) == 0) {
            d >>= 1;
            ++s;
        }
        static constexpr uint64_t bases[] = {2, 325, 9375, 28178, 450775, 9780504, 1795265022};
        for (uint64_t a : bases) {
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

    bool is_eisenstein_prime(Pt p) const noexcept {
        if (delta3(p.a, p.b) == 0) return false;
        if (p.a == 0 || p.b == 0 || p.a == p.b) {
            int64_t r = std::max(std::abs(int64_t(p.a)), std::abs(int64_t(p.b)));
            return (r % 3 == 2) && is_prime_u64(static_cast<uint64_t>(r));
        }
        int64_t norm = int64_t(p.a) * p.a - int64_t(p.a) * p.b + int64_t(p.b) * p.b;
        return (norm > 0) && (norm % 3 == 1) && is_prime_u64(static_cast<uint64_t>(norm));
    }

private:
    static uint64_t mul_mod(uint64_t a, uint64_t b, uint64_t m) noexcept {
        return static_cast<uint64_t>((static_cast<unsigned __int128>(a) * b) % m);
    }

    static uint64_t pow_mod(uint64_t a, uint64_t e, uint64_t m) noexcept {
        uint64_t r = 1;
        while (e > 0) {
            if (e & 1) r = mul_mod(r, a, m);
            a = mul_mod(a, a, m);
            e >>= 1;
        }
        return r;
    }

    std::vector<uint8_t> is_comp_;
};

inline bool is_eisenstein_prime(Pt p) noexcept {
    return PrimeOracle::instance().is_eisenstein_prime(p);
}

// ---------------------------------------------------------------------------
// 局部等距无三连共线集合（Local 3-AP-Free）
// 禁止存在 v ∈ F 与 d ∈ N_2(0,0) 使 v-d, v, v+d 同时在 F 中
// （严格等价于图距离 <= 6 内无等差三点共线 u + w = 2v）
// ---------------------------------------------------------------------------
struct TrapSet {
    std::vector<Pt> pts;
    std::unordered_set<uint64_t> pt_set;

    TrapSet() {
        pts.reserve(4096);
        pt_set.reserve(8192);
    }

    bool contains(Pt p) const noexcept {
        return pt_set.find(pack_pt(p)) != pt_set.end();
    }

    int size() const noexcept {
        return static_cast<int>(pts.size());
    }

    bool violates_local_3ap(Pt p, bool has_extra, Pt extra) const noexcept {
        auto has = [&](Pt q) -> bool {
            if (has_extra && q == extra) return true;
            return contains(q);
        };
        for (Pt d : N2_AXES) {
            Pt m1{p.a - d.a, p.b - d.b};
            Pt p1{p.a + d.a, p.b + d.b};
            Pt m2{p.a - 2 * d.a, p.b - 2 * d.b};
            Pt p2{p.a + 2 * d.a, p.b + 2 * d.b};
            if (has(m1) && has(p1)) return true;
            if (has(m1) && has(m2)) return true;
            if (has(p1) && has(p2)) return true;
        }
        return false;
    }

    // 公开状态合法性检验（先于幽灵弹开判定！）
    bool validate_round_public(const std::vector<Pt>& placed, std::string& err_msg) const {
        if (placed.size() > 2) {
            err_msg = "k_t must be in {0, 1, 2}";
            return false;
        }
        for (size_t i = 0; i < placed.size(); ++i) {
            if (!in_bounds(placed[i])) {
                err_msg = "trap coordinate out of bounds [-100000, 100000]";
                return false;
            }
            if (delta3(placed[i].a, placed[i].b) == 0) {
                err_msg = "trap coordinate not in H_omega ((a+b) mod 3 == 0)";
                return false;
            }
            if (contains(placed[i])) {
                err_msg = "duplicate trap on already blocked vertex";
                return false;
            }
        }
        if (placed.size() == 2 && placed[0] == placed[1]) {
            err_msg = "duplicate traps within the same round";
            return false;
        }
        if (placed.size() >= 1 && violates_local_3ap(placed[0], false, {0, 0})) {
            err_msg = "Local 3-AP-Free violation (3-step straight wall forbidden)";
            return false;
        }
        if (placed.size() == 2 && violates_local_3ap(placed[1], true, placed[0])) {
            err_msg = "Local 3-AP-Free violation (3-step straight wall forbidden)";
            return false;
        }
        return true;
    }

    void add(Pt p) {
        pts.push_back(p);
        pt_set.insert(pack_pt(p));
    }
};

struct ExtraBlocked {
    const std::unordered_set<uint64_t>* base = nullptr;
    uint64_t extra[2] = {0, 0};
    int n_extra = 0;

    inline bool has(uint64_t k) const noexcept {
        for (int i = 0; i < n_extra; ++i) {
            if (extra[i] == k) return true;
        }
        return base->find(k) != base->end();
    }
};

inline void get_legal_moves_ex(Pt u, const ExtraBlocked& blk, std::vector<Pt>& out) {
    out.clear();
    for (Pt v : get_n1(u)) {
        if (!blk.has(pack_pt(v))) out.push_back(v);
    }
    if (is_eisenstein_prime(u)) {
        for (Pt off : N2_OFF) {
            Pt v{u.a + off.a, u.b + off.b};
            if (!blk.has(pack_pt(v))) out.push_back(v);
        }
    }
}

inline void get_legal_moves(Pt u, const std::unordered_set<uint64_t>& blk, std::vector<Pt>& out) {
    ExtraBlocked eb{&blk, {0, 0}, 0};
    get_legal_moves_ex(u, eb, out);
}

// ---------------------------------------------------------------------------
// 精确双轨计分公式
// ---------------------------------------------------------------------------
struct ScoreBreakdown {
    bool singularity = false;
    double raw_score = 0.0;   // 奇点时置 0.0 并设 singularity=true（避免裸 Infinity 破坏 JSON）
    double norm_score = 0.0;  // 0..100，奇点时为 100.0
};

inline ScoreBreakdown compute_score(int64_t P_red, int64_t Q_red, bool captured) noexcept {
    ScoreBreakdown sb;
    if (!captured || Q_red <= 0 || P_red <= 0) return sb;
    if (P_red >= 2 * Q_red) {
        sb.raw_score = 0.0;
        sb.norm_score = 0.0;
        return sb;
    }
    if (2 * P_red <= Q_red) {
        sb.singularity = true;
        sb.raw_score = 0.0;
        sb.norm_score = 100.0;
        return sb;
    }
    double num = 100.0 * double(4 * Q_red - 2 * P_red);
    double den = double(2 * P_red - Q_red);
    sb.raw_score = num / den;
    // NormScore(r) = 100 * r / (r + 200) == 100 * (2*Q' - P') / (P' + Q')
    sb.norm_score = 100.0 * double(2 * Q_red - P_red) / double(P_red + Q_red);
    return sb;
}

// ---------------------------------------------------------------------------
// 严格单行整数解析（防止超长行 OLE 或多余垃圾字符）
// ---------------------------------------------------------------------------
inline bool read_bounded_line(std::istream& in, std::string& line, bool& ole_triggered) {
    line.clear();
    ole_triggered = false;
    char ch = 0;
    while (in.get(ch)) {
        if (ch == '\n') {
            if (!line.empty() && line.back() == '\r') line.pop_back();
            return true;
        }
        if (static_cast<int64_t>(line.size()) >= MAX_LINE_BYTES) {
            ole_triggered = true;
            return false;
        }
        line.push_back(ch);
    }
    if (!line.empty()) {
        if (line.back() == '\r') line.pop_back();
        return true;
    }
    return false;
}

inline bool parse_int64_tokens(const std::string& line, std::vector<int64_t>& out) {
    out.clear();
    const char* s = line.c_str();
    const char* end = s + line.size();
    while (s < end) {
        while (s < end && (*s == ' ' || *s == '\t')) ++s;
        if (s >= end) break;
        bool neg = false;
        if (*s == '-') {
            neg = true;
            ++s;
        } else if (*s == '+') {
            ++s;
        }
        if (s >= end || *s < '0' || *s > '9') return false;
        uint64_t val = 0;
        int digits = 0;
        while (s < end && *s >= '0' && *s <= '9') {
            if (digits > 15) return false;
            val = val * 10 + uint64_t(*s - '0');
            ++digits;
            ++s;
        }
        if (s < end && *s != ' ' && *s != '\t') return false;
        int64_t sv = neg ? -static_cast<int64_t>(val) : static_cast<int64_t>(val);
        out.push_back(sv);
        if (out.size() > 16) return false;
    }
    return true;
}

// ---------------------------------------------------------------------------
// 交互回合回放帧 & JSON 工具
// ---------------------------------------------------------------------------
struct ReplayTurn {
    int turn = 0;
    int64_t tokens_before = 0;
    int64_t tokens_after = 0;
    std::vector<Pt> attempted;
    std::vector<Pt> landed;
    Pt sonar{0, 0};
    int status = 0;
    int dist = 0;
    int flash = 0;
    int bounce_idx = 0;
    Pt rabbit_before{1, 0};
    Pt rabbit_after{1, 0};
    int belief_size = 1;
    std::vector<Pt> belief_sample;  // 至多 32 个信念采样点供可视化热区显示
};

struct MatchSummary {
    std::string strategy;
    uint64_t seed = 0;
    std::string verdict = "WA";  // AC, WA, TLE, PE, OLE, RE
    std::string message;
    int64_t P_raw = 0, Q_raw = 0;
    int64_t P_red = 0, Q_red = 0;
    bool captured = false;
    bool singularity = false;
    double raw_score = 0.0;
    double norm_score = 0.0;
    int turns = 0;
    int traps_placed = 0;
    int bounces = 0;
    int prime_flashes = 0;
    double solver_active_ms = 0.0;
    double handshake_ms = 0.0;
    double interactor_ms = 0.0;
    double total_wall_ms = 0.0;
    std::vector<ReplayTurn> replay_turns;
    std::vector<Pt> witness_path;
};

inline std::string json_escape(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 8);
    for (unsigned char c : s) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", int(c));
                    out += buf;
                } else {
                    out.push_back(static_cast<char>(c));
                }
        }
    }
    return out;
}

inline void write_pts_json(std::ostream& os, const std::vector<Pt>& pts) {
    os << '[';
    for (size_t i = 0; i < pts.size(); ++i) {
        if (i) os << ',';
        os << '[' << pts[i].a << ',' << pts[i].b << ']';
    }
    os << ']';
}

inline bool write_result_json(const std::string& path, const MatchSummary& s) {
    std::ofstream ofs(path, std::ios::binary);
    if (!ofs) return false;
    ofs << std::fixed << std::setprecision(6);
    ofs << "{\n"
        << "  \"strategy\": \"" << json_escape(s.strategy) << "\",\n"
        << "  \"seed\": " << s.seed << ",\n"
        << "  \"verdict\": \"" << json_escape(s.verdict) << "\",\n"
        << "  \"message\": \"" << json_escape(s.message) << "\",\n"
        << "  \"P\": " << s.P_raw << ",\n"
        << "  \"Q\": " << s.Q_raw << ",\n"
        << "  \"P_red\": " << s.P_red << ",\n"
        << "  \"Q_red\": " << s.Q_red << ",\n"
        << "  \"q\": " << (s.Q_red > 0 ? double(s.P_red) / double(s.Q_red) : 0.0) << ",\n"
        << "  \"captured\": " << (s.captured ? "true" : "false") << ",\n"
        << "  \"singularity\": " << (s.singularity ? "true" : "false") << ",\n"
        << "  \"raw_score\": " << s.raw_score << ",\n"
        << "  \"norm_score\": " << s.norm_score << ",\n"
        << "  \"turns\": " << s.turns << ",\n"
        << "  \"traps_placed\": " << s.traps_placed << ",\n"
        << "  \"bounces\": " << s.bounces << ",\n"
        << "  \"prime_flashes\": " << s.prime_flashes << ",\n"
        << "  \"solver_active_ms\": " << std::setprecision(3) << s.solver_active_ms << ",\n"
        << "  \"handshake_ms\": " << s.handshake_ms << ",\n"
        << "  \"interactor_ms\": " << s.interactor_ms << ",\n"
        << "  \"total_wall_ms\": " << s.total_wall_ms << "\n"
        << "}\n";
    return bool(ofs);
}

inline bool write_replay_json(const std::string& path, const MatchSummary& s) {
    std::ofstream ofs(path, std::ios::binary);
    if (!ofs) return false;
    ofs << std::fixed << std::setprecision(6);
    ofs << "{\n"
        << "  \"format\": \"ghost_rabbit_replay/1\",\n"
        << "  \"problem\": \"ghost_rabbit\",\n"
        << "  \"strategy\": \"" << json_escape(s.strategy) << "\",\n"
        << "  \"seed\": " << s.seed << ",\n"
        << "  \"verdict\": \"" << json_escape(s.verdict) << "\",\n"
        << "  \"message\": \"" << json_escape(s.message) << "\",\n"
        << "  \"P_red\": " << s.P_red << ",\n"
        << "  \"Q_red\": " << s.Q_red << ",\n"
        << "  \"q\": " << (s.Q_red > 0 ? double(s.P_red) / double(s.Q_red) : 0.0) << ",\n"
        << "  \"captured\": " << (s.captured ? "true" : "false") << ",\n"
        << "  \"singularity\": " << (s.singularity ? "true" : "false") << ",\n"
        << "  \"raw_score\": " << s.raw_score << ",\n"
        << "  \"norm_score\": " << s.norm_score << ",\n"
        << "  \"turns\": " << s.turns << ",\n"
        << "  \"traps_placed\": " << s.traps_placed << ",\n"
        << "  \"bounces\": " << s.bounces << ",\n"
        << "  \"prime_flashes\": " << s.prime_flashes << ",\n"
        << "  \"solver_active_ms\": " << std::setprecision(3) << s.solver_active_ms << ",\n"
        << "  \"handshake_ms\": " << s.handshake_ms << ",\n"
        << "  \"interactor_ms\": " << s.interactor_ms << ",\n"
        << "  \"witness_path\": ";
    write_pts_json(ofs, s.witness_path);
    ofs << ",\n  \"frames\": [\n";
    for (size_t i = 0; i < s.replay_turns.size(); ++i) {
        const auto& fr = s.replay_turns[i];
        ofs << "    {\"t\":" << fr.turn
            << ",\"tok0\":" << fr.tokens_before
            << ",\"tok1\":" << fr.tokens_after
            << ",\"att\":";
        write_pts_json(ofs, fr.attempted);
        ofs << ",\"land\":";
        write_pts_json(ofs, fr.landed);
        ofs << ",\"sonar\":[" << fr.sonar.a << "," << fr.sonar.b << "]"
            << ",\"st\":" << fr.status
            << ",\"dist\":" << fr.dist
            << ",\"fl\":" << fr.flash
            << ",\"bn\":" << fr.bounce_idx
            << ",\"r0\":[" << fr.rabbit_before.a << "," << fr.rabbit_before.b << "]"
            << ",\"r1\":[" << fr.rabbit_after.a << "," << fr.rabbit_after.b << "]"
            << ",\"bsz\":" << fr.belief_size
            << ",\"bel\":";
        write_pts_json(ofs, fr.belief_sample);
        ofs << "}" << (i + 1 < s.replay_turns.size() ? ",\n" : "\n");
    }
    ofs << "  ]\n}\n";
    return bool(ofs);
}

// ---------------------------------------------------------------------------
// 通用逃逸评估 BFS（零堆分配，线程安全，含 p=2 素数跳跃边）
// ---------------------------------------------------------------------------
struct EscapeEval {
    bool escaped = false;
    int reached = 0;
    int prime_count = 0;
    double best_dir = -1e18;
};

inline EscapeEval eval_escape_bfs(
    Pt start,
    const ExtraBlocked& blk,
    int max_depth,
    int max_nodes,
    double pref_dx = 0.0,
    double pref_dy = 0.0
) {
    EscapeEval res;
    if (blk.has(pack_pt(start))) return res;
    static constexpr int VSIZE = 512;
    static constexpr int VMASK = VSIZE - 1;
    thread_local std::array<uint64_t, VSIZE> vkey{};
    thread_local std::array<uint32_t, VSIZE> vgen{};
    thread_local uint32_t cur_gen = 1;
    thread_local std::vector<std::pair<Pt, int>> q;
    q.clear();
    if (++cur_gen == 0) {
        vgen.fill(0);
        cur_gen = 1;
    }
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
    int cap = std::min(max_nodes, 256);
    auto note = [&](Pt p) {
        res.reached++;
        if (is_eisenstein_prime(p)) res.prime_count++;
        double sc = (p.a - 0.5 * p.b) * pref_dx + (0.8660254037844386 * p.b) * pref_dy;
        if (sc > res.best_dir) res.best_dir = sc;
    };
    q.push_back({start, 0});
    ins(pack_pt(start));
    note(start);
    int cnt = 1;
    std::vector<Pt> mvs;
    mvs.reserve(9);
    for (int head = 0; head < static_cast<int>(q.size()) && cnt < cap; ++head) {
        auto [u, d] = q[head];
        if (d >= max_depth) {
            res.escaped = true;
            continue;
        }
        get_legal_moves_ex(u, blk, mvs);
        for (Pt v : mvs) {
            if (ins(pack_pt(v))) {
                q.push_back({v, d + 1});
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

// ---------------------------------------------------------------------------
// 交互器公共策略基类 & 统一主循环（供公开练习交互器与隐藏正式交互器共用）
// ---------------------------------------------------------------------------
struct StepOutcome {
    int status = 0;  // 0 continue, 1 captured
    int dist = 0;
    int flash = 0;
    int bounce_idx = 0;
    Pt r_before{1, 0};
    Pt r_after{1, 0};
    std::vector<Pt> landed;
    int belief_size = 1;
    std::vector<Pt> belief_sample;
};

class RabbitStrategyBase {
public:
    virtual ~RabbitStrategyBase() = default;
    // 在公开状态校验通过后调用：执行幽灵弹开判定、检查 |F|<=max_traps、移动或判捕获
    virtual bool step(
        const TrapSet& prev_traps,
        const std::vector<Pt>& placed,
        Pt sonar,
        int max_traps,
        TrapSet& next_traps,
        StepOutcome& out,
        std::string& err_msg
    ) = 0;

    // 对局结束后返回与全程观测 100% 一致的实体见证路径
    virtual std::vector<Pt> get_witness_path() const = 0;
};

struct RunConfig {
    std::string strategy_name;
    uint64_t seed = 1;
    int max_turns = DEFAULT_MAX_TURNS;
    int max_traps = DEFAULT_MAX_TRAPS;
    int64_t solver_tl_ms = 5000;
    std::string result_path;
    std::string replay_path;
    int max_replay_frames = 5000;
};

inline MatchSummary run_interactive_session(RabbitStrategyBase& strat, const RunConfig& cfg) {
    using Clock = std::chrono::steady_clock;
    auto t_start = Clock::now();

    MatchSummary sum;
    sum.strategy = cfg.strategy_name;
    sum.seed = cfg.seed;

    // 阶段一：读入速率声明 P Q（单独记录 handshake_ms，不将沙盒/系统拉起抖动误算进局内交互计时；预计算纯 CPU 由外层进程树精确计量）
    std::string line;
    bool ole = false;
    auto t_r0 = Clock::now();
    bool got = read_bounded_line(std::cin, line, ole);
    sum.handshake_ms = std::chrono::duration<double, std::milli>(Clock::now() - t_r0).count();

    if (ole) {
        sum.verdict = "OLE";
        sum.message = "Rate declaration line exceeded 1024 bytes";
        sum.total_wall_ms = std::chrono::duration<double, std::milli>(Clock::now() - t_start).count();
        return sum;
    }
    if (!got) {
        sum.verdict = "PE";
        sum.message = "Unexpected EOF before rate declaration (P Q)";
        sum.total_wall_ms = std::chrono::duration<double, std::milli>(Clock::now() - t_start).count();
        return sum;
    }
    std::vector<int64_t> toks;
    if (!parse_int64_tokens(line, toks) || toks.size() != 2) {
        sum.verdict = "PE";
        sum.message = "Invalid rate declaration format; expected two integers 'P Q'";
        sum.total_wall_ms = std::chrono::duration<double, std::milli>(Clock::now() - t_start).count();
        return sum;
    }
    int64_t P = toks[0], Q = toks[1];
    sum.P_raw = P;
    sum.Q_raw = Q;
    if (Q < 1 || Q > MAX_Q_DENOM || P < 1 || P > 2 * Q) {
        sum.verdict = "WA";
        sum.message = "Rate (P, Q) out of range: require 1 <= Q <= 10^6 and 1 <= P <= 2Q";
        sum.total_wall_ms = std::chrono::duration<double, std::milli>(Clock::now() - t_start).count();
        return sum;
    }

    int64_t g = std::gcd(P, Q);
    int64_t Pp = P / g;
    int64_t Qp = Q / g;
    sum.P_red = Pp;
    sum.Q_red = Qp;

    int64_t tokens = 0;
    int64_t cap = 2 * Qp;
    TrapSet traps;
    const bool record_replay = !cfg.replay_path.empty();

    for (int t = 1; t <= cfg.max_turns; ++t) {
        // 漏桶补给
        tokens = std::min(tokens + Pp, cap);
        int64_t tok_before = tokens;

        // 读入选手第 t 轮指令
        auto tr0 = Clock::now();
        bool ok_line = read_bounded_line(std::cin, line, ole);
        double wait_ms = std::chrono::duration<double, std::milli>(Clock::now() - tr0).count();
        sum.solver_active_ms += wait_ms;

        if (cfg.solver_tl_ms > 0 && sum.solver_active_ms > double(cfg.solver_tl_ms) + 250.0) {
            sum.verdict = "TLE";
            sum.message = "Solver active wait time exceeded limit";
            sum.turns = t;
            break;
        }
        if (ole) {
            sum.verdict = "OLE";
            sum.message = "Output line exceeded 1024 bytes on turn " + std::to_string(t);
            sum.turns = t;
            break;
        }
        if (!ok_line) {
            sum.verdict = "PE";
            sum.message = "Unexpected EOF from solver on turn " + std::to_string(t);
            sum.turns = t;
            break;
        }

        auto ti0 = Clock::now();
        if (!parse_int64_tokens(line, toks) || toks.size() < 3) {
            sum.verdict = "PE";
            sum.message = "Malformed turn output on turn " + std::to_string(t);
            sum.turns = t;
            break;
        }
        int64_t kt = toks[0];
        if (kt < 0 || kt > 2 || toks.size() != static_cast<size_t>(2 * kt + 3)) {
            sum.verdict = "WA";
            sum.message = "Invalid k_t or token count on turn " + std::to_string(t);
            sum.turns = t;
            break;
        }
        if (kt * Qp > tokens) {
            sum.verdict = "WA";
            sum.message = "Insufficient leaky-bucket tokens on turn " + std::to_string(t);
            sum.turns = t;
            break;
        }

        std::vector<Pt> placed;
        placed.reserve(static_cast<size_t>(kt));
        for (int64_t j = 0; j < kt; ++j) {
            int64_t a = toks[static_cast<size_t>(1 + 2 * j)];
            int64_t b = toks[static_cast<size_t>(2 + 2 * j)];
            if (a < -COORD_LIMIT || a > COORD_LIMIT || b < -COORD_LIMIT || b > COORD_LIMIT) {
                sum.verdict = "WA";
                sum.message = "Trap coordinate out of bounds on turn " + std::to_string(t);
                sum.turns = t;
                break;
            }
            placed.push_back({static_cast<int>(a), static_cast<int>(b)});
        }
        if (sum.verdict == "WA" && !sum.message.empty()) break;

        int64_t pa = toks[static_cast<size_t>(1 + 2 * kt)];
        int64_t pb = toks[static_cast<size_t>(2 + 2 * kt)];
        if (pa < -COORD_LIMIT || pa > COORD_LIMIT || pb < -COORD_LIMIT || pb > COORD_LIMIT) {
            sum.verdict = "WA";
            sum.message = "Sonar coordinate out of bounds on turn " + std::to_string(t);
            sum.turns = t;
            break;
        }
        Pt sonar{static_cast<int>(pa), static_cast<int>(pb)};
        if (!valid_grid(sonar)) {
            sum.verdict = "WA";
            sum.message = "Sonar coordinate not in H_omega on turn " + std::to_string(t);
            sum.turns = t;
            break;
        }

        // 公开状态优先校验（坐标合法/无重复/局部 3-AP-Free）
        std::string err_msg;
        if (!traps.validate_round_public(placed, err_msg)) {
            sum.verdict = "WA";
            sum.message = "Turn " + std::to_string(t) + ": " + err_msg;
            sum.turns = t;
            break;
        }

        // 扣减代币
        tokens -= kt * Qp;

        // 执行交互器步进（含幽灵弹开、封锁数上限校验、兔子移动或捕获判定）
        StepOutcome out;
        if (!strat.step(traps, placed, sonar, cfg.max_traps, traps, out, err_msg)) {
            sum.verdict = "WA";
            sum.message = "Turn " + std::to_string(t) + ": " + err_msg;
            sum.turns = t;
            break;
        }

        sum.turns = t;
        sum.traps_placed = traps.size();
        if (out.bounce_idx > 0) sum.bounces++;
        if (out.flash == 1) sum.prime_flashes++;

        if (record_replay && static_cast<int>(sum.replay_turns.size()) < cfg.max_replay_frames) {
            ReplayTurn rt;
            rt.turn = t;
            rt.tokens_before = tok_before;
            rt.tokens_after = tokens;
            rt.attempted = placed;
            rt.landed = out.landed;
            rt.sonar = sonar;
            rt.status = out.status;
            rt.dist = out.dist;
            rt.flash = out.flash;
            rt.bounce_idx = out.bounce_idx;
            rt.rabbit_before = out.r_before;
            rt.rabbit_after = out.r_after;
            rt.belief_size = out.belief_size;
            rt.belief_sample = out.belief_sample;
            sum.replay_turns.push_back(std::move(rt));
        }

        sum.interactor_ms += std::chrono::duration<double, std::milli>(Clock::now() - ti0).count();

        if (out.status == 1) {
            // 追截成功！发送 1 0 0 0 并立即刷新
            std::cout << "1 0 0 0\n" << std::flush;
            sum.captured = true;
            sum.verdict = "AC";
            auto sb = compute_score(Pp, Qp, true);
            sum.singularity = sb.singularity;
            sum.raw_score = sb.raw_score;
            sum.norm_score = sb.norm_score;
            sum.message = "Captured on turn " + std::to_string(t) + " with " + std::to_string(traps.size()) + " traps";
            // 循环排空至 EOF，禁止选手在 status=1 后违规输出任何非空白内容或超长垃圾行
            std::string extra_line;
            bool extra_ole = false;
            bool bad_extra = false;
            while (true) {
                extra_ole = false;
                bool got_extra = read_bounded_line(std::cin, extra_line, extra_ole);
                if (extra_ole) {
                    bad_extra = true;
                    break;
                }
                if (!got_extra) break;
                for (char c : extra_line) {
                    if (c != ' ' && c != '\t' && c != '\r' && c != '\n') {
                        bad_extra = true;
                        break;
                    }
                }
                if (bad_extra) break;
            }
            if (bad_extra) {
                sum.verdict = extra_ole ? "OLE" : "PE";
                sum.captured = false;
                sum.singularity = false;
                sum.raw_score = 0.0;
                sum.norm_score = 0.0;
                sum.message = extra_ole
                    ? "Solver emitted oversized line after status=1 capture signal"
                    : "Solver emitted extra output after status=1 capture signal";
            }
            break;
        }

        // 未捕获：回传 0 dist flash bounce_idx
        std::cout << "0 " << out.dist << ' ' << out.flash << ' ' << out.bounce_idx << '\n' << std::flush;
        if (!std::cout.good()) {
            sum.verdict = "RE";
            sum.message = "Broken pipe writing response on turn " + std::to_string(t);
            break;
        }
    }

    if (!sum.captured && sum.verdict == "WA" && sum.message.empty()) {
        sum.message = "Rabbit escaped after " + std::to_string(cfg.max_turns) + " turns";
    }
    if (record_replay) {
        sum.witness_path = strat.get_witness_path();
        // 对量子策略，将 witness_path 回填至 replay_turns，确保回放里每一步都有精确实体轨迹
        if (!sum.witness_path.empty()) {
            for (size_t i = 0; i < sum.replay_turns.size(); ++i) {
                if (i < sum.witness_path.size()) {
                    sum.replay_turns[i].rabbit_before = sum.witness_path[i];
                    sum.replay_turns[i].rabbit_after =
                        (i + 1 < sum.witness_path.size()) ? sum.witness_path[i + 1] : sum.witness_path[i];
                }
            }
        }
    }
    sum.total_wall_ms = std::chrono::duration<double, std::milli>(Clock::now() - t_start).count();
    return sum;
}

inline bool parse_cli_args(int argc, char** argv, RunConfig& cfg) {
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        auto need_val = [&]() -> const char* {
            if (i + 1 >= argc) return nullptr;
            return argv[++i];
        };
        if (arg == "--strategy") {
            const char* v = need_val(); if (!v) return false; cfg.strategy_name = v;
        } else if (arg == "--seed") {
            const char* v = need_val(); if (!v) return false; cfg.seed = std::strtoull(v, nullptr, 10);
        } else if (arg == "--max-turns") {
            const char* v = need_val(); if (!v) return false; cfg.max_turns = std::atoi(v);
        } else if (arg == "--max-traps") {
            const char* v = need_val(); if (!v) return false; cfg.max_traps = std::atoi(v);
        } else if (arg == "--solver-tl-ms") {
            const char* v = need_val(); if (!v) return false; cfg.solver_tl_ms = std::strtoll(v, nullptr, 10);
        } else if (arg == "--result") {
            const char* v = need_val(); if (!v) return false; cfg.result_path = v;
        } else if (arg == "--replay") {
            const char* v = need_val(); if (!v) return false; cfg.replay_path = v;
        } else if (arg == "--max-replay-frames") {
            const char* v = need_val(); if (!v) return false; cfg.max_replay_frames = std::atoi(v);
        } else {
            return false;
        }
    }
    return !cfg.strategy_name.empty();
}

}  // namespace ghost_rabbit
