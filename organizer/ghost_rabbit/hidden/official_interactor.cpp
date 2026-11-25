// 《天翼幽灵兔：四重奇点追截战》（ghost_rabbit）正式隐藏强交互器（hidden/official_interactor.cpp）。
// 仅用于正式评测，绝不导出至选手工作区！
//
// 4 类互补强逃逸/对抗交互器家族（基于深层 BFS 12 扇区开阔缺口锁定 + 素数跳板全速突围 + 反声呐催眠量子信念空间）：
//   1. F1_PrimeCone   : 素数高速逃逸锥（深层含 p=2 跳跃边最短路规划 + 最开阔扇区持久冲刺 + 2-ply 防即死 + 残局保命）
//   2. F2_WallHugger  : 贴墙绕端突围者（遇近墙优先切向紧贴墙端高速滑行并借素数跃墙突围，遇封死立即切换最宽缺口）
//   3. F3_QuantumAdv  : 量子信念空间对抗者（维护完整一致信念集 + 强推进势能反声呐催眠 + 12 扇区缺口锁定 + 故意弹开）
//   4. F4_Stochastic  : 随机扰动混合突围者（深层开阔缺口主干 + 温和温度 Softmax 与周期微偏防过拟合）

#include "../engine/judge_core.hpp"

#include <cmath>
#include <memory>
#include <random>

using namespace ghost_rabbit;

namespace {

inline double pt_x(Pt p) { return p.a - 0.5 * p.b; }
inline double pt_y(Pt p) { return 0.8660254037844386 * p.b; }

struct DeepPlan {
    bool escaped = false;
    int total_reached = 0;
    int max_dist = 0;
    int best_move_idx = 0;
    double best_proj_delta = -1e18;
    std::vector<double> move_score;
    std::vector<bool> move_escaped;
    // 12 扇区开阔缺口方向（当当前方向被真实墙体挡死时，精准指向包围网最宽、陷阱最少的未封缺口）
    double gap_hx = 1.0;
    double gap_hy = 0.0;
    bool has_gap = false;
};

inline DeepPlan run_deep_escape_plan(
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

    static constexpr int VSIZE = 2048;
    static constexpr int VMASK = VSIZE - 1;
    thread_local std::array<uint64_t, VSIZE> vkey{};
    thread_local std::array<uint32_t, VSIZE> vgen{};
    thread_local uint32_t cur_gen = 1;
    if (++cur_gen == 0) {
        vgen.fill(0);
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

    struct Node {
        Pt pt;
        int dist;
        int first_idx;
        int primes;
    };
    thread_local std::vector<Node> q;
    q.clear();
    const int cap = std::min(max_nodes, 640);
    ins(pack_pt(start));
    q.push_back({start, 0, -1, 0});

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

    for (int idx : order) {
        Pt v = mvs[static_cast<size_t>(idx)];
        if (ins(pack_pt(v))) {
            q.push_back({v, 1, idx, is_eisenstein_prime(v) ? 1 : 0});
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
        for (Pt v : nxt) {
            if (ins(pack_pt(v))) {
                int nd = cur.dist + 1;
                q.push_back({v, nd, cur.first_idx, cur.primes + (is_eisenstein_prime(v) ? 1 : 0)});
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

    // 统计 12 扇区外层前沿可达密度，同时计算当前射线方向最优起步动作
    static constexpr int NSEC = 12;
    std::array<double, NSEC> sec_front{};
    std::array<double, NSEC> sec_maxr{};
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
            sec_front[static_cast<size_t>(s)] += 1.0 + 0.15 * r;
            if (r > sec_maxr[static_cast<size_t>(s)]) sec_maxr[static_cast<size_t>(s)] = r;
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
        if (proj > plan.best_proj_delta) {
            plan.best_proj_delta = proj;
        }
        if (sc > global_best) {
            global_best = sc;
            plan.best_move_idx = nd.first_idx;
        }
    }

    // 结合远场陷阱分布，为 12 个扇区评分找出最宽、陷阱最少的开阔逃逸缺口
    double best_gap_sc = -1e18;
    int best_s = -1;
    for (int s = 0; s < NSEC; ++s) {
        int sl = (s + NSEC - 1) % NSEC;
        int sr = (s + 1) % NSEC;
        double reach_w = sec_front[static_cast<size_t>(s)]
                         + 0.45 * (sec_front[static_cast<size_t>(sl)] + sec_front[static_cast<size_t>(sr)]);
        double maxr_w = sec_maxr[static_cast<size_t>(s)];
        if (reach_w < 1.0 && maxr_w < 2.0) continue;

        double ang = (s + 0.5) * (6.283185307179586 / NSEC);
        double dx = std::cos(ang), dy = std::sin(ang);
        double trap_pen = 0.0;
        for (const Pt& f : traps.pts) {
            double fx = pt_x(f) - cx, fy = pt_y(f) - cy;
            double d2 = fx * fx + fy * fy;
            if (d2 < 0.25 || d2 > 6400.0) continue;  // 80 格内包围墙密度
            double d = std::sqrt(d2);
            double dot = (fx * dx + fy * dy) / d;
            if (dot > 0.55) {
                trap_pen += (dot - 0.45) * 45.0 / (d + 4.0);
            }
        }
        // 避免完全沿当前堵死方向重复选择，轻度偏好单侧绕行方向
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

// 轻量 12 扇区缺口探测（供 F3_QuantumAdv 停滞时寻找真实最开阔缺口）
inline std::pair<double, double> find_open_gap_direction(
    Pt start,
    const TrapSet& traps,
    double cur_hx,
    double cur_hy,
    int hug_side
) {
    std::vector<Pt> mvs;
    get_legal_moves(start, traps.pt_set, mvs);
    if (mvs.empty()) return {cur_hx, cur_hy};
    DeepPlan dp = run_deep_escape_plan(start, mvs, traps, cur_hx, cur_hy, hug_side, 0.25, 16, 420);
    if (dp.has_gap) return {dp.gap_hx, dp.gap_hy};
    double ang = hug_side * 1.2566370614;
    double c = std::cos(ang), s = std::sin(ang);
    return {cur_hx * c - cur_hy * s, cur_hx * s + cur_hy * c};
}

class ConcreteOfficialRabbit final : public RabbitStrategyBase {
public:
    enum class Mode { PrimeCone, WallHugger, Stochastic };

    ConcreteOfficialRabbit(Mode mode, uint64_t seed) : mode_(mode), rng_(seed) {
        double ang = std::uniform_real_distribution<double>(0.0, 6.283185307179586)(rng_);
        hx_ = std::cos(ang);
        hy_ = std::sin(ang);
        hug_side_ = (rng_() & 1ULL) ? 1 : -1;
        path_.push_back(rpos_);
        vcnt_[pack_pt(rpos_)] = 1;
        best_proj_ = pt_x(rpos_) * hx_ + pt_y(rpos_) * hy_;
    }

    bool step(
        const TrapSet& prev_traps,
        const std::vector<Pt>& placed,
        Pt sonar,
        int max_traps,
        TrapSet& next_traps,
        StepOutcome& out,
        std::string& err_msg
    ) override {
        out = StepOutcome{};
        out.r_before = rpos_;
        int bounce = 0;
        std::vector<Pt> landed;
        for (size_t j = 0; j < placed.size(); ++j) {
            if (placed[j] == rpos_) {
                bounce = static_cast<int>(j + 1);
            } else {
                landed.push_back(placed[j]);
            }
        }
        if (prev_traps.size() + static_cast<int>(landed.size()) > max_traps) {
            err_msg = "Total locked traps exceeded limit (" + std::to_string(max_traps) + ")";
            return false;
        }
        for (Pt p : landed) next_traps.add(p);
        out.landed = landed;
        out.bounce_idx = bounce;
        out.flash = is_eisenstein_prime(rpos_) ? 1 : 0;

        std::vector<Pt> mvs;
        get_legal_moves(rpos_, next_traps.pt_set, mvs);
        if (mvs.empty()) {
            out.status = 1;
            out.r_after = rpos_;
            out.belief_size = 0;
            return true;
        }

        turn_++;
        Pt chosen = choose_move(mvs, next_traps);
        rpos_ = chosen;
        path_.push_back(rpos_);
        vcnt_[pack_pt(rpos_)]++;
        out.status = 0;
        out.r_after = rpos_;
        out.dist = hex_dist(sonar, rpos_);
        out.belief_size = 1;
        out.belief_sample = {rpos_};
        return true;
    }

    std::vector<Pt> get_witness_path() const override { return path_; }

private:
    int count_near_traps(Pt p, const TrapSet& traps, int radius) const {
        int cnt = 0;
        for (const Pt& f : traps.pts) {
            if (hex_dist(p, f) <= radius) cnt++;
        }
        return cnt;
    }

    bool wall_ahead_close(Pt cur, const TrapSet& traps) const {
        double cx = pt_x(cur), cy = pt_y(cur);
        int close_front = 0;
        for (const Pt& f : traps.pts) {
            double fx = pt_x(f) - cx, fy = pt_y(f) - cy;
            double d2 = fx * fx + fy * fy;
            if (d2 > 49.0 || d2 < 0.25) continue;  // 仅看 7 格内真实贴脸墙体
            double d = std::sqrt(d2);
            if ((fx * hx_ + fy * hy_) / d > 0.55) close_front++;
        }
        return close_front >= 3;
    }

    void lock_heading(double nx, double ny, int commit_turns = 18) {
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

        // F4_Stochastic 每 36 轮在顺畅冲刺期做温和角度微偏（±18°），保持高速逃逸同时防对手硬预测
        if (mode_ == Mode::Stochastic && stalled_turns_ == 0 && turn_ % 36 == 0 && turn_ >= commit_until_) {
            double jitter = std::uniform_real_distribution<double>(-0.31, 0.31)(rng_);
            double c = std::cos(jitter), s = std::sin(jitter);
            lock_heading(hx_ * c - hy_ * s, hx_ * s + hy_ * c, 14);
        }

        int plan_depth = (mode_ == Mode::WallHugger) ? 20 : 18;
        int plan_cap = (mode_ == Mode::WallHugger) ? 580 : 540;
        double perp_bias = (mode_ == Mode::WallHugger) ? 0.42 : 0.20;

        DeepPlan plan = run_deep_escape_plan(
            rpos_, mvs, traps, hx_, hy_, hug_side_, perp_bias, plan_depth, plan_cap
        );

        // 当且仅当前方真实成墙（深层前沿无法前推，或连续停滞 >= 10 轮，或 F2 贴近厚墙）时，锁定 12 扇区最宽未封缺口全速冲出！
        bool need_pivot = plan.escaped && turn_ >= commit_until_ && (
            plan.best_proj_delta < 3.0
            || stalled_turns_ >= 10
            || (mode_ == Mode::WallHugger && wall_ahead_close(rpos_, traps) && plan.best_proj_delta < 7.0)
        );
        if (need_pivot && plan.has_gap) {
            lock_heading(plan.gap_hx, plan.gap_hy, 18);
            plan = run_deep_escape_plan(
                rpos_, mvs, traps, hx_, hy_, hug_side_, perp_bias, plan_depth, plan_cap
            );
        }

        double cx = pt_x(rpos_), cy = pt_y(rpos_);
        ExtraBlocked eb{&traps.pt_set, {0, 0}, 0};
        std::vector<double> scores(mvs.size(), -1e18);
        std::vector<Pt> nm;
        for (size_t i = 0; i < mvs.size(); ++i) {
            Pt v = mvs[i];
            get_legal_moves(v, traps.pt_set, nm);
            bool vp = is_eisenstein_prime(v);
            int vc = vcnt_.count(pack_pt(v)) ? vcnt_[pack_pt(v)] : 0;

            double safety = 0.0;
            if (nm.empty()) safety = -100000.0;
            else if (nm.size() == 1) safety = -35000.0;
            else if (nm.size() == 2 && !vp) safety = -600.0;
            else safety = 120.0;

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
                if (mode_ == Mode::PrimeCone) {
                    sc += step_proj * 14.0 + (vp ? 14.0 : 0.0);
                } else if (mode_ == Mode::WallHugger) {
                    sc += (jumped ? 22.0 : 0.0);
                }
                sc += std::uniform_real_distribution<double>(-0.35, 0.35)(rng_);
                scores[i] = sc;
            } else {
                auto ev = eval_escape_bfs(v, eb, 10, 220, hx_, hy_);
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

        size_t best_i = static_cast<size_t>(
            std::max_element(scores.begin(), scores.end()) - scores.begin()
        );
        if (mode_ == Mode::Stochastic) {
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

    Mode mode_;
    std::mt19937_64 rng_;
    Pt rpos_{1, 0};
    double hx_ = 1.0, hy_ = 0.0;
    int hug_side_ = 1;
    int turn_ = 0;
    int stalled_turns_ = 0;
    int commit_until_ = 0;
    double best_proj_ = 0.0;
    std::vector<Pt> path_;
    std::unordered_map<uint64_t, int> vcnt_;
};

class QuantumAdversaryRabbit final : public RabbitStrategyBase {
public:
    explicit QuantumAdversaryRabbit(uint64_t seed) : rng_(seed) {
        double ang = std::uniform_real_distribution<double>(0.0, 6.283185307179586)(rng_);
        hx_ = std::cos(ang);
        hy_ = std::sin(ang);
        hug_side_ = (rng_() & 1ULL) ? 1 : -1;
        belief_ = {{1, 0}};
        best_proj_ = pt_x({1, 0}) * hx_ + pt_y({1, 0}) * hy_;
    }

    bool step(
        const TrapSet& prev_traps,
        const std::vector<Pt>& placed,
        Pt sonar,
        int max_traps,
        TrapSet& next_traps,
        StepOutcome& out,
        std::string& err_msg
    ) override {
        out = StepOutcome{};
        turn_++;
        struct Bucket {
            int bounce = 0, flash = 0, dist = 0;
            std::vector<Pt> states;
            std::unordered_map<uint64_t, Pt> parent;
        };
        std::unordered_map<uint64_t, Bucket> buckets;

        ExtraBlocked blk0{&prev_traps.pt_set, {0, 0}, static_cast<int>(placed.size())};
        for (size_t i = 0; i < placed.size(); ++i) blk0.extra[i] = pack_pt(placed[i]);

        // 分支 0：无弹开（全部陷阱落地）
        if (prev_traps.size() + static_cast<int>(placed.size()) <= max_traps) {
            std::vector<Pt> mvs;
            for (Pt u : belief_) {
                bool hit = false;
                for (Pt p : placed) if (p == u) { hit = true; break; }
                if (hit) continue;
                int fl = is_eisenstein_prime(u) ? 1 : 0;
                get_legal_moves_ex(u, blk0, mvs);
                for (Pt v : mvs) {
                    int d = hex_dist(sonar, v);
                    uint64_t bk = (uint64_t(0) << 22) | (uint64_t(fl) << 21) | uint64_t(d);
                    auto& b = buckets[bk];
                    if (b.states.empty()) { b.bounce = 0; b.flash = fl; b.dist = d; }
                    uint64_t kv = pack_pt(v);
                    if (b.parent.find(kv) == b.parent.end()) {
                        b.parent[kv] = u;
                        b.states.push_back(v);
                    }
                }
            }
        }

        // 分支 j+1：主动选择命中第 j+1 枚陷阱触发幽灵弹开（防止关键封口陷阱落地）
        for (size_t j = 0; j < placed.size(); ++j) {
            Pt target = placed[j];
            bool in_b = false;
            for (Pt u : belief_) if (u == target) { in_b = true; break; }
            if (!in_b) continue;
            if (prev_traps.size() + static_cast<int>(placed.size()) - 1 > max_traps) continue;
            ExtraBlocked blkj{&prev_traps.pt_set, {0, 0}, 0};
            for (size_t i = 0; i < placed.size(); ++i) {
                if (i != j) blkj.extra[blkj.n_extra++] = pack_pt(placed[i]);
            }
            int fl = is_eisenstein_prime(target) ? 1 : 0;
            std::vector<Pt> mvs;
            get_legal_moves_ex(target, blkj, mvs);
            for (Pt v : mvs) {
                int d = hex_dist(sonar, v);
                uint64_t bk = (uint64_t(j + 1) << 22) | (uint64_t(fl) << 21) | uint64_t(d);
                auto& b = buckets[bk];
                if (b.states.empty()) { b.bounce = static_cast<int>(j + 1); b.flash = fl; b.dist = d; }
                uint64_t kv = pack_pt(v);
                if (b.parent.find(kv) == b.parent.end()) {
                    b.parent[kv] = target;
                    b.states.push_back(v);
                }
            }
        }

        if (buckets.empty()) {
            bool found_cap = false;
            Pt cap_pt{1, 0};
            int cap_bounce = 0;
            if (prev_traps.size() + static_cast<int>(placed.size()) <= max_traps) {
                for (Pt u : belief_) {
                    bool hit = false;
                    for (Pt p : placed) if (p == u) { hit = true; break; }
                    if (!hit) { cap_pt = u; cap_bounce = 0; found_cap = true; break; }
                }
            }
            if (!found_cap && prev_traps.size() + static_cast<int>(placed.size()) - 1 <= max_traps) {
                for (size_t j = 0; j < placed.size() && !found_cap; ++j) {
                    for (Pt u : belief_) {
                        if (u == placed[j]) {
                            cap_pt = u;
                            cap_bounce = static_cast<int>(j + 1);
                            found_cap = true;
                            break;
                        }
                    }
                }
            }
            if (!found_cap) {
                err_msg = "Total locked traps exceeded limit (" + std::to_string(max_traps) + ")";
                return false;
            }
            for (size_t i = 0; i < placed.size(); ++i) {
                if (static_cast<int>(i) != cap_bounce - 1) {
                    next_traps.add(placed[i]);
                    out.landed.push_back(placed[i]);
                }
            }
            out.status = 1;
            out.bounce_idx = cap_bounce;
            out.flash = is_eisenstein_prime(cap_pt) ? 1 : 0;
            out.r_before = cap_pt;
            out.r_after = cap_pt;
            out.belief_size = 0;
            last_captured_pt_ = cap_pt;
            belief_.clear();
            return true;
        }

        // 反声呐催眠 + 持久定向突围评分：沿逃逸射线外推 1 格收益远大于原地滞留素数点
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
        for (auto& [bk, b] : buckets) {
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
                auto ev = eval_escape_bfs(v, blk, 12, 160, hx_, hy_);
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

        auto& ch = buckets[best_bk];
        for (size_t i = 0; i < placed.size(); ++i) {
            if (static_cast<int>(i) != ch.bounce - 1) {
                next_traps.add(placed[i]);
                out.landed.push_back(placed[i]);
            }
        }
        parent_hist_.push_back(std::move(ch.parent));
        if (ch.states.size() > 512) {
            std::partial_sort(ch.states.begin(), ch.states.begin() + 512, ch.states.end(),
                              [&](Pt a, Pt b) { return srank(a) > srank(b); });
            ch.states.resize(512);
        }
        belief_ = std::move(ch.states);

        // 当领跑状态前方遇墙停滞 >= 8 轮，用 12 扇区深层 BFS 锁定包围网最宽未封缺口全速冲出！
        double lead_proj = pt_x(belief_[0]) * hx_ + pt_y(belief_[0]) * hy_;
        if (lead_proj > best_proj_ + 1.2) {
            best_proj_ = lead_proj;
            stalled_turns_ = 0;
        } else {
            stalled_turns_++;
            if (stalled_turns_ >= 8 && turn_ >= commit_until_) {
                auto [gx, gy] = find_open_gap_direction(belief_[0], next_traps, hx_, hy_, hug_side_);
                double nrm = std::hypot(gx, gy);
                if (nrm > 1e-9) {
                    hx_ = gx / nrm;
                    hy_ = gy / nrm;
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
        out.belief_size = static_cast<int>(belief_.size());
        out.r_after = belief_[0];
        out.r_before = parent_hist_.back()[pack_pt(out.r_after)];
        for (size_t i = 0; i < std::min<size_t>(belief_.size(), 32); ++i) {
            out.belief_sample.push_back(belief_[i]);
        }
        return true;
    }

    std::vector<Pt> get_witness_path() const override {
        Pt cur = belief_.empty() ? last_captured_pt_ : belief_[0];
        std::vector<Pt> rev;
        rev.push_back(cur);
        for (int t = static_cast<int>(parent_hist_.size()) - 1; t >= 0; --t) {
            auto it = parent_hist_[static_cast<size_t>(t)].find(pack_pt(cur));
            if (it == parent_hist_[static_cast<size_t>(t)].end()) break;
            cur = it->second;
            rev.push_back(cur);
        }
        std::reverse(rev.begin(), rev.end());
        return rev;
    }

private:
    std::mt19937_64 rng_;
    double hx_ = 1.0, hy_ = 0.0;
    int hug_side_ = 1;
    int turn_ = 0;
    int stalled_turns_ = 0;
    int commit_until_ = 0;
    double best_proj_ = 0.0;
    std::vector<Pt> belief_;
    std::vector<std::unordered_map<uint64_t, Pt>> parent_hist_;
    Pt last_captured_pt_{1, 0};
};

}  // namespace

int main(int argc, char** argv) {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);

    RunConfig cfg;
    if (!parse_cli_args(argc, argv, cfg)) {
        std::cerr << "Usage: official_judge --strategy F1_PrimeCone|F2_WallHugger|F3_QuantumAdv|F4_Stochastic "
                  << "--seed <u64> [--max-turns N] [--max-traps M] [--solver-tl-ms MS] "
                  << "[--result path.json] [--replay path.json]\n";
        return 2;
    }

    std::unique_ptr<RabbitStrategyBase> strat;
    if (cfg.strategy_name == "F1_PrimeCone") {
        strat = std::make_unique<ConcreteOfficialRabbit>(ConcreteOfficialRabbit::Mode::PrimeCone, cfg.seed);
    } else if (cfg.strategy_name == "F2_WallHugger") {
        strat = std::make_unique<ConcreteOfficialRabbit>(ConcreteOfficialRabbit::Mode::WallHugger, cfg.seed);
    } else if (cfg.strategy_name == "F3_QuantumAdv") {
        strat = std::make_unique<QuantumAdversaryRabbit>(cfg.seed);
    } else if (cfg.strategy_name == "F4_Stochastic") {
        strat = std::make_unique<ConcreteOfficialRabbit>(ConcreteOfficialRabbit::Mode::Stochastic, cfg.seed);
    } else {
        std::cerr << "Unknown official strategy: " << cfg.strategy_name << "\n";
        return 2;
    }

    MatchSummary summary = run_interactive_session(*strat, cfg);
    if (!cfg.result_path.empty()) {
        if (!write_result_json(cfg.result_path, summary)) {
            std::cerr << "Failed to write result JSON: " << cfg.result_path << "\n";
            return 3;
        }
    }
    if (!cfg.replay_path.empty()) {
        if (!write_replay_json(cfg.replay_path, summary)) {
            std::cerr << "Failed to write replay JSON: " << cfg.replay_path << "\n";
            return 3;
        }
    }
    return 0;
}
