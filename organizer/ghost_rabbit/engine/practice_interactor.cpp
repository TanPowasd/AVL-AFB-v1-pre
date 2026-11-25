// 《天翼幽灵兔：四重奇点追截战》（ghost_rabbit）公开练习交互器（engine/practice_interactor.cpp）。
//
// 提供 3 档公开练习兔子（仅供本地 spar.py 自测与可视化调试）：
//   1. P1_BasicEscape : 基础逃逸兔（防即死 + 远离近期陷阱质心 + 轻度随机扰动）
//   2. P2_PrimeGreedy : 素数贪心兔（主动寻找艾森斯坦素数跳板并向开阔扇区拉扯）
//   3. P3_LiteBelief  : 轻量信念对抗兔（维护小规模合法信念集，规避暴露与死胡同）

#include "judge_core.hpp"

#include <cmath>
#include <memory>
#include <random>

using namespace ghost_rabbit;

namespace {

class ConcretePracticeRabbit final : public RabbitStrategyBase {
public:
    ConcretePracticeRabbit(int mode, uint64_t seed) : mode_(mode), rng_(seed) {
        double ang = std::uniform_real_distribution<double>(0.0, 6.283185307179586)(rng_);
        hx_ = std::cos(ang);
        hy_ = std::sin(ang);
        path_.push_back(rpos_);
        vcnt_[pack_pt(rpos_)] = 1;
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
    Pt choose_move(const std::vector<Pt>& mvs, const TrapSet& traps) {
        if (mvs.size() == 1) return mvs[0];
        double cx = rpos_.a - 0.5 * rpos_.b;
        double cy = 0.8660254037844386 * rpos_.b;
        double tcx = 0.0, tcy = 0.0;
        if (!traps.pts.empty()) {
            for (const Pt& f : traps.pts) {
                tcx += f.a - 0.5 * f.b;
                tcy += 0.8660254037844386 * f.b;
            }
            tcx /= traps.pts.size();
            tcy /= traps.pts.size();
        }

        // P2_PrimeGreedy 定期微调逃逸扇区（仅看 10 格内近墙，避免无谓震荡）
        if (mode_ == 2 && (traps.size() <= 20 || traps.size() % 10 == 0)) {
            double best = -1e18, bx = hx_, by = hy_;
            for (int k = 0; k < 8; ++k) {
                double ang = 6.283185307179586 * k / 8.0;
                double dx = std::cos(ang), dy = std::sin(ang);
                double sc = 3.0 * (dx * hx_ + dy * hy_);
                for (const Pt& f : traps.pts) {
                    double fx = f.a - 0.5 * f.b - cx;
                    double fy = 0.8660254037844386 * f.b - cy;
                    double d2 = fx * fx + fy * fy;
                    if (d2 < 0.5 || d2 > 120.0) continue;
                    double d = std::sqrt(d2);
                    double dot = (fx * dx + fy * dy) / d;
                    if (dot > 0.40) sc -= dot * 20.0 / (d + 2.0);
                }
                if (sc > best) { best = sc; bx = dx; by = dy; }
            }
            hx_ = bx;
            hy_ = by;
        }

        ExtraBlocked eb{&traps.pt_set, {0, 0}, 0};
        double best_sc = -1e18;
        Pt best_v = mvs[0];
        std::vector<Pt> nm;
        for (Pt v : mvs) {
            get_legal_moves(v, traps.pt_set, nm);
            double vx = v.a - 0.5 * v.b;
            double vy = 0.8660254037844386 * v.b;
            double prog = (vx - cx) * hx_ + (vy - cy) * hy_;
            double rep = std::hypot(vx - tcx, vy - tcy) - std::hypot(cx - tcx, cy - tcy);
            int vc = vcnt_.count(pack_pt(v)) ? vcnt_[pack_pt(v)] : 0;
            double sc = 0.0;
            if (nm.empty()) sc -= 20000.0;
            else if (nm.size() == 1) sc -= 8000.0;
            sc += nm.size() * 15.0;
            if (mode_ == 1) {
                // P1_BasicEscape：温和基础逃逸兔（Tier 1 教学级）
                sc += rep * 14.0 + prog * 14.0 + (is_eisenstein_prime(v) ? 10.0 : 0.0) - vc * 14.0;
                sc += std::uniform_real_distribution<double>(-10.0, 10.0)(rng_);
            } else {
                // P2_PrimeGreedy：素数贪心兔（Tier 2）
                auto ev = eval_escape_bfs(v, eb, 6, 64, hx_, hy_);
                sc += (ev.escaped ? 4000.0 : 0.0) + ev.reached * 22.0 + ev.best_dir * 16.0;
                sc += (is_eisenstein_prime(v) ? 28.0 : 0.0) + prog * 42.0 + rep * 12.0 - vc * 24.0;
                sc += std::uniform_real_distribution<double>(-1.5, 1.5)(rng_);
            }
            if (sc > best_sc) {
                best_sc = sc;
                best_v = v;
            }
        }
        return best_v;
    }

    int mode_ = 1;
    std::mt19937_64 rng_;
    Pt rpos_{1, 0};
    double hx_ = 1.0, hy_ = 0.0;
    std::vector<Pt> path_;
    std::unordered_map<uint64_t, int> vcnt_;
};

class LiteBeliefPracticeRabbit final : public RabbitStrategyBase {
public:
    explicit LiteBeliefPracticeRabbit(uint64_t seed) : rng_(seed) {
        double ang = std::uniform_real_distribution<double>(0.0, 6.283185307179586)(rng_);
        hx_ = std::cos(ang);
        hy_ = std::sin(ang);
        belief_ = {{1, 0}};
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
        struct Bucket {
            int bounce = 0, flash = 0, dist = 0;
            std::vector<Pt> states;
            std::unordered_map<uint64_t, Pt> parent;
        };
        std::unordered_map<uint64_t, Bucket> buckets;

        ExtraBlocked blk0{&prev_traps.pt_set, {0, 0}, static_cast<int>(placed.size())};
        for (size_t i = 0; i < placed.size(); ++i) blk0.extra[i] = pack_pt(placed[i]);

        if (prev_traps.size() + static_cast<int>(placed.size()) <= max_traps) {
            std::vector<Pt> mvs;
            for (Pt u : belief_) {
                bool hit = false;
                for (Pt p : placed) if (p == u) hit = true;
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
            // 选择合法捕获见证点 u_cap，并严格区分无弹开捕获与弹开后捕获的陷阱落地数
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

        auto srank = [&](Pt v) -> double {
            double vx = v.a - 0.5 * v.b;
            double vy = 0.8660254037844386 * v.b;
            return (vx * hx_ + vy * hy_) * 3.0 + std::hypot(vx, vy) * 0.6 + (is_eisenstein_prime(v) ? 0.8 : 0.0);
        };

        double best_sc = -1e18;
        uint64_t best_bk = 0;
        for (auto& [bk, b] : buckets) {
            ExtraBlocked blk = blk0;
            if (b.bounce > 0) {
                blk.n_extra = 0;
                for (size_t i = 0; i < placed.size(); ++i) {
                    if (static_cast<int>(i) != b.bounce - 1) blk.extra[blk.n_extra++] = pack_pt(placed[i]);
                }
            }
            int sample_n = std::min<int>(static_cast<int>(b.states.size()), 3);
            if (static_cast<int>(b.states.size()) > sample_n) {
                std::partial_sort(b.states.begin(), b.states.begin() + sample_n, b.states.end(),
                                  [&](Pt x, Pt y) { return srank(x) > srank(y); });
            } else {
                std::sort(b.states.begin(), b.states.end(),
                          [&](Pt x, Pt y) { return srank(x) > srank(y); });
            }
            double best_ev = -1e18;
            for (int i = 0; i < sample_n; ++i) {
                Pt v = b.states[static_cast<size_t>(i)];
                auto ev = eval_escape_bfs(v, blk, 5, 48, hx_, hy_);
                std::vector<Pt> nm;
                get_legal_moves_ex(v, blk, nm);
                double safe = nm.size() <= 1 ? -8000.0 : 80.0;
                double sc_v = safe + (ev.escaped ? 4500.0 : 0.0) + ev.reached * 22.0
                              + (ev.escaped ? ev.best_dir * 16.0 : ev.prime_count * 10.0)
                              + srank(v) * 10.0;
                if (sc_v > best_ev) best_ev = sc_v;
            }
            double sc = best_ev + 14.0 * std::log(1.0 + double(b.states.size()))
                        - (b.bounce > 0 ? 55.0 : 0.0)
                        + std::uniform_real_distribution<double>(-1.5, 1.5)(rng_);
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
        if (ch.states.size() > 128) {
            std::partial_sort(ch.states.begin(), ch.states.begin() + 128, ch.states.end(),
                              [&](Pt a, Pt b) { return srank(a) > srank(b); });
            ch.states.resize(128);
        }
        belief_ = std::move(ch.states);
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
        std::cerr << "Usage: practice_judge --strategy P1_BasicEscape|P2_PrimeGreedy|P3_LiteBelief "
                  << "--seed <u64> [--max-turns N] [--max-traps M] [--solver-tl-ms MS] "
                  << "[--result path.json] [--replay path.json]\n";
        return 2;
    }

    std::unique_ptr<RabbitStrategyBase> strat;
    if (cfg.strategy_name == "P1_BasicEscape") {
        strat = std::make_unique<ConcretePracticeRabbit>(1, cfg.seed);
    } else if (cfg.strategy_name == "P2_PrimeGreedy") {
        strat = std::make_unique<ConcretePracticeRabbit>(2, cfg.seed);
    } else if (cfg.strategy_name == "P3_LiteBelief") {
        strat = std::make_unique<LiteBeliefPracticeRabbit>(cfg.seed);
    } else {
        std::cerr << "Unknown practice strategy: " << cfg.strategy_name << "\n";
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
