// Starter template for "Minimum Effort" (minimum_effort / U755456).
// Standalone C++20: reads n, M, c, target ranks R[1..n], M history records (u, m, t),
// and optional fixed-shape schedule (L, (u_j, P_j)). Replace the placeholder with your solver.

#include <iostream>
#include <vector>

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);

    int n = 0, M = 0, c = 0;
    if (!(std::cin >> n >> M >> c)) return 0;

    std::vector<int> R(n + 1);
    for (int i = 1; i <= n; ++i) {
        std::cin >> R[i];
    }

    struct Record {
        int u;
        long long m;
        long long t;
    };
    std::vector<Record> hist(M);
    for (int j = 0; j < M; ++j) {
        std::cin >> hist[j].u >> hist[j].m >> hist[j].t;
    }

    if (c == 1) {
        int L = 0;
        std::cin >> L;
        std::vector<std::pair<int, int>> ops(L);
        for (int j = 0; j < L; ++j) {
            std::cin >> ops[j].first >> ops[j].second;
        }
    }

    // Placeholder: outputs 0 (valid only when all submitters already meet R_i).
    std::cout << 0 << "\n";
    return 0;
}
