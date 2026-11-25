// Native C++20 Judge & Replay Recorder CLI for "The SK Singularity" (sk_singularity).
// Usage:
//   judge_cli --case <case_in_path> --expr <expr_out_path> [--lref <val>] [--trace <steps>]
//   judge_cli --brute-check <case_in_path> --expr <expr_out_path> --limit <max_steps>
//   judge_cli --batch

#include "sk_core.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

using namespace sk_singularity;

static bool read_file_bytes(const std::string& path, std::string& out, size_t max_bytes = 65536) {
    std::ifstream ifs(path, std::ios::binary);
    if (!ifs) return false;
    out.clear();
    char buf[4096];
    while (ifs.read(buf, sizeof(buf)) || ifs.gcount() > 0) {
        size_t got = static_cast<size_t>(ifs.gcount());
        if (out.size() + got > max_bytes) {
            out.append(buf, max_bytes - out.size());
            return true;
        }
        out.append(buf, got);
    }
    return true;
}

static std::string trim_line(std::string s) {
    while (!s.empty() && (s.back() == '\n' || s.back() == '\r' || s.back() == ' ' || s.back() == '\t')) s.pop_back();
    size_t i = 0;
    while (i < s.size() && (s[i] == ' ' || s[i] == '\t')) i++;
    return s.substr(i);
}

struct ParsedCase {
    int N = 0;
    int aS = 3;
    int aK = 2;
    int vmax = DEFAULT_MAX_NODES;
    int tl_ms = 5000;
    RuleSpec rule;
    bool has_req = false;
    std::string req_raw = "-";
    ExprAst req_ast;
};

static bool parse_case_stream(std::istream& ifs, ParsedCase& pc, std::string& err) {
    pc = ParsedCase{};
    std::string l1, l2, l3, l4;
    if (!std::getline(ifs, l1) || !std::getline(ifs, l2) || !std::getline(ifs, l3) || !std::getline(ifs, l4)) {
        err = "Case file must have 4 lines";
        return false;
    }
    std::istringstream iss(l1);
    if (!(iss >> pc.N >> pc.aS >> pc.aK >> pc.vmax >> pc.tl_ms)) {
        err = "Malformed header line in case file";
        return false;
    }
    if (pc.N < 1 || pc.N > 64 || pc.vmax < 16 || pc.vmax > 20000000) {
        err = "Case header parameters out of range";
        return false;
    }
    l2 = trim_line(l2);
    l3 = trim_line(l3);
    l4 = trim_line(l4);
    if (!make_rule_spec(pc.aS, l2, pc.aK, l3, pc.rule, err)) {
        return false;
    }
    pc.req_raw = l4;
    if (l4 != "-" && !l4.empty()) {
        pc.has_req = true;
        if (!parse_expr_strict(l4, pc.req_ast, err)) {
            err = "Invalid E_req in case file: " + err;
            return false;
        }
    }
    return true;
}

static bool load_case_file(const std::string& path, ParsedCase& pc, std::string& err) {
    std::ifstream ifs(path);
    if (!ifs) {
        err = "Failed to open case file: " + path;
        return false;
    }
    return parse_case_stream(ifs, pc, err);
}

static bool parse_case_text(const std::string& text, ParsedCase& pc, std::string& err) {
    std::istringstream iss(text);
    return parse_case_stream(iss, pc, err);
}

static void emit_error_json(
    std::ostream& os,
    const std::string& verdict,
    const std::string& msg,
    double judge_ms,
    int leaves = 0,
    int dag_nodes = 0
) {
    std::ostringstream oss;
    oss << "{"
        << "\"verdict\":\"" << json_escape(verdict) << "\","
        << "\"message\":\"" << json_escape(msg) << "\","
        << "\"halted\":false,"
        << "\"leaves\":" << leaves << ","
        << "\"is_exact\":true,"
        << "\"exact_steps\":0,"
        << "\"steps_display\":\"0\","
        << "\"log2_steps\":0.0,"
        << "\"norm_score\":0.0,"
        << "\"singularity\":false,"
        << "\"dag_nodes\":" << dag_nodes << ","
        << "\"redex_fires\":0,"
        << "\"whnf_hits\":0,"
        << "\"nf_hits\":0,"
        << "\"peak_stack\":0,"
        << "\"nf_preview\":\"\","
        << "\"judge_ms\":" << std::fixed << std::setprecision(3) << judge_ms
        << "}\n";
    os << oss.str();
}

static void evaluate_and_emit_json(
    std::ostream& os,
    const ParsedCase& pc,
    const std::string& raw_expr,
    double l_ref,
    int trace_steps,
    MemoEngine* reuse_eng,
    std::chrono::steady_clock::time_point t0
) {
    auto elapsed_ms = [&]() -> double {
        return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
    };

    if (static_cast<int>(raw_expr.size()) > MAX_EXPR_BYTES) {
        emit_error_json(os, "PE", "Output exceeds 4096 bytes limit", elapsed_ms());
        return;
    }

    ExprAst ast;
    std::string err;
    if (!parse_expr_strict(raw_expr, ast, err)) {
        emit_error_json(os, "PE", "Syntax error: " + err, elapsed_ms());
        return;
    }
    if (ast.leaves > pc.N) {
        emit_error_json(
            os,
            "WA",
            "Leaf count " + std::to_string(ast.leaves) + " exceeds limit N=" + std::to_string(pc.N),
            elapsed_ms(),
            ast.leaves
        );
        return;
    }
    if (pc.has_req && !ast_contains_subtree(ast, ast.root, pc.req_ast)) {
        emit_error_json(os, "WA", "Required subtree remnant E_req not found in expression", elapsed_ms(), ast.leaves);
        return;
    }

    int stack_lim = std::max(DEFAULT_MAX_STACK, pc.vmax * 4);
    std::unique_ptr<MemoEngine> local_eng;
    MemoEngine* eng_ptr = reuse_eng;
    if (eng_ptr) {
        eng_ptr->reconfigure(pc.rule, pc.vmax, 0, stack_lim);
    } else {
        local_eng = std::make_unique<MemoEngine>(pc.rule, pc.vmax, 0, stack_lim);
        eng_ptr = local_eng.get();
    }
    MemoEngine& eng = *eng_ptr;
    int root_id = eng.build_ast(ast, ast.root);
    if (root_id < 0) {
        emit_error_json(os, "NL", "Expression itself exceeded DAG node limit", elapsed_ms(), ast.leaves, pc.vmax);
        return;
    }
    EvalResult res = eng.eval_nf(root_id);
    double jms = elapsed_ms();

    if (res.status != EvalStatus::HALTED) {
        std::string v = (res.status == EvalStatus::CYCLE_WHNF || res.status == EvalStatus::CYCLE_NF) ? "DIV" : "NL";
        std::string msg = std::string("Non-halting or guard exceeded: ") + status_name(res.status);
        emit_error_json(os, v, msg, jms, ast.leaves, res.dag_nodes);
        return;
    }

    double log2t = res.steps.log2_plus1();
    double norm = (l_ref > 0.0) ? compute_norm_score(log2t, l_ref) : 0.0;
    bool sing = (log2t >= SINGULARITY_LOG2_THRESHOLD);
    std::string nf_prev = eng.dag_preview(res.nf_root, 180);

    std::ostringstream oss;
    oss << "{"
        << "\"verdict\":\"AC\","
        << "\"message\":\"OK\","
        << "\"halted\":true,"
        << "\"leaves\":" << ast.leaves << ","
        << "\"expr_canonical\":\"" << json_escape(ast_to_sexp(ast, ast.root)) << "\","
        << "\"is_exact\":" << (res.steps.is_exact() ? "true" : "false") << ","
        << "\"exact_steps\":" << (res.steps.is_exact() ? res.steps.mant : 0ULL) << ","
        << "\"steps_display\":\"" << json_escape(res.steps.display_string()) << "\","
        << "\"log2_steps\":" << std::fixed << std::setprecision(6) << log2t << ","
        << "\"norm_score\":" << std::fixed << std::setprecision(4) << norm << ","
        << "\"singularity\":" << (sing ? "true" : "false") << ","
        << "\"dag_nodes\":" << res.dag_nodes << ","
        << "\"redex_fires\":" << res.redex_fires << ","
        << "\"whnf_hits\":" << res.whnf_hits << ","
        << "\"nf_hits\":" << res.nf_hits << ","
        << "\"peak_stack\":" << res.peak_stack << ","
        << "\"nf_preview\":\"" << json_escape(nf_prev) << "\","
        << "\"judge_ms\":" << std::fixed << std::setprecision(3) << jms;

    if (trace_steps > 0) {
        BruteTracer bt(pc.rule, 300000);
        auto frames = bt.record_trace(ast, trace_steps, 520);
        oss << ",\"trace\":[";
        for (size_t i = 0; i < frames.size(); ++i) {
            if (i) oss << ",";
            oss << "{\"step\":" << frames[i].step
                << ",\"expr\":\"" << json_escape(frames[i].expr) << "\""
                << ",\"rule\":\"" << json_escape(frames[i].rule_fired) << "\""
                << ",\"path\":\"" << json_escape(frames[i].redex_path) << "\""
                << ",\"leaves\":" << frames[i].tree_leaves << "}";
        }
        oss << "]";

        // Also export DAG explosion curve (sampled across completed WHNF/NF nodes) + top 18 hot DAG nodes!
        struct HotNode { int id; double log2t; int l; int r; StepCount sc; };
        const int n_nodes = eng.node_count();
        std::vector<HotNode> completed;
        completed.reserve(static_cast<size_t>(n_nodes));
        for (int u = 2; u < n_nodes; ++u) {
            bool has_nf = (eng.nf_st(u) == 2);
            bool has_w = (eng.whnf_st(u) == 2);
            if (!has_nf && !has_w) continue;
            StepCount sc = has_nf ? eng.nf_sc(u) : eng.whnf_sc(u);
            double lt = sc.log2_plus1();
            if (lt > 0.0) completed.push_back({u, lt, eng.left(u), eng.right(u), sc});
        }
        std::vector<HotNode> growth = completed;
        std::sort(growth.begin(), growth.end(), [](const HotNode& a, const HotNode& b) {
            return a.log2t < b.log2t;
        });
        oss << ",\"dag_growth\":[";
        size_t m = growth.size();
        size_t stride = std::max<size_t>(1, m / 64);
        bool first_g = true;
        for (size_t i = 0; i < m; i += stride) {
            if (!first_g) oss << ",";
            first_g = false;
            oss << "{\"id\":" << (i + 1)
                << ",\"log2\":" << std::fixed << std::setprecision(4) << growth[i].log2t << "}";
        }
        if (m > 0 && ((m - 1) % stride) != 0) {
            if (!first_g) oss << ",";
            oss << "{\"id\":" << m
                << ",\"log2\":" << std::fixed << std::setprecision(4) << growth.back().log2t << "}";
        }
        oss << "]";

        std::sort(completed.begin(), completed.end(), [](const HotNode& a, const HotNode& b) {
            return a.log2t > b.log2t;
        });
        size_t topk = std::min<size_t>(18, completed.size());
        oss << ",\"hot_dag_nodes\":[";
        for (size_t i = 0; i < topk; ++i) {
            if (i) oss << ",";
            int u = completed[i].id;
            oss << "{\"id\":" << u
                << ",\"l\":" << completed[i].l
                << ",\"r\":" << completed[i].r
                << ",\"log2\":" << std::fixed << std::setprecision(4) << completed[i].log2t
                << ",\"steps_display\":\"" << json_escape(completed[i].sc.display_string()) << "\""
                << ",\"preview\":\"" << json_escape(eng.dag_preview(u, 96)) << "\"}";
        }
        oss << "]";
    }

    oss << "}\n";
    os << oss.str();
}

static bool read_exact_bytes(std::istream& is, size_t n, std::string& out) {
    out.resize(n);
    if (n == 0) return true;
    is.read(&out[0], static_cast<std::streamsize>(n));
    return static_cast<size_t>(is.gcount()) == n;
}

static int run_batch_mode() {
#ifdef _WIN32
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);
#endif
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);

    RuleSpec init_rule;
    std::string init_err;
    make_rule_spec(3, "((0 2) (1 2))", 2, "0", init_rule, init_err);
    MemoEngine eng(init_rule, DEFAULT_MAX_NODES, 0, DEFAULT_MAX_STACK);

    std::string header;
    std::string case_buf;
    std::string expr_buf;
    static constexpr size_t MAX_FRAME_BYTES = 1048576;

    while (std::getline(std::cin, header)) {
        if (!header.empty() && header.back() == '\r') header.pop_back();
        if (header.empty()) continue;
        std::istringstream hiss(header);
        std::string cmd;
        if (!(hiss >> cmd)) continue;
        if (cmd == "QUIT") break;
        if (cmd != "EVAL") {
            emit_error_json(std::cout, "IE", "Protocol error: unknown batch command", 0.0);
            std::cout.flush();
            return 2;
        }
        size_t case_len = 0;
        size_t expr_len = 0;
        double l_ref = 0.0;
        int trace_steps = 0;
        if (!(hiss >> case_len >> expr_len >> l_ref >> trace_steps)) {
            emit_error_json(std::cout, "IE", "Protocol error: malformed EVAL header", 0.0);
            std::cout.flush();
            return 2;
        }
        if (case_len > MAX_FRAME_BYTES || expr_len > MAX_FRAME_BYTES) {
            emit_error_json(std::cout, "IE", "Protocol error: frame exceeds 1 MiB cap", 0.0);
            std::cout.flush();
            return 2;
        }
        if (!read_exact_bytes(std::cin, case_len, case_buf)) break;
        if (!read_exact_bytes(std::cin, expr_len, expr_buf)) break;

        auto t0 = std::chrono::steady_clock::now();
        ParsedCase pc;
        std::string err;
        if (!parse_case_text(case_buf, pc, err)) {
            double jms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
            emit_error_json(std::cout, "IE", err, jms);
            std::cout.flush();
            continue;
        }
        evaluate_and_emit_json(std::cout, pc, expr_buf, l_ref, trace_steps, &eng, t0);
        std::cout.flush();
        eng.trim_memory_if_oversized();
    }
    return 0;
}

int main(int argc, char** argv) {
    std::string case_path, expr_path;
    double l_ref = 0.0;
    int trace_steps = 0;
    bool brute_mode = false;
    bool batch_mode = false;
    int brute_limit = 5000;

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--batch") batch_mode = true;
        else if (arg == "--case" && i + 1 < argc) case_path = argv[++i];
        else if (arg == "--expr" && i + 1 < argc) expr_path = argv[++i];
        else if (arg == "--lref" && i + 1 < argc) l_ref = std::stod(argv[++i]);
        else if (arg == "--trace" && i + 1 < argc) trace_steps = std::stoi(argv[++i]);
        else if (arg == "--brute-check" && i + 1 < argc) { brute_mode = true; case_path = argv[++i]; }
        else if (arg == "--limit" && i + 1 < argc) brute_limit = std::stoi(argv[++i]);
    }

    if (batch_mode) {
        return run_batch_mode();
    }

    if (case_path.empty() || expr_path.empty()) {
        std::cerr << "Usage: judge_cli --case <in> --expr <out> [--lref <val>] [--trace <steps>]\n"
                  << "       judge_cli --batch\n";
        return 2;
    }

    auto t0 = std::chrono::steady_clock::now();
    auto elapsed_ms = [&]() -> double {
        return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
    };

    ParsedCase pc;
    std::string err;
    if (!load_case_file(case_path, pc, err)) {
        emit_error_json(std::cout, "IE", err, elapsed_ms());
        return 1;
    }

    std::string raw_expr;
    if (!read_file_bytes(expr_path, raw_expr, MAX_EXPR_BYTES + 16)) {
        emit_error_json(std::cout, "PE", "Failed to read expression output file", elapsed_ms());
        return 0;
    }

    if (brute_mode) {
        if (static_cast<int>(raw_expr.size()) > MAX_EXPR_BYTES) {
            emit_error_json(std::cout, "PE", "Output exceeds 4096 bytes limit", elapsed_ms());
            return 0;
        }
        ExprAst ast;
        if (!parse_expr_strict(raw_expr, ast, err)) {
            emit_error_json(std::cout, "PE", "Syntax error: " + err, elapsed_ms());
            return 0;
        }
        if (ast.leaves > pc.N) {
            emit_error_json(
                std::cout,
                "WA",
                "Leaf count " + std::to_string(ast.leaves) + " exceeds limit N=" + std::to_string(pc.N),
                elapsed_ms(),
                ast.leaves
            );
            return 0;
        }
        if (pc.has_req && !ast_contains_subtree(ast, ast.root, pc.req_ast)) {
            emit_error_json(std::cout, "WA", "Required subtree remnant E_req not found in expression", elapsed_ms(), ast.leaves);
            return 0;
        }
        BruteTracer bt(pc.rule, 400000);
        int cur = bt.build_ast(ast, ast.root);
        int steps = -1;
        bool halted = false;
        for (int t = 0; t <= brute_limit; ++t) {
            std::string path;
            int fired = -1;
            int nxt = bt.step_one(cur, path, fired);
            if (nxt == -2) {
                steps = t;
                halted = true;
                break;
            }
            if (nxt == -1) break;
            cur = nxt;
        }
        std::cout << "{\"halted\":" << (halted ? "true" : "false")
                  << ",\"steps\":" << steps << "}\n";
        return 0;
    }

    evaluate_and_emit_json(std::cout, pc, raw_expr, l_ref, trace_steps, nullptr, t0);
    return 0;
}
