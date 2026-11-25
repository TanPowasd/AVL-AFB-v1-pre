#include <array>
#include <iostream>

struct Move {
    int count;
    int ax, ay, bx, by;
    int sx, sy;
    int d1, next1;
    int d2, next2;
};

// Stage table. A node's outgoing labels are exact sonar distances.
// Nodes 3 and 4 close the initial position. Nodes 9 and 10 spend no
// credit while the rabbit makes a forced move inside a two-vertex chamber.
// Nodes 11 and 12 then close the chamber.
constexpr std::array<Move, 13> policy{{
    {1,  2,  0,  0,  0, 1, 1, 0, 1, 2, 2},
    {1,  2,  2,  0,  0, 1, 0, 0, 3, 2, 5},
    {1,  0, -2,  0,  0, 1, 0, 0, 4, 2, 6},
    {2,  0, -1,  1,  1, 1, 0,-1,-1,-1,-1},
    {2,  0, -1,  1,  1, 1, 0,-1,-1,-1,-1},
    {2, -1,  0,  0,  2, 1, 1, 0, 7,-1,-1},
    {2, -2, -2, -1,  0, 0,-1, 0, 8,-1,-1},
    {1,  1,  0,  0,  0, 0, 1, 0, 9,-1,-1},
    {1,  1,  0,  0,  0,-1,-1, 0,10,-1,-1},
    {0,  0,  0,  0,  0, 1, 1, 0,11,-1,-1},
    {0,  0,  0,  0,  0, 0,-1, 0,12,-1,-1},
    {1,  0,  1,  0,  0, 1, 0,-1,-1,-1,-1},
    {1, -1, -1,  0,  0, 1, 0,-1,-1,-1,-1}
}};

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    std::cout << "4 3\n" << std::flush;
    int i = 0;
    for (int round = 1; round <= 5000 && i >= 0; ++round) {
        const Move& m = policy[i];
        std::cout << m.count;
        if (m.count >= 1) std::cout << ' ' << m.ax << ' ' << m.ay;
        if (m.count >= 2) std::cout << ' ' << m.bx << ' ' << m.by;
        std::cout << ' ' << m.sx << ' ' << m.sy << '\n' << std::flush;
        int status, distance, flash, bounce;
        if (!(std::cin >> status >> distance >> flash >> bounce) || status == 1) return 0;
        if (status != 0 || flash != 0 || bounce != 0) return 0;
        i = distance == m.d1 ? m.next1 : distance == m.d2 ? m.next2 : -1;
    }
    return 0;
}
