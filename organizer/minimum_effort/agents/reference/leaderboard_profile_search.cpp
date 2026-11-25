#include <unordered_map>
#include <bitset>
#include <algorithm>
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <utility>
#include <limits>
#include <memory>
#include <queue>
#include <numeric>
#include <vector>

using i64 = std::int64_t;

static constexpr int MAX_SCORE = 1000000000;
static constexpr int MAX_TIME = 1000000000;
static constexpr int GENERAL = 10;
static constexpr int MOD = 1000000007;

struct Input {
    int read() {
        char c = next();
        while (c < '0' || c > '9') {
            c = next();
        }
        int value = c - '0';
        if (end - pos >= 11) {
            const char* p = buffer + pos;
            while (unsigned(*p - '0') < 10) {
                value = 10 * value + (*p++ - '0');
            }
            pos = int(p - buffer) + 1;
            return value;
        }
        c = next();
        while (unsigned(c - '0') < 10) {
            value = 10 * value + (c - '0');
            c = next();
        }
        return value;
    }

private:
    char buffer[1 << 18];
    int pos = 0;
    int end = 0;

    char next() {
        if (pos == end) {
            end = static_cast<int>(std::fread(buffer, 1, sizeof(buffer), stdin));
            pos = 0;
            if (end == 0) {
                return 0;
            }
        }
        return buffer[pos++];
    }
};

struct Record {
    int owner;
    int score;
    int time;
};

struct Player {
    int target = 0;
    int rank = 0;
};

template<int Capacity>
struct BinarySum {
    std::array<int, Capacity> bits;
    int size = 0;

    BinarySum() : bits{} {}

    void add(int exponent) {
        int pos = 0;
        while (pos < size && bits[pos] < exponent) {
            ++pos;
        }
        int end=pos;
        while(end<size && bits[end]==exponent){++end;++exponent;}
        if(end==pos){
            for(int k=size;k>pos;--k)bits[k]=bits[k-1];
        }else{
            for(int k=end;k<size;++k)bits[pos+1+k-end]=bits[k];
        }
        size+=1-(end-pos);bits[pos]=exponent;
    }

    bool operator<(const BinarySum& other) const {
        int i = size - 1;
        int j = other.size - 1;
        while (i >= 0 && j >= 0 && bits[i] == other.bits[j]) {
            --i;
            --j;
        }
        if (i < 0 || j < 0) {
            return i < j;
        }
        return bits[i] < other.bits[j];
    }

    void add(const BinarySum& other) {
        for (int i = 0; i < other.size; ++i) {
            add(other.bits[i]);
        }
    }

    int residue() const {
        i64 result = 0;
        for (int i = 0; i < size; ++i) {
            i64 base = 2;
            i64 power = 1;
            for (i64 exponent = bits[i]; exponent > 0; exponent >>= 1) {
                if (exponent & 1) {
                    power = power * base % MOD;
                }
                base = base * base % MOD;
            }
            result = (result + power) % MOD;
        }
        return static_cast<int>(result);
    }
};

struct Bound {
    int from, to;
    int limit;
};

struct Term {
    int from, to, constant;
};

template<int Capacity>
BinarySum<Capacity> evaluate(const std::vector<Term>& fees, const std::vector<int>& scores) {
    BinarySum<Capacity> result;
    for (auto [u,v,c] : fees) {
        result.add(int(i64{c}+scores[v]-scores[u]));
    }
    return result;
}

template<int Words>
class Unsigned {
    using Word = std::uint64_t;
    std::array<Word, Words> word{};
    int length = 0;

public:
    Unsigned() = default;

    static Unsigned power(int exponent) {
        Unsigned value;
        value.length = exponent / 64 + 1;
        assert(value.length <= Words);
        value.word[value.length - 1] = Word{1} << (exponent % 64);
        return value;
    }

    explicit operator bool() const {
        return length != 0;
    }

    bool operator<(const Unsigned& other) const {
        if (length != other.length) {
            return length < other.length;
        }
        for (int i = length - 1; i >= 0; --i) {
            if (word[i] != other.word[i]) {
                return word[i] < other.word[i];
            }
        }
        return false;
    }

    Unsigned& operator+=(const Unsigned& other) {
        Word carry = 0;
        int n = std::max(length, other.length);
        for (int i = 0; i < n; ++i) {
            Word b = i < other.length ? other.word[i] : 0;
            Word sum = word[i] + b;
            Word next = (sum < word[i]);
            word[i] = sum + carry;
            carry = next | (word[i] < sum);
        }
        length = n;
        if (carry) {
            assert(length < Words);
            word[length++] = carry;
        }
        return *this;
    }

    Unsigned& operator-=(const Unsigned& other) {
        Word borrow = 0;
        for (int i = 0; i < length; ++i) {
            Word b = i < other.length ? other.word[i] : 0;
            Word diff = word[i] - b;
            Word next = (word[i] < b);
            word[i] = diff - borrow;
            borrow = next | (word[i] > diff);
            if (i + 1 >= other.length && !borrow) {
                break;
            }
        }
        assert(!borrow);
        while (length && !word[length - 1]) {
            --length;
        }
        return *this;
    }

    friend Unsigned operator+(Unsigned a, const Unsigned& b) { return a += b; }
    friend Unsigned operator-(Unsigned a, const Unsigned& b) { return a -= b; }
};

template<int Words>
class Flow {
    using Weight = Unsigned<Words>;
    struct Edge {
        int to, reverse;
        Weight capacity;
    };
    std::vector<std::vector<Edge>> graph;
    std::vector<int> level, next;
    int source, sink;

    bool bfs() {
        std::fill(level.begin(), level.end(), -1);
        std::vector<int> queue;
        queue.reserve(graph.size());
        queue.push_back(source);
        level[source] = 0;
        for (std::size_t i = 0; i < queue.size(); ++i) {
            int u = queue[i];
            for (const auto& e : graph[u]) {
                if (e.capacity && level[e.to] < 0) {
                    level[e.to] = level[u] + 1;
                    queue.push_back(e.to);
                }
            }
        }
        return level[sink] >= 0;
    }

    Weight send(int u, const Weight& allowance) {
        if (u == sink) {
            return allowance;
        }
        for (int& i = next[u]; i < int(graph[u].size()); ++i) {
            auto& e = graph[u][i];
            if (e.capacity && level[e.to] == level[u] + 1) {
                auto flow = send(e.to, std::min(allowance, e.capacity));
                if (flow) {
                    e.capacity -= flow;
                    graph[e.to][e.reverse].capacity += flow;
                    return flow;
                }
            }
        }
        return {};
    }

public:
    explicit Flow(int n) : graph(n + 2), level(n + 2), next(n + 2), source(n), sink(n + 1) {}

    void add(int from, int to, const Weight& cap) {
        if (cap && from != to) {
            int index = int(graph[from].size());
            graph[from].push_back({to, int(graph[to].size()), cap});
            graph[to].push_back({from, index, {}});
        }
    }

    void field(int v, const Weight& cap, bool negative) {
        if (negative) {
            add(source, v, cap);
        } else {
            add(v, sink, cap);
        }
    }

    std::vector<int> minimumCut(const Weight& infinity) {
        while (bfs()) {
            std::fill(next.begin(), next.end(), 0);
            while (send(source, infinity)) {}
        }
        std::vector<int> selected(source);
        for (int v = 0; v < source; ++v) {
            selected[v] = level[v] >= 0;
        }
        return selected;
    }
};

template<int Words>
std::vector<int> scoreCut(const std::vector<int>& scores, int step,
                          const std::vector<Bound>& bounds, const std::vector<Term>& terms) {
    using Weight = Unsigned<Words>;
    int n = int(scores.size());
    std::vector<i64> exponents, center;
    exponents.reserve(3 * terms.size());
    for (auto [u, v, c] : terms) {
        i64 d = i64{c} + scores[v] - scores[u];
        center.push_back(d);
        exponents.insert(exponents.end(), {d - step, d, d + step});
    }
    std::sort(exponents.begin(), exponents.end());
    exponents.erase(std::unique(exponents.begin(), exponents.end()), exponents.end());
    int gap = 32 - __builtin_clz(2 * unsigned(terms.size()));
    std::vector<int> compressed(exponents.size());
    for (std::size_t i = 1; i < exponents.size(); ++i) {
        compressed[i] = compressed[i - 1] + int(std::min<i64>(gap, exponents[i] - exponents[i - 1]));
    }
    auto power = [&](i64 exponent) {
        auto i = std::lower_bound(exponents.begin(), exponents.end(), exponent) - exponents.begin();
        return Weight::power(compressed[i]);
    };
    Flow<Words> network(n);
    Weight infinity = Weight::power(0);
    std::vector<Weight> positive(n), negative(n);
    for (std::size_t i = 0; i < terms.size(); ++i) {
        auto [u, v, c] = terms[i];
        auto at = power(center[i]);
        auto after = power(center[i] + step);
        auto capacity = after + power(center[i] - step) - at - at;
        auto delta = after - at;
        positive[v] += delta;
        negative[u] += delta;
        network.add(u, v, capacity);
        infinity += capacity;
    }
    for (int v = 0; v < n; ++v) {
        bool neg = positive[v] < negative[v];
        auto magnitude = neg ? negative[v] - positive[v] : positive[v] - negative[v];
        network.field(v, magnitude, neg);
        infinity += magnitude;
    }
    for (auto [u, v, c] : bounds) {
        if (i64{scores[v]} - scores[u] + step > c) {
            network.add(v, u, infinity);
        }
    }
    return network.minimumCut(infinity);
}

template<int Words>
std::vector<int> minimizeScores(std::vector<int> scores, const std::vector<Bound>& bounds,
                                const std::vector<Term>& fees) {
    std::vector<Term> terms;
    for (auto fee : fees) {
        if (fee.from != fee.to) {
            terms.push_back(fee);
        }
    }
    if (terms.empty()) {
        return scores;
    }
    int step = 1;
    while (step < MAX_SCORE) {
        step *= 2;
    }
    for (step /= 2; step; step /= 2) {
        for (;;) {
            auto chosen = scoreCut<Words>(scores, step, bounds, terms);
            if (std::none_of(chosen.begin(), chosen.end(), [](int b) { return b != 0; })) {
                break;
            }
            for (std::size_t v = 1; v < scores.size(); ++v) {
                scores[v] += step * (chosen[v] - chosen[0]);
            }
        }
    }
    return scores;
}

static bool historicalOrder(const Record& a, const Record& b) {
    return a.score != b.score ? a.score > b.score : a.time < b.time;
}

template<class Key>
void radixHistory(Record* records, Record* buffer, int size, Key key) {
    static constexpr int bits = 10, radix = 1 << bits, mask = radix - 1;
    int histogram[3][radix]{};
    for (const Record* it = records; it != records + size; ++it) {
        unsigned k = key(*it);
        ++histogram[0][k & mask];
        ++histogram[1][(k >> bits) & mask];
        ++histogram[2][k >> (2 * bits)];
    }
    auto src = records;
    auto dst = buffer;
    for (int j = 0; j < 3; ++j) {
        auto& offset = histogram[j];
        if (std::find(offset, offset + radix, size) != offset + radix) {
            continue;
        }
        int index = 0;
        for (int& c : offset) {
            int next = index + c;
            c = index;
            index = next;
        }
        int shift = bits * j;
        for (const Record* it = src; it != src + size; ++it) {
            unsigned digit = (key(*it) >> shift) & mask;
            dst[offset[digit]++] = *it;
        }
        std::swap(src, dst);
    }
    if (src != records) {
        std::copy_n(src, size, records);
    }
}

void sortHistory(std::vector<Record>& old, std::vector<Player>& players) {
    int size = int(old.size()) - 1;
    if (size < 32768) {
        std::sort(old.begin() + 1, old.end(), historicalOrder);
    } else {
        std::unique_ptr<Record[]> scratch(new Record[size]);
        radixHistory(old.data() + 1, scratch.get(), size,
                     [](const Record& r) { return unsigned(MAX_SCORE - r.score); });
        for (int left = 1; left <= size;) {
            int right = left + 1;
            while (right <= size && old[right].score == old[left].score) {
                ++right;
            }
            if (right - left >= 32768) {
                radixHistory(old.data() + left, scratch.get(), right - left,
                             [](const Record& r) { return unsigned(r.time); });
            } else if (right - left > 1) {
                std::sort(old.begin() + left, old.begin() + right,
                          [](const Record& a, const Record& b) { return a.time < b.time; });
            }
            left = right;
        }
    }
    for (int r = 1; r < int(old.size()); ++r) {
        int owner = old[r].owner;
        if (!players[owner].rank) {
            players[owner].rank = r;
        }
    }
}

class RankCounter {
    std::vector<int> counts;
    int top = 1;

public:
    explicit RankCounter(int size) : counts(size + 1) {
        while (top <= size / 2) top *= 2;
    }

    void add(int position) {
        for (int p = position; p < int(counts.size()); p += p & -p) ++counts[p];
    }

    int kth(int order) const {
        int pos = 0;
        for (int g = top; g; g >>= 1) {
            int p = pos + g;
            if (p < int(counts.size()) && counts[p] < order) {
                pos = p;
                order -= counts[p];
            }
        }
        return pos + 1;
    }

    int appendedBefore(int rank, const std::vector<int>& historyPrefix) const {
        int pos = 0, prefix = 0;
        for (int g = top; g; g >>= 1) {
            int p = pos + g;
            if (p < int(counts.size()) && historyPrefix[p] + prefix + counts[p] < rank) {
                pos = p;
                prefix += counts[p];
            }
        }
        return prefix;
    }
};

static int powerMod(int exponent) {
    constexpr int BITS = 31;
    static const auto powers = [] {
        std::array<int, BITS> values{};
        values[0] = 2;
        for (int i = 1; i < BITS; ++i) values[i] = i64{values[i - 1]} * values[i - 1] % MOD;
        return values;
    }();
    i64 answer = 1;
    for (unsigned e = exponent; e; e &= e - 1) {
        answer = answer * powers[__builtin_ctz(e)] % MOD;
    }
    return int(answer);
}

class DisjointSets {
    std::vector<int> parent;

public:
    explicit DisjointSets(int n) : parent(n, -1) {}

    int root(int v) {
        return parent[v] < 0 ? v : parent[v] = root(parent[v]);
    }

    void join(int a, int b) {
        a = root(a);
        b = root(b);
        if (a == b) return;
        if (parent[a] > parent[b]) std::swap(a,b);
        parent[a] += parent[b];
        parent[b] = a;
    }
};

template<class Weight>
class FixedFlow {
    struct Edge {
        int to, next;
        Weight cap;
    };
    std::vector<Edge> edges;
    std::vector<int> head, level, cursor, queue;
    int source, sink;

    bool bfs() {
        std::fill(level.begin(), level.end(), -1);
        int last = 1;
        queue[0] = source;
        level[source] = 0;
        for (int i = 0; i < last; ++i) {
            int u = queue[i];
            if (u == sink) break;
            for (int e = head[u]; e != -1; e = edges[e].next) {
                int v = edges[e].to;
                if (level[v] == -1 && edges[e].cap) {
                    level[v] = level[u] + 1;
                    queue[last++] = v;
                }
            }
        }
        return level[sink] >= 0;
    }

    Weight send(int u, const Weight& cap) {
        if (u == sink) return cap;
        for (int& e = cursor[u]; e != -1; e = edges[e].next) {
            auto& arc = edges[e];
            if (arc.cap && level[arc.to] == level[u] + 1) {
                auto pushed = send(arc.to, std::min(cap, arc.cap));
                if (pushed) {
                    arc.cap -= pushed;
                    edges[e ^ 1].cap += pushed;
                    return pushed;
                }
            }
        }
        level[u] = -1;
        return {};
    }

    void shortestAugment() {
        const int n = int(head.size());
        std::fill(level.begin(),level.end(),n);
        int count = 1;
        queue[0] = sink;
        level[sink] = 0;
        for (int i = 0; i < count; ++i) {
            int u = queue[i];
            for (int e = head[u]; e >= 0; e = edges[e].next) {
                int v = edges[e].to;
                if (level[v] == n && edges[e^1].cap) {
                    level[v] = level[u] + 1;
                    queue[count++] = v;
                }
            }
        }
        std::vector<int> labelCount(n+1), previous(n), bottleneck(n,-1);
        for (int h : level) ++labelCount[h];
        cursor = head;
        int u = source;
        while (level[source] < n) {
            if (u == sink) {
                Weight pushed = edges[bottleneck[sink]].cap;
                int back = source;
                while (u != source) {
                    int e = previous[u];
                    edges[e].cap -= pushed;
                    edges[e^1].cap += pushed;
                    u = edges[e^1].to;
                    if (!edges[e].cap) back = u;
                }
                u = back;
                continue;
            }
            int& at = cursor[u];
            while (at >= 0 && (!edges[at].cap || level[u] != level[edges[at].to] + 1)) {
                at = edges[at].next;
            }
            if (at >= 0) {
                int v = edges[at].to;
                previous[v] = at;
                int best = bottleneck[u];
                bottleneck[v] = best == -1 || edges[at].cap < edges[best].cap ? at : best;
                u = v;
                continue;
            }
            if (!--labelCount[level[u]]) return;
            int best = n;
            for (int e = head[u]; e >= 0; e = edges[e].next) {
                if (edges[e].cap && level[edges[e].to] + 1 < best) {
                    best = level[edges[e].to] + 1;
                    at = e;
                }
            }
            level[u] = best;
            ++labelCount[best];
            if (u != source) u = edges[previous[u]^1].to;
        }
    }

public:
    explicit FixedFlow(int vertices, int reserve)
        : head(vertices + 2, -1), level(vertices + 2), cursor(vertices + 2),
          queue(vertices + 2), source(vertices), sink(vertices + 1) {
        edges.reserve(2 * reserve);
    }

    int add(int u, int v, Weight capacity) {
        if (!capacity || u == v) return -1;
        int a = int(edges.size());
        edges.push_back({v, head[u], std::move(capacity)});
        edges.push_back({u, head[v], {}});
        head[u] = a;
        head[v] = a + 1;
        return a;
    }

    bool reversePositive(int edge) const {
        return edge >= 0 && bool(edges[edge ^ 1].cap);
    }

    std::vector<std::pair<int,int>> implications() const {
        std::vector<std::pair<int,int>> result;
        result.reserve(edges.size());
        for (int e = 0; e < int(edges.size()); ++e) if (edges[e].cap) {
            result.emplace_back(edges[e ^ 1].to, edges[e].to);
        }
        return result;
    }

    void field(int v, const Weight& positive, const Weight& negative) {
        if (positive < negative) add(source, v, negative - positive);
        else if (negative < positive) add(v, sink, positive - negative);
    }

    std::vector<int> cut(const Weight& infinity, bool direct = false) {
        if (direct && source >= 96 && sizeof(Weight) <= 32) {
            shortestAugment();
            [[maybe_unused]] bool augmentable = bfs();
            assert(!augmentable);
        } else {
            while (bfs()) {
                cursor = head;
                while (send(source, infinity)) {}
            }
        }
        std::vector<int> result(source);
        for (int v = 0; v < source; ++v) result[v] = level[v] >= 0;
        return result;
    }
};

class WideUnsigned {
    using Word = std::uint64_t;
    std::vector<Word> words;
    int offset = 0;

    int end() const { return offset + int(words.size()); }

    Word at(int i) const {
        return i < offset || i >= end() ? 0 : words[i - offset];
    }

    void prepend(int position) {
        if (position < offset) {
            words.insert(words.begin(), offset - position, 0);
            offset = position;
        }
    }

public:
    static WideUnsigned power(int exponent) {
        WideUnsigned result;
        result.offset = exponent / 64;
        result.words.push_back(Word{1} << (exponent % 64));
        return result;
    }

    explicit operator bool() const { return !words.empty(); }

    bool operator<(const WideUnsigned& other) const {
        if (end() != other.end()) return end() < other.end();
        int start = std::min(offset, other.offset);
        for (int i = end() - 1; i >= start; --i) {
            Word a = at(i), b = other.at(i);
            if (a != b) return a < b;
        }
        return false;
    }

    WideUnsigned& operator+=(const WideUnsigned& other) {
        if (!other) return *this;
        if (!*this) {
            *this = other;
            return *this;
        }
        int right = std::max(end(),other.end());
        prepend(other.offset);
        words.resize(right - offset);
        Word carry = 0;
        int i = other.offset;
        for (; i < other.end(); ++i) {
            Word& a = words[i - offset];
            Word b = other.words[i - other.offset];
            Word t = a + b;
            Word c = (t < a);
            a = t + carry;
            carry = c | (a < t);
        }
        while (carry && i < right) carry = !++words[i++ - offset];
        if (carry) words.push_back(carry);
        return *this;
    }

    WideUnsigned& operator-=(const WideUnsigned& other) {
        if (!other) return *this;
        prepend(other.offset);
        Word borrow = 0;
        int i = other.offset;
        for (; i < other.end(); ++i) {
            Word& a = words[i - offset];
            Word b = other.words[i - other.offset];
            Word t = a - b;
            Word c = (a < b);
            a = t - borrow;
            borrow = c | (a > t);
        }
        while (borrow) {
            assert(i < end());
            Word& a = words[i++ - offset];
            borrow = !a--;
        }
        while (!words.empty() && !words.back()) words.pop_back();
        if (words.empty()) offset = 0;
        return *this;
    }

    friend WideUnsigned operator-(WideUnsigned a, const WideUnsigned& b) { return a -= b; }
};

struct FixedUInt256 {
    __uint128_t low = 0, high = 0;

    static FixedUInt256 power(int exponent) {
        if (exponent < 128) return {__uint128_t{1} << exponent,0};
        return {0,__uint128_t{1} << (exponent-128)};
    }
    explicit operator bool() const { return (low | high) != 0; }
    bool operator<(const FixedUInt256& other) const {
        return high == other.high ? low < other.low : high < other.high;
    }
    FixedUInt256& operator+=(const FixedUInt256& other) {
        __uint128_t sum = low + other.low;
        high += other.high + (sum < low);
        low = sum;
        return *this;
    }
    FixedUInt256& operator-=(const FixedUInt256& other) {
        high -= other.high + (low < other.low);
        low -= other.low;
        return *this;
    }
    friend FixedUInt256 operator-(FixedUInt256 a, const FixedUInt256& b) { return a -= b; }
};

struct SteepEdge {
    int from, to;
    int high, low;
};

#ifdef FIXED_STATS
static int cutCount = 0, largestBits = 0, largestComponent = 0;
static std::int64_t flowWork = 0;
#endif

template<class Weight, class Power>
static std::vector<int> fixedCut(int size, const std::vector<SteepEdge>& fees,
                                 const std::vector<std::pair<int,int>>& closures,
                                 int infinityBit, Power power) {
    std::vector<Weight> positive(size), negative(size);
    FixedFlow<Weight> flow(size, size + int(fees.size() + closures.size()));
    Weight infinity = power(infinityBit);
    for (auto [from,to,high,low] : fees) {
        Weight a = power(std::max(high,low));
        positive[to] += a;
        negative[from] += a;
        if (high >= 0 && low >= 0) flow.add(from, to, a - power(low));
    }
    for (int v = 0; v < size; ++v) flow.field(v, positive[v], negative[v]);
    for (auto [from,to] : closures) flow.add(from, to, infinity);
    return flow.cut(infinity,true);
}

struct BandPart {
    int term;
    int high, low;
};

struct LexBand {
    int digits;
    std::vector<BandPart> objective;
};

template<class Weight, class Power>
static std::vector<int> refineBand(int size, const std::vector<SteepEdge>& fees,
                                    const LexBand& band, int infinityBit,
                                    std::vector<std::pair<int,int>>& closures,
                                    std::vector<int>& crossing, Power power) {
    std::vector<Weight> positive(size), negative(size);
    FixedFlow<Weight> flow(size, size + int(band.objective.size() + closures.size()));
    const Weight infinity = power(infinityBit);
    std::vector<std::pair<int,int>> deferred;
    for (const auto& [term,hi,lo] : band.objective) {
        int from = fees[term].from, to = fees[term].to;
        if (from == to) continue;
        if (hi >= 0 && lo >= 0) {
            const Weight a = power(hi);
            if (to < size) positive[to] += a;
            if (from < size) negative[from] += a;
            flow.add(from, to, a - power(lo));
        } else if (hi >= 0 && fees[term].low >= 0) {
            deferred.emplace_back(term, flow.add(to, from, power(hi)));
        } else if (hi >= 0 || fees[term].high < 0 || crossing[term] == 1) {
            const auto w = power(std::max(hi,lo));
            if (to < size) positive[to] += w;
            if (from < size) negative[from] += w;
        }
    }
    for (int i = 0; i < size; ++i) flow.field(i, positive[i], negative[i]);
    for (auto [u,v] : closures) flow.add(u, v, infinity);
    auto cut = flow.cut(infinity);
    for (auto [term,e] : deferred) {
        crossing[term] = flow.reversePositive(e) ? 2 : 1;
    }
    closures = flow.implications();
    std::sort(closures.begin(), closures.end());
    closures.erase(std::unique(closures.begin(), closures.end()), closures.end());
    return cut;
}

static std::vector<LexBand> makeBands(const std::vector<std::pair<i64,int>>& exponents,
                                      int gap, int terms) {
    constexpr int PREFERRED = 106;
    // Splitting only at a dominating gap preserves the sign of all comparisons
    // between cuts, including carries inside each band.
    std::vector<int> end{0};
    for (int i = 1; i < int(exponents.size()); ++i) {
        if (exponents[i].first - exponents[i-1].first >= gap) end.push_back(i);
    }
    end.push_back(int(exponents.size()));
    if (end.size() <= 2) return {};
    std::vector<LexBand> bands;
    int right = int(end.size()) - 1;
    std::vector<int> high(terms, -1), low(terms, -1);
    while (right > 0) {
        int left = right - 1;
        i64 span = exponents[end[right]-1].first - exponents[end[left]].first;
        while (left > 0) {
            i64 extend = gap + exponents[end[left]-1].first - exponents[end[left-1]].first;
            if (span + extend > PREFERRED) break;
            --left;
            span += extend;
        }
        LexBand band;
        int digit = 0;
        i64 last = exponents[end[left]].first;
        std::vector<int> present;
        for (int i = end[left]; i < end[right]; ++i) {
            auto [e,k] = exponents[i];
            digit += int(std::min<i64>(gap, e - last));
            last = e;
            if (high[k/2] < 0 && low[k/2] < 0) present.push_back(k/2);
            (k&1 ? low : high)[k/2] = digit;
        }
        band.digits = digit;
        for (int i : present) {
            band.objective.push_back({i,high[i],low[i]});
            high[i] = low[i] = -1;
        }
        bands.push_back(std::move(band));
        right = left;
    }
    return bands;
}

// All minimizers of one cut are forward-closed sets of residual vertices.
// Contracting equal vertices keeps the implication graph small in later bands.
static int contractClosures(int size, std::vector<int>& mapping,
                              std::vector<std::pair<int,int>>& arcs) {
    std::vector<int> head(size+2,-1), next(arcs.size()), pos(size+2), low(size+2);
    for (int i = 0; i < int(arcs.size()); ++i) {
        int u = arcs[i].first;
        next[i] = head[u];
        head[u] = i;
    }
    std::vector<int> frontier;
    frontier.reserve(size+2);
    mapping.assign(size+2,-1);
    int timestamp=0, groups=0;
    auto dfs = [&](auto&& self,int u) -> void {
        low[u] = pos[u] = ++timestamp;
        frontier.push_back(u);
        for (int e = head[u]; e != -1; e = next[e]) {
            int v = arcs[e].second;
            if (!pos[v]) { self(self,v); low[u] = std::min(low[u],low[v]); }
            else if (mapping[v] == -1) low[u] = std::min(low[u],pos[v]);
        }
        if (low[u] != pos[u]) return;
        int v;
        do {
            v = frontier.back();
            frontier.pop_back();
            mapping[v] = groups;
        } while (u != v);
        ++groups;
    };
    for (int u = 0; u < size+2; ++u) if (!pos[u]) dfs(dfs,u);
    int a = mapping[size], b = mapping[size+1];
    assert(a != b);
    std::vector<int> renumber(groups);
    int free = 0;
    for (int g = 0; g < groups; ++g) if (g != a && g != b) renumber[g] = free++;
    renumber[a] = free;
    renumber[b] = free+1;
    for (auto& at : mapping) at = renumber[at];
    for (auto& [u,v] : arcs) { u=mapping[u]; v=mapping[v]; }
    arcs.erase(std::remove_if(arcs.begin(),arcs.end(),[&](auto edge) {
        return edge.first == edge.second || edge.first == free+1 || edge.second == free;
    }),arcs.end());
    return free;
}

static std::vector<int> lexCut(int initialSize, std::vector<SteepEdge> fees,
                                const std::vector<LexBand>& bands,
                                std::vector<std::pair<int,int>> closures) {
    int size = initialSize;
    std::vector<int> crossing(fees.size()), position(initialSize), mapping;
    std::iota(position.begin(),position.end(),0);
    std::vector<int> selected;
    for (const auto& band : bands) {
        size = contractClosures(size,mapping,closures);
        for (int& v : position) v = mapping[v];
        for (auto& fee : fees) {
            fee.from = mapping[fee.from];
            fee.to = mapping[fee.to];
        }

        bool onlyForward = std::none_of(closures.begin(),closures.end(),[&](auto arc) {
            return arc.first == size || arc.second == size+1;
        });
        if (onlyForward) {
            for (auto [term,hi,lo] : band.objective) {
                if (fees[term].from == fees[term].to) continue;
                if ((hi >= 0 && (fees[term].from >= size || fees[term].to >= size || fees[term].low < 0))
                        || (lo >= 0 && (hi >= 0 || fees[term].high < 0 || crossing[term] == 1))) {
                    onlyForward = false;
                    break;
                }
            }
        }
        if (onlyForward && &band != &bands.back()) {
            for (auto [term,hi,lo] : band.objective) if (hi >= 0) {
                auto [u,v,a,b] = fees[term];
                crossing[term] = 1;
                if (u != v) closures.emplace_back(v,u);
            }
            continue;
        }
        int infinityBit = band.digits + 1 + (32 - __builtin_clz(3 * unsigned(band.objective.size())));
        if (infinityBit < 64) {
            selected = refineBand<std::uint64_t>(size,fees,band,infinityBit,closures,crossing,
                                                  [](int e) { return std::uint64_t{1} << e; });
        } else if (infinityBit < 128) {
            selected = refineBand<__uint128_t>(size,fees,band,infinityBit,closures,crossing,
                                                [](int e) { return __uint128_t{1} << e; });
        } else if (infinityBit < 256) {
            selected = refineBand<FixedUInt256>(size,fees,band,infinityBit,closures,crossing,FixedUInt256::power);
        } else {
            selected = refineBand<WideUnsigned>(size,fees,band,infinityBit,closures,crossing,WideUnsigned::power);
        }
    }
    selected.push_back(1);
    selected.push_back(0);
    std::vector<int> move;
    for (int v : position) move.push_back(selected[v]);
    return move;
}

static std::vector<int> fixedStep(const std::vector<int>& x, int g,
                                  const std::vector<Bound>& bounds,
                                  const std::vector<Term>& terms) {
    std::vector<std::pair<int,int>> closures;
    closures.reserve(bounds.size());
    for (auto [u,v,limit] : bounds) {
        if (i64{x[v]} - x[u] + g > limit) closures.emplace_back(v,u);
    }
    // Collapse vertices that are forced to make the same move as the anchor.
    int size = int(x.size());
    std::vector<int> forward(size,-1), backward(size,-1);
    std::vector<int> nextForward, nextBackward;
    nextForward.reserve(closures.size() + terms.size());
    nextBackward.reserve(closures.size() + terms.size());
    auto connect = [&](int u, int v) {
        int index = int(nextForward.size());
        nextForward.push_back(forward[u]);
        nextBackward.push_back(backward[v]);
        forward[u] = backward[v] = index;
    };
    for (auto [u,v] : closures) connect(u,v);
    auto visit = [&](const std::vector<int>& head, const std::vector<int>& next, bool reverse) {
        std::vector<unsigned char> seen(size);
        std::vector<int> todo{0};
        seen[0] = 1;
        for (std::size_t i = 0; i < todo.size(); ++i) {
            for (int id = head[todo[i]]; id != -1; id = next[id]) {
                int v = reverse ? closures[id].first : closures[id].second;
                if (!seen[v]) {
                    seen[v] = 1;
                    todo.push_back(v);
                }
            }
        }
        return seen;
    };
    auto toZero = visit(backward,nextBackward,true), fromZero = visit(forward,nextForward,false);
    auto implies = [&](int u, int v) {
        if (u == v) return true;
        if (u == 0) return bool(fromZero[v]);
        if (v == 0) return bool(toZero[u]);
        if (toZero[u] && fromZero[v]) return true;
        for (int id = forward[u]; id != -1; id = nextForward[id]) {
            if (closures[id].second == v) return true;
        }
        return false;
    };
    // The empty move has zero gain. Any increasing fee that exceeds the sum
    // of all possible decreases can therefore be forbidden outright.
    i64 greatestDecrease = std::numeric_limits<i64>::min();
    std::vector<i64> decreases;
    decreases.reserve(terms.size());
    for (auto [u,v,c] : terms) if (!implies(u,v)) {
        i64 exponent = i64{c} + x[v] - x[u] - g;
        greatestDecrease = std::max(greatestDecrease,exponent);
        decreases.push_back(exponent);
    }
    if (decreases.empty()) return std::vector<int>(size);
    // Round each magnitude upward to a multiple of 2^unit before summing.
    i64 unit = greatestDecrease - 32;
    std::uint64_t total = 0;
    for (i64 exponent : decreases) {
        total += std::uint64_t{1} << std::max<i64>(0,exponent-unit);
    }
    i64 permitted = unit + 63 - __builtin_clzll(total);
    std::size_t oldClosures = closures.size();
    for (auto [u,v,c] : terms) {
        if (i64{c} + x[v] - x[u] > permitted && !implies(v,u)) {
            closures.emplace_back(v,u);
            connect(v,u);
        }
    }
    if (oldClosures != closures.size()) {
        toZero = visit(backward,nextBackward,true);
        fromZero = visit(forward,nextForward,false);
    }
    std::vector<int> map(size);
    int freeSize = 1;
    for (int j = 1; j < size; ++j) {
        if (!(toZero[j] && fromZero[j])) map[j] = freeSize++;
    }
    if (freeSize == 1) return std::vector<int>(size);

    std::vector<SteepEdge> fees;
    std::vector<std::pair<i64,int>> exponents;
    fees.reserve(terms.size());
    exponents.reserve(2 * terms.size());
    for (auto [u,v,c] : terms) {
        int a = map[u], b = map[v];
        if (a == b) continue;
        bool up = !implies(v,u), down = !implies(u,v);
        if (!up && !down) continue;
        i64 e = i64{c} + x[v] - x[u];
        int k = 2 * int(fees.size());
        fees.push_back({a,b,-1,-1});
        if (up) exponents.emplace_back(e,k);
        if (down) exponents.emplace_back(e-g,k+1);
    }
    if (fees.empty()) return std::vector<int>(size);

    for (auto& [u,v] : closures) { u = map[u]; v = map[v]; }
    std::sort(exponents.begin(), exponents.end());
    int digit = 0;
    i64 last = exponents.front().first;
    const int gap = 32 - __builtin_clz(2 * unsigned(fees.size()));
    for (auto [e,k] : exponents) {
        digit += int(std::min<i64>(gap, e - last));
        last = e;
        if (k & 1) fees[k / 2].low = digit;
        else fees[k / 2].high = digit;
    }
    int infinityBit = digit + 1 + (32 - __builtin_clz(3 * unsigned(fees.size())));
#ifdef FIXED_STATS
    ++cutCount;
    largestBits = std::max(largestBits,infinityBit);
    flowWork += int(fees.size());
#endif
    std::vector<int> moves;
    auto bands = infinityBit >= 256 ? makeBands(exponents,gap,int(fees.size())) : std::vector<LexBand>{};
    if (bands.size() > 1) {
        moves = lexCut(freeSize,fees,bands,closures);
    } else if (infinityBit < 64) {
        moves = fixedCut<std::uint64_t>(freeSize, fees, closures, infinityBit,
                                        [](int e) { return std::uint64_t{1} << e; });
    } else if (infinityBit < 128) {
        moves = fixedCut<__uint128_t>(freeSize, fees, closures, infinityBit,
                                      [](int e) { return __uint128_t{1} << e; });
    } else if (infinityBit < 256) {
        moves = fixedCut<FixedUInt256>(freeSize, fees, closures, infinityBit, FixedUInt256::power);
    } else {
        moves = fixedCut<WideUnsigned>(freeSize, fees, closures, infinityBit, WideUnsigned::power);
    }
    std::vector<int> result(size);
    for (int j = 0; j < size; ++j) result[j] = moves[map[j]];
    return result;
}

static std::vector<i64> normalizePowers(std::vector<i64> exponents) {
    std::sort(exponents.begin(), exponents.end());
    std::vector<i64> digits;
    digits.reserve(exponents.size());
    i64 place = 0;
    std::size_t i = 0, carry = 0;
    while (i < exponents.size() || carry) {
        if (!carry) place = exponents[i];
        while (i < exponents.size() && exponents[i] == place) {
            ++carry;
            ++i;
        }
        if (carry & 1) digits.push_back(place);
        carry >>= 1;
        ++place;
    }
    return digits;
}

// A chain with priced differences reduces to distributing a fixed score span
// over independently priced increments. Its solution is also a certificate
// when the otherwise relaxed interior bounds happen to be satisfied.
static bool solvePricedChain(std::vector<int>& x, const std::vector<Bound>& bounds,
                             const std::vector<Term>& fees) {
    int n = int(x.size()) - 1;
    if (n <= 0) return true;
    std::vector<int> successor(n+1), predecessor(n+1), minimum(n+1);
    std::vector<int> low(n+1), high(n+1);
    int links = 0;
    for (auto [u,v,c] : bounds) {
        if (!u) high[v] = c;
        else if (!v) low[u] = -c;
        else {
            if (successor[v] || predecessor[u] || c > 0) return false;
            successor[v] = u;
            predecessor[u] = v;
            minimum[u] = -c;
            ++links;
        }
    }
    if (links != n - 1) return false;
    int bottom = 1;
    while (predecessor[bottom]) bottom = predecessor[bottom];
    std::vector<int> ascending, position(n+1);
    for (int j = bottom; j; j = successor[j]) {
        position[j] = int(ascending.size());
        ascending.push_back(j);
    }
    if (int(ascending.size()) != n) return false;

    std::vector<std::vector<i64>> weight(n+1);
    int base = low[bottom], ceiling = high[ascending.back()];
    for (auto [u,v,c] : fees) {
        if (!u) {
            if (v != bottom) return false;
            weight[0].push_back(i64{c} + base);
        } else if (!v) {
            if (u != ascending.back()) return false;
            weight[n].push_back(i64{c} - ceiling);
        } else {
            if (successor[u] != v) return false;
            weight[position[v]].push_back(i64{c} + minimum[v]);
        }
    }

    i64 budget = ceiling - base;
    for (int j : ascending) budget -= minimum[j];
    if (budget < 0) return false;
    std::vector<i64> increment(n+1), leading(n+1);
    int free = -1;
    i64 smallest = std::numeric_limits<i64>::max();
    for (int k = 0; k <= n; ++k) {
        if (weight[k].empty()) free = k;
        else {
            weight[k] = normalizePowers(std::move(weight[k]));
            leading[k] = weight[k].back();
            smallest = std::min(smallest, leading[k]);
        }
    }
    if (free >= 0) {
        increment[free] = budget;
    } else {
        i64 lo = smallest, hi = smallest + budget;
        while (lo < hi) {
            i64 mid = (lo + hi + 1) / 2;
            i64 need = 0;
            for (i64 h : leading) need += std::max<i64>(0,mid-h);
            if (need <= budget) lo = mid;
            else hi = mid - 1;
        }
        std::vector<int> tied;
        for (int k = 0; k <= n; ++k) {
            increment[k] = std::max<i64>(0,lo-leading[k]);
            budget -= increment[k];
            if (leading[k] <= lo) tied.push_back(k);
        }
        if (budget) {
            auto cmp = [&](int a,int b) {
                const auto& p = weight[a];
                const auto& q = weight[b];
                int i = int(p.size()) - 1, j = int(q.size()) - 1;
                while (i >= 0 && j >= 0) {
                    i64 v = p[i] - leading[a], w = q[j] - leading[b];
                    if (v != w) return v < w;
                    --i;
                    --j;
                }
                return i < j || (i == j && a < b);
            };
            assert(budget < int(tied.size()));
            std::nth_element(tied.begin(), tied.begin() + budget, tied.end(), cmp);
            for (int i = 0; i < budget; ++i) ++increment[tied[i]];
        }
    }

    std::vector<int> candidate(n+1);
    i64 next = base;
    for (int i = 0; i < n; ++i) {
        int j = ascending[i];
        next += minimum[j] + increment[i];
        if (next < low[j] || next > high[j]) return false;
        candidate[j] = int(next);
    }
    x.swap(candidate);
    return true;
}

struct FixedComponent {
    std::vector<int> members;
    std::vector<Bound> bounds;
    std::vector<Term> costs;
};

// Normalize strict ranking differences before propagating fee ceilings.
// All propagation arcs then have nonpositive weight.
class FixedExponentCeiling {
    struct Arc {
        int tail, head, next, reversed, loss;
    };
    struct LimitedFee {
        int from, to, constant, minimum, maximum;
    };

    std::vector<int> first, last;
    std::vector<Arc> arcs;
    std::vector<std::pair<int,int>> ordering;
    std::vector<LimitedFee> terms;

    void add(int u, int v, int loss) {
        assert(loss >= 0);
        arcs.push_back({u,v,first[u],last[v],loss});
        first[u] = last[v] = int(arcs.size()) - 1;
    }

    bool propagate(std::vector<int>& values, const std::vector<int>& upper,
                   bool reverse) const {
        std::vector<std::pair<int,int>> entries;
        entries.reserve(values.size());
        for (int j = 1; j < int(values.size()); ++j) {
            if (values[j] > upper[j]) return false;
            entries.emplace_back(values[j],j);
        }
        std::priority_queue<std::pair<int,int>> queue(
            std::less<std::pair<int,int>>{}, std::move(entries));
        while (!queue.empty()) {
            auto [label,u] = queue.top();
            queue.pop();
            if (label != values[u]) continue;
            for (int j = (reverse ? last : first)[u]; j >= 0;) {
                const auto& arc = arcs[j];
                int v = reverse ? arc.tail : arc.head;
                j = reverse ? arc.reversed : arc.next;
                int value = label - arc.loss;
                if (value <= values[v]) continue;
                if (value > upper[v]) return false;
                values[v] = value;
                queue.emplace(value,v);
            }
        }
        return true;
    }

public:
    FixedExponentCeiling(const FixedComponent& component, const std::vector<int>& offset,
                         const std::vector<int>& low, const std::vector<int>& high)
        : first(low.size(), -1), last(low.size(), -1) {
        for (auto [u,v,c] : component.bounds) if (u && v) {
            assert(c + offset[u] - offset[v] == 0);
            ordering.emplace_back(v,u);
        }
        for (auto [u,v,c] : component.costs) {
            c += offset[v] - offset[u];
            int small = low[v] - high[u];
            if (u && v) small = std::max(0,small);
            terms.push_back({u,v,c,c+small,c+high[v]-low[u]});
        }
        arcs.reserve(ordering.size() + terms.size());
    }

    bool solve(int limit, const std::vector<int>& low, const std::vector<int>& high,
               std::vector<int>& lower, std::vector<int>* upper) {
        std::fill(first.begin(),first.end(),-1);
        std::fill(last.begin(),last.end(),-1);
        arcs.clear();
        for (auto [u,v] : ordering) add(u,v,0);
        lower = low;
        std::vector<int> ceilings = high;
        for (auto [u,v,c,small,large] : terms) {
            int cap = std::max(small,limit);
            if (cap >= large) continue;
            int difference = cap - c;
            if (!u) ceilings[v] = std::min(ceilings[v],difference);
            else if (!v) lower[u] = std::max(lower[u],-difference);
            else add(v,u,difference);
        }
        if (!propagate(lower,ceilings,false)) return false;
        if (upper) {
            for (int& value : ceilings) value = -value;
            std::vector<int> opposite(lower.size());
            for (int j = 1; j < int(opposite.size()); ++j) opposite[j] = -lower[j];
            [[maybe_unused]] bool feasible = propagate(ceilings,opposite,true);
            assert(feasible);
            for (int& value : ceilings) value = -value;
            *upper = std::move(ceilings);
        }
        return true;
    }

    bool tighten(std::vector<int>& low, std::vector<int>& high, std::vector<int>& seed) {
        int a = 0, b = 0;
        for (const auto& term : terms) b = std::max(b,term.maximum);
        std::vector<int> lower, upper;
        bool allMinimum = solve(0,low,high,lower,&upper);
        if (!allMinimum) {
            while (a < b) {
                int mid = a + (b-a)/2;
                if (solve(mid,low,high,lower,nullptr)) b = mid;
                else a = mid+1;
            }
            solve(a,low,high,lower,&upper);
        }
        seed.resize(low.size());
        for (int j = 1; j < int(low.size()); ++j) {
            seed[j] = lower[j] + (upper[j]-lower[j])/2;
        }
        if (allMinimum) return true;
        int margin = 32 - __builtin_clz(unsigned(terms.size()));
        solve(a+margin,low,high,lower,&upper);
        low.swap(lower);
        high.swap(upper);
        return false;
    }
};

static int balanceShift(int lower, int upper, int g,
                        std::vector<i64> rise, std::vector<i64> fall) {
    if (rise.empty()) return fall.empty() ? 0 : upper;
    if (fall.empty()) return lower;
    rise = normalizePowers(std::move(rise));
    fall = normalizePowers(std::move(fall));
    i64 gap = fall.back() - rise.back();
    i64 q = gap - g;
    i64 step = q >= 0 ? (q + 2*i64(g)-1)/(2*i64(g)) : q/(2*i64(g));
    if ((2*step+1)*g == gap) {
        auto i = rise.rbegin(), j = fall.rbegin();
        while (i != rise.rend() && j != fall.rend() && *i + gap == *j) { ++i; ++j; }
        if (j != fall.rend() && (i == rise.rend() || *i+gap < *j)) ++step;
    }
    return int(std::clamp(step,i64(lower),i64(upper)));
}

static void fixedSweep(std::vector<int>& x, const std::vector<int>& base, int g,
                       const std::vector<Bound>& bounds, const std::vector<Term>& fees,
                       int orderingEnd, bool reverse) {
    int n = int(x.size());
    DisjointSets sets(n);
    for (int j = 0; j < orderingEnd; ++j) {
        auto [u,v,c] = bounds[j];
        if (u && v && i64(x[v])-x[u]+g > c) sets.join(u,v);
    }
    struct Bucket { std::vector<int> members, bounds, fees; };
    std::vector<Bucket> groups(n);
    std::vector<int> group(n), order;
    for (int j = 1; j < n; ++j) {
        int b = group[j] = sets.root(j);
        if (groups[b].members.empty()) order.push_back(b);
        groups[b].members.push_back(j);
    }
    std::sort(order.begin(),order.end(),[&](int a,int b) {
        if (x[a] == x[b]) return a < b;
        return reverse ? x[a] > x[b] : x[a] < x[b];
    });
    for (int j = 0; j < int(bounds.size()); ++j) {
        auto [u,v,c] = bounds[j];
        if (group[u] == group[v]) continue;
        if (u) groups[group[u]].bounds.push_back(j);
        if (v) groups[group[v]].bounds.push_back(j);
    }
    for (int j = 0; j < int(fees.size()); ++j) {
        auto [u,v,c] = fees[j];
        if (group[u] == group[v]) continue;
        if (u) groups[group[u]].fees.push_back(j);
        if (v) groups[group[v]].fees.push_back(j);
    }
    std::vector<i64> rise, fall;
    for (int k : order) {
        int lower = -(n-1), upper = n-1;
        for (int j : groups[k].members) {
            int displacement = (x[j]-base[j])/g;
            lower = std::max(lower,-(n-1)-displacement);
            upper = std::min(upper,n-1-displacement);
        }
        for (int j : groups[k].bounds) {
            auto [u,v,c] = bounds[j];
            int slack = int((i64(c)+x[u]-x[v])/g);
            if (group[v] == k) upper = std::min(upper,slack);
            else lower = std::max(lower,-slack);
        }
        if (!lower && !upper) continue;
        rise.clear();
        fall.clear();
        for (int j : groups[k].fees) {
            auto [u,v,c] = fees[j];
            auto& side = group[v] == k ? rise : fall;
            side.push_back(i64(c) + x[v] - x[u]);
        }
        int step = balanceShift(lower,upper,g,rise,fall);
        for (int j : groups[k].members) x[j] += step*g;
    }
}

static void fixedMinimize(std::vector<int>& x, std::vector<Bound>& bounds,
                          const std::vector<Term>& fees, int width,
                          const std::vector<int>& low, const std::vector<int>& high,
                          const std::vector<int>& offset) {
#ifdef FIXED_STATS
    largestComponent = std::max(largestComponent, int(x.size()) - 1);
#endif
    if (fees.empty() || solvePricedChain(x, bounds, fees)) return;
    int orderingEnd = int(bounds.size());
    if (x.size() >= 96) {
        std::vector<i64> surplus;
        std::vector<int> baseline;
        for (auto [u,v,c] : fees) {
            int lo = low[v] - high[u];
            if (u && v) lo = std::max(lo,offset[v]-offset[u]);
            baseline.push_back(c+lo);
            int actual = c+x[v]-x[u];
            if (actual > c+lo) surplus.push_back(actual);
        }
        if (surplus.empty()) return;
        int top = int(normalizePowers(std::move(surplus)).back()) + 1;
        for (int j = 0; j < int(fees.size()); ++j) {
            auto [u,v,c] = fees[j];
            int limit = std::max(top,baseline[j])-c;
            if (high[v]-low[u] > limit) bounds.push_back({u,v,limit});
        }
    }
    int g = 1;
    while (g <= width / 2) g *= 2;
    for (; g; g >>= 1) {
        if (x.size() >= 96 && width >= 256) {
            auto origin = x;
            fixedSweep(x,origin,g,bounds,fees,orderingEnd,(g & 0x55555555) != 0);
        }
        for (;;) {
            auto move = fixedStep(x, g, bounds, fees);
            if (std::none_of(move.begin(), move.end(), [](int b) { return b != 0; })) break;
            for (int j = 1; j < int(x.size()); ++j) x[j] += g * (move[j] - move[0]);
        }
    }
}

static std::vector<int> minimizeFixed(const std::vector<int>& order,
                                       std::vector<int> low, std::vector<int> high,
                                       const std::vector<Term>& fees,
                                       std::vector<int> seed = {}) {
    const int L = int(order.size());
    std::vector<int> offset(L + 1);
    for (int j = L - 2; j >= 0; --j) {
        int a = order[j], b = order[j+1];
        offset[a] = offset[b] + (a > b);
        low[a] = std::max(low[a],low[b] + (a > b));
    }
    for (int j = 1; j < L; ++j) {
        int a = order[j-1], b = order[j];
        high[b] = std::min(high[b],high[a] - (a > b));
    }

    std::vector<unsigned char> signs(L + 1);
    for (auto [u,v,c] : fees) if (u != v) {
        signs[u] |= 1;
        signs[v] |= 2;
    }
    signs[0] = 0;
    int next = 0, prior = 0;
    for (int p = L - 1; p >= 0; --p) {
        int j = order[p];
        if (signs[j] == 2 && (!next || low[j] - offset[j] >= high[next] - offset[next])) {
            high[j] = low[j];
        }
        if (low[j] != high[j] && signs[j]) next = j;
    }
    for (int j : order) {
        if (signs[j] == 1 && (!prior || high[j] - offset[j] <= low[prior] - offset[prior])) {
            low[j] = high[j];
        }
        if (low[j] != high[j] && signs[j]) prior = j;
    }

    std::vector<int> root(L + 1);
    std::iota(root.begin(),root.end(),0);
    // A coordinate with only increasing fees can slide down to its next
    // priced neighbor if their normalized lower bounds coincide.
    next = 0;
    for (int p = L - 1; p >= 0; --p) {
        int j = order[p];
        if (!signs[j] || low[j] == high[j]) continue;
        if (next && signs[j] == 2 && low[j] - offset[j] == low[next] - offset[next]) {
            root[j] = next;
            signs[next] |= 2;
        } else {
            next = j;
        }
    }
    // Symmetrically slide coordinates with only decreasing fees upward.
    prior = 0;
    for (int j : order) {
        if (root[j] != j || !signs[j] || low[j] == high[j]) continue;
        if (prior && signs[j] == 1 && high[j] - offset[j] == high[prior] - offset[prior]) {
            root[j] = prior;
            signs[prior] |= 1;
        } else {
            prior = j;
        }
    }
    std::vector<int> active(L + 1);
    for (int j = 1; j <= L; ++j) {
        if (root[j] != j) root[j] = root[root[j]];
        active[j] = root[j] == j && low[j] < high[j] && signs[j];
    }
    DisjointSets groups(L + 1);
    std::vector<Bound> adjacent;
    int prev = 0;
    for (int j : order) if (active[j]) {
        if (prev) {
            int d = offset[prev] - offset[j];
            if (low[prev] - high[j] < d) {
                groups.join(prev,j);
                adjacent.push_back({prev,j,-d});
            }
        }
        prev = j;
    }

    std::vector<Term> costs;
    costs.reserve(fees.size());
    for (auto [u,v,c] : fees) if (u != v) {
        c += offset[v] - offset[root[v]] - offset[u] + offset[root[u]];
        u = root[u];
        v = root[v];
        if (u && !active[u]) { c -= low[u]; u = 0; }
        if (v && !active[v]) { c += low[v]; v = 0; }
        if (u != v) {
            if (u && v) groups.join(u,v);
            costs.push_back({u,v,c});
        }
    }
    std::vector<FixedComponent> components;
    std::vector<int> component(L+1,-1), number(L+1);
    for (int j = 1; j <= L; ++j) if (active[j]) {
        int v = groups.root(j);
        if (component[v] == -1) {
            component[v] = int(components.size());
            components.emplace_back();
        }
        int k = component[j] = component[v];
        number[j] = int(components[k].members.size()) + 1;
        components[k].members.push_back(j);
        components[k].bounds.push_back({0, number[j], high[j]});
        components[k].bounds.push_back({number[j], 0, -low[j]});
    }
    for (auto [a,b,c] : adjacent) components[component[a]].bounds.push_back({number[a],number[b],c});
    for (auto [a,b,c] : costs) components[component[a ? a : b]].costs.push_back({number[a],number[b],c});

    if (seed.empty()) {
        bool prepared = false;
        seed = low;
        for (const auto& block : components) {
            const auto& members = block.members;
            if (members.size() < 96) continue;
            std::vector<int> blockSeed(1);
            blockSeed.reserve(members.size() + 1);
            for (int j : members) blockSeed.push_back(low[j]);
            if (solvePricedChain(blockSeed,block.bounds,block.costs)) {
                for (int k = 1; k < int(blockSeed.size()); ++k) {
                    int j = members[k-1];
                    low[j] = high[j] = seed[j] = blockSeed[k];
                }
                prepared = true;
                continue;
            }
            std::vector<int> normalizedLow(1), normalizedHigh(1), normalizedOffset(1);
            for (int j : members) {
                normalizedLow.push_back(low[j] - offset[j]);
                normalizedHigh.push_back(high[j] - offset[j]);
                normalizedOffset.push_back(offset[j]);
            }
            FixedExponentCeiling preprocessing(block,normalizedOffset,normalizedLow,normalizedHigh);
            bool allMinimum = preprocessing.tighten(normalizedLow,normalizedHigh,blockSeed);
            for (int k = 1; k < int(normalizedLow.size()); ++k) {
                int j = members[k-1];
                seed[j] = blockSeed[k] + offset[j];
                low[j] = allMinimum ? seed[j] : normalizedLow[k] + offset[j];
                high[j] = allMinimum ? seed[j] : normalizedHigh[k] + offset[j];
            }
            prepared = true;
        }
        if (prepared) {
            for (int j = 1; j <= L; ++j) if (root[j] != j) {
                seed[j] = seed[root[j]] + offset[j] - offset[root[j]];
            }
            return minimizeFixed(order,std::move(low),std::move(high),fees,std::move(seed));
        }
        seed.clear();
    }

    auto solution = low;
    for (auto& [members,bounds,fees] : components) {
        int width = 0;
        std::vector<int> x(1), componentLow(1), componentHigh(1), componentOffset(1);
        x.reserve(members.size() + 1);
        for (int j : members) {
            x.push_back(seed.empty() ? low[j] : std::clamp(seed[j],low[j],high[j]));
            componentLow.push_back(low[j]);
            componentHigh.push_back(high[j]);
            componentOffset.push_back(offset[j]);
            width = std::max(width,high[j]-low[j]);
        }
        fixedMinimize(x,bounds,fees,width,componentLow,componentHigh,componentOffset);
        for (std::size_t i = 0; i < members.size(); ++i) solution[members[i]] = x[i+1];
    }

    for (int j = 1; j <= L; ++j) if (root[j] != j) {
        solution[j] = solution[root[j]] + offset[j] - offset[root[j]];
    }
    // Reconstruct the unpriced ranks eliminated from the order chain.
    for (int i = L - 2; i >= 0; --i) {
        int a = order[i], b = order[i+1];
        if (!signs[a]) solution[a] = std::max(solution[a],solution[b]+(a>b));
    }
    return solution;
}

int fixedLeaderboard(std::vector<Record>& records, const std::vector<Record>& original,
                     const std::vector<Player>& players, int latest,
                     const std::vector<int>& owner, const std::vector<int>& rank) {
    int L = int(owner.size()) - 1;
    if (!L) {
        return 0;
    }
    std::vector<int> order(L), inverse(L + 1), oldBefore(L + 1);
    std::vector<int> predecessor(L + 1), low(L + 1), high(L + 1);
    std::iota(order.begin(), order.end(), 1);
    std::sort(order.begin(), order.end(), [&](int i, int j) { return rank[i] < rank[j]; });
    int M = int(records.size()) - 1;
    for (int p = 0; p < L; ++p) {
        int j = order[p], r = rank[j] - p - 1;
        inverse[j] = p + 1;
        oldBefore[p + 1] = predecessor[j] = r;
    }
    std::vector<int> oldGate(L + 1), newGate(L + 1);
    RankCounter active(L);
    for (int j = 1; j <= L; ++j) {
        int R = players[owner[j]].target;
        int before = active.appendedBefore(R,oldBefore);
        if (before < j - 1) {
            int k = active.kth(before + 1);
            if (oldBefore[k] + before + 1 == R) newGate[j] = order[k - 1];
        }
        if (!newGate[j]) {
            oldGate[j] = R - before;
        }
        active.add(inverse[j]);
    }
    std::sort(records.begin() + 1,records.end(),historicalOrder);

    for (int j : order) {
        int r = predecessor[j];
        low[j] = r < M ? records[r + 1].score + 1 : 1;
        high[j] = r ? records[r].score : MAX_SCORE;
    }
    std::vector<int> best(players.size());
    std::vector<Term> fees;
    for (int j = 1; j <= L; ++j) {
        int i = owner[j], a = best[i];
        bool oldA = !a || (predecessor[a] &&
            !historicalOrder(records[predecessor[a]], original[i]));
        int aScore = oldA ? original[i].score : 0;
        int aTime = oldA ? original[i].time : latest + a;

        int b = newGate[j];
        int bScore = b ? 0 : records[oldGate[j]].score;
        int bTime = b ? latest + b : records[oldGate[j]].time;
        fees.push_back({oldA ? 0 : a, b, bScore - aScore + std::abs(bTime - aTime)});
        if (!best[i] || rank[j] < rank[best[i]]) {
            best[i] = j;
        }
    }
    auto solution = minimizeFixed(order,std::move(low),std::move(high),fees);
    int answer = 0;
    for (auto [u,v,c] : fees) {
        answer += powerMod(c + solution[v] - solution[u]);
        if (answer >= MOD) answer -= MOD;
    }
#ifdef FIXED_STATS
    std::fprintf(stderr, "cuts=%d bits=%d component=%d arcs=%lld\n",
                 cutCount, largestBits, largestComponent, (long long)flowWork);
#endif
#ifdef FIXED_WITNESS
    for (int j = 1; j <= L; ++j) std::fprintf(stderr, "%d ", solution[j]);
    std::fputc('\n',stderr);
#endif
    return answer;
}

class GeneralSolver {
    static constexpr int G = GENERAL;
    static constexpr int BAD = 2000000001;
    static constexpr int STATES = 1 << G;
    using Cost = BinarySum<G>;
    using Scores = std::array<int, G + 1>;

    struct Domain {
        Scores lo{}, hi{};
        std::array<int, G + 1> above{};
    };

    struct Quantiles {
        Scores lo{}, hi{};
    };

    struct Split {
        int slot = 0;
        int against = 0;
        int value = 0;
        bool prefer = false;
        int limit = -1, where = -1;
        explicit operator bool() const { return slot != 0 || where >= 0; }
    };

    struct CountBound {
        int length, score, low, high;
    };

    class CountEnvelope {
        const std::vector<CountBound>& bounds;
        std::vector<int> thresholds;
        std::array<std::vector<int>, G + 1> below, above;

    public:
        CountEnvelope(const std::vector<CountBound>& source, int length) : bounds(source) {
            if (bounds.size() < 9) {
                return;
            }
            thresholds.reserve(bounds.size());
            for (const auto& c : bounds) {
                thresholds.push_back(c.score);
            }
            std::sort(thresholds.begin(), thresholds.end());
            thresholds.erase(std::unique(thresholds.begin(), thresholds.end()), thresholds.end());
            std::vector<int> at;
            at.reserve(bounds.size());
            for (const auto& c : bounds) {
                at.push_back(int(std::lower_bound(thresholds.begin(), thresholds.end(), c.score) -
                                 thresholds.begin()));
            }
            for (int p = 0; p <= length; ++p) {
                auto& lo = below[p];
                auto& hi = above[p];
                lo.assign(thresholds.size() + 1, 0);
                hi.assign(thresholds.size() + 1, p);
                for (int i = 0; i < int(bounds.size()); ++i) {
                    const auto& c = bounds[i];
                    lo[at[i]] = std::max(lo[at[i]], c.low - std::max(0, c.length - p));
                    hi[at[i] + 1] = std::min(hi[at[i] + 1], c.high + std::max(0, p - c.length));
                }
                for (int i = 1; i <= int(thresholds.size()); ++i) {
                    hi[i] = std::min(hi[i], hi[i - 1]);
                }
                for (int i = int(thresholds.size()) - 1; i >= 0; --i) {
                    lo[i] = std::max(lo[i], lo[i + 1]);
                }
            }
        }

        std::pair<int, int> query(int p, int score, int low, int high) const {
            if (thresholds.empty()) {
                for (const auto& c : bounds) {
                    if (score >= c.score) {
                        high = std::min(high, c.high + std::max(0, p - c.length));
                    }
                    if (score <= c.score) {
                        low = std::max(low, c.low - std::max(0, c.length - p));
                    }
                }
                return {low, high};
            }
            auto first = std::lower_bound(thresholds.begin(), thresholds.end(), score) - thresholds.begin();
            auto last = first + (first < int(thresholds.size()) && thresholds[first] == score);
            return {std::max(low, below[p][first]), std::min(high, above[p][last])};
        }
    };

    struct RankCheck {
        int prefix, rank;
        bool upper;
    };

    struct Price {
        int exponent = BAD;
        int cutoff = 0;
        int need = 0;
    };

    struct Cell {
        bool oldCanFail = false;
        int mustOwnFail = 0;
        std::array<Price, G + 1> fee{};
        std::array<std::array<Price, G + 1>, 3> repeat{};
    };

    struct Extremes {
        std::array<int, G> better{}, worse{};
        int lowSize = 0, highSize = 0, forced = 0;
    };

    // A quote is a lower bound for one possible source and cutoff.
    struct Analysis {
        Extremes rank[G + 1][G + 1]{};
        Cell cell[G][G + 1]{};
        Scores finalNeed[G]{};
        Scores finalSafe[G]{};
        Price first[G][G + 1][G + 1]{};
        int tighter[G]{};
        Price gate[G][2 * G + 1]{};
        std::array<int, G + 1> prepIndex{};
    };

    const std::vector<Record>& old;
    const std::vector<Player>& people;
    int T, H, M;
    int L = 0, actors = 0, full = 0;
    int preparation = -1;
    std::vector<int> initiallyFail;
    std::array<std::vector<int>, G> groups;
    std::array<int, G> ids{}, force{}, firstGate{}, firstTime{};
    std::array<std::vector<int>, G> forbid;
    Quantiles quantiles;
    std::array<int, G> winnerFloor{};
    std::vector<CountBound> countBounds;
    std::vector<int> historicalThresholds;
    std::array<int,G> firstCap{};
    int repeatCap=BAD;
    enum class SearchStyle { Basic, Ranked, Assigned, Anchor };
    SearchStyle style=SearchStyle::Basic;
    int nodesRemaining=0;
    bool interrupted=false;

    bool hasAnswer = false;
    Cost answer;

    static int bit(int j) { return 1 << (j - 1); }
    static int count(int mask) {
        static constexpr auto table=[] {
            std::array<unsigned char,STATES> out{};
            for(int k=1;k<STATES;++k) out[k]=out[k>>1]+(k&1);
            return out;
        }();
        return table[mask];
    }
    static int index(int one) { return __builtin_ctz(unsigned(one)) + 1; }
    static int last(int mask) { return 32 - __builtin_clz(unsigned(mask)); }
    static int prefix(int p) { return (1 << p) - 1; }
    static bool ahead(int m, int x, int n, int y) { return m > n || (m == n && x < y); }

    bool definitely(const Domain& box, int k, int j) const {
        return (box.above[j] & bit(k)) || ahead(box.lo[k], k, box.hi[j], j);
    }

    int gradeBase=1;
    std::vector<int> gradeRanks;
    int oldAhead(int score) const {
        if(unsigned(score-gradeBase)<gradeRanks.size())return gradeRanks[score-gradeBase];
        int lo = 1, hi = M + 1;
        while (lo < hi) {
            int p = (lo + hi) / 2;
            if (old[p].score >= score) {
                lo = p + 1;
            } else {
                hi = p;
            }
        }
        return lo - 1;
    }

    int betterMask(const Scores& score, int p, int threshold) const {
        int result = 0;
        for (int j = 1; j <= p; ++j) {
            if (score[j] > threshold) {
                result |= bit(j);
            }
        }
        return result;
    }

    // C(p,score) is monotone in both arguments, with a unit prefix increment.
    std::pair<int, int> range(int p, int score, int low, int high) const {
        for (const auto& card : countBounds) {
            if (score >= card.score) {
                high = std::min(high, card.high + std::max(0, p - card.length));
            }
            if (score <= card.score) {
                low = std::max(low, card.low - std::max(0, card.length - p));
            }
        }
        return {low, high};
    }

    std::pair<int, int> range(const Domain& box, int p, int score) const {
        return range(p, score, count(betterMask(box.lo, p, score)), count(betterMask(box.hi, p, score)));
    }

    Split splitCount(const Domain& box, int p, int score, int target, bool equality) const {
        auto [low, high] = range(box, p, score);
        if ((equality && low < high && low <= target && target <= high) || (!equality && low < target && target <= high)) {
            return {0, 0, score, equality, target, p};
        }
        return {};
    }

    bool couldImprove(const Cost& cost) const { return !hasAnswer || cost < answer; }

    void take(const Cost& cost) {
        if (couldImprove(cost)) {
            hasAnswer = true;
            answer = cost;
        }
    }

    bool normalize(Domain& box) const {
        CountEnvelope envelope(countBounds, L);
        Scores order{};
        for (int j = 1; j <= L; ++j) {
            order[j] = j;
        }
        std::sort(order.begin() + 1, order.begin() + L + 1, [&](int a, int b) {
            return count(box.above[a]) < count(box.above[b]);
        });
        bool changed = true;
        while (changed) {
            changed = false;
            for (int p = L; p >= 1; --p) {
                int j = order[p];
                for (int m = box.above[j]; m; m &= m - 1) {
                    int k = index(m);
                    box.lo[k] = std::max(box.lo[k], box.lo[j] + (k > j));
                }
            }
            for (int p = 1; p <= L; ++p) {
                int j = order[p];
                for (int m = box.above[j]; m; m &= m - 1) {
                    int k = index(m);
                    box.hi[j] = std::min(box.hi[j], box.hi[k] - (k > j));
                }
                if (box.lo[j] > box.hi[j]) {
                    return false;
                }
            }
            for (int p = 1; p <= L; ++p) {
                int low = quantiles.lo[p] - 1;
                int high = quantiles.hi[p];
                if (low) {
                    int must = betterMask(box.lo, L, low);
                    int may = betterMask(box.hi, L, low);
                    if (count(may) < p) {
                        return false;
                    }
                    if (count(may) == p && may != must) {
                        for (int m = may ^ must; m; m &= m - 1) {
                            box.lo[index(m)] = low + 1;
                        }
                        changed = true;
                    }
                }
                if (high < MAX_SCORE) {
                    int must = betterMask(box.lo, L, high);
                    int may = betterMask(box.hi, L, high);
                    if (count(must) >= p) {
                        return false;
                    }
                    if (count(must) == p - 1 && may != must) {
                        for (int m = may ^ must; m; m &= m - 1) {
                            box.hi[index(m)] = high;
                        }
                        changed = true;
                    }
                }
            }
            for (const auto& c : countBounds) {
                int forced = betterMask(box.lo, c.length, c.score);
                int possible = betterMask(box.hi, c.length, c.score);
                auto [mi, ma] = envelope.query(c.length, c.score, count(forced), count(possible));
                if (mi > ma) {
                    return false;
                }
                if (forced != possible && (mi == count(possible) || ma == count(forced))) {
                    for (int m = forced ^ possible; m; m &= m - 1) {
                        if (mi == count(possible)) {
                            box.lo[index(m)] = c.score + 1;
                        } else {
                            box.hi[index(m)] = c.score;
                        }
                    }
                    changed = true;
                }
            }
            for (int j = 1; j <= L; ++j) {
                if (box.lo[j] > box.hi[j]) {
                    return false;
                }
            }
        }
        return true;
    }

    void setRelation(Domain& box, int a, int b) const {
        int reach = box.above[a] | bit(a);
        box.above[b] |= reach;
        for (int j = 1; j <= L; ++j) {
            if (box.above[j] & bit(b)) {
                box.above[j] |= reach;
            }
        }
    }

    static void insert(std::array<int, G>& arr, int& size, int value) {
        int p = size++;
        while (p && arr[p - 1] < value) {
            arr[p] = arr[p - 1];
            --p;
        }
        arr[p] = value;
    }

    using MaskSet = std::bitset<STATES>;

    struct ProfileMasks {
        std::array<MaskSet, G + 1> includes{};
        MaskSet atMost[G + 1][G + 1]{};

        ProfileMasks() {
            for (int s = 0; s < STATES; ++s) {
                int c = 0;
                for (int p = 0; p <= G; ++p) {
                    if (p && (s & bit(p))) {
                        includes[p].set(s);
                        ++c;
                    }
                    atMost[p][c].set(s);
                }
            }
            for (int p = 0; p <= G; ++p) {
                for (int k = 1; k <= G; ++k) {
                    atMost[p][k] |= atMost[p][k - 1];
                }
            }
        }
    };

    static const ProfileMasks& profileMasks() {
        static const ProfileMasks masks;
        return masks;
    }

    struct Profile {
        std::vector<int> score;
        std::vector<MaskSet> masks;
        std::vector<int> oldBetter;

        int at(int threshold) const {
            auto it = std::lower_bound(score.begin(), score.end(), threshold, std::greater<int>{});
            return it != score.end() && *it == threshold ? int(it - score.begin()) : -1;
        }

        bool supports(int i, int p, int k) const {
            if (i < 0) {
                return true;
            }
            const auto& cap = profileMasks().atMost[p];
            if (!k) {
                return (masks[i] & cap[0]).any();
            }
            return (masks[i] & cap[k] & ~cap[k - 1]).any();
        }
    };

    // Sweep score boundaries. Each bit denotes the whole set of slots above that score.
    // Order inside a score interval is relaxed, so all genuine plans survive this pass.
    bool gradeProfile(Domain& box, Profile& profile) const {
        auto& cuts = profile.score;
        cuts = {MAX_SCORE, 0};
        for (int j = 1; j <= L; ++j) {
            cuts.push_back(box.lo[j] - 1);
            cuts.push_back(box.hi[j]);
        }
        std::vector<CountBound> constraints = countBounds;
        for (int j = 1; j <= L; ++j) {
            constraints.push_back({L, quantiles.lo[j] - 1, j, L});
            constraints.push_back({L, quantiles.hi[j], 0, j - 1});
        }
        for (const auto& c : constraints) {
            if (c.score < 0 || c.score > MAX_SCORE) {
                return false;
            }
            cuts.push_back(c.score);
        }
        cuts.insert(cuts.end(), historicalThresholds.begin(), historicalThresholds.end());
        std::sort(cuts.begin(), cuts.end(), std::greater<int>{});
        cuts.erase(std::unique(cuts.begin(), cuts.end()), cuts.end());
        const auto& table = profileMasks();
        MaskSet relation;
        relation.set();
        for (int j = 1; j <= L; ++j) {
            for (int m = box.above[j]; m; m &= m - 1) {
                relation &= ~table.includes[j] | table.includes[index(m)];
            }
        }
        auto bounds = std::vector<MaskSet>(cuts.size(), relation);
        for (const auto& c : constraints) {
            int i = profile.at(c.score);
            auto& allowed = bounds[i];
            if (c.low > c.high || c.low > c.length || c.high < 0) {
                return false;
            }
            allowed &= table.atMost[c.length][std::min(c.high, c.length)];
            if (c.low > 0) {
                allowed &= ~table.atMost[c.length][c.low - 1];
            }
        }
        for (int i = 0; i < int(cuts.size()); ++i) {
            for (int j = 1; j <= L; ++j) {
                if (box.lo[j] > cuts[i]) {
                    bounds[i] &= table.includes[j];
                }
                if (box.hi[j] <= cuts[i]) {
                    bounds[i] &= ~table.includes[j];
                }
            }
        }
        auto& both = profile.masks;
        both.resize(cuts.size());
        profile.oldBetter.reserve(cuts.size());
        for (int score : cuts) {
            profile.oldBetter.push_back(oldAhead(score + 1));
        }
        MaskSet live;
        live.set(0);
        both[0] = live &= bounds[0];
        for (int i = 1; i < int(cuts.size()); ++i) {
            for (int j = 1; j <= L; ++j) {
                if (box.lo[j] <= cuts[i - 1] && box.hi[j] > cuts[i]) {
                    live |= (live & ~table.includes[j]) << bit(j);
                }
            }
            both[i] = live &= bounds[i];
            if (live.none()) {
                return false;
            }
        }
        live.reset();
        live.set(full);
        both.back() &= live;
        if (both.back().none()) {
            return false;
        }
        auto lo = box.lo, hi = box.hi;
        for (int i = int(cuts.size()) - 2; i >= 0; --i) {
            for (int j = 1; j <= L; ++j) {
                if (box.lo[j] <= cuts[i] && box.hi[j] > cuts[i + 1]) {
                    live |= (live & table.includes[j]) >> bit(j);
                }
            }
            live &= bounds[i];
            both[i] &= live;
            if (both[i].none()) {
                return false;
            }
            for (int j = 1; j <= L; ++j) {
                if ((both[i] & table.includes[j]).none()) {
                    hi[j] = std::min(hi[j], cuts[i]);
                }
                if ((both[i] & ~table.includes[j]).none()) {
                    lo[j] = std::max(lo[j], cuts[i] + 1);
                }
            }
        }
        box.lo = lo;
        box.hi = hi;
        return true;
    }

    void prepareRanks(const Domain& box, Analysis& data) const {
        for (int k = 1; k <= L; ++k) {
            Extremes current;
            for (int p = 1; p <= L; ++p) {
                if (k != p) {
                    if (box.above[k] & bit(p)) {
                        ++current.forced;
                    } else {
                        insert(current.better, current.lowSize, box.lo[p] + (k > p));
                    }
                    if (!(box.above[p] & bit(k))) {
                        insert(current.worse, current.highSize, box.hi[p] + (k > p));
                    }
                }
                if (p >= k) {
                    data.rank[k][p] = current;
                }
            }
        }
    }

    int mergeThreshold(int k, const std::array<int, G>& other, int size) const {
        if (k <= 0) {
            return MAX_SCORE + 1;
        }
        int lo = 0, hi = std::min(k, size);
        while (lo < hi) {
            int take = (lo + hi + 1) / 2;
            if (other[take - 1] >= old[k - take + 1].score + 1) {
                lo = take;
            } else {
                hi = take - 1;
            }
        }
        int a = lo ? other[lo - 1] : MAX_SCORE + 1;
        int b = lo < k ? old[k - lo].score + 1 : MAX_SCORE + 1;
        return std::min(a, b);
    }

    int needed(const Analysis& data, int k, int p, int R, bool optimistic) const {
        const Extremes& r = data.rank[k][p];
        if (optimistic) {
            return mergeThreshold(R - r.forced, r.better, r.lowSize);
        }
        return mergeThreshold(R, r.worse, r.highSize);
    }

    static void price(Price& prior, int exponent, int cutoff, int need = 0) {
        if (exponent < prior.exponent) {
            prior = {exponent, cutoff, need};
        }
    }

    void makePrices(const Domain& box, Analysis& data, const Profile* profile = nullptr) const {
        CountEnvelope envelope(countBounds, L);
        bool repeating = actors < L;
        bool withPrep = preparation >= 0;
        int winners = withPrep ? full ^ preparation : full;
        if (withPrep) {
            int where = 0;
            for (int m = preparation; m; m &= m - 1) {
                data.prepIndex[index(m)] = ++where;
            }
        }
        prepareRanks(box, data);
        for (int a = 0; a < actors; ++a) {
            const auto& person = people[ids[a]];
            int R = person.target, r = person.rank;
            int cap=firstCap[a]?firstCap[a]:BAD;
            const auto original = old[r];
            int h = original.score;
            for (int b = 0; b < actors; ++b) {
                if (people[ids[b]].target <= R) {
                    data.tighter[a] |= bit(b + 1);
                }
            }
            int failMask = betterMask(box.hi, L, h);
            for (int j = 1; j <= L; ++j) {
                data.finalNeed[a][j] = std::max(winnerFloor[a], needed(data, j, L, R, true));
                data.finalSafe[a][j] = needed(data, j, L, R, false);
            }
            const int originalProfile = profile ? profile->at(h) : -1;
            int countLo[G + 1]{}, countHi[G + 1]{}, cutoffProfile[G + 1]{};
            std::fill_n(cutoffProfile, G + 1, -1);
            for (int k = 0; k < L && k < R; ++k) {
                countLo[k] = betterMask(box.lo, L, old[R - k].score);
                countHi[k] = betterMask(box.hi, L, old[R - k].score);
                cutoffProfile[k] = profile ? profile->at(old[R - k].score) : -1;
            }
            for (int j = 1; j <= L; ++j) {
                auto& cell = data.cell[a][j];
                int P = prefix(j - 1);
                int mandatory = withPrep ? certainTighter(data, a, j, 0) :
                    std::max(0, j - 1 - (L - count(data.tighter[a])));
                bool finalSlot = withPrep ? bool(winners & bit(j)) : !repeating;
                if (mandatory >= R) {
                    continue;
                }
                cell.oldCanFail = r + envelope.query(j - 1, h, 0, count(failMask & P)).second > R;
                if (cell.oldCanFail && originalProfile >= 0 && r <= R) {
                    const auto& after = profile->masks[originalProfile];
                    cell.oldCanFail = (after & ~profileMasks().atMost[j - 1][R - r]).any();
                }
                if (!cell.oldCanFail) {
                    continue;
                }
                int gateLow = 1, gateHigh = MAX_SCORE;
                if (profile) {
                    for (int k = 0; k < j && k < R; ++k) {
                        int i = cutoffProfile[k];
                        int need = R - profile->oldBetter[i];
                        const auto& set = profile->masks[i];
                        if (need <= 0 || (need <= j - 1 &&
                                (set & profileMasks().atMost[j - 1][need - 1]).none())) {
                            gateLow = std::max(gateLow, profile->score[i] + 1);
                        }
                        if (need > j - 1 || (need > 0 &&
                                (set & ~profileMasks().atMost[j - 1][need - 1]).none())) {
                            gateHigh = std::min(gateHigh, profile->score[i]);
                        }
                    }
                }
                Scores maxSource{};
                if (repeating) {
                    for (int k = 1; k < j; ++k) {
                        if (withPrep && !(preparation & bit(k))) {
                            continue;
                        }
                        int maximum = std::min(box.hi[k], needed(data, k, j - 1, R, false) - 1);
                        if (maximum >= box.lo[k]) {
                            cell.mustOwnFail |= bit(k);
                            if (maximum > h) {
                                maxSource[k] = maximum;
                            }
                        }
                    }
                }
                for (int k = mandatory; k < j && k < R; ++k) {
                    auto [lowCount, highCount] = envelope.query(j - 1, old[R - k].score, count(countLo[k] & P), count(countHi[k] & P));
                    if (lowCount > k || highCount < k ||
                            (profile && !profile->supports(cutoffProfile[k], j - 1, k))) {
                        continue;
                    }
                    int b = R - k;
                    auto gate = old[b];
                    if (b < r && (!finalSlot || box.hi[j] > gate.score)) {
                        int exponent = gate.score - h + std::abs(gate.time - original.time);
                        price(cell.fee[0], exponent, b);
                        if (withPrep) {
                            for (int p = mandatory; p <= k; ++p) {
                                price(cell.repeat[0][p], exponent, b);
                            }
                        }
                        if (exponent<=cap && (!firstGate[a] || firstGate[a] == b) &&
                                (!firstTime[a] || firstTime[a] == j)) {
                            if (!finalSlot || box.hi[j] >= data.finalNeed[a][j]) {
                                price(data.gate[a][k], exponent, b);
                            }
                            for (int p = mandatory; p <= k; ++p) {
                                price(data.first[a][j][p], exponent, b);
                            }
                        }
                    }
                    if (repeating) {
                        for (int u = 1; u < j; ++u) {
                            if (maxSource[u] && box.lo[u] <= gate.score) {
                                if (finalSlot && box.hi[j] <= gate.score) {
                                    continue;
                                }
                                int exponent = std::max(0, gate.score - maxSource[u]) + T + u - gate.time;
                                price(cell.fee[u], exponent, b);
                                if (withPrep) {
                                    for (int p = mandatory; p <= k; ++p) {
                                        price(cell.repeat[data.prepIndex[u]][p], exponent, b);
                                    }
                                }
                            }
                        }
                    }
                }
                for (int k = 1; k < j; ++k) {
                    if (mandatory == j - 1) {
                        break;
                    }
                    int low = std::max(box.lo[k], old[R - mandatory].score + 1);
                    if (!repeating && hasAnswer &&
                            std::max(low, h + 1) - h + T + k - original.time >
                                answer.bits[answer.size - 1]) {
                        continue;
                    }
                    int start = std::max({low, gateLow, needed(data, k, j - 1, R, true)});
                    int end = std::min({box.hi[k], gateHigh, needed(data, k, j - 1, R - 1, false) - 1});
                    if (start > end) {
                        continue;
                    }
                    int minForOld = std::max(start, h + 1);
                    int oldEnd = !finalSlot ? end : std::min(end, box.hi[j] - 1);
                    if (minForOld <= oldEnd) {
                        price(cell.fee[0], minForOld - h + T + k - original.time, -k, minForOld);
                        if (withPrep) {
                            for (int p = mandatory; p < j - 1 && p < R; ++p) {
                                int x = std::max(minForOld, old[R - p].score + 1);
                                if (x > oldEnd) {
                                    break;
                                }
                                price(cell.repeat[0][p], x - h + T + k - original.time, -k, x);
                            }
                        }
                        if (minForOld-h+T+k-original.time<=cap &&
                                (!firstGate[a] || firstGate[a] == -k) && (!firstTime[a] || firstTime[a] == j)) {
                            if (!finalSlot || box.hi[j] >= data.finalNeed[a][j]) {
                                price(data.gate[a][G + k], minForOld - h + T + k - original.time, -k, minForOld);
                            }
                            for (int p = mandatory; p < j - 1 && p < R; ++p) {
                                int x = std::max(minForOld, old[R - p].score + 1);
                                if (x > oldEnd || x-h+T+k-original.time>cap) {
                                    break;
                                }
                                price(data.first[a][j][p], x - h + T + k - original.time, -k, x);
                            }
                        }
                    }
                    if (repeating) {
                        for (int u = 1; u < j; ++u) {
                            if (u != k && maxSource[u] && !definitely(box, u, k)) {
                                if (start > oldEnd) {
                                    continue;
                                }
                                price(cell.fee[u], std::max(int(k > u), start - maxSource[u]) +
                                      std::abs(k - u), -k, start);
                                if (withPrep) {
                                    for (int p = mandatory; p < j - 1 && p < R; ++p) {
                                        int x = std::max(start, old[R - p].score + 1);
                                        if (x > oldEnd) {
                                            break;
                                        }
                                        price(cell.repeat[data.prepIndex[u]][p],
                                              std::max(int(k > u), x - maxSource[u]) + std::abs(k - u), -k, x);
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    std::pair<int, Price> bestPrice(const Domain& box, const Cell& cell, int h, int previous) const {
        Price best;
        int source = 0;
        if (!previous || !(betterMask(box.lo, L, h) & previous)) {
            best = cell.fee[0];
        }
        for (int m = previous; m; m &= m - 1) {
            int u = index(m);
            if (cell.fee[u].exponent >= best.exponent) {
                continue;
            }
            bool possible = true;
            for (int others = previous ^ bit(u); others; others &= others - 1) {
                int v = index(others);
                if (definitely(box, v, u)) {
                    possible = false;
                    break;
                }
            }
            if (possible) {
                best = cell.fee[u];
                source = u;
            }
        }
        return {source, best};
    }

    bool canUse(int actor, int mask) const {
        if (force[actor] && mask != force[actor]) {
            return false;
        }
        const auto& list = forbid[actor];
        return std::find(list.begin(), list.end(), mask) == list.end();
    }

    // All earlier terminal records with tighter deadlines precede the gate.
    bool chooseSingles(const Domain& box, const Analysis& data,
                       std::array<int, G>& assignment, Cost& bound) const {
        std::array<Cost, STATES> dp;
        std::array<int, STATES> parent{};
        parent[0] = -1;
        for (int S = 0; S < full; ++S) {
            if (!parent[S]) {
                continue;
            }
            int j = count(S) + 1;
            for (int m = full ^ S; m; m &= m - 1) {
                int a = index(m) - 1;
                const Price& fee = data.first[a][j][count(data.tighter[a] & S)];
                if (fee.exponent == BAD || box.hi[j] < data.finalNeed[a][j] || !canUse(a, bit(j))) {
                    continue;
                }
                Cost next = dp[S];
                next.add(fee.exponent);
                if (!couldImprove(next)) {
                    continue;
                }
                int U = S | bit(a + 1);
                if (!parent[U] || next < dp[U]) {
                    dp[U] = next;
                    parent[U] = a + 1;
                }
            }
        }
        if (!parent[full]) {
            return false;
        }
        for (int S = full; S;) {
            int a = parent[S];
            assignment[a - 1] = bit(count(S));
            S ^= bit(a);
        }
        bound = dp[full];
        return true;
    }

    bool firstFeeBound(const Analysis& data) const {
        if (!hasAnswer) {
            return true;
        }
        int mask = prefix(actors);
        std::array<Cost, STATES> prev, next;
        std::array<bool, STATES> good{}, nextGood{};
        good[0] = true;
        for (int j = 1; j <= L; ++j) {
            std::fill_n(nextGood.begin(), mask + 1, false);
            for (int S = 0; S <= mask; ++S) {
                if (!good[S]) {
                    continue;
                }
                int spare = (L - actors) - (j - 1 - count(S));
                if (S && spare) {
                    auto value = prev[S];
                    value.add(1);
                    if (couldImprove(value) && (!nextGood[S] || value < next[S])) {
                        nextGood[S] = true;
                        next[S] = value;
                    }
                }
                for (int m = mask ^ S; m; m &= m - 1) {
                    int a = index(m) - 1;
                    int p = std::max({0, count(S & data.tighter[a]) - spare,
                                      j - 1 - (L - count(data.tighter[a]))});
                    int exponent = data.first[a][j][p].exponent;
                    if (exponent == BAD) {
                        continue;
                    }
                    int U = S | bit(a + 1);
                    auto value = prev[S];
                    value.add(exponent);
                    if (couldImprove(value) && (!nextGood[U] || value < next[U])) {
                        nextGood[U] = true;
                        next[U] = value;
                    }
                }
            }
            for (int S = 0; S <= mask; ++S) {
                good[S] = nextGood[S];
                if (good[S]) {
                    prev[S] = next[S];
                }
            }
        }
        return good[mask];
    }

    int tighterInLayout(const Analysis& data, int a, int j, const std::array<int, G>& match) const {
        int tight = 0;
        for (int b = 0; b < actors; ++b) {
            tight += (data.tighter[a] & bit(b + 1)) && last(match[b]) < j;
        }
        return tight;
    }

    // When preparation slots are fixed, assigning actors in increasing target order
    // gives an extra lower bound on each fee, including repeated fees.
    Price selectedFirst(const Analysis& data, int a, int j, const std::array<int, G>& match) const {
        return data.first[a][j][tighterInLayout(data, a, j, match)];
    }

    std::pair<int, Price> prepPrice(const Domain& box, const Analysis& data, int a, int j,
                                   int previous, int tighterBefore) const {
        const Cell& c = data.cell[a][j];
        int p = std::min(tighterBefore, j - 1);
        if (!previous) {
            return {0, data.first[a][j][p]};
        }
        int h = old[people[ids[a]].rank].score;
        Price answer;
        int source = 0;
        if (!(betterMask(box.lo, L, h) & previous)) {
            answer = c.repeat[0][p];
        }
        for (int m = previous; m; m &= m - 1) {
            int u = index(m);
            auto fee = c.repeat[data.prepIndex[u]][p];
            if (fee.exponent >= answer.exponent) {
                continue;
            }
            bool canBeBest = true;
            for (int w = previous ^ bit(u); w; w &= w - 1) {
                if (definitely(box, index(w), u)) {
                    canBeBest = false;
                    break;
                }
            }
            if (canBeBest) {
                answer = fee;
                source = u;
            }
        }
        return {source, answer};
    }

    int certainTighter(const Analysis& data, int a, int j, int earlierWinners) const {
        int earlier = prefix(j - 1);
        int t = count(data.tighter[a]);
        return std::max(count(earlierWinners & earlier),
                        count((full ^ preparation) & earlier) - (actors - t));
    }

    bool chooseWithPreparations(const Domain& box, const Analysis& data,
                                std::array<int, G>& assignment, Cost& bound) const {
        const int winners = full ^ preparation;
        std::array<int, G> order{};
        std::iota(order.begin(), order.begin() + actors, 0);
        std::sort(order.begin(), order.begin() + actors, [&](int a, int b) {
            return people[ids[a]].target < people[ids[b]].target;
        });
        std::array<Cost, STATES> prev, next;
        std::array<int, STATES> states{}, newStates{};
        int parent[G][STATES]{};
        int statesCount = 1;
        for (int row = 0; row < actors; ++row) {
            int a = order[row];
            std::array<bool, STATES> possible{};
            std::vector<int> options;
            for (int w = winners; w; w &= w - 1) {
                int j = index(w);
                if (box.hi[j] < data.finalNeed[a][j]) {
                    continue;
                }
                int avail = preparation & prefix(j - 1);
                for (int prepared = avail;; prepared = (prepared - 1) & avail) {
                    int used = prepared | bit(j);
                    if (canUse(a, used)) {
                        bool legal = true;
                        int prior = 0;
                        for (int m = used; m; m &= m - 1) {
                            int k = index(m);
                            const auto& c = data.cell[a][k];
                            if (!c.oldCanFail || (prior & ~c.mustOwnFail) ||
                                (k != j && definitely(box, k, j))) {
                                legal = false;
                                break;
                            }
                            int p = certainTighter(data, a, k, 0);
                            if (prepPrice(box, data, a, k, prior, p).second.exponent == BAD) {
                                legal = false;
                                break;
                            }
                            prior |= bit(k);
                        }
                        if (legal) {
                            possible[used] = true;
                            options.push_back(used);
                        }
                    }
                    if (!prepared) {
                        break;
                    }
                }
            }
            if (options.empty()) {
                return false;
            }
            int out = 0;
            for (int z = 0; z < statesCount; ++z) {
                int S = states[z];
                int certain = S & winners;
                auto visit = [&](int B) {
                    if (!possible[B]) {
                        return;
                    }
                    auto cost = prev[S];
                    int earlier = 0;
                    for (int m = B; m; m &= m - 1) {
                        int j = index(m);
                        int p = certainTighter(data, a, j, certain);
                        int fee = prepPrice(box, data, a, j, earlier, p).second.exponent;
                        if (fee == BAD) {
                            return;
                        }
                        cost.add(fee);
                        if (!couldImprove(cost)) {
                            return;
                        }
                        earlier |= bit(j);
                    }
                    int U = S | B;
                    if (!parent[row][U] || cost < next[U]) {
                        if (!parent[row][U]) {
                            newStates[out++] = U;
                        }
                        parent[row][U] = B;
                        next[U] = cost;
                    }
                };
                if (row + 1 == actors) {
                    visit(full ^ S);
                } else {
                    for (int B : options) {
                        if (!(S & B)) {
                            visit(B);
                        }
                    }
                }
            }
            if (!out) {
                return false;
            }
            statesCount = out;
            for (int i = 0; i < out; ++i) {
                states[i] = newStates[i];
                prev[states[i]] = next[states[i]];
            }
        }
        bound = prev[full];
        for (int row = actors - 1, mask = full; row >= 0; --row) {
            int a = order[row];
            assignment[a] = parent[row][mask];
            mask ^= assignment[a];
        }
        return true;
    }

    // Each actor owns one nonempty subset of the chronological slots.
    bool chooseAssignment(const Domain& box, const Analysis& data,
                          std::array<int, G>& assignment, Cost& bound) const {
        int extra = L - actors;
        if (!extra) {
            return chooseSingles(box, data, assignment, bound);
        }
        if (preparation < 0 && !firstFeeBound(data)) {
            return false;
        }
        if (preparation >= 0) {
            return chooseWithPreparations(box, data, assignment, bound);
        }
        std::array<Cost, STATES> prev{}, next{};
        std::array<int, STATES> active{}, newActive{};
        int table[G][STATES]{};
        active[0] = 0;
        int entries = 1;

        for (int a = 0; a < actors; ++a) {
            std::array<Cost, STATES> local;
            std::array<bool, STATES> valid{}, possible{};
            valid[0] = true;
            int h = old[people[ids[a]].rank].score;
            int bitCount = count(data.tighter[a]);
            const auto& banned = forbid[a];
            bool one = false;
            for (int S = 1; S <= full; ++S) {
                if (count(S) > extra + 1) {
                    continue;
                }
                int j = last(S), earlier = S ^ bit(j);
                const Cell& cell = data.cell[a][j];
                if (!valid[earlier] || !cell.oldCanFail || (earlier & ~cell.mustOwnFail)) {
                    continue;
                }
                int exponent = earlier ? bestPrice(box, cell, h, earlier).second.exponent :
                    data.first[a][j][std::max(0, j - 1 - (L - bitCount))].exponent;
                if (exponent == BAD) {
                    continue;
                }
                valid[S] = true;
                local[S] = local[earlier];
                local[S].add(exponent);
                if (box.hi[j] < data.finalNeed[a][j] || (force[a] && force[a] != S) ||
                        std::find(banned.begin(), banned.end(), S) != banned.end()) {
                    continue;
                }
                bool winner = true;
                for (int m = earlier; m; m &= m - 1) {
                    if (definitely(box, index(m), j)) {
                        winner = false;
                        break;
                    }
                }
                if (winner) {
                    possible[S] = true;
                    one = true;
                }
            }
            if (!one) {
                return false;
            }

            int newEntries = 0;
            for (int i = 0; i < entries; ++i) {
                int S = active[i];
                int remaining = full ^ S;
                int used = L - count(remaining);
                int capacity = extra + a + 1 - used;
                if (capacity <= 0) {
                    continue;
                }
                auto visit = [&](int B) {
                    if (!possible[B]) {
                        return;
                    }
                    int U = S | B;
                    auto candidate = prev[S];
                    candidate.add(local[B]);
                    if (!couldImprove(candidate)) {
                        return;
                    }
                    if (!table[a][U] || candidate < next[U]) {
                        if (!table[a][U]) {
                            newActive[newEntries++] = U;
                        }
                        table[a][U] = B;
                        next[U] = candidate;
                    }
                };
                if (a == actors - 1) {
                    visit(remaining);
                } else if (force[a]) {
                    if (!(force[a] & S) && count(force[a]) <= capacity) {
                        visit(force[a]);
                    }
                } else if (capacity <= 2) {
                    for (int m = remaining; m; m &= m - 1) {
                        int b = m & -m;
                        visit(b);
                        if (capacity == 2) {
                            for (int k = m ^ b; k; k &= k - 1) {
                                visit(b | (k & -k));
                            }
                        }
                    }
                } else {
                    for (int B = remaining; B; B = (B - 1) & remaining) {
                        if (count(B) <= capacity) {
                            visit(B);
                        }
                    }
                }
            }
            if (!newEntries) {
                return false;
            }
            entries = newEntries;
            std::copy_n(newActive.begin(), entries, active.begin());
            for (int i = 0; i < entries; ++i) {
                prev[active[i]] = next[active[i]];
            }
        }
        bound = prev[full];
        for (int a = actors - 1, mask = full; a >= 0; --a) {
            assignment[a] = table[a][mask];
            mask ^= assignment[a];
        }
        return true;
    }

    // Sweep scores from low to high. A subset describes the placed suffix.
    bool finalWitness(const Domain& box, const std::array<int, G>& assignment, Scores& scores,
                      const std::array<std::vector<RankCheck>, G + 1>* checks = nullptr) const {
        Scores goal{};
        for (int a = 0; a < actors; ++a) {
            goal[last(assignment[a])] = people[ids[a]].target;
        }
        std::vector<CountBound> cuts = countBounds;
        for (int p = 1; p <= L; ++p) {
            if (quantiles.lo[p] > 1) {
                cuts.push_back({L, quantiles.lo[p] - 1, p, L});
            }
            if (quantiles.hi[p] < MAX_SCORE) {
                cuts.push_back({L, quantiles.hi[p], 0, p - 1});
            }
        }
        cuts.erase(std::remove_if(cuts.begin(), cuts.end(), [&](auto c) {
            return count(betterMask(box.lo, c.length, c.score)) >= c.low &&
                   count(betterMask(box.hi, c.length, c.score)) <= c.high;
        }), cuts.end());
        std::sort(cuts.begin(), cuts.end(), [](auto a, auto b) { return a.score < b.score; });
        cuts.push_back({L, MAX_SCORE, 0, L});
        std::array<i64, STATES> key;
        std::array<Scores, STATES> path;
        const i64 invalid = i64{MAX_SCORE + 2} * (L + 1);
        std::fill_n(key.begin(), full + 1, invalid);
        key[0] = 0;
        path[0] = {};
        int floor = 1;
        for (std::size_t left = 0; left < cuts.size();) {
            int upper = cuts[left].score;
            std::size_t right = left;
            while (right < cuts.size() && cuts[right].score == upper) {
                ++right;
            }
            for (int S = 0; S < full; ++S) {
                if (key[S] == invalid) {
                    continue;
                }
                int p = L - count(S);
                int at = int(key[S] / (L + 1));
                int previous = L + 1 - int(key[S] % (L + 1));
                for (int m = full ^ S; m; m &= m - 1) {
                    int i = index(m);
                    if (box.above[i] & S) {
                        continue;
                    }
                    int x = std::max({box.lo[i], floor, at + (i > previous)});
                    int maxScore = std::min(box.hi[i], upper);
                    if (checks) {
                        for (auto c : (*checks)[i]) {
                            int before = count(prefix(c.prefix) & ~S & ~bit(i));
                            int r = c.rank - before;
                            if (c.upper) {
                                if (r <= 0) {
                                    maxScore = 0;
                                    break;
                                }
                                x = std::max(x, old[r].score + 1);
                            } else if (r > 0) {
                                maxScore = std::min(maxScore, old[r].score);
                            }
                        }
                    }
                    if (goal[i]) {
                        if (goal[i] < p) {
                            continue;
                        }
                        x = std::max(x, old[goal[i] - p + 1].score + 1);
                    }
                    if (x <= maxScore) {
                        int U = S | bit(i);
                        i64 k = i64{x} * (L + 1) + L + 1 - i;
                        if (k < key[U]) {
                            key[U] = k;
                            path[U] = path[S];
                            path[U][i] = x;
                        }
                    }
                }
            }
            bool any = false;
            for (int S = 0; S <= full; ++S) {
                if (key[S] == invalid) {
                    continue;
                }
                for (auto i = left; i < right; ++i) {
                    const auto& c = cuts[i];
                    int n = count(prefix(c.length) & ~S);
                    if (n < c.low || n > c.high) {
                        key[S] = invalid;
                        break;
                    }
                }
                any |= key[S] != invalid;
            }
            if (!any) {
                return false;
            }
            left = right;
            floor = upper + 1;
        }
        if (key[full] == invalid) {
            return false;
        }
        scores = path[full];
        return true;
    }

    struct RankedPlan {
        std::array<int, G> slots{};
        std::array<int, G> order{};
        std::array<Price, G + 1> quote{};
        std::array<int, G + 1> origin{};
        Scores visible{};
    };

    bool quoteWitness(Domain box, const Analysis& data, const std::array<int, G>& assignment,
                      Scores& scores, const RankedPlan* descPlan = nullptr) {
        std::array<std::vector<RankCheck>, G + 1> checks;
        std::size_t originalSize = countBounds.size();
        bool consistent = true;
        for (int a = 0; a < actors && consistent; ++a) {
            auto user = people[ids[a]];
            auto initial = old[user.rank];
            int h = initial.score, R = user.target;
            int previous = 0;
            for (int m = assignment[a]; m && consistent; m &= m - 1) {
                int j = index(m);
                auto [u, fee] = bestPrice(box, data.cell[a][j], h, previous);
                if (preparation >= 0) {
                    auto quote = prepPrice(box, data, a, j, previous, tighterInLayout(data, a, j, assignment));
                    u = quote.first;
                    fee = quote.second;
                } else if (L == actors) {
                    fee = selectedFirst(data, a, j, assignment);
                } else if (!previous) {
                    fee = data.first[a][j][std::max(0, j - 1 - (L - count(data.tighter[a])))];
                }
                if (descPlan) {
                    fee=descPlan->quote[j];
                    u=descPlan->origin[j];
                }
                // Lower-bound quotes were formed from the original domain.
                if (fee.exponent == BAD) {
                    consistent = false;
                    break;
                }
                if (user.rank <= R) {
                    countBounds.push_back({j - 1, h, R - user.rank + 1, j - 1});
                }
                for (int m2 = previous; m2; m2 &= m2 - 1) {
                    int k = index(m2);
                    checks[k].push_back({j - 1, R, false});
                    if (!u) {
                        box.hi[k] = std::min(box.hi[k], h);
                    } else if (k != u) {
                        if (definitely(box, k, u)) {
                            consistent = false;
                            break;
                        }
                        if (!definitely(box, u, k)) {
                            setRelation(box, u, k);
                        }
                    }
                }
                if (u) {
                    box.lo[u] = std::max(box.lo[u], h + 1);
                }
                if (fee.cutoff > 0) {
                    auto b = old[fee.cutoff];
                    int k = R - fee.cutoff;
                    countBounds.push_back({j - 1, b.score, k, k});
                    if (u) {
                        box.lo[u] = std::max(box.lo[u], b.score + T + u - b.time - fee.exponent);
                    }
                } else {
                    int k = -fee.cutoff;
                    checks[k].push_back({j - 1, R, true});
                    checks[k].push_back({j - 1, R - 1, false});
                    if (!u) {
                        box.hi[k] = std::min(box.hi[k], fee.exponent + h - (T + k - initial.time));
                    }
                }
                previous |= bit(j);
            }
        }
        bool okay = consistent && normalize(box) && finalWitness(box, assignment, scores, &checks);
        countBounds.resize(originalSize);
        return okay;
    }

    struct Point {
        Scores merit{}, above{};
        std::array<int, G> order{};

        int cutoff(int R, int j) const {
            int passed = 0;
            for (int k : order) {
                if (k && k < j) {
                    int rank = above[k] + passed + 1;
                    if (rank >= R) {
                        if (rank == R) {
                            return -k;
                        }
                        break;
                    }
                    ++passed;
                }
            }
            return R - passed;
        }

        int rank(int k, int p) const {
            int result = above[k] + 1;
            for (int j : order) {
                if (j == k) {
                    break;
                }
                result += j && j <= p;
            }
            return result;
        }
    };

    Point point(const Scores& values) const {
        Point v;
        v.merit = values;
        for (int j = 1; j <= L; ++j) {
            v.above[j] = oldAhead(values[j]);
            v.order[j - 1] = j;
        }
        std::sort(v.order.begin(), v.order.begin() + L, [&](int i, int j) {
            return ahead(values[i], i, values[j], j);
        });
        return v;
    }

    Term feeTerm(const Record& original, int a, int b) const {
        int s = a ? 0 : original.score;
        int t = a ? T + a : original.time;
        int bs = b > 0 ? old[b].score : 0;
        int bt = b > 0 ? old[b].time : T - b;
        return {a, b < 0 ? -b : 0, bs - s + std::abs(bt - t)};
    }

    bool realize(const Point& v, const std::array<int, G>& assignment, std::vector<Term>& fees) const {
        for (const auto& c : countBounds) {
            int k = count(betterMask(v.merit, c.length, c.score));
            if (k < c.low || k > c.high) {
                return false;
            }
        }
        for (int p = 1; p <= L; ++p) {
            if (v.merit[v.order[p - 1]] < quantiles.lo[p] || v.merit[v.order[p - 1]] > quantiles.hi[p]) {
                return false;
            }
        }
        for (int a = 0; a < actors; ++a) {
            int mask = assignment[a], best = 0;
            auto user = people[ids[a]];
            auto original = old[user.rank];
            int R = user.target;
            if (v.rank(last(mask), L) > R) {
                return false;
            }
            for (int m = mask; m; m &= m - 1) {
                int j = index(m);
                int rank = user.rank + count(betterMask(v.merit, j - 1, original.score));
                if (best) {
                    rank = std::min(rank, v.rank(best, j - 1));
                }
                if (rank <= R) {
                    return false;
                }
                int source = best && v.merit[best] > original.score ? best : 0;
                int gate = v.cutoff(R, j);
                if (!best && ((firstGate[a] && gate != firstGate[a]) ||
                             (firstTime[a] && j != firstTime[a]))) {
                    return false;
                }
                fees.push_back(feeTerm(original, source, gate));
                if (!best || ahead(v.merit[j], j, v.merit[best], best)) {
                    best = j;
                }
            }
        }
        return true;
    }

    static Cost costAt(const std::vector<Term>& fees, const Scores& x) {
        Cost sum;
        for (auto [u, v, c] : fees) {
            sum.add(int(i64{c} + x[v] - x[u]));
        }
        return sum;
    }

    Split crossing(const Domain& box, int mask, int threshold, bool higher) const {
        int open = 0;
        int width = -1;
        for (int m = mask; m; m &= m - 1) {
            int k = index(m);
            if (box.lo[k] <= threshold && box.hi[k] > threshold) {
                int w = box.hi[k] - box.lo[k];
                if (w > width) {
                    open = k;
                    width = w;
                }
            }
        }
        return {open, 0, threshold, higher};
    }

    Split orderSplit(const Domain& box, int i, int j, bool iAhead) const {
        if (!definitely(box, i, j) && !definitely(box, j, i)) {
            return {i, j, 0, iAhead};
        }
        return {};
    }

    Split rankSplit(const Domain& box, int i, int p, int R, bool high) const {
        if (R <= 0) {
            return {};
        }
        int r0 = std::max(1, R - p + 1);
        int r1 = R;
        int first = 0, last = 0;
        for (int r = r0; r <= r1; ++r) {
            if (box.lo[i] <= old[r].score && old[r].score < box.hi[i]) {
                if (!first) {
                    first = r;
                }
                last = r;
            }
        }
        if (first) {
            return {i, 0, old[(first + last) / 2].score, high};
        }
        for (int j = 1; j <= p; ++j) {
            if (j != i) {
                if (auto split = orderSplit(box, i, j, high)) {
                    return split;
                }
            }
        }
        return {};
    }

    Split gateSplit(const Domain& box, const Analysis& data, int R, int j,
                    Price fee) const {
        if (fee.cutoff > 0) {
            int score = old[fee.cutoff].score;
            if (actors == L) {
                int current = count(betterMask(box.lo, j - 1, score));
                return crossing(box, prefix(j - 1), score, current < R - fee.cutoff);
            }
            return splitCount(box, j - 1, score, R - fee.cutoff, true);
        }
        int k = -fee.cutoff;
        int end = needed(data, k, j - 1, R - 1, false) - 1;
        int highestHistory = R - j + 2;
        if (highestHistory > 0) end=std::min(end,old[highestHistory].score);
        if (box.lo[k] <= end && end < box.hi[k]) {
            return {k, 0, end, false};
        }
        if (box.lo[k] < fee.need && fee.need > 1) {
            for (int r = std::max(1, R - j + 1); r <= R; ++r) {
                if (old[r].score + 1 == fee.need) {
                    return {k, 0, fee.need - 1, true};
                }
            }
        }
        if (box.lo[k] < needed(data, k, j - 1, R, false)) {
            if (auto split = rankSplit(box, k, j - 1, R, true)) {
                return split;
            }
        }
        if (box.hi[k] >= needed(data, k, j - 1, R - 1, true)) {
            return rankSplit(box, k, j - 1, R - 1, false);
        }
        return {};
    }

    // A single new record has one immutable grade. Different requests can
    // refer to it only when their possible historical rank windows overlap.
    Split donorConflict(const Domain& box, const Analysis& data,
                        const std::array<int, G>& assignment) const {
        struct Claim { int low, high, borrower, price; };
        Claim claims[G + 1][G]{};
        int length[G + 1]{};
        int bestImp = -1;
        Split result;
        for (int a = 0; a < actors; ++a) {
            auto person = people[ids[a]];
            int j = index(assignment[a]);
            int R = person.target;
            Price p = L == actors ? selectedFirst(data,a,j,assignment) :
                data.first[a][j][std::max(0,j-1-(L-count(data.tighter[a])))];
            if (preparation >= 0) {
                p = selectedFirst(data,a,j,assignment);
            }
            if (p.cutoff >= 0) continue;
            int k = -p.cutoff;
            int row = R - j + 2;
            int lo = std::max(box.lo[k],p.need);
            int hi = row > 0 ? std::min(box.hi[k],old[row].score) : box.hi[k];
            for (int t = 0; t < length[k]; ++t) {
                auto q = claims[k][t];
                if (std::max(lo,q.low) <= std::min(hi,q.high)) continue;
                int cut = lo > q.high ? q.high : hi;
                int imp = std::max(p.exponent,q.price);
                if (box.lo[k] <= cut && cut < box.hi[k] && imp > bestImp) {
                    bestImp=imp;
                    result={k,0,cut,lo>q.high};
                }
            }
            claims[k][length[k]++] = {lo,hi,a,p.exponent};
        }
        return result;
    }

    Split chooseSplit(const Domain& box, const Analysis& data,
                      const std::array<int, G>& assignment) const {
        Split costSplit;
        int significance = -1;
        bool candidateInvalidAtLow = false;
        for (int a = 0; a < actors; ++a) {
            int mask = assignment[a], jlast = last(mask);
            int R = people[ids[a]].target, r = people[ids[a]].rank, h = old[r].score;
            if (box.lo[jlast] < data.finalNeed[a][jlast]) {
                if (!hasAnswer) {
                    if (box.lo[jlast] < winnerFloor[a]) {
                        return {jlast, 0, winnerFloor[a] - 1, true};
                    }
                    if (auto split = rankSplit(box, jlast, L, R, true)) {
                        return split;
                    }
                }
                candidateInvalidAtLow = true;
            }
            int previous = 0;
            for (int m = mask; m; m &= m - 1) {
                int j = index(m);
                auto [u, fee] = bestPrice(box, data.cell[a][j], h, previous);
                if (preparation >= 0) {
                    auto quote = prepPrice(box, data, a, j, previous, tighterInLayout(data, a, j, assignment));
                    u = quote.first;
                    fee = quote.second;
                } else if (L == actors) {
                    fee = selectedFirst(data, a, j, assignment);
                } else if (!previous) {
                    fee = data.first[a][j][std::max(0, j - 1 - (L - count(data.tighter[a])))];
                }
                if (fee.exponent != BAD && fee.exponent > significance) {
                    Split split;
                    if (!u) {
                        split = crossing(box, previous, h, false);
                    } else if (box.lo[u] <= h) {
                        split = {u, 0, h, true};
                    } else {
                        for (int s = previous ^ bit(u); s && !split; s &= s - 1) {
                            split = orderSplit(box, u, index(s), true);
                        }
                    }
                    if (!split) {
                        split = gateSplit(box, data, R, j, fee);
                    }
                    if (split) {
                        significance = fee.exponent;
                        costSplit = split;
                    }
                }
                previous |= bit(j);
            }
        }
        if (costSplit && (hasAnswer || !candidateInvalidAtLow)) {
            return costSplit;
        }
        for (int a = 0; a < actors; ++a) {
            int mask = assignment[a], jlast = last(mask);
            auto user = people[ids[a]];
            int R = user.target, r = user.rank, h = old[r].score;
            if (box.lo[jlast] < winnerFloor[a]) {
                return {jlast, 0, winnerFloor[a] - 1, true};
            }
            if (box.lo[jlast] < data.finalSafe[a][jlast]) {
                if (auto split = rankSplit(box, jlast, L, R, true)) {
                    return split;
                }
            }
            for (int m = mask; m; m &= m - 1) {
                int j = index(m);
                if (r + count(betterMask(box.lo, j - 1, h)) <= R) {
                    if (auto split = splitCount(box, j - 1, h, R - r + 1, false)) {
                        return split;
                    }
                }
                for (int s = mask & prefix(j - 1); s; s &= s - 1) {
                    int u = index(s);
                    if (box.hi[u] >= needed(data, u, j - 1, R, true)) {
                        if (auto split = rankSplit(box, u, j - 1, R, false)) {
                            return split;
                        }
                    }
                }
            }
        }
        for (int p = 1; p <= L; ++p) {
            int ll = quantiles.lo[p] - 1;
            if (count(betterMask(box.lo, L, ll)) < p) {
                if (auto split = splitCount(box, L, ll, p, false)) {
                    return split;
                }
            }
            int hh = quantiles.hi[p];
            if (count(betterMask(box.hi, L, hh)) >= p) {
                if (auto split = splitCount(box, L, hh, p, false)) {
                    split.limit = -split.limit;
                    return split;
                }
            }
        }
        return costSplit;
    }

    // When the optimistic quotes are incompatible, also check the actual
    // quotes at the lower endpoint, so a solved node has fixed fees.
    Split settleActual(const Domain& box, const Analysis& data, const Point& v,
                       const std::array<int, G>& assignment) const {
        for (int a = 0; a < actors; ++a) {
            auto user = people[ids[a]];
            int h = old[user.rank].score;
            int mask = assignment[a], best = 0;
            for (int m = mask; m; m &= m - 1) {
                int j = index(m);
                int previous = mask & prefix(j - 1);
                if (best) {
                    if (auto split = crossing(box, bit(best), h, false)) {
                        return split;
                    }
                    for (int s = previous ^ bit(best); s; s &= s - 1) {
                        if (auto split = orderSplit(box, best, index(s), true)) {
                            return split;
                        }
                    }
                }
                if (auto split = gateSplit(box, data, user.target, j, {0, v.cutoff(user.target, j), 0})) {
                    return split;
                }
                if (!best || ahead(v.merit[j], j, v.merit[best], best)) {
                    best = j;
                }
            }
        }
        return {};
    }

    void finishAssignment(const Domain& box, const std::vector<Term>& fees) {
        bool plus = true, minus = true;
        for (auto [u, v, c] : fees) {
            if (u != v) {
                plus &= u == 0;
                minus &= v == 0;
            }
        }
        if (plus || minus) {
            take(costAt(fees, plus ? box.lo : box.hi));
            return;
        }
        std::vector<Bound> bounds;
        for (int j = 1; j <= L; ++j) {
            bounds.push_back({0, j, box.hi[j]});
            bounds.push_back({j, 0, -box.lo[j]});
            for (int m = box.above[j]; m; m &= m - 1) {
                int k = index(m);
                bounds.push_back({k, j, -(k > j)});
            }
        }
        auto best = minimizeScores<4>({box.lo.begin(), box.lo.begin() + L + 1}, bounds, fees);
        take(evaluate<G>(fees, best));
    }

    int affordablePower(const Cost& base) const {
        int a=answer.size-1,b=base.size-1;
        while(a>=0 && b>=0 && answer.bits[a]==base.bits[b]){--a;--b;}
        if(a<0 || (b>=0 && answer.bits[a]<base.bits[b]))return 0;
        int h=answer.bits[a--];
        // Adding bit h ties the leading difference. If the low suffix is
        // unaffordable, test each contiguous carry into h without copying sums.
        for(int e=h;e>=1;--e){
            int i=a,j=b;
            while(i>=0 && j>=0 && answer.bits[i]==base.bits[j]){--i;--j;}
            if(i>=0 && (j<0 || answer.bits[i]>base.bits[j]))return e;
            if(b<0 || base.bits[b]!=e-1)return e-1;
            --b;
        }
        return 0;
    }

    int bestGateBranch(const Analysis& data, std::array<int, G>& single,
                       std::vector<int>& choices, std::array<int, G>& floors,
                       Scores& donorHi, std::array<int,G>& caps, int& repeat) const {
        Scores best{};
        Cost total;
        for (int a = 0; a < actors; ++a) {
            best[a] = BAD;
            for (auto fee : data.gate[a]) {
                best[a] = std::min(best[a], fee.exponent);
            }
            if (best[a] == BAD) {
                return -2;
            }
            total.add(best[a]);
        }
        for (int j = actors; j < L; ++j) {
            total.add(1);
        }
        if (!couldImprove(total)) {
            return -2;
        }
        if (L > actors && style != SearchStyle::Anchor) {
            Cost base;
            for(int a=0;a<actors;++a) base.add(best[a]);
            for(int j=actors+1;j<L;++j) base.add(1);
            repeat=std::min(repeat,affordablePower(base));
        }
        int chosen = -1;
        for (int a = 0; a < actors; ++a) {
            Cost others;
            for (int b = 0; b < actors; ++b) {
                if (a != b) {
                    others.add(best[b]);
                }
            }
            for (int j = actors; j < L; ++j) {
                others.add(1);
            }
            std::vector<Price> options;
            for (auto fee : data.gate[a]) {
                if (fee.exponent == BAD) {
                    continue;
                }
                auto cost = others;
                cost.add(fee.exponent);
                if (couldImprove(cost)) {
                    options.push_back(fee);
                }
            }
            if (options.empty()) {
                return -2;
            }
            const bool refineBudget = style != SearchStyle::Anchor;
            int cap = refineBudget ? affordablePower(others) : answer.bits[answer.size - 1];
            if (refineBudget && (!caps[a] || cap < caps[a])) caps[a] = cap;
            if (firstGate[a]<0 || (options.size()==1 && options[0].cutoff<0)) {
                auto own=old[people[ids[a]].rank];
                int k=firstGate[a]<0?-firstGate[a]:-options[0].cutoff;
                donorHi[k]=std::min<i64>(donorHi[k],
                    i64{cap}+own.time+own.score-T-k);
            }
            const int R = people[ids[a]].target;
            int required = MAX_SCORE + 2, personal = MAX_SCORE + 2;
            for (auto fee : options) {
                int x = fee.cutoff > 0 ? old[fee.cutoff].score + 1 : old[R].score + 1;
                required = std::min(required, x);
                personal = std::min(personal, x + (fee.cutoff < 0));
            }
            for (int b = 0; b < actors; ++b) {
                if (people[ids[b]].target <= R) {
                    floors[b] = std::max(floors[b], required);
                }
            }
            floors[a] = std::max(floors[a], personal);
            if (firstGate[a]) {
                continue;
            }
            if (options.size() == 1) {
                single[a] = options[0].cutoff;
            } else if (options.size() <= (style == SearchStyle::Basic ? 3 : 6) &&
                       (chosen < 0 || (refineBudget && style != SearchStyle::Basic &&
                                      best[a] > best[chosen]) ||
                        ((!refineBudget || style == SearchStyle::Basic || best[a] == best[chosen]) &&
                         options.size() < choices.size()))) {
                chosen = a;
                choices.clear();
                std::sort(options.begin(), options.end(), [](auto x, auto y) {
                    return x.exponent < y.exponent;
                });
                for (auto fee : options) {
                    choices.push_back(fee.cutoff);
                }
            }
        }
        return chosen;
    }

    int bestTimeBranch(const Analysis& data, std::array<int, G>& singleton,
                       std::vector<int>& visits) const {
        std::array<int, G> cheapest{};
        int already = 0;
        for (int a = 0; a < actors; ++a) {
            cheapest[a] = BAD;
            for (auto fee : data.gate[a]) {
                cheapest[a] = std::min(cheapest[a], fee.exponent);
            }
            if (firstTime[a]) {
                already |= bit(firstTime[a]);
            }
        }
        int actor = -1;
        for (int a = 0; a < actors; ++a) {
            if (firstTime[a]) {
                continue;
            }
            Cost others;
            for (int b = 0; b < actors; ++b) {
                if (b != a) {
                    if (cheapest[b] == BAD) {
                        return -2;
                    }
                    others.add(cheapest[b]);
                }
            }
            for (int j = actors; j < L; ++j) {
                others.add(1);
            }
            std::vector<int> possible;
            for (int j = 1; j <= L; ++j) {
                int lower = preparation >= 0 ? certainTighter(data, a, j, 0) :
                    std::max(0, j - 1 - (L - count(data.tighter[a])));
                if (already & bit(j)) {
                    continue;
                }
                int exponent = data.first[a][j][lower].exponent;
                if (exponent == BAD) {
                    continue;
                }
                auto cap = others;
                cap.add(exponent);
                if (couldImprove(cap)) {
                    possible.push_back(j);
                }
            }
            if (possible.empty()) {
                return -2;
            }
            if (possible.size() == 1) {
                singleton[a] = possible[0];
                if (already & bit(possible[0])) {
                    return -2;
                }
                already |= bit(possible[0]);
            } else if (firstGate[a] &&
                       (possible.size() <= 3 || (firstGate[a]<0 && preparation>=0 &&
                                                actors-count(data.tighter[a])<=1)) &&
                       (actor < 0 || possible.size() < visits.size())) {
                actor = a;
                visits = std::move(possible);
            }
        }
        return actor;
    }

    void propagateKnownFirst(Domain box, int depth) {
        std::size_t current = countBounds.size();
        auto addCount = [&](CountBound condition) {
            auto existing = range(box, condition.length, condition.score);
            if (existing.first < condition.low || existing.second > condition.high) {
                countBounds.push_back(condition);
            }
        };
        for (int a = 0; a < actors; ++a) {
            int j = firstTime[a], b = firstGate[a];
            const auto user = people[ids[a]];
            int looser=0;
            for(int c=0;c<actors;++c) looser += people[ids[c]].target > user.target;
            const int terminals=preparation>=0?(full^preparation):(L==actors?full:0);
            if (b < 0) {
                int donor = -b;
                int before = j ? j - 1 : L - 1;
                int aboveHistory = user.target - before;
                box.lo[donor] = std::max({box.lo[donor], old[user.target].score + 1,
                                         old[user.rank].score + 1});
                if (aboveHistory > 0) {
                    box.hi[donor] = std::min(box.hi[donor],old[aboveHistory].score);
                }
                if (j) {
                    int mandatory=std::max(0,count(terminals & prefix(j-1))-looser);
                    int r=user.target-mandatory;
                    if(r<=0) {
                        countBounds.resize(current);
                        return;
                    }
                    box.lo[donor]=std::max(box.lo[donor],old[r].score+1);
                }
                if(!looser && terminals) {
                    if(terminals&bit(donor)) {
                        countBounds.resize(current);
                        return;
                    }
                    for(int m=terminals;m;m&=m-1) {
                        int w=index(m);
                        if(definitely(box,donor,w)) {
                            countBounds.resize(current);
                            return;
                        }
                        if(!definitely(box,w,donor)) setRelation(box,w,donor);
                    }
                }
                if (hasAnswer) {
                    const auto own=old[user.rank];
                    box.hi[donor] = std::min<i64>(box.hi[donor],
                        i64{answer.bits[answer.size-1]} + own.score + own.time - T - donor);
                }
            }
            if (style != SearchStyle::Anchor && j && preparation >= 0 && (preparation & bit(j))) {
                // A preparation is followed by another action of its owner.
                // Before that action it cannot be among the first R records.
                int countAbove=user.target-(L-2);
                if(countAbove>0) box.hi[j]=std::min(box.hi[j],old[countAbove].score);
                std::array<int,G> possibleAbove{};
                int n=0;
                for(int k=1;k<L;++k) {
                    if(k!=j && !(box.above[k]&bit(j))) {
                        insert(possibleAbove,n,box.hi[k]+(j>k));
                    }
                }
                box.hi[j]=std::min(box.hi[j],mergeThreshold(user.target,possibleAbove,n)-1);
            }
            if (!j) {
                continue;
            }
            if (user.rank <= user.target) {
                addCount({j - 1, old[user.rank].score, user.target - user.rank + 1, j - 1});
            }
            if (b > 0) {
                int count = user.target - b, score = old[b].score;
                addCount({j - 1, score, count, count});
                if (!looser && terminals) {
                    for(int m=terminals;m;m&=m-1) {
                        int w=index(m);
                        box.lo[w]=std::max(box.lo[w],score+1);
                    }
                }
                if (actors == L) {
                    box.lo[j] = std::max(box.lo[j], score + 1);
                    for (int c = 0; c < actors; ++c) {
                        if (firstTime[c] && firstTime[c] < j &&
                                people[ids[c]].target <= user.target) {
                            int k = firstTime[c];
                            box.lo[k] = std::max(box.lo[k], score + 1);
                        }
                    }
                }
            } else if (b < 0) {
                int k = -b;
                if (k >= j) {
                    countBounds.resize(current);
                    return;
                }
                if (actors == L && !definitely(box, j, k)) {
                    if (definitely(box, k, j)) {
                        countBounds.resize(current);
                        return;
                    }
                    setRelation(box, j, k);
                }
            }
        }
        search(box, depth);
        countBounds.resize(current);
    }

    // The already assigned winners have weaker targets. All other terminal
    // slots are guaranteed to precede the gate and can be removed exactly.
    struct HistoricOffer {
        int lower=0, upper=0;
    };

    struct LayoutOffer {
        Price price;
        int source=0;
    };

    Price remainingPrice(const Domain& box, int R, int r,
                         int visible, int owned, int forcedWinners,
                         int required, int& source, const HistoricOffer* offers,
                         const int* newAheadLo, const int* newAheadHi,
                         const int* oldAtLo, const int* oldAtHi,
                         int okayOld, int minGate, int maxGate, int exponentCap) const {
        const int visibleN = count(visible);
        const auto h = old[r];
        const int originalR=R+count(forcedWinners);
        Price best;
        source = 0;
        std::array<int,G+1> upper{};
        int poss = owned;
        bool useOld = true;
        for (int w=owned;w;w&=w-1) {
            int u = index(w);
            if (box.lo[u] > h.score) useOld = false;
            bool valid = box.hi[u] > h.score;
            for(int m=owned^bit(u);m && valid;m&=m-1) {
                if(definitely(box,index(m),u)) valid=false;
            }
            if(valid) upper[u]=box.hi[u];
            else poss &= ~bit(u);
        }
        auto takePrice = [&](int fee,int gate,int lo,int from) {
            if (fee<=exponentCap && fee < best.exponent) {
                best = {fee,gate,lo};
                source = from;
            }
        };
        int firstOld = std::max(1, R - visibleN);
        int lastOld = std::min(R, r - 1);
        if (required) {
            firstOld = std::max(firstOld, required);
            lastOld = std::min(lastOld, required);
        }
        for (int b = firstOld; b <= lastOld; ++b) {
            if (!(okayOld & bit(originalR - b + 1))) continue;
            int k=R-b;
            const auto record=old[b];
            auto const& possible=offers[originalR-b];
            if ((forcedWinners & ~possible.upper) || count(visible & possible.lower)>k ||
                count(visible & possible.upper)<k) continue;
            if(useOld) {
                takePrice(record.score-h.score+std::abs(record.time-h.time),b,0,0);
            }
            for(int w=poss;w;w&=w-1) {
                int u=index(w);
                if(box.lo[u]>record.score) continue;
                takePrice(std::max(0,record.score-upper[u])+T+u-record.time,b,0,u);
            }
        }
        int newOffers = required == 0 ? visible : (required < 0 ? visible & bit(-required) : 0);
        for (int w = newOffers; w; w &= w - 1) {
            int k = index(w);
            if(!owned && box.lo[k]-h.score+T+k-h.time>=best.exponent) continue;
            if(oldAtHi[k]+count((newAheadLo[k]|box.above[k])&visible)>=R ||
               oldAtLo[k]+count(newAheadHi[k]&visible)<R-1) continue;
            std::array<int,G> lowScores{},highScores{};
            int ln=0,hn=0,forced=0;
            for(int m=visible ^ bit(k);m;m&=m-1) {
                int u=index(m);
                if(box.above[k]&bit(u)) ++forced;
                else insert(lowScores,ln,box.lo[u]+(k>u));
                if(!(box.above[u]&bit(k))) insert(highScores,hn,box.hi[u]+(k>u));
            }
            int lower=std::max({box.lo[k],minGate,mergeThreshold(R-forced,lowScores,ln)});
            int higher=std::min({box.hi[k],maxGate,mergeThreshold(R-1,highScores,hn)-1});
            for(int m=forcedWinners;m;m&=m-1) {
                int u=index(m);
                higher=std::min(higher,box.hi[u]-(u>k));
            }
            if(lower>higher) continue;
            if(useOld && std::max(lower,h.score+1)<=higher) {
                int x=std::max(lower,h.score+1);
                takePrice(x-h.score+T+k-h.time,-k,x,0);
            }
            for(int w2=poss & ~bit(k);w2;w2&=w2-1) {
                int u=index(w2);
                if(definitely(box,u,k)||box.lo[u]+(u<k)>higher) continue;
                int x=std::max(lower,box.lo[u]+(u<k));
                takePrice(std::max(int(u<k),x-upper[u])+std::abs(u-k),-k,x,u);
            }
        }
        return best;
    }

    bool chooseDescending(const Domain& box, RankedPlan& result, Cost& bound) const {
        const int prep=preparation>=0?preparation:0;
        const int winners=full ^ prep;
        int aboveOld[G+1]{}, oldAtLo[G+1]{}, newAheadHi[G+1]{};
        int aboveLow[G+1]{};
        HistoricOffer offers[G][G]{};
        Scores approved[G], minGate[G], maxGate[G];
        const CountEnvelope envelope(countBounds, L);
        std::vector<int> aboveCount;
        for(const auto& c:countBounds) aboveCount.push_back(oldAhead(c.score+1));
        for(int a=0;a<actors;++a) {
            int R=people[ids[a]].target;
            minGate[a].fill(1);
            maxGate[a].fill(MAX_SCORE);
            approved[a].fill(countBounds.empty()?full:0);
            for(int k=0;k<L && k<R;++k) {
                int g=old[R-k].score;
                offers[a][k]={betterMask(box.lo,L,g),betterMask(box.hi,L,g)};
                if(!countBounds.empty()) {
                    for(int j=k+1;j<=L;++j) {
                        int bm=prefix(j-1);
                        auto [lo,hi]=envelope.query(j-1,g,count(offers[a][k].lower & bm),
                                                   count(offers[a][k].upper & bm));
                        if(lo<=k && k<=hi) approved[a][j]|=bit(k+1);
                    }
                }
            }
            for(std::size_t c=0;c<countBounds.size();++c) {
                const auto cut=countBounds[c];
                for(int j=1;j<=L;++j) {
                    int lo=cut.low-std::max(0,cut.length-j+1);
                    int hi=cut.high+std::max(0,j-1-cut.length);
                    if(aboveCount[c]+lo>=R) minGate[a][j]=std::max(minGate[a][j],cut.score+1);
                    if(aboveCount[c]+hi<R) maxGate[a][j]=std::min(maxGate[a][j],cut.score);
                }
            }
        }
        for(int j=1;j<=L;++j) {
            aboveOld[j]=oldAhead(box.hi[j]);
            oldAtLo[j]=oldAhead(box.lo[j]);
            for(int k=1;k<=L;++k) {
                if(k!=j && ahead(box.lo[k],k,box.hi[j],j)) aboveLow[j]|=bit(k);
                if(k!=j && ahead(box.hi[k],k,box.lo[j],j)) newAheadHi[j]|=bit(k);
            }
        }
        auto& order=result.order;
        std::iota(order.begin(),order.begin()+actors,0);
        std::sort(order.begin(),order.begin()+actors,[&](int a,int b){
            return people[ids[a]].target>people[ids[b]].target;
        });
        Scores neededLater{};
        for (int row = actors - 1; row >= 0; --row) {
            const int j = firstTime[order[row]];
            neededLater[row] = neededLater[row + 1] | (j ? bit(j) : 0);
            if (force[order[row]]) neededLater[row] |= force[order[row]];
        }
        std::array<Cost,STATES> dp, next;
        std::array<int,STATES> fifo, pending;
        int parents[G][STATES]{};
        LayoutOffer cache[4 * STATES];
        unsigned char stamp[4 * STATES]{};
        int reducedPrep[G+1]{};
        int nPrep=0;
        for(int m=prep;m;m&=m-1) {
            reducedPrep[index(m)]=1<<nPrep++;
        }
        assert(nPrep<=2);
        auto quote=[&](int row,int a,int j,int prev,int visible,int tight) {
            int sourceShape=0;
            for(int m=prev;m;m&=m-1) sourceShape |= reducedPrep[index(m)];
            int code=4*(visible|bit(j))+sourceShape;
            if(stamp[code]!=row+1) {
                const auto actor=people[ids[a]];
                auto& v=cache[code];
                v.price=remainingPrice(box,actor.target-count(tight),actor.rank,visible,prev,tight,
                    prev?0:firstGate[a],v.source,offers[a],aboveLow,newAheadHi,oldAtLo,aboveOld,
                    approved[a][j],minGate[a][j],maxGate[a][j],
                    prev?repeatCap:(firstCap[a]?firstCap[a]:BAD));
                stamp[code]=row+1;
            }
            return cache[code];
        };
        fifo[0]=0;
        int states=1;
        for(int row=0;row<actors;++row) {
            int a=order[row];
            const auto actor=people[ids[a]];
            int out=0;
            const int fixed=force[a];
            for(int pos=0;pos<states;++pos) {
                int S=fifo[pos];
                int remainingWinners=winners & ~S;
                int keep=prep | (S & winners);
                int worst=0;
                for(int m=remainingWinners;m;m&=m-1) {
                    int j=index(m);
                    if(!worst || ahead(box.hi[worst],worst,box.hi[j],j)) worst=j;
                }
                if (!worst || aboveOld[worst]+count(aboveLow[worst]&keep)+count(remainingWinners)>actor.target) {
                    continue;
                }
                auto visit=[&](int B) {
                    if ((B & neededLater[row + 1]) || (firstTime[a] && index(B) != firstTime[a])
                        || !canUse(a, B)) return;
                    int terminal=last(B);
                    for(int m=B ^ bit(terminal);m;m&=m-1) {
                        int v=index(m);
                        if(box.lo[v]>=box.hi[terminal] || definitely(box,v,terminal)) return;
                    }
                    auto value=dp[S];
                    int prev=0;
                    for(int m=B;m;m&=m-1) {
                        int j=index(m);
                        int before=prefix(j-1);
                        int tight=remainingWinners & before;
                        int r=actor.target-count(tight);
                        if(r<=0 || (!prev && firstTime[a] && firstTime[a]!=j)) return;
                        auto fee=quote(row,a,j,prev,keep & before,tight).price;
                        if(fee.exponent==BAD) return;
                        value.add(fee.exponent);
                        if(!couldImprove(value)) return;
                        prev |= bit(j);
                    }
                    int U=S|B;
                    if(!parents[row][U] || value<next[U]) {
                        if(!parents[row][U]) pending[out++]=U;
                        parents[row][U]=B;
                        next[U]=value;
                    }
                };
                if (row+1==actors) {
                    int B=full^S;
                    if((winners & B)==bit(last(B))) visit(B);
                } else if (fixed) {
                    if(!(fixed&S) && (winners & fixed)==bit(last(fixed))) visit(fixed);
                } else {
                    int choices = remainingWinners & ~neededLater[row + 1];
                    if (firstTime[a] && !prep) choices &= bit(firstTime[a]);
                    for(int m=choices;m;m&=m-1) {
                        int j=index(m);
                        if(box.hi[j] < winnerFloor[a]) continue;
                        int possible=prep & ~S & prefix(j-1);
                        for(int B=possible;;B=(B-1)&possible) {
                            visit(bit(j)|B);
                            if(!B) break;
                        }
                    }
                }
            }
            if(!out) return false;
            states=out;
            for(int p=0;p<out;++p) {
                fifo[p]=pending[p];
                dp[fifo[p]]=next[fifo[p]];
            }
        }
        bound=dp[full];
        for(int row=actors-1,S=full;row>=0;--row) {
            result.slots[order[row]]=parents[row][S];
            S ^= parents[row][S];
        }
        int S=0;
        for(int row=0;row<actors;++row) {
            int a=order[row];
            int B=result.slots[a],prev=0;
            int keep=prep | (S & winners);
            for(int m=B;m;m&=m-1) {
                int j=index(m), before=prefix(j-1);
                int tight=(winners & ~S) & before;
                result.visible[j]=keep & before;
                const auto offer=quote(row,a,j,prev,result.visible[j],tight);
                result.quote[j]=offer.price;
                result.origin[j]=offer.source;
                prev |= bit(j);
            }
            S |= B;
        }
        return true;
    }

    Split descendingSplit(const Domain& box,const Analysis& data,const RankedPlan& plan) const {
        int value=-1;
        Split chosen;
        for(int a=0;a<actors;++a) {
            const auto user=people[ids[a]];
            int R=user.target;
            int prev=0;
            for(int m=plan.slots[a];m;m&=m-1) {
                int j=index(m);
                auto fee=plan.quote[j];
                if (fee.exponent>value) {
                    int u=plan.origin[j];
                    Split split;
                    if(!u) split=crossing(box,prev,old[user.rank].score,false);
                    else {
                        if(box.lo[u]<=old[user.rank].score) split={u,0,old[user.rank].score,true};
                        for(int w=prev & ~bit(u);w && !split;w&=w-1) {
                            split=orderSplit(box,u,index(w),true);
                        }
                    }
                    if (fee.cutoff>0 && !split) {
                        int cut=old[fee.cutoff].score;
                        split=splitCount(box,j-1,cut,R-fee.cutoff,true);
                    } else if(fee.cutoff<0 && !split) {
                        int k=-fee.cutoff;
                        if(box.lo[k]<fee.need && box.hi[k]>=fee.need) {
                            split={k,0,fee.need-1,true};
                        } else {
                            split=gateSplit(box,data,R,j,fee);
                        }
                    }
                    if(split) {chosen=split;value=fee.exponent;}
                }
                prev |= bit(j);
            }
        }
        return chosen;
    }

    void search(Domain box, int depth) {
        if(interrupted) return;
        if(nodesRemaining>=0 && nodesRemaining--==0) {
            interrupted=true;
            return;
        }
        if (!normalize(box)) {
            return;
        }
        const bool useProfile = L >= 7 && countBounds.size() >= 2 &&
            historicalThresholds.size() <= 3 * std::size_t(L);
        Profile profile;
        if (useProfile && (!gradeProfile(box, profile) || !normalize(box))) {
            return;
        }
        std::unique_ptr<Analysis> data;
        auto analyze = [&] {
            if (!data) {
                data = std::make_unique<Analysis>();
                makePrices(box, *data, useProfile ? &profile : nullptr);
            }
        };
        std::array<int, G> assignment{};
        Cost bound;
        const bool descending = (style == SearchStyle::Ranked || style == SearchStyle::Assigned) &&
            L >= 7 && (L == actors || preparation >= 0);
        RankedPlan plan;
        if (descending) {
            if (!chooseDescending(box,plan,bound) || !couldImprove(bound)) {
                return;
            }
            assignment = plan.slots;
        } else {
            analyze();
            if (!chooseAssignment(box, *data, assignment, bound) || !couldImprove(bound)) return;
        }

        auto low = point(box.lo);
        std::vector<Term> fees;
        bool ok = realize(low, assignment, fees);
        if (ok) {
            take(costAt(fees, box.lo));
            if (!couldImprove(bound)) {
                return;
            }
        }
        analyze();
        if (depth < 3 || (!hasAnswer && depth < 8)) {
            Scores witness{};
            if (quoteWitness(box, *data, assignment, witness, descending ? &plan : nullptr)) {
                std::vector<Term> preferred;
                if (realize(point(witness), assignment, preferred)) {
                    take(costAt(preferred, witness));
                    if (!couldImprove(bound)) {
                        return;
                    }
                }
            }
        }

        if (hasAnswer && (style!=SearchStyle::Basic || L!=actors || depth<6)) {
            std::array<int, G> single{};
            std::vector<int> choices;
            auto floors = winnerFloor;
            auto cutoffs = box.hi;
            auto caps=firstCap;
            auto repeat=repeatCap;
            int chosen = bestGateBranch(*data, single, choices, floors, cutoffs,caps,repeat);
            if (chosen == -2) {
                return;
            }
            if (floors != winnerFloor || cutoffs != box.hi ||
                    caps!=firstCap || repeat!=repeatCap) {
                box.hi=cutoffs;
                auto oldCaps=firstCap;
                int oldRepeat=repeatCap;
                firstCap=caps;
                repeatCap=repeat;
                const auto oldFloor = winnerFloor;
                const auto oldQuantiles = quantiles;
                winnerFloor = floors;
                std::sort(floors.begin(), floors.begin() + actors, std::greater<int>{});
                for (int p = 1; p <= actors; ++p) {
                    quantiles.lo[p] = std::max(quantiles.lo[p], floors[p - 1]);
                }
                data.reset();
                search(box, depth + 1);
                winnerFloor = oldFloor;
                quantiles = oldQuantiles;
                firstCap=oldCaps;
                repeatCap=oldRepeat;
                return;
            }
            bool fixed = std::any_of(single.begin(), single.end(), [](int b) { return b != 0; });
            if (fixed || chosen >= 0) {
                const auto saved = firstGate;
                data.reset();
                if (fixed) {
                    for (int a = 0; a < actors; ++a) {
                        if (single[a]) {
                            firstGate[a] = single[a];
                        }
                    }
                    propagateKnownFirst(box, depth + 1);
                } else {
                    for (int gate : choices) {
                        firstGate[chosen] = gate;
                        propagateKnownFirst(box, depth + 1);
                    }
                }
                firstGate = saved;
                return;
            }
            std::vector<int> times;
            chosen = bestTimeBranch(*data, single, times);
            if (chosen == -2) {
                return;
            }
            fixed = std::any_of(single.begin(), single.end(), [](int j) { return j != 0; });
            if (fixed || chosen >= 0) {
                const auto saved = firstTime;
                data.reset();
                if (fixed) {
                    for (int a = 0; a < actors; ++a) {
                        if (single[a]) {
                            firstTime[a] = single[a];
                        }
                    }
                    propagateKnownFirst(box, depth + 1);
                } else {
                    for (int j : times) {
                        firstTime[chosen] = j;
                        propagateKnownFirst(box, depth + 1);
                    }
                }
                firstTime = saved;
                return;
            }
        }


        Split split;
        if(descending) {
            split=descendingSplit(box,*data,plan);
        } else if(style!=SearchStyle::Basic) {
            split=donorConflict(box,*data,assignment);
        }
        if (!split) {
            split = chooseSplit(box, *data, assignment);
        }
        bool finalPossible = true;
        Scores witness{};
        if ((!hasAnswer || depth < 2 || !split) && !ok) {
            finalPossible = finalWitness(box, assignment, witness);
            if (finalPossible) {
                low = point(witness);
                fees.clear();
                ok = realize(low, assignment, fees);
                if (ok) {
                    take(costAt(fees, witness));
                    if (!couldImprove(bound)) {
                        return;
                    }
                }
            }
        }
        if (finalPossible && !split) {
            split = settleActual(box, *data, low, assignment);
            if (!split && ok) {
                bool variable = false;
                for (auto [u, v, c] : fees) {
                    variable |= u != v;
                }
                if (variable) {
                    for (const auto& c : countBounds) {
                        int forced = count(betterMask(box.lo, c.length, c.score));
                        int possible = count(betterMask(box.hi, c.length, c.score));
                        if (forced < c.low || possible > c.high) {
                            split = crossing(box, prefix(c.length), c.score, forced < c.low);
                            if (split) {
                                break;
                            }
                        }
                    }
                    for (int p = 1; p <= L && !split; ++p) {
                        if (count(betterMask(box.lo, L, quantiles.lo[p] - 1)) < p) {
                            split = crossing(box, full, quantiles.lo[p] - 1, true);
                        } else if (count(betterMask(box.hi, L, quantiles.hi[p])) >= p) {
                            split = crossing(box, full, quantiles.hi[p], false);
                        }
                    }
                }
            }
        }
        data.reset();

        if (finalPossible && split) {
            if (split.where >= 0) {
                auto [mi, ma] = range(box, split.where, split.value);
                int k = std::abs(split.limit);
                auto visit = [&](int l, int h) {
                    if (l <= h && couldImprove(bound)) {
                        countBounds.push_back({split.where, split.value, l, h});
                        search(box, depth + 1);
                        countBounds.pop_back();
                    }
                };
                if (split.prefer) {
                    visit(k, k);
                    visit(mi, k - 1);
                    visit(k + 1, ma);
                } else if (split.limit > 0) {
                    visit(k, ma);
                    visit(mi, k - 1);
                } else {
                    visit(mi, k - 1);
                    visit(k, ma);
                }
            } else {
                for (bool side : {split.prefer, !split.prefer}) {
                    if (!couldImprove(bound)) {
                        break;
                    }
                    Domain next = box;
                    if (split.against) {
                        setRelation(next, side ? split.slot : split.against,
                                          side ? split.against : split.slot);
                    } else if (side) {
                        next.lo[split.slot] = split.value + 1;
                    } else {
                        next.hi[split.slot] = split.value;
                    }
                    search(next, depth + 1);
                }
            }
            return;
        }

        if (ok) {
            finishAssignment(box, fees);
        }
        auto saved = force;
        for (int a = 0; a < actors && couldImprove(bound); ++a) {
            if (!force[a]) {
                forbid[a].push_back(assignment[a]);
                search(box, depth + 1);
                forbid[a].pop_back();
                force[a] = assignment[a];
            }
        }
        force = saved;
    }

    bool optimisticGroup(const Quantiles& given) {
        if (actors == 1 && L > 1) {
            return false;
        }
        quantiles = given;
        std::array<int, G> deadlines{};
        for (int a = 0; a < actors; ++a) {
            deadlines[a] = people[ids[a]].target;
        }
        std::sort(deadlines.begin(), deadlines.begin() + actors);
        int useful = MAX_SCORE;
        for (int a = 0; a < actors; ++a) {
            const auto person = people[ids[a]];
            useful = std::min({useful, old[person.rank].score, old[person.target].score});
        }
        quantiles.lo[L] = std::max(quantiles.lo[L], useful + 1);
        for (int a = 0; a < actors; ++a) {
            int R = people[ids[a]].target;
            winnerFloor[a] = 1;
            for (int k = 0; k < actors; ++k) {
                if (deadlines[k] >= R) {
                    int r = deadlines[k] - k;
                    if (r <= 0) {
                        return false;
                    }
                    winnerFloor[a] = std::max(winnerFloor[a], old[r].score + 1);
                }
            }
        }
        for (int a = 0; a < actors; ++a) {
            int r = deadlines[a] - a;
            if (r <= 0) {
                return false;
            }
            quantiles.lo[a + 1] = std::max(quantiles.lo[a + 1], old[r].score + 1);
        }
        for (int p = L - 1; p >= 1; --p) {
            quantiles.lo[p] = std::max(quantiles.lo[p], quantiles.lo[p + 1]);
        }
        for (int p = 2; p <= L; ++p) {
            quantiles.hi[p] = std::min(quantiles.hi[p], quantiles.hi[p - 1]);
        }
        for (int p = 1; p <= L; ++p) {
            if (quantiles.lo[p] > quantiles.hi[p]) {
                return false;
            }
        }
        Cost lb;
        for (int a = 0; a < actors; ++a) {
            int r = people[ids[a]].rank, R = people[ids[a]].target;
            auto own = old[r];
            int minimum = BAD;
            for (int p = std::max(1, R - L + 1); p <= R && p < r; ++p) {
                if (int(quantiles.hi[1]) <= old[p].score && p != R) {
                    continue;
                }
                minimum = std::min(minimum, old[p].score - own.score + std::abs(old[p].time - own.time));
            }
            int x = std::max(old[R].score, own.score) + 1;
            if (L > 1 && x <= quantiles.hi[1]) {
                minimum = std::min(minimum, x - own.score + T + 1 - own.time);
            }
            if (minimum == BAD) {
                return false;
            }
            lb.add(minimum);
        }
        for (int j = actors; j < L; ++j) {
            lb.add(1);
        }
        return couldImprove(lb);
    }

    // Equal deadlines make every terminal submission invisible as a fee object.
    // Only the at most two nonterminal scores remain variable in this case.
    struct EqualOption {
        int slots;
        Cost lower;
        std::array<Term, 3> fees;
    };

    std::array<Cost, STATES> equalCosts[2];
    int equalParent[G][STATES];
    int equalMasks[2][STATES];

    void equalCellSolve(const Domain& box, const std::array<std::vector<EqualOption>, G>& all) {
        auto* dp = &equalCosts[0];
        auto* next = &equalCosts[1];
        (*dp)[0].size = 0;
        auto* current = equalMasks[0];
        auto* pending = equalMasks[1];
        current[0] = 0;
        int size = 1;
        for (int a = 0; a < actors; ++a) {
            std::array<const EqualOption*, 4 * G> eligible;
            int choices = 0;
            for (const auto& option : all[a]) {
                if (canUse(a, option.slots)) {
                    eligible[choices++] = &option;
                }
            }
            std::bitset<STATES> seen;
            int reached = 0;
            for (int p = 0; p < size; ++p) {
                int S = current[p];
                for (int i = 0; i < choices; ++i) {
                    const auto& o = *eligible[i];
                    if (S & o.slots) {
                        continue;
                    }
                    auto cost = (*dp)[S];
                    cost.add(o.lower);
                    if (!couldImprove(cost)) {
                        continue;
                    }
                    int U = S | o.slots;
                    if (!seen[U]) {
                        seen[U] = true;
                        pending[reached++] = U;
                        (*next)[U] = cost;
                        equalParent[a][U] = o.slots;
                    } else if (cost < (*next)[U]) {
                        (*next)[U] = cost;
                        equalParent[a][U] = o.slots;
                    }
                }
            }
            if (!reached || (a == actors - 1 && !seen[full])) {
                return;
            }
            std::swap(current, pending);
            std::swap(dp, next);
            size = reached;
        }
        Cost lower = (*dp)[full];
        std::array<int, G> chosen{};
        std::vector<Term> terms;
        for (int S = full, a = actors - 1; a >= 0; --a) {
            chosen[a] = equalParent[a][S];
            assert(chosen[a]);
            for (const auto& o : all[a]) {
                if (o.slots == chosen[a]) {
                    terms.insert(terms.end(), o.fees.begin(), o.fees.begin() + count(o.slots));
                    break;
                }
            }
            S ^= chosen[a];
        }
        finishAssignment(box, terms);
        auto saved = force;
        for (int a = 0; a < actors && couldImprove(lower); ++a) {
            if (!force[a]) {
                forbid[a].push_back(chosen[a]);
                equalCellSolve(box, all);
                forbid[a].pop_back();
                force[a] = chosen[a];
            }
        }
        force = saved;
    }

    void equalCell(Domain box, int prep, int R) {
        int terminal = full ^ prep;
        // Use the order of the preparation records in the present cell.
        std::array<int, 2> order{};
        Scores oldCounts{};
        int total = 0;
        for (int m = prep; m; m &= m - 1) {
            int j = index(m);
            order[total++] = j;
            oldCounts[j] = oldAhead(box.lo[j]);
        }
        if (total == 2 && ahead(box.lo[order[1]], order[1], box.lo[order[0]], order[0])) {
            std::swap(order[0], order[1]);
        }
        auto gate = [&](int desired, int until) {
            int passed = 0;
            for (int p = 0; p < total; ++p) {
                int k = order[p];
                if (k >= until) {
                    continue;
                }
                int rank = oldCounts[k] + passed + 1;
                if (rank >= desired) {
                    if (rank == desired) {
                        return -k;
                    }
                    break;
                }
                ++passed;
            }
            return desired - passed;
        };

        int finalGate = gate(R - actors + 1, L + 1);
        // Independently smallest terminal grades that let all of them fit in top R.
        for (int m = terminal; m; m &= m - 1) {
            int j = index(m);
            int x = finalGate > 0 ? old[finalGate].score + 1 :
                box.lo[-finalGate] + (j > -finalGate);
            if (x > MAX_SCORE) {
                return;
            }
            // The terminal values do not occur in the objective.
            box.lo[j] = box.hi[j] = x;
        }
        for (int p = 1; p <= L; ++p) {
            if (count(betterMask(box.lo, L, quantiles.lo[p] - 1)) < p ||
                    count(betterMask(box.lo, L, quantiles.hi[p])) >= p) {
                return;
            }
        }
        Scores cutoff{};
        for (int j = 1; j <= L; ++j) {
            cutoff[j] = gate(R - count(terminal & prefix(j - 1)), j);
        }
        auto merit = [&](int r) { return r > 0 ? old[r].score : box.lo[-r]; };
        auto time = [&](int r) { return r > 0 ? old[r].time : T - r; };
        auto better = [&](int a, int b) {
            if (a > 0 && b > 0) {
                return a < b;
            }
            int x = merit(a), y = merit(b);
            return x > y || (x == y && time(a) < time(b));
        };
        Cost firstFees;
        for (int a = 0; a < actors; ++a) {
            int r = people[ids[a]].rank;
            int cheapest = BAD;
            for (int j = 1; j <= L; ++j) {
                int b = cutoff[j];
                if (better(b, r)) {
                    cheapest = std::min(cheapest, merit(b) - old[r].score + std::abs(time(b) - old[r].time));
                }
            }
            if (cheapest == BAD) {
                return;
            }
            firstFees.add(cheapest);
        }
        for (int j = 0; j < total; ++j) {
            firstFees.add(1);
        }
        if (!couldImprove(firstFees)) {
            return;
        }
        std::array<std::vector<EqualOption>, G> options;
        Cost sum;
        for (int a = 0; a < actors; ++a) {
            auto user = people[ids[a]];
            auto historic = old[user.rank];
            bool any = false;
            Cost smallest;
            for (int P = prep;; P = (P - 1) & prep) {
                int present = user.rank;
                Cost partial;
                std::array<Term, 3> terms{};
                int size = 0;
                auto bill = [&](int j) {
                    int b = cutoff[j];
                    if (!better(b, present)) {
                        return false;
                    }
                    auto term = feeTerm(historic, present > 0 ? 0 : -present, b);
                    int d = box.lo[term.to] - box.hi[term.from];
                    if (term.from && term.to && (box.above[term.from] & bit(term.to))) {
                        d = std::max(d, int(term.to > term.from));
                    }
                    partial.add(int(i64{term.constant} + d));
                    terms[size++] = term;
                    return true;
                };
                bool okay = true;
                for (int m = P; m; m &= m - 1) {
                    int j = index(m);
                    if (!bill(j)) {
                        okay = false;
                        break;
                    }
                    if (better(-j, present)) {
                        present = -j;
                    }
                }
                if (okay) {
                    int lastPrep = P ? last(P) : 0;
                    for (int m = terminal & ~prefix(lastPrep); m; m &= m - 1) {
                        int j = index(m);
                        auto saved = partial;
                        if (bill(j)) {
                            options[a].push_back({P | bit(j), partial, terms});
                            if (!any || partial < smallest) {
                                any = true;
                                smallest = partial;
                            }
                            --size;
                            partial = saved;
                        }
                    }
                }
                if (!P) {
                    break;
                }
            }
            if (!any) {
                return;
            }
            sum.add(smallest);
            if (!couldImprove(sum)) {
                return;
            }
        }
        equalCellSolve(box, options);
    }

    bool equalTargets() {
        int R = people[ids[0]].target;
        int P = L - actors;
        if (P > 2) {
            return false;
        }
        for (int a = 1; a < actors; ++a) {
            if (people[ids[a]].target != R) {
                return false;
            }
        }
        int low = quantiles.lo[L], high = quantiles.hi[1];
        std::vector<int> cuts{low, high + 1};
        auto separate = [&](int score) {
            if (low < score + 1 && score < high) {
                cuts.push_back(score + 1);
            }
        };
        for (int r = std::max(1, R - L); r <= R; ++r) {
            separate(old[r].score);
        }
        for (int a = 0; a < actors; ++a) {
            separate(old[people[ids[a]].rank].score);
        }
        for (int p = 1; p <= L; ++p) {
            for (int s : {quantiles.lo[p] - 1, quantiles.hi[p]}) {
                separate(s);
                separate(s - 1);
            }
        }
        separate(MAX_SCORE - 1);
        std::sort(cuts.begin(), cuts.end());
        cuts.erase(std::unique(cuts.begin(), cuts.end()), cuts.end());
        for (int mask = 0; mask < bit(L); ++mask) {
            if (count(mask) != P) {
                continue;
            }
            if (P == 0) {
                equalCell(Domain{}, 0, R);
                continue;
            }
            int a = index(mask);
            int b = last(mask);
            for (std::size_t i = 1; i < cuts.size(); ++i) {
                Domain box;
                box.lo[a] = cuts[i - 1];
                box.hi[a] = cuts[i] - 1;
                if (P == 1) {
                    equalCell(box, mask, R);
                    continue;
                }
                for (std::size_t j = 1; j < cuts.size(); ++j) {
                    auto next = box;
                    next.lo[b] = cuts[j - 1];
                    next.hi[b] = cuts[j] - 1;
                    for (int first : {a, b}) {
                        int second = a + b - first;
                        auto shape = next;
                        int delta = first > second;
                        shape.lo[first] = std::max(shape.lo[first], shape.lo[second] + delta);
                        shape.hi[second] = std::min(shape.hi[second], shape.hi[first] - delta);
                        shape.above[second] |= bit(first);
                        if (shape.lo[first] <= shape.hi[first] && shape.lo[second] <= shape.hi[second]) {
                            equalCell(shape, mask, R);
                        }
                    }
                }
            }
        }
        return true;
    }

    bool gradePointCost(const Scores& scores, Cost& result) const {
        auto board=point(scores);
        for(int k=1;k<=L;++k) {
            if(scores[board.order[k-1]]<quantiles.lo[k] ||
               scores[board.order[k-1]]>quantiles.hi[k]) return false;
        }
        int extra=L-actors;
        std::array<Cost,STATES> dp,next;
        std::array<int,STATES> queue, pending;
        queue[0]=0;
        int sz=1;
        for(int a=0;a<actors;++a) {
            auto owner=people[ids[a]];
            auto original=old[owner.rank];
            int gates[G+1]{}, priorRank[G+1]{}, currRank[G+1]{};
            for(int j=1;j<=L;++j) {
                currRank[j]=board.rank(j,L);
                gates[j]=board.cutoff(owner.target,j);
                priorRank[j]=owner.rank+count(betterMask(scores,j-1,original.score));
            }
            int countLocal=0;
            std::array<int,STATES> options{};
            std::array<Cost,STATES> expense;
            for(int B=1;B<=full;++B) {
                if(count(B)>extra+1 || currRank[last(B)]>owner.target) continue;
                bool good=true;
                int previous=0;
                Cost price;
                for(int m=B;m;m&=m-1) {
                    int j=index(m);
                    if (priorRank[j]<=owner.target ||
                            (previous && board.rank(previous,j-1)<=owner.target)) {
                        good=false;
                        break;
                    }
                    int source=previous && scores[previous]>original.score?previous:0;
                    auto f=feeTerm(original,source,gates[j]);
                    price.add(f.constant+scores[f.to]-scores[f.from]);
                    if(!previous || ahead(scores[j],j,scores[previous],previous)) previous=j;
                }
                if(good && couldImprove(price)) {
                    options[countLocal++]=B;
                    expense[B]=price;
                }
            }
            if(!countLocal) return false;
            int out=0;
            std::bitset<STATES> seen;
            for(int q=0;q<sz;++q) {
                int S=queue[q];
                for(int i=0;i<countLocal;++i) {
                    int B=options[i], U=B|S;
                    if((S&B) || (a==actors-1 && U!=full) ||
                            L-count(U)<actors-1-a) continue;
                    auto value=dp[S];
                    value.add(expense[B]);
                    if(!couldImprove(value)) continue;
                    if(!seen[U] || value<next[U]) {
                        if(!seen[U]) pending[out++]=U;
                        seen[U]=true;
                        next[U]=value;
                    }
                }
            }
            if(!out) return false;
            sz=out;
            for(int q=0;q<out;++q) {
                queue[q]=pending[q];
                dp[queue[q]]=next[queue[q]];
            }
        }
        result=dp[full];
        return true;
    }

    void seedIncumbent(const Domain& start) {
        std::vector<int> pivots{1, MAX_SCORE};
        for (int a = 0; a < actors; ++a) {
            int R = people[ids[a]].target;
            for (int r = std::max(1, R - L); r <= R; ++r) {
                pivots.push_back(old[r].score + 1);
            }
        }
        std::sort(pivots.begin(), pivots.end());
        pivots.erase(std::unique(pivots.begin(), pivots.end()), pivots.end());
        std::uint64_t rng = 88172645463325252ULL;
        auto rand = [&]() {
            rng ^= rng << 13;
            rng ^= rng >> 7;
            rng ^= rng << 17;
            return rng;
        };
        Scores best{};
        Cost localBest;
        bool hasLocal = false;
        auto probe = [&](const Scores& scores) {
            Cost cost;
            if(!gradePointCost(scores,cost)) return;
            if (!hasLocal || cost < localBest) {
                localBest = cost;
                best = scores;
                hasLocal = true;
            }
            take(cost);
        };
        for (int attempt = 0; attempt < 36; ++attempt) {
            int pivot = pivots[(std::size_t(attempt) / 3) * (pivots.size() - 1) / 11];
            Scores x{};
            for (int p = 1; p <= L; ++p) {
                x[p] = std::clamp(pivot, quantiles.lo[p], quantiles.hi[p]);
            }
            if (attempt % 3 == 0) {
                std::reverse(x.begin() + 1, x.begin() + L + 1);
            } else if (attempt % 3 == 1) {
                for (int j = L; j > 1; --j) {
                    std::swap(x[j], x[1 + rand() % j]);
                }
            }
            probe(x);
        }
        for (int k = 0; k < 84 && hasLocal; ++k) {
            auto x = best;
            int j = 1 + rand() % L;
            if (k % 2) {
                std::swap(x[j], x[1 + rand() % L]);
            } else {
                x[j] = std::clamp(pivots[rand() % pivots.size()], quantiles.lo[L], quantiles.hi[1]);
            }
            probe(x);
        }
        for(int pass=0;pass<2 && hasLocal;++pass) {
            for(int j=1;j<=L;++j) {
                auto x=best;
                for(int value:pivots) {
                    if(value>=start.lo[j] && value<=start.hi[j]) {
                        x[j]=value;
                        probe(x);
                    }
                }
            }
        }
    }

    void searchFirst(Domain box) {
        if (interrupted) return;
        if (style == SearchStyle::Anchor) {
            search(box, 0);
            return;
        }
        if(style==SearchStyle::Assigned && preparation>=0 && (preparation&1) &&
                L>=8 && actors>=7 && hasAnswer) {
            std::array<int,G> order{};
            std::iota(order.begin(),order.begin()+actors,0);
            auto price=[&](int a) {
                auto person=people[ids[a]];
                if(person.rank<=person.target) return BAD;
                auto x=old[person.rank],y=old[person.target];
                return y.score-x.score+std::abs(y.time-x.time);
            };
            std::sort(order.begin(),order.begin()+actors,[&](int a,int b) {
                return price(a)<price(b);
            });
            for(int i=0;i<actors && !interrupted;++i) {
                int a=order[i],e=price(a);
                if(e>answer.bits[answer.size-1]) break;
                const auto person=people[ids[a]];
                auto local=box;
                int r=person.target-(L-2);
                if(r>0) local.hi[1]=std::min(local.hi[1],old[r].score);
                firstTime[a]=1;
                firstGate[a]=person.target;
                search(local,0);
                firstTime[a]=0;
                firstGate[a]=0;
            }
            return;
        }
        int first=-1;
        if (hasAnswer && L>=7 && actors>=5) {
            for(int a=0;a<actors;++a) {
                auto u=people[ids[a]];
                if(u.rank<=u.target) continue;
                const auto x=old[u.rank], y=old[u.target];
                int quote=y.score-x.score+std::abs(y.time-x.time);
                Cost c;
                c.add(quote);
                if(!couldImprove(c)) continue;
                if(first>=0) {first=-2;break;}
                first=a;
            }
        }
        if(first>=0) {
            firstTime[first]=1;
            firstGate[first]=people[ids[first]].target;
            if (L==actors || (preparation>=0 && !(preparation&1))) {
                box.lo[1]=std::max(box.lo[1],winnerFloor[first]);
            } else if(preparation>=0 && (preparation&1)) {
                int cutoff=people[ids[first]].target-(L-2);
                if(cutoff>0) box.hi[1]=std::min(box.hi[1],old[cutoff].score);
            }
        }
        search(box,0);
        if(first>=0) {
            firstTime[first]=0;
            firstGate[first]=0;
        }
    }

    // Label an action by (owner, occurrence), independently of its time slot.
    // A prefix is an order ideal in these disjoint chains.
    struct NamedSearch {
        GeneralSolver &g;
        int len, all, aCount;
        int who[G]{}, prior[G]{}, terminal[G]{}, target[G]{}, rank[G]{};
        int low[G]{}, high[G]{}, time[G]{}, source[G]{}, gate[G]{};
        int tighter[G]{}, earlier[G]{};
        int mult[G]{};
        int remaining = -1, completed = 0, indexCase = 0;
        bool stopped = false;
        bool limitReached() {
            if (stopped)
                return true;
            if (remaining < 0)
                return false;
            if (!remaining) {
                stopped = true;
                return true;
            }
            --remaining;
            return false;
        }
        bool run(int limit) {
            remaining = limit;
            stopped = false;
            indexCase = 0;
            distribute(0, len - aCount);
            return !stopped;
        }
        struct Box {
            int lo[G], hi[G];
        };
        struct Option {
            int action, src, cut, price;
        };
        struct Node {
            bool valid = false;
            Cost suffix;
            std::vector<Option> edges;
        };
        std::vector<Node> nodes;
        explicit NamedSearch(GeneralSolver &parent)
            : g(parent), len(g.L), all((1 << len) - 1), aCount(g.actors) {}
        static int pop(int s) { return count(s); }
        const Record &history(int r) const { return g.old[r]; }
        int at(int r) const { return history(r).score; }
        void distribute(int a, int left) {
            if (stopped)
                return;
            if (a == aCount) {
                if (!left && indexCase++ >= completed) {
                    diagram();
                    if (!stopped)
                        ++completed;
                }
                return;
            }
            for (int k = (a + 1 == aCount ? left : 0); k <= left; ++k) {
                mult[a] = 1 + k;
                distribute(a + 1, left - k);
            }
        }
        // Restrict a fee gate to its historical rank window after accounting
        // for terminal records which must outrank it, even with loose score bounds.
        template <class Emit> void offers(int s, int j, const Box &b, Emit emit) {
            int R = target[j], p = pop(s), tight = s & tighter[j];
            const auto &orig = history(rank[j]);
            int before[G], eligible[G], ns = 0;
            eligible[ns++] = -1;
            for (int z = prior[j]; z; z &= z - 1)
                eligible[ns++] = __builtin_ctz(unsigned(z));
            for (int m = s; m; m &= m - 1) {
                int k = __builtin_ctz(unsigned(m));
                before[k] = time[k] ? time[k] : 1 + pop(prior[k]);
            }
            auto list = [&](int cut, int gl, int gh, int gt, int gtMax) {
                for (int t = 0; t < ns; ++t) {
                    int u = eligible[t];
                    if (u < 0 && cut >= 0 && cut >= rank[j])
                        continue;
                    if (u < 0 && cut < 0 && gh <= orig.score)
                        continue;
                    if (u >= 0 && (cut == ~u || b.hi[u] <= orig.score || b.lo[u] > gh))
                        continue;
                    int sl = u < 0 ? orig.score : std::max(b.lo[u], orig.score + 1);
                    int sh = u < 0 ? orig.score : b.hi[u];
                    if (sl > gh)
                        continue;
                    int dt;
                    if (u < 0)
                        dt = cut >= 0 ? std::abs(history(cut).time - orig.time) : g.T + gt - orig.time;
                    else if (cut >= 0)
                        dt = g.T + before[u] - history(cut).time;
                    else {
                        int uhi = time[u] ? time[u] : p;
                        dt = std::max({1, gt - uhi, before[u] - gtMax});
                    }
                    int gap = std::max(0, gl - sh);
                    if (u < 0 && cut < 0)
                        gap = std::max(1, gap);
                    emit(Option{j, u, cut, dt + gap});
                }
            };
            for (int k = pop(tight); k <= p && k < R; ++k) {
                int r = R - k, h = at(r), must = 0, can = 0;
                for (int z = s; z; z &= z - 1) {
                    int v = __builtin_ctz(unsigned(z));
                    must += (b.lo[v] > h || (tight >> v & 1));
                    can += b.hi[v] > h;
                }
                if (must <= k && k <= can)
                    list(r, h, h, 0, 0);
            }
            int blocked = prior[j];
            for (int m = s; m; m &= m - 1) {
                int k = __builtin_ctz(unsigned(m));
                if (target[k] >= R)
                    blocked |= prior[k];
            }
            for (int m = s & ~tight & ~blocked; m; m &= m - 1) {
                int v = __builtin_ctz(unsigned(m)), ahead = 0, behind = 0;
                for (int z = s ^ (1 << v); z; z &= z - 1) {
                    int u = __builtin_ctz(unsigned(z));
                    ahead += ((tight >> u) & 1) || b.lo[u] > b.hi[v];
                    behind += b.hi[u] < b.lo[v];
                }
                int lastOld = R - 1 - ahead, firstOld = std::max(0, R - p + behind);
                if (lastOld < firstOld)
                    continue;
                int gl = std::max(b.lo[v], at(lastOld + 1) + 1),
                    gh = std::min(b.hi[v], firstOld ? at(firstOld) : MAX_SCORE);
                if (gl <= gh)
                    list(~v, gl, gh, before[v], time[v] ? time[v] : p);
            }
        }
        std::array<std::vector<std::pair<int, int>>, G> histOptions;
        int fixedHist[G]{}, minHist[G]{};
        int feeCap[G]{};

        // These first-fee candidates depend on the occurrence chains, but
        // not on a choice of their interleaving or on concrete scores.
        template<class Emit>
        void firstQuotes(int j, const Box& b, Emit emit) const {
            int R = target[j], a = who[j], spare = len - mult[a];
            const auto& self = history(rank[j]);
            for (int r = std::max(1, R - spare); r <= R && r < rank[j]; ++r) {
                int h = at(r), can = 0;
                for (int v = 0; v < len; ++v) {
                    if (who[v] != a) can += b.hi[v] > h;
                }
                if (can < R - r || g.quantiles.hi[pop(tighter[j])] <= h)
                    continue;
                emit(h - self.score + std::abs(history(r).time - self.time), h + 1, h + 1);
            }
            int historicalFloor = std::max(at(R), self.score) + 1;
            for (int v = 0; v < len; ++v) {
                if (who[v] == a || (tighter[j] >> v & 1)) continue;
                int earliest = std::max(1, target[v] - rank[v] + 2) + pop(prior[v]);
                if (earliest > spare) continue;
                int h = std::max(historicalFloor, b.lo[v]);
                if (h > b.hi[v] || g.quantiles.hi[pop(tighter[j]) + 1] < h) continue;
                emit(h - self.score + g.T + earliest - self.time, h, h + 1);
            }
        }

        bool imposeFeeCaps(Box& b) {
            std::fill(feeCap, feeCap + len, BAD);
            if (!g.hasAnswer) return true;
            int first[G], number = 0;
            for (int j = 0; j < len; ++j) {
                if (!prior[j]) first[number++] = j;
            }
            for (int pass = 0; pass < 2; ++pass) {
                int minimum[G];
                Cost mandatory;
                for (int a = 0; a < number; ++a) {
                    minimum[a] = BAD;
                    firstQuotes(first[a], b, [&](int e, int, int) { minimum[a] = std::min(minimum[a], e); });
                    if (minimum[a] == BAD) return false;
                    mandatory.add(minimum[a]);
                }
                for (int j = number; j < len; ++j) mandatory.add(1);
                if (!g.couldImprove(mandatory)) return false;

                int repeated = BAD;
                if (len > number) {
                    Cost rest;
                    for (int a = 0; a < number; ++a) rest.add(minimum[a]);
                    for (int j = number + 1; j < len; ++j) rest.add(1);
                    repeated = g.affordablePower(rest);
                }

                bool changed = false;
                auto raise = [&](int j, int x) {
                    if (b.lo[j] < x) {
                        b.lo[j] = x;
                        changed = true;
                    }
                };
                for (int a = 0; a < number; ++a) {
                    int j = first[a];
                    Cost rest;
                    for (int k = 0; k < number; ++k) if (k != a) rest.add(minimum[k]);
                    for (int k = number; k < len; ++k) rest.add(1);
                    feeCap[j] = std::min(feeCap[j], g.affordablePower(rest));
                    int safe = MAX_SCORE + 2, personal = MAX_SCORE + 2;
                    firstQuotes(j, b, [&](int e, int y, int z) {
                        if (e <= feeCap[j]) {
                            safe = std::min(safe, y);
                            personal = std::min(personal, z);
                        }
                    });
                    if (safe > MAX_SCORE) return false;
                    for (int z = tighter[j]; z; z &= z - 1) {
                        int v = __builtin_ctz(unsigned(z));
                        raise(v, who[v] == a ? personal : safe);
                    }
                    int last = j + mult[a];
                    for (int v = j + 1; v < last; ++v) feeCap[v] = std::min(feeCap[v], repeated);
                    if (mult[a] > 1) {
                        int R = target[j];
                        const auto& self = history(rank[j]);
                        int oldSource = std::max(1, at(R) + 1 - self.score) + g.T + 1 - self.time;
                        for (int r = std::max(1, R - len + mult[a]); r <= R && r < rank[j]; ++r)
                            oldSource = std::min(oldSource, at(r) - self.score + std::abs(history(r).time - self.time));
                        if (oldSource <= repeated) continue;
                        // The second fee cannot use the historical best.
                        // Its only preceding record from this owner is j.
                        int least = MAX_SCORE + 2;
                        int t = g.T + std::max(1, R - rank[j] + 2);
                        for (int r = std::max(1, R - len + 1); r <= R; ++r) {
                            int dt = t - history(r).time;
                            if (dt <= repeated) least = std::min(least, at(r) - repeated + dt);
                        }
                        for (int v = 0; v < len; ++v) {
                            if (who[v] != a && !(tighter[j] >> v & 1)) {
                                least = std::min(least, std::max(at(R) + 1, b.lo[v]) - repeated + 1);
                            }
                        }
                        raise(j, std::max(at(rank[j]) + 1, least));
                    }
                }
                for (int j = 0; j < len; ++j) if (b.lo[j] > b.hi[j]) return false;
                if (!changed) break;
            }
            return true;
        }

        bool historyOnly(const Box &b) {
            if (len != aCount || !g.hasAnswer)
                return false;
            int cheapestNew[G];
            Cost base;
            for (int j = 0; j < len; ++j) {
                const auto &own = history(rank[j]);
                int R = target[j], best = BAD;
                auto &options = histOptions[j];
                options.clear();
                for (int r = std::max(1, R - len + 1); r <= R && r < rank[j]; ++r) {
                    int exponent = at(r) - own.score + std::abs(history(r).time - own.time);
                    options.push_back({exponent, r});
                    best = std::min(best, exponent);
                }
                std::sort(options.begin(), options.end());
                int add = BAD;
                for (int v = 0; v < len; ++v)
                    if (!(tighter[j] >> v & 1)) {
                        int score = std::max({b.lo[v], at(R) + 1, own.score + 1});
                        if (score <= b.hi[v])
                            add = std::min(add, score - own.score + g.T + 1 - own.time);
                    }
                cheapestNew[j] = add;
                minHist[j] = std::min(add, best);
                if (minHist[j] == BAD)
                    return true;
                base.add(minHist[j]);
            }
            if (!g.couldImprove(base))
                return true;
            for (int j = 0; j < len; ++j)
                if (cheapestNew[j] != BAD) {
                    Cost trial;
                    for (int k = 0; k < len; ++k)
                        trial.add(k == j ? cheapestNew[j] : minHist[k]);
                    if (g.couldImprove(trial))
                        return false;
                }
            std::fill(fixedHist, fixedHist + len, 0);
            histAssign(0, b, Cost{});
            return true;
        }
        void histAssign(int mask, const Box &b, Cost paid) {
            if (limitReached())
                return;
            if (mask == all) {
                histTimes(0, b, paid);
                return;
            }
            int choice = -1, fewest = INT32_MAX;
            Cost optimistic = paid;
            for (int m = all ^ mask; m; m &= m - 1) {
                int j = __builtin_ctz(unsigned(m)), best = BAD, countOptions = 0;
                for (auto [e, r] : histOptions[j]) {
                    if (!histFree(j, r, mask))
                        continue;
                    if (best == BAD)
                        best = e;
                    Cost trial = paid;
                    trial.add(e);
                    for (int rest = (all ^ mask) ^ (1 << j); rest; rest &= rest - 1)
                        trial.add(minHist[__builtin_ctz(unsigned(rest))]);
                    if (!g.couldImprove(trial))
                        break;
                    ++countOptions;
                }
                if (!countOptions)
                    return;
                optimistic.add(best);
                if (countOptions < fewest || (countOptions == fewest && best > minHist[choice])) {
                    choice = j;
                    fewest = countOptions;
                }
            }
            if (!g.couldImprove(optimistic))
                return;
            int j = choice, rest = (all ^ mask) ^ (1 << j);
            for (auto [e, r] : histOptions[j]) {
                if (!histFree(j, r, mask))
                    continue;
                Cost trial = paid;
                trial.add(e);
                Cost lower = trial;
                for (int m = rest; m; m &= m - 1)
                    lower.add(minHist[__builtin_ctz(unsigned(m))]);
                if (!g.couldImprove(lower))
                    break;
                auto nb = b;
                bool ok = true;
                for (int m = tighter[j]; m; m &= m - 1) {
                    int k = __builtin_ctz(unsigned(m));
                    nb.lo[k] = std::max(nb.lo[k], at(r) + 1);
                    ok &= nb.lo[k] <= nb.hi[k];
                }
                if (!ok)
                    continue;
                fixedHist[j] = r;
                histAssign(mask | (1 << j), nb, trial);
                fixedHist[j] = 0;
            }
        }
        bool histFree(int j, int r, int assigned) const {
            for (int m = assigned; m; m &= m - 1) {
                int k = __builtin_ctz(unsigned(m));
                if (target[k] == target[j] && fixedHist[k] == r)
                    return false;
            }
            return true;
        }
        bool historyConsistent(int s, int j, int r, const Box &b) const {
            int h = at(r), want = target[j] - r;
            for (int m = s; m; m &= m - 1) {
                int k = __builtin_ctz(unsigned(m)), q = gate[k];
                if (q < 0)
                    continue;
                int oldCount = target[k] - q, added = s ^ earlier[k];
                if (at(q) >= h) {
                    int forced = 0;
                    for (int z = added; z; z &= z - 1) {
                        int v = __builtin_ctz(unsigned(z));
                        forced += b.lo[v] > h;
                    }
                    if (oldCount + forced > want)
                        return false;
                }
                if (at(q) <= h) {
                    int possible = 0;
                    for (int z = added; z; z &= z - 1) {
                        int v = __builtin_ctz(unsigned(z));
                        possible += b.hi[v] > h;
                    }
                    if (oldCount + possible < want)
                        return false;
                }
            }
            return true;
        }
        void histTimes(int s, const Box &b, const Cost &value) {
            if (limitReached())
                return;
            if (!g.couldImprove(value))
                return;
            if (s == all) {
                leaf(b, value);
                return;
            }
            for (int m = all ^ s; m; m &= m - 1) {
                int j = __builtin_ctz(unsigned(m)), r = fixedHist[j];
                bool first = true;
                for (int z = all ^ s; z; z &= z - 1) {
                    int k = __builtin_ctz(unsigned(z));
                    if (target[j] == target[k] && fixedHist[k] > r) {
                        first = false;
                        break;
                    }
                }
                if (!first || target[j] - r > pop(s))
                    continue;
                auto nb = b;
                if (!narrow(nb, s, j, -1, r) || !historyConsistent(s, j, r, nb))
                    continue;
                time[j] = pop(s) + 1;
                earlier[j] = s;
                source[j] = -1;
                gate[j] = r;
                histTimes(s | (1 << j), nb, value);
                time[j] = 0;
            }
        }
        void diagram() {
            int pos = 0;
            for (int a = 0; a < aCount; ++a) {
                int mask = 0;
                const auto u = g.people[g.ids[a]];
                for (int t = 0; t < mult[a]; ++t, ++pos) {
                    who[pos] = a;
                    prior[pos] = mask;
                    mask |= 1 << pos;
                    target[pos] = u.target;
                    rank[pos] = u.rank;
                    terminal[pos] = (t + 1 == mult[a]);
                    low[pos] = std::max(g.quantiles.lo[len], terminal[pos] ? g.winnerFloor[a] : 1);
                    int r = u.target - (len - 2);
                    high[pos] = std::min(g.quantiles.hi[1], !terminal[pos] && r > 0 ? at(r) : MAX_SCORE);
                    if (low[pos] > high[pos])
                        return;
                }
            }
            std::fill(time, time + len, 0);
            Box b{};
            for (int j = 0; j < len; ++j) {
                tighter[j] = 0;
                for (int v = 0; v < len; ++v)
                    if (terminal[v] && target[v] <= target[j])
                        tighter[j] |= 1 << v;
                b.lo[j] = low[j];
                b.hi[j] = high[j];
            }
            common = true;
            for (int k = 1; k < len; ++k)
                common &= target[k] == target[0];
            auto certified = b;
            if (!imposeFeeCaps(certified)) return;
            if (common) {
                queue.clear();
                visited.clear();
                fixedWinners = g.quantiles.hi[len] == g.quantiles.hi[1];
                if (fixedWinners)
                    for (int j = 0; j < len; ++j) {
                        if (terminal[j])
                            b.lo[j] = b.hi[j] = g.quantiles.hi[1];
                        else
                            b.hi[j] = std::min(b.hi[j], g.quantiles.hi[1] - 1);
                    }
                Zone z;
                z.box = b;
                if (queueBound(0, 0, target[0], b).valid)
                    queueWalk(0, 0, target[0], z, Cost{});
                return;
            }
            if (historyOnly(b))
                return;
            nodes.clear();
            nodes.resize(all + 1);
            nodes[all].valid = true;
            for (int s = all - 1; s >= 0; --s) {
                auto &n = nodes[s];
                for (int m = all ^ s; m; m &= m - 1) {
                    int j = __builtin_ctz(unsigned(m)), next = s | (1 << j);
                    if ((prior[j] & s) != prior[j] || !nodes[next].valid)
                        continue;
                    if (!prior[j] && rank[j] + pop(s) <= target[j])
                        continue;
                    int best = BAD;
                    offers(s, j, b, [&](Option o) { best = std::min(best, o.price); });
                    if (best == BAD)
                        continue;
                    Cost value = nodes[next].suffix;
                    value.add(best);
                    if (!n.valid || value < n.suffix) {
                        n.valid = true;
                        n.suffix = value;
                    }
                    n.edges.push_back({j, 0, 0, best});
                }
                if (n.valid && !g.couldImprove(n.suffix)) {
                    n.valid = false;
                    continue;
                }
                std::sort(n.edges.begin(), n.edges.end(), [&](Option x, Option y) {
                    Cost a = nodes[s | (1 << x.action)].suffix, bval = nodes[s | (1 << y.action)].suffix;
                    a.add(x.price);
                    bval.add(y.price);
                    return a < bval;
                });
            }
            if (nodes[0].valid) {
                Zone z;
                z.box = b;
                exactWalk(0, z, Cost{});
            }
        }
        bool narrow(Box &b, int s, int j, int u, int cut) {
            const auto &orig = history(rank[j]);
            if (u < 0) {
                for (int m = prior[j]; m; m &= m - 1) {
                    int v = __builtin_ctz(unsigned(m));
                    b.hi[v] = std::min(b.hi[v], orig.score);
                }
            } else
                b.lo[u] = std::max(b.lo[u], orig.score + 1);
            if (cut >= 0) {
                int h = at(cut), k = target[j] - cut, forced = 0, possible = 0;
                for (int m = tighter[j]; m; m &= m - 1) {
                    int v = __builtin_ctz(unsigned(m));
                    b.lo[v] = std::max(b.lo[v], h + 1);
                }
                if (u >= 0)
                    b.hi[u] = std::min(b.hi[u], h);
                for (int m = s; m; m &= m - 1) {
                    int v = __builtin_ctz(unsigned(m));
                    if (b.lo[v] > h)
                        forced |= 1 << v;
                    if (b.hi[v] > h)
                        possible |= 1 << v;
                }
                if (pop(forced) > k || pop(possible) < k)
                    return false;
                if (pop(forced) == k)
                    for (int m = s & ~forced; m; m &= m - 1) {
                        int v = __builtin_ctz(unsigned(m));
                        b.hi[v] = std::min(b.hi[v], h);
                    }
                if (pop(possible) == k)
                    for (int m = possible; m; m &= m - 1) {
                        int v = __builtin_ctz(unsigned(m));
                        b.lo[v] = std::max(b.lo[v], h + 1);
                    }
            } else {
                int v = ~cut;
                if (u < 0)
                    b.lo[v] = std::max(b.lo[v], orig.score + 1);
                for (int m = tighter[j]; m; m &= m - 1) {
                    int k = __builtin_ctz(unsigned(m));
                    if (k == v)
                        return false;
                    int strict = (time[k] == 0 || time[k] > time[v]);
                    b.lo[k] = std::max(b.lo[k], b.lo[v] + strict);
                    b.hi[v] = std::min(b.hi[v], b.hi[k] - strict);
                }
                int mx = target[j] - pop(s & tighter[j]);
                if (mx <= 0)
                    return false;
                b.lo[v] = std::max(b.lo[v], at(mx) + 1);
                int mn = target[j] - pop(s);
                if (mn > 0)
                    b.hi[v] = std::min(b.hi[v], at(mn));
            }
            for (int v = 0; v < len; ++v)
                if (b.lo[v] > b.hi[v])
                    return false;
            return true;
        }
        int chosenPrice[G]{};
        struct Card {
            int mask, score, k;
        };
        struct Shape {
            Box box;
            int stronger[G]{};
            std::vector<Card> cards;
            std::vector<Term> fees;
        };
        void leaf(const Box &box, const Cost &lb) {
            Shape sh;
            sh.box = box;
            for (int j = 0; j < len; ++j) {
                int u = source[j], v = gate[j];
                const auto &orig = history(rank[j]);
                if (u >= 0)
                    sh.stronger[u] |= prior[j] ^ (1 << u);
                if (v > 0)
                    sh.cards.push_back({earlier[j], at(v), target[j] - v});
                else {
                    int w = ~v;
                    if (u >= 0)
                        sh.stronger[w] |= 1 << u;
                    for (int z = tighter[j]; z; z &= z - 1)
                        sh.stronger[__builtin_ctz(unsigned(z))] |= 1 << w;
                }
                int ut = u < 0 ? orig.time : g.T + time[u], vt = v > 0 ? history(v).time : g.T + time[~v];
                sh.fees.push_back({u + 1, v > 0 ? 0 : 1 + (~v),
                                   (v > 0 ? at(v) : 0) - (u < 0 ? orig.score : 0) + std::abs(vt - ut)});
            }
            Zone z;
            z.box = box;
            std::copy_n(sh.stronger, len, z.below);
            zoneSearch(sh, z, lb);
        }
        struct Zone {
            Box box;
            int below[G]{};
        };
        struct Test {
            int pivot, other, threshold;
            bool higher;
        };
        bool above(const Zone &z, int j, int k) const {
            return (z.below[j] >> k & 1) || z.box.lo[j] > z.box.hi[k] ||
                   (z.box.lo[j] == z.box.hi[k] && time[j] < time[k]);
        }
        bool boxClosure(Zone &z) const {
            for (int k = 0; k < len; ++k)
                for (int j = 0; j < len; ++j)
                    if (z.below[j] >> k & 1)
                        z.below[j] |= z.below[k];
            for (int j = 0; j < len; ++j)
                if (z.below[j] >> j & 1)
                    return false;
            for (int step = 0; step < len; ++step) {
                bool changed = false;
                for (int j = 0; j < len; ++j)
                    for (int m = z.below[j]; m; m &= m - 1) {
                        int k = __builtin_ctz(unsigned(m)),
                            d = (time[j] && time[k]) ? time[j] > time[k]
                                                     : (!time[j] && (time[k] || (prior[j] >> k & 1)));
                        if (z.box.lo[j] < z.box.lo[k] + d) {
                            z.box.lo[j] = z.box.lo[k] + d;
                            changed = true;
                        }
                        if (z.box.hi[k] > z.box.hi[j] - d) {
                            z.box.hi[k] = z.box.hi[j] - d;
                            changed = true;
                        }
                    }
                if (!changed)
                    break;
            }
            for (int k = 0; k < len; ++k)
                if (z.box.lo[k] > z.box.hi[k])
                    return false;
            return true;
        }
        void zoneSearch(const Shape &sh, Zone z, const Cost &pathBound) {
            if (limitReached())
                return;
            if (!g.couldImprove(pathBound) || !boxClosure(z))
                return;
            auto &b = z.box;
            Test split{-1, 0, 0, false};
            bool change = false, ok = true;
            auto cardinal = [&](int mask, int h, int lowCount, int highCount) {
                int must = 0, may = 0;
                for (int m = mask; m; m &= m - 1) {
                    int j = __builtin_ctz(unsigned(m));
                    if (b.lo[j] > h)
                        must |= 1 << j;
                    if (b.hi[j] > h)
                        may |= 1 << j;
                }
                if (pop(must) > highCount || pop(may) < lowCount) {
                    ok = false;
                    return;
                }
                int open = may ^ must;
                if (!open)
                    return;
                if (pop(must) == highCount || pop(may) == lowCount) {
                    bool raise = pop(may) == lowCount;
                    for (int m = open; m; m &= m - 1) {
                        int j = __builtin_ctz(unsigned(m));
                        if (raise)
                            b.lo[j] = h + 1;
                        else
                            b.hi[j] = h;
                    }
                    change = true;
                } else if (split.pivot < 0 && (pop(must) < lowCount || pop(may) > highCount)) {
                    split = {__builtin_ctz(unsigned(open)), -1, h, pop(must) < lowCount};
                }
            };
            auto rankConstraint = [&](int j, int mask, int R, bool exact) {
                int must = 0, may = 0;
                for (int m = mask; m; m &= m - 1) {
                    int k = __builtin_ctz(unsigned(m));
                    if (above(z, k, j))
                        must |= 1 << k;
                    if (!above(z, j, k))
                        may |= 1 << k;
                }
                int mx = R - pop(must), mn = R - 1 - pop(may);
                if (mx <= 0) {
                    ok = false;
                    return;
                }
                if (b.lo[j] <= at(mx)) {
                    b.lo[j] = at(mx) + 1;
                    change = true;
                }
                if (exact && mn > 0 && b.hi[j] > at(mn)) {
                    b.hi[j] = at(mn);
                    change = true;
                }
                if (b.lo[j] > b.hi[j]) {
                    ok = false;
                    return;
                }
                int open = may ^ must;
                if (!open)
                    return;
                int least = g.oldAhead(b.hi[j]) + 1 + pop(must);
                int worst = g.oldAhead(b.lo[j]) + 1 + pop(may);
                if (least > R || (exact && worst < R)) {
                    ok = false;
                    return;
                }
                if (least == R || (exact && worst == R)) {
                    for (int m = open; m; m &= m - 1) {
                        int k = __builtin_ctz(unsigned(m));
                        if (least == R)
                            z.below[j] |= 1 << k;
                        else
                            z.below[k] |= 1 << j;
                    }
                    change = true;
                } else if (split.pivot < 0 && (worst > R || (exact && least < R))) {
                    split = {j, __builtin_ctz(unsigned(open)), 0, true};
                }
            };
            for (auto c : sh.cards)
                cardinal(c.mask, c.score, c.k, c.k);
            for (int j = 0; j < len && ok && !common; ++j) {
                if (terminal[j])
                    rankConstraint(j, all ^ (1 << j), target[j], false);
                if (gate[j] < 0)
                    rankConstraint(~gate[j], earlier[j] ^ (1 << (~gate[j])), target[j], true);
            }
            for (int r = 1; r <= len && ok; ++r) {
                if (g.quantiles.hi[r] < MAX_SCORE)
                    cardinal(all, g.quantiles.hi[r], 0, r - 1);
                if (g.quantiles.lo[r] > 1)
                    cardinal(all, g.quantiles.lo[r] - 1, r, len);
            }
            if (!ok)
                return;
            if (change) {
                zoneSearch(sh, z, pathBound);
                return;
            }
            Cost lower;
            bool decreasing = false;
            for (auto f : sh.fees) {
                int delta = (f.to ? b.lo[f.to - 1] : 0) - (f.from ? b.hi[f.from - 1] : 0);
                if (f.from && f.to)
                    delta = std::max(delta, int(time[f.to - 1] > time[f.from - 1]));
                lower.add(f.constant + delta);
                decreasing |= f.from && b.lo[f.from - 1] < b.hi[f.from - 1];
            }
            if (!g.couldImprove(lower))
                return;
            if (split.pivot >= 0) {
                for (bool side : {split.higher, !split.higher}) {
                    auto child = z;
                    int j = split.pivot, k = split.other;
                    if (k < 0) {
                        if (side)
                            child.box.lo[j] = split.threshold + 1;
                        else
                            child.box.hi[j] = split.threshold;
                    } else if (side)
                        child.below[j] |= 1 << k;
                    else
                        child.below[k] |= 1 << j;
                    zoneSearch(sh, child, lower);
                }
                return;
            }
            std::vector<int> x(len + 1);
            std::copy_n(b.lo, len, x.begin() + 1);
            g.take(evaluate<G>(sh.fees, x));
            if (!decreasing || !g.couldImprove(lower))
                return;
            int neg = 0;
            for (auto f : sh.fees)
                if (f.from && b.lo[f.from - 1] < b.hi[f.from - 1])
                    neg |= 1 << (f.from - 1);
            if (!(neg & (neg - 1))) {
                int j = __builtin_ctz(unsigned(neg));
                int gap[G];
                std::fill(gap, gap + len, -100);
                gap[j] = 0;
                for (int d = 0; d < len; ++d) {
                    bool more = false;
                    for (int k = 0; k < len; ++k)
                        for (int m = z.below[k]; m; m &= m - 1) {
                            int t = __builtin_ctz(unsigned(m)), v = gap[t] + int(time[k] > time[t]);
                            if (gap[t] >= 0 && v > gap[k]) {
                                gap[k] = v;
                                more = true;
                            }
                        }
                    if (!more)
                        break;
                }
                auto value = [&](int y) {
                    for (int k = 0; k < len; ++k)
                        x[k + 1] = gap[k] < 0 ? b.lo[k] : std::max(b.lo[k], y + gap[k]);
                    return evaluate<G>(sh.fees, x);
                };
                int left = b.lo[j], right = b.hi[j];
                while (left < right) {
                    int mid = left + (right - left) / 2;
                    if (value(mid + 1) < value(mid))
                        left = mid + 1;
                    else
                        right = mid;
                }
                g.take(value(left));
                return;
            }
            std::vector<Bound> limits;
            for (int j = 0; j < len; ++j) {
                limits.push_back({0, j + 1, b.hi[j]});
                limits.push_back({j + 1, 0, -b.lo[j]});
                for (int m = z.below[j]; m; m &= m - 1) {
                    int k = __builtin_ctz(unsigned(m));
                    limits.push_back({j + 1, k + 1, -int(time[j] > time[k])});
                }
            }
            auto best = minimizeScores<4>(x, limits, sh.fees);
            g.take(evaluate<G>(sh.fees, best));
        }
        // At a common deadline, top R consists of a prefix of history and a set B
        // of appended records. A promotion evicts precisely the present cutoff.
        struct QueueStep {
            int action, members, nextGate;
            bool enters;
        };
        struct QueueNode {
            Cost lower, conditional;
            int stamp = 0;
            bool valid = false, reachable = false;
            std::vector<QueueStep> steps;
        };
        int generation = 0;
        std::unordered_map<unsigned, QueueNode> queue;
        struct KeyHash {
            std::size_t operator()(const std::vector<int> &v) const {
                std::size_t h = 1469598103934665603ULL;
                for (int a : v) {
                    h ^= unsigned(a);
                    h *= 1099511628211ULL;
                }
                return h;
            }
        };
        std::unordered_map<std::vector<int>, Cost, KeyHash> visited;
        bool common = false, fixedWinners = false;
        bool dominated(int s, int members, int cut, const Zone &z) {
            if (!fixedWinners || pop(s) < 3 || pop(s) >= len)
                return false;
            std::vector<int> key;
            key.reserve(8 * len);
            key.push_back(queueKey(s, members, cut));
            for (int j = 0; j < len; ++j)
                if (!terminal[j]) {
                    key.push_back(time[j]);
                    key.push_back(z.box.lo[j]);
                    key.push_back(z.box.hi[j]);
                    key.push_back(z.below[j]);
                }
            std::array<std::pair<int, int>, G> vars;
            int used = 0;
            Cost constant;
            for (int m = s; m; m &= m - 1) {
                int j = __builtin_ctz(unsigned(m)), u = source[j], v = gate[j] < 0 ? ~gate[j] : -1;
                int st = u < 0 ? history(rank[j]).time : g.T + time[u];
                int gt = v < 0 ? history(gate[j]).time : g.T + time[v];
                int c = (v < 0 ? at(gate[j]) : 0) - (u < 0 ? at(rank[j]) : 0) + std::abs(gt - st);
                if (u >= 0 && z.box.lo[u] == z.box.hi[u]) {
                    c -= z.box.lo[u];
                    u = -1;
                }
                if (v >= 0 && z.box.lo[v] == z.box.hi[v]) {
                    c += z.box.lo[v];
                    v = -1;
                }
                if (u == v)
                    constant.add(c);
                else
                    vars[used++] = {(u + 1) * 16 + v + 1, c};
            }
            std::sort(vars.begin(), vars.begin() + used);
            for (int j = 0; j < used; ++j) {
                key.push_back(vars[j].first);
                key.push_back(vars[j].second);
            }
            auto it = visited.find(key);
            if (it != visited.end()) {
                if (!(constant < it->second))
                    return true;
                it->second = constant;
            } else if (visited.size() < 24000)
                visited.emplace(std::move(key), constant);
            return false;
        }
        template <class F> void sources(int j, int cut, const Box &b, int q, bool known, F f) const {
            int gl = cut < 0 ? std::max(b.lo[~cut], at(q + 1) + 1) : at(cut);
            int gh = cut < 0 ? std::min(b.hi[~cut], q ? at(q) : MAX_SCORE) : at(cut);
            if (gl > gh)
                return;
            for (int choices = prior[j], u = -1;;) {
                int sl = u < 0 ? at(rank[j]) : std::max(b.lo[u], at(rank[j]) + 1);
                int sh = u < 0 ? sl : b.hi[u];
                if (sl <= sh && sl <= gh && (u >= 0 || cut > 0 || sl < gh) &&
                    (u >= 0 || cut < 0 || rank[j] > cut)) {
                    int dt;
                    if (cut > 0 && u < 0)
                        dt = std::abs(history(cut).time - history(rank[j]).time);
                    else if (cut < 0 && u < 0)
                        dt = g.T + (known ? time[~cut] : 1 + pop(prior[~cut])) - history(rank[j]).time;
                    else if (cut > 0)
                        dt = g.T + (known ? time[u] : 1 + pop(prior[u])) - history(cut).time;
                    else
                        dt = known ? std::abs(time[~cut] - time[u]) : 1;
                    f(u, dt + std::max(int(cut < 0 && u < 0), gl - sh));
                }
                if (!choices)
                    break;
                u = __builtin_ctz(unsigned(choices));
                choices &= choices - 1;
            }
        }
        unsigned queueKey(int s, int members, int cut) const {
            return ((unsigned(s) << len | members) << 4) | unsigned(cut < 0 ? 1 + (~cut) : 0);
        }
        QueueNode &queueBound(int s, int members, int cut, const Box &b) {
            unsigned key = queueKey(s, members, cut);
            auto [it, inserted] = queue.try_emplace(key);
            auto &node = it->second;
            if (!inserted)
                return node;
            if (s == all) {
                node.valid = true;
                return node;
            }
            int R = target[0], q = R - pop(members);
            if (cut > 0 && q <= 0)
                return node;
            for (int choices = all ^ s; choices; choices &= choices - 1) {
                int j = __builtin_ctz(unsigned(choices));
                if ((prior[j] & s) != prior[j] || (prior[j] & members) || rank[j] <= q)
                    continue;
                int best = BAD;
                sources(j, cut, b, q, false, [&](int, int e) { best = std::min(best, e); });
                if (best == BAD)
                    continue;
                auto accept = [&](int m, int w, bool enters) {
                    auto &next = queueBound(s | (1 << j), m, w, b);
                    if (!next.valid)
                        return;
                    auto cost = next.lower;
                    cost.add(best);
                    if (!g.couldImprove(cost))
                        return;
                    if (!node.valid || cost < node.lower) {
                        node.valid = true;
                        node.lower = cost;
                    }
                    node.steps.push_back({j, m, w, enters});
                };
                if (!terminal[j])
                    accept(members, cut, false);
                int after = (members & ~(cut < 0 ? (1 << ~cut) : 0)) | (1 << j);
                int remaining = R - pop(after);
                if (remaining < 0)
                    continue;
                if ((s | (1 << j)) == all) {
                    if (pop(after) == aCount)
                        accept(after, 0, true);
                    continue;
                }
                if (remaining > 0)
                    accept(after, remaining, true);
                for (int z = after; z; z &= z - 1) {
                    int k = __builtin_ctz(unsigned(z));
                    if (!terminal[k])
                        accept(after, ~k, true);
                }
            }
            return node;
        }
        bool priceZone(Zone &z, int mask, const Cost &suffix, Cost &paid) {
            if (!mask)
                return true;
            int lower[G], u[G], v[G], c[G];
            for (int m = mask; m; m &= m - 1) {
                int j = __builtin_ctz(unsigned(m));
                u[j] = source[j];
                v[j] = gate[j] < 0 ? ~gate[j] : -1;
                int st = u[j] < 0 ? history(rank[j]).time : g.T + time[u[j]];
                int gt = v[j] < 0 ? history(gate[j]).time : g.T + time[v[j]];
                c[j] = (v[j] < 0 ? at(gate[j]) : 0) - (u[j] < 0 ? at(rank[j]) : 0) + std::abs(st - gt);
            }
            for (int pass = 0; pass < 3; ++pass) {
                paid = Cost{};
                for (int m = mask; m; m &= m - 1) {
                    int j = __builtin_ctz(unsigned(m));
                    int d = (v[j] < 0 ? 0 : z.box.lo[v[j]]) - (u[j] < 0 ? 0 : z.box.hi[u[j]]);
                    if (u[j] >= 0 && v[j] >= 0)
                        d = std::max(d, int(time[v[j]] > time[u[j]]));
                    lower[j] = std::max(chosenPrice[j], c[j] + d);
                    paid.add(lower[j]);
                }
                auto total = paid;
                total.add(suffix);
                if (!g.couldImprove(total))
                    return false;
                if (!g.hasAnswer)
                    break;
                bool changed = false;
                for (int m = mask; m; m &= m - 1) {
                    int j = __builtin_ctz(unsigned(m));
                    if (v[j] < 0 && u[j] < 0)
                        continue;
                    Cost other = suffix;
                    for (int t = mask ^ (1 << j); t; t &= t - 1)
                        other.add(lower[__builtin_ctz(unsigned(t))]);
                    int cap = g.affordablePower(other);
                    i64 diff = i64(cap) - c[j];
                    if (v[j] >= 0) {
                        i64 hi = (u[j] < 0 ? 0 : z.box.hi[u[j]]) + diff;
                        if (hi < z.box.lo[v[j]])
                            return false;
                        if (hi < z.box.hi[v[j]]) {
                            z.box.hi[v[j]] = int(hi);
                            changed = true;
                        }
                    }
                    if (u[j] >= 0) {
                        i64 lo = (v[j] < 0 ? 0 : z.box.lo[v[j]]) - diff;
                        if (lo > z.box.hi[u[j]])
                            return false;
                        if (lo > z.box.lo[u[j]]) {
                            z.box.lo[u[j]] = int(lo);
                            changed = true;
                        }
                    }
                }
                if (!changed || pass == 2)
                    break;
                if (!boxClosure(z))
                    return false;
            }
            return true;
        }
        bool conditionalQueue(int base, int s, int members, int cut, const Box &b, Cost &out) {
            if (s == all) {
                out = Cost{};
                return true;
            }
            auto &node = queue.at(queueKey(s, members, cut));
            if (node.stamp == generation) {
                out = node.conditional;
                return node.reachable;
            }
            node.stamp = generation;
            node.reachable = false;
            int q = target[0] - pop(members);
            int gl = cut > 0 ? at(cut) : std::max(at(q + 1) + 1, b.lo[~cut]);
            int gh = cut > 0 ? at(cut) : std::min(q ? at(q) : MAX_SCORE, b.hi[~cut]);
            if (gl > gh)
                return false;
            int price[G];
            std::fill(price, price + len, BAD);
            auto earliest = [&](int j) {
                return (base >> j & 1) ? time[j] : pop(base) + 1 + pop(prior[j] & ~base);
            };
            for (auto e : node.steps) {
                int j = e.action;
                if (price[j] == BAD) {
                    int origin = at(rank[j]);
                    bool canOld = true;
                    int gt = cut > 0 ? history(cut).time : g.T + earliest(~cut);
                    for (int m = prior[j]; m; m &= m - 1) {
                        int u = __builtin_ctz(unsigned(m));
                        if (b.lo[u] > origin)
                            canOld = false;
                        if (b.hi[u] <= origin || b.lo[u] > gh)
                            continue;
                        int dt = cut > 0 ? g.T + earliest(u) - gt
                                         : ((base >> u & 1) && (base >> ~cut & 1)
                                                ? std::abs(time[u] - time[~cut])
                                                : ((base >> u & 1)      ? earliest(~cut) - time[u]
                                                   : (base >> ~cut & 1) ? earliest(u) - time[~cut]
                                                                        : 1));
                        price[j] = std::min(price[j], std::max(0, gl - b.hi[u]) + dt);
                    }
                    if (canOld && (cut > 0 ? rank[j] > cut : gh > origin))
                        price[j] = std::min(price[j], std::max(int(cut < 0), gl - origin) +
                                                          std::abs(gt - history(rank[j]).time));
                }
                if (price[j] == BAD)
                    continue;
                if (e.enters ? b.hi[j] <= gl : b.lo[j] > gh)
                    continue;
                Cost tail;
                if (!conditionalQueue(base, s | (1 << j), e.members, e.nextGate, b, tail))
                    continue;
                tail.add(price[j]);
                if (!node.reachable || tail < node.conditional) {
                    node.reachable = true;
                    node.conditional = tail;
                }
            }
            out = node.conditional;
            return node.reachable;
        }
        void queueWalk(int s, int members, int cut, Zone zone, Cost paid) {
            if (limitReached())
                return;
            auto &node = queue.at(queueKey(s, members, cut));
            if (!node.valid)
                return;
            if (!priceZone(zone, s, node.lower, paid) || dominated(s, members, cut, zone))
                return;
            if (s && s != all && g.hasAnswer) {
                ++generation;
                Cost stronger;
                if (!conditionalQueue(s, s, members, cut, zone.box, stronger))
                    return;
                stronger.add(paid);
                if (!g.couldImprove(stronger))
                    return;
            }
            if (s == all) {
                Shape sh;
                sh.box = zone.box;
                for (int j = 0; j < len; ++j) {
                    int u = source[j], v = gate[j];
                    auto own = history(rank[j]);
                    int st = u < 0 ? own.time : g.T + time[u], gt = v > 0 ? history(v).time : g.T + time[~v];
                    sh.fees.push_back({u + 1, v > 0 ? 0 : 1 + (~v),
                                       (v > 0 ? at(v) : 0) - (u < 0 ? own.score : 0) + std::abs(gt - st)});
                }
                zoneSearch(sh, zone, paid);
                return;
            }
            int q = target[0] - pop(members);
            if (cut > 0) {
                for (int z = s; z; z &= z - 1) {
                    int k = __builtin_ctz(unsigned(z));
                    if (members >> k & 1)
                        zone.box.lo[k] = std::max(zone.box.lo[k], at(cut) + 1);
                    else
                        zone.box.hi[k] = std::min(zone.box.hi[k], at(cut));
                }
            } else {
                int v = ~cut;
                zone.box.lo[v] = std::max(zone.box.lo[v], at(q + 1) + 1);
                if (q)
                    zone.box.hi[v] = std::min(zone.box.hi[v], at(q));
                for (int z = members ^ (1 << v); z; z &= z - 1)
                    zone.below[__builtin_ctz(unsigned(z))] |= 1 << v;
                zone.below[v] |= s ^ members;
            }
            // Known chronological ties only involve actions which have already happened.
            if (!boxClosure(zone))
                return;
            for (auto step : node.steps) {
                int j = step.action, next = s | (1 << j);
                const auto &tail = queue.at(queueKey(next, step.members, step.nextGate)).lower;
                auto base = paid;
                base.add(tail);
                if (!g.couldImprove(base))
                    continue;
                Zone b = zone;
                if (cut > 0) {
                    if (step.enters)
                        b.box.lo[j] = std::max(b.box.lo[j], at(cut) + 1);
                    else
                        b.box.hi[j] = std::min(b.box.hi[j], at(cut));
                } else if (step.enters)
                    b.below[j] |= 1 << ~cut;
                else
                    b.below[~cut] |= 1 << j;
                time[j] = pop(s) + 1;
                gate[j] = cut;
                earlier[j] = s;
                membersAt[j] = members;
                historyAt[j] = q;
                sources(j, cut, b.box, q, true, [&](int u, int e) {
                    auto test = base;
                    test.add(e);
                    if (!g.couldImprove(test))
                        return;
                    auto z = b;
                    if (u >= 0) {
                        z.box.lo[u] = std::max(z.box.lo[u], at(rank[j]) + 1);
                        z.below[u] |= prior[j] ^ (1 << u);
                    } else
                        for (int m = prior[j]; m; m &= m - 1) {
                            int k = __builtin_ctz(unsigned(m));
                            z.box.hi[k] = std::min(z.box.hi[k], at(rank[j]));
                        }
                    source[j] = u;
                    chosenPrice[j] = e;
                    auto value = paid;
                    value.add(e);
                    queueWalk(next, step.members, step.nextGate, z, value);
                });
                time[j] = 0;
            }
        }
        int membersAt[G]{}, historyAt[G]{};
        bool project(int s, int q, int mask) const {
            for (int m = s; m; m &= m - 1) {
                int a = __builtin_ctz(unsigned(m)), old = membersAt[a], now = mask & earlier[a];
                if (q >= historyAt[a] && (old & ~now) && (q > historyAt[a] || (now & ~old)))
                    return false;
                if (q < historyAt[a] && (now & ~old))
                    return false;
            }
            return true;
        }
        bool lookAhead(int s, Zone &z, const Cost &paid) const {
            if (!g.hasAnswer)
                return true;
            if (s == all)
                return true;
            struct Candidate {
                int src, cut, e, dt, gl;
            };
            Candidate offers[G][2 * G * G];
            int sizes[G]{}, mins[G]{};
            Cost total = paid;
            auto &b = z.box;
            for (int left = all ^ s; left; left &= left - 1) {
                int j = __builtin_ctz(unsigned(left)), R = target[j];
                int must = s | prior[j], may = all ^ (1 << j);
                for (int m = may; m; m &= m - 1) {
                    int v = __builtin_ctz(unsigned(m));
                    if (prior[v] >> j & 1)
                        may ^= 1 << v;
                }
                int best = BAD;
                auto price = [&](int cut, int gl, int gh) {
                    auto orig = history(rank[j]);
                    int vt = cut > 0 ? history(cut).time : g.T + (time[~cut] ? time[~cut] : pop(s) + 1);
                    auto save = [&](int src, int dt, int e) {
                        offers[j][sizes[j]++] = {src, cut, e, dt, gl};
                        best = std::min(best, e);
                    };
                    if ((cut > 0 ? cut < rank[j] : gh > orig.score)) {
                        int dt = std::abs(vt - orig.time);
                        save(-1, dt, std::max(int(cut < 0), gl - orig.score) + dt);
                    }
                    for (int m = prior[j]; m; m &= m - 1) {
                        int u = __builtin_ctz(unsigned(m));
                        if (b.hi[u] <= orig.score || b.lo[u] > gh)
                            continue;
                        int dt = cut > 0 ? g.T + (time[u] ? time[u] : pop(s) + 1) - history(cut).time
                                         : (time[u] && time[~cut] ? std::abs(time[u] - time[~cut]) : 1);
                        save(u, dt, std::max(0, gl - b.hi[u]) + dt);
                    }
                };
                for (int k = 0; k <= pop(may) && k < R; ++k) {
                    int r = R - k, h = at(r);
                    if (r >= rank[j] && prior[j] == 0)
                        continue;
                    int required = must & tighter[j], allowed = may & ~prior[j];
                    for (int m = s; m; m &= m - 1) {
                        int i = __builtin_ctz(unsigned(m));
                        if (r > historyAt[i])
                            required |= membersAt[i];
                        if (r < historyAt[i])
                            allowed &= ~(earlier[i] ^ membersAt[i]);
                        // This old gate cannot move behind a previous, looser cutoff.
                        if (target[i] >= R)
                            allowed &= ~prior[i];
                    }
                    for (int m = may; m; m &= m - 1) {
                        int v = __builtin_ctz(unsigned(m));
                        if (b.lo[v] > h && (must >> v & 1))
                            required |= 1 << v;
                        if (b.hi[v] <= h)
                            allowed &= ~(1 << v);
                    }
                    if ((required & ~allowed) || pop(required) > k || pop(allowed) < k)
                        continue;
                    price(r, h, h);
                }
                int forbidden = prior[j] | tighter[j];
                for (int m = s; m; m &= m - 1) {
                    int k = __builtin_ctz(unsigned(m));
                    if (target[k] >= R)
                        forbidden |= prior[k] | (earlier[k] ^ membersAt[k]);
                }
                int gl = at(R) + 1;
                for (int m = s; m; m &= m - 1) {
                    int k = __builtin_ctz(unsigned(m));
                    if (target[k] >= R)
                        gl = std::max(gl, gate[k] > 0 ? at(gate[k]) + 1 : b.lo[~gate[k]]);
                }
                for (int m = may & ~forbidden; m; m &= m - 1) {
                    int v = __builtin_ctz(unsigned(m));
                    int lower = std::max(gl, b.lo[v]);
                    if (lower <= b.hi[v])
                        price(~v, lower, b.hi[v]);
                }
                if (best == BAD)
                    return false;
                mins[j] = best;
                total.add(best);
                if (!g.couldImprove(total))
                    return false;
            }
            for (int left = all ^ s; left; left &= left - 1) {
                int j = __builtin_ctz(unsigned(left));
                Cost other = paid;
                for (int m = (all ^ s) ^ (1 << j); m; m &= m - 1)
                    other.add(mins[__builtin_ctz(unsigned(m))]);
                int cap = g.affordablePower(other), minWinner = BAD, maxGate = 0;
                int src = -2, cut = 0, srcLow = BAD, cutHigh = 0;
                for (int t = 0; t < sizes[j]; ++t) {
                    auto o = offers[j][t];
                    if (o.e > cap)
                        continue;
                    int sh = o.src < 0 ? at(rank[j]) : b.hi[o.src];
                    int gh = o.cut > 0 ? at(o.cut) : int(std::min<i64>(b.hi[~o.cut], i64(sh) + cap - o.dt));
                    if (gh < o.gl)
                        continue;
                    minWinner = std::min(minWinner, o.gl + int(o.cut > 0));
                    maxGate = std::max(maxGate, gh);
                    if (src == -2)
                        src = o.src;
                    else if (src != o.src)
                        src = -3;
                    if (!cut)
                        cut = o.cut;
                    else if (cut != o.cut)
                        cut = BAD;
                    srcLow = std::min(srcLow, o.gl - cap + o.dt);
                    cutHigh = std::max(cutHigh, gh);
                }
                if (!maxGate)
                    return false;
                if (src >= 0)
                    b.lo[src] = std::max({b.lo[src], srcLow, at(rank[j]) + 1});
                if (cut < 0)
                    b.hi[~cut] = std::min(b.hi[~cut], cutHigh);
                for (int m = prior[j]; m; m &= m - 1) {
                    int u = __builtin_ctz(unsigned(m));
                    b.hi[u] = std::min(b.hi[u], src == -1 ? std::min(maxGate, at(rank[j])) : maxGate);
                }
                for (int m = tighter[j]; m; m &= m - 1) {
                    int v = __builtin_ctz(unsigned(m));
                    b.lo[v] = std::max(b.lo[v], minWinner);
                }
            }
            for (int k = 0; k < len; ++k)
                if (b.lo[k] > b.hi[k])
                    return false;
            return true;
        }
        // An earlier nonterminal must either have occupied a billed top-R prefix
        // or have been the personal source at its owner's next submission.
        bool usefulPrefix(int s, const Zone &z) const {
            int used = 0;
            for (int m = s; m; m &= m - 1) {
                int k = __builtin_ctz(unsigned(m));
                used |= membersAt[k];
                if (source[k] >= 0)
                    used |= 1 << source[k];
            }
            for (int m = s & ~used; m; m &= m - 1) {
                int v = __builtin_ctz(unsigned(m));
                if (terminal[v])
                    continue;
                if (!(s >> (v + 1) & 1) && z.box.hi[v] > at(rank[v]))
                    continue;
                int maxR = 0;
                for (int f = all ^ s; f; f &= f - 1) {
                    int k = __builtin_ctz(unsigned(f));
                    if (who[k] != who[v])
                        maxR = std::max(maxR, target[k]);
                }
                if (!maxR)
                    return false;
                int bestRank = g.oldAhead(z.box.hi[v]) + 1;
                for (int t = s ^ (1 << v); t; t &= t - 1) {
                    int k = __builtin_ctz(unsigned(t));
                    bestRank += above(z, k, v);
                }
                if (bestRank > maxR)
                    return false;
                for (int t = s; t; t &= t - 1) {
                    int k = __builtin_ctz(unsigned(t));
                    if (target[k] >= maxR && (earlier[k] >> v & 1) && !(membersAt[k] >> v & 1))
                        return false;
                }
            }
            return true;
        }
        void exactWalk(int s, Zone z, Cost paid) {
            if (limitReached())
                return;
            auto &node = nodes[s];
            if (!usefulPrefix(s, z) || !priceZone(z, s, node.suffix, paid) || !lookAhead(s, z, paid))
                return;
            if (s == all) {
                Shape sh;
                sh.box = z.box;
                for (int j = 0; j < len; ++j) {
                    int u = source[j], v = gate[j];
                    auto own = history(rank[j]);
                    int st = u < 0 ? own.time : g.T + time[u], gt = v > 0 ? history(v).time : g.T + time[~v];
                    sh.fees.push_back({u + 1, v > 0 ? 0 : 1 + (~v),
                                       (v > 0 ? at(v) : 0) - (u < 0 ? own.score : 0) + std::abs(gt - st)});
                }
                zoneSearch(sh, z, paid);
                return;
            }
            int p = pop(s);
            for (auto first : node.edges) {
                int j = first.action, next = s | (1 << j);
                Cost base = paid;
                base.add(nodes[next].suffix);
                Cost quick = base;
                quick.add(first.price);
                if (!g.couldImprove(quick))
                    continue;
                std::vector<Option> opts;
                offers(s, j, z.box, [&](Option o) {
                    auto v = base;
                    v.add(o.price);
                    if (g.couldImprove(v))
                        opts.push_back(o);
                });
                std::sort(opts.begin(), opts.end(), [](auto x, auto y) { return x.price < y.price; });
                time[j] = p + 1;
                earlier[j] = s;
                for (auto o : opts) {
                    auto value = base;
                    value.add(o.price);
                    if (!g.couldImprove(value))
                        break;
                    Zone bz = z;
                    int v = o.cut, u = o.src;
                    if (!narrow(bz.box, s, j, u, v))
                        continue;
                    if (u >= 0)
                        bz.below[u] |= prior[j] ^ (1 << u);
                    int req = s & tighter[j], allow = s & ~prior[j];
                    for (int m = s; m; m &= m - 1) {
                        int k = __builtin_ctz(unsigned(m));
                        if (target[k] >= target[j])
                            allow &= ~(prior[k] | (earlier[k] ^ membersAt[k]));
                    }
                    if (v > 0) {
                        int h = at(v);
                        for (int m = s; m; m &= m - 1) {
                            int k = __builtin_ctz(unsigned(m));
                            if (bz.box.lo[k] > h)
                                req |= 1 << k;
                            if (bz.box.hi[k] <= h)
                                allow &= ~(1 << k);
                        }
                    } else {
                        int w = ~v;
                        req |= 1 << w;
                        for (int m = s ^ (1 << w); m; m &= m - 1) {
                            int k = __builtin_ctz(unsigned(m));
                            if (above(bz, k, w))
                                req |= 1 << k;
                            if (above(bz, w, k))
                                allow &= ~(1 << k);
                        }
                        for (int m = tighter[j]; m; m &= m - 1)
                            bz.below[__builtin_ctz(unsigned(m))] |= 1 << w;
                    }
                    if (req & ~allow)
                        continue;
                    int open = allow ^ req;
                    source[j] = u;
                    gate[j] = v;
                    chosenPrice[j] = o.price;
                    for (int sub = open;; sub = (sub - 1) & open) {
                        int mem = req | sub, q = target[j] - pop(mem);
                        if (q >= 0 && (v < 0 || q == v) && project(s, q, mem)) {
                            auto b = bz;
                            if (v > 0) {
                                for (int m = s; m; m &= m - 1) {
                                    int k = __builtin_ctz(unsigned(m));
                                    if (mem >> k & 1)
                                        b.box.lo[k] = std::max(b.box.lo[k], at(v) + 1);
                                    else
                                        b.box.hi[k] = std::min(b.box.hi[k], at(v));
                                }
                            } else {
                                int w = ~v;
                                b.box.lo[w] = std::max(b.box.lo[w], at(q + 1) + 1);
                                if (q)
                                    b.box.hi[w] = std::min(b.box.hi[w], at(q));
                                b.below[w] |= s ^ mem;
                                for (int m = mem ^ (1 << w); m; m &= m - 1)
                                    b.below[__builtin_ctz(unsigned(m))] |= 1 << w;
                            }
                            if (boxClosure(b)) {
                                membersAt[j] = mem;
                                historyAt[j] = q;
                                auto cost = paid;
                                cost.add(o.price);
                                exactWalk(next, b, cost);
                            }
                        }
                        if (!sub)
                            break;
                    }
                }
                time[j] = 0;
            }
        }
    };

    // The R-a historical survivors never leave top R. Its other a original
    // members must each be evicted, and each eviction incurs a distinct fee.
    bool requiredEvictions(int R) const {
        if (!hasAnswer || R < actors)
            return true;
        const int all = (1 << actors) - 1;
        int first[G];
        for (int a = 0; a < actors; ++a) {
            auto person = people[ids[a]];
            auto orig = old[person.rank];
            first[a] = std::max(1, old[R].score + 1 - orig.score) + T + 1 - orig.time;
            for (int r = R - actors + 1; r <= R && r < person.rank; ++r)
                first[a] = std::min(first[a], old[r].score - orig.score + std::abs(old[r].time - orig.time));
        }
        std::array<Cost, STATES> dp, next;
        std::bitset<STATES> seen, found;
        seen.set(0);
        for (int k = 0; k < actors; ++k) {
            found.reset();
            int r = R - k;
            for (int s = 0; s <= all; ++s)
                if (seen[s]) {
                    auto put = [&](int mask, int e) {
                        auto v = dp[s];
                        v.add(e);
                        if (couldImprove(v) && (!found[mask] || v < next[mask])) {
                            found.set(mask);
                            next[mask] = v;
                        }
                    };
                    if (k - count(s) < L - actors)
                        put(s, T + 1 - old[r].time);
                    for (int m = all ^ s; m; m &= m - 1) {
                        int a = __builtin_ctz(unsigned(m)), h = people[ids[a]].rank;
                        if (h > r)
                            put(s | (1 << a),
                                old[r].score - old[h].score + std::abs(old[r].time - old[h].time));
                    }
                }
            std::swap(dp, next);
            seen = found;
        }
        for (int s = 0; s <= all; ++s)
            if (seen[s]) {
                auto v = dp[s];
                for (int m = all ^ s; m; m &= m - 1)
                    v.add(first[__builtin_ctz(unsigned(m))]);
                for (int j = 2 * actors - count(s); j < L; ++j)
                    v.add(1);
                if (couldImprove(v))
                    return true;
            }
        return false;
    }
    void legacySearch(Domain box) {
        bool separated = L == actors + 1;
        for (int a = 0; separated && a < actors; ++a) {
            for (int b = 0; b < a; ++b) {
                separated &= std::abs(people[ids[a]].target - people[ids[b]].target) >= L;
            }
        }
        if (L >= 7 && L - actors >= 1 && L - actors <= 2 && !separated &&
            ((style == SearchStyle::Ranked || style == SearchStyle::Assigned) ||
             historicalThresholds.size() <= 3 * std::size_t(L))) {
            for (int P = 1; P < bit(L) && !interrupted; ++P) {
                if (count(P) != L - actors) {
                    continue;
                }
                preparation = P;
                Domain next = box;
                for (int m = full ^ P; m; m &= m - 1) {
                    int j = index(m);
                    next.lo[j] = std::max(
                        next.lo[j], *std::min_element(winnerFloor.begin(), winnerFloor.begin() + actors));
                }
                searchFirst(next);
            }
            preparation = -1;
        } else {
            searchFirst(box);
        }
    }
    std::array<int, G> forcedPrefix{};

    int forcedCount() {
        // Place each mandatory terminal as late as its deadline permits.
        // Every historical rank in this relaxed board is a lower bound.
        for (;;) {
            std::vector<int> slots;
            for (int i : initiallyFail) slots.push_back(people[i].target);
            for (int d = 0; d < H; ++d)
                for (int k = 0; k < forcedPrefix[d]; ++k)
                    slots.push_back(people[groups[d][k]].target);
            std::sort(slots.begin(), slots.end());
            for (int k = int(slots.size()) - 2; k >= 0; --k)
                slots[k] = std::min(slots[k], slots[k + 1] - 1);
            bool changed = false;
            for (int d = 0; d < H; ++d) {
                for (int k = forcedPrefix[d]; k < int(groups[d].size()); ++k) {
                    auto u = people[groups[d][k]];
                    int best = u.rank;
                    for (int p : slots) best += (p <= best);
                    if (best > u.target) {
                        forcedPrefix[d] = k + 1;
                        changed = true;
                    }
                }
            }
            if (!changed) return int(slots.size());
        }
    }

    void selectActors(int d, const Quantiles &bound) {
        if (interrupted)
            return;
        if (d == L) {
            if (!optimisticGroup(bound)) {
                return;
            }
            // In both exterior score intervals, at most L distinct appended
            // grades need be kept. Saturate toward the nearest relevant cutoff.
            int bottom = quantiles.hi[L], top = 1;
            for (int a = 0; a < actors; ++a) {
                auto u = people[ids[a]];
                bottom = std::min(bottom, old[u.target].score);
                top = std::max({top, old[u.rank].score, old[std::max(1, u.target - L)].score});
            }
            top = std::max(top, quantiles.lo[1] - 1);
            for (int k = 1; k <= L; ++k) {
                quantiles.lo[k] = std::max(quantiles.lo[k], bottom - L + 1);
                quantiles.hi[k] = std::min(quantiles.hi[k], std::min(MAX_SCORE, top + L));
                if (quantiles.lo[k] > quantiles.hi[k])
                    return;
            }
            if (quantiles.hi[1] - quantiles.lo[L] <= 2048) {
                int low = quantiles.lo[L], high = quantiles.hi[1];
                if (gradeRanks.empty() || low < gradeBase || high >= gradeBase + int(gradeRanks.size())) {
                    gradeRanks.clear();
                    gradeBase = low;
                    int r = oldAhead(low);
                    gradeRanks.resize(high - low + 1);
                    for (int x = low; x <= high; ++x) {
                        while (r && old[r].score < x)
                            --r;
                        gradeRanks[x - low] = r;
                    }
                }
            }
            bool commonTarget = true;
            for (int a = 1; a < actors; ++a)
                commonTarget &= people[ids[a]].target == people[ids[0]].target;
            if (commonTarget && !requiredEvictions(people[ids[0]].target))
                return;
            if (L <= 5 && equalTargets()) {
                return;
            }
            historicalThresholds.clear();
            for (int a = 0; a < actors; ++a) {
                const auto &person = people[ids[a]];
                historicalThresholds.push_back(old[person.rank].score);
                for (int k = 0; k < L && k < person.target; ++k) {
                    historicalThresholds.push_back(old[person.target - k].score);
                }
            }
            std::sort(historicalThresholds.begin(), historicalThresholds.end());
            historicalThresholds.erase(std::unique(historicalThresholds.begin(), historicalThresholds.end()),
                                       historicalThresholds.end());
            Domain box;
            for (int j = 1; j <= L; ++j) {
                box.lo[j] = quantiles.lo[L];
                if (L == actors) {
                    box.lo[j] = std::max(
                        box.lo[j], *std::min_element(winnerFloor.begin(), winnerFloor.begin() + actors));
                }
                box.hi[j] = quantiles.hi[1];
            }
            if (L >= 8 && actors >= 5) {
                seedIncumbent(box);
            }

            auto probe = [&](int limit, SearchStyle order = SearchStyle::Basic) {
                style = order;
                nodesRemaining = limit;
                interrupted = false;
                legacySearch(box);
                bool done = !interrupted;
                interrupted = false;
                nodesRemaining = -1;
                return done;
            };
            if (L <= 4) {
                probe(-1);
                return;
            }
            NamedSearch solver(*this);
            if (commonTarget) {
                solver.run(-1);
                return;
            }
            if (L - actors >= 5) {
                solver.run(-1);
                return;
            }
            if (probe(70) || solver.run(16000) || probe(1800))
                return;
            if (L >= 7 && L - actors <= 2 &&
                probe(7000, L == actors ? SearchStyle::Ranked : SearchStyle::Assigned))
                return;
            solver.run(-1);
            return;
        }
        const auto &group = groups[d];
        int keep = actors;
        int limit = std::min({L - d - 1, L - actors, int(group.size())});
        for (int k = 0; k <= limit; ++k) {
            Quantiles next = bound;
            if (k) {
                ids[actors++] = group[k - 1];
                next.lo[d + 1] = old[people[group[k - 1]].rank].score + 1;
            }
            if (k < int(group.size())) {
                next.hi[d + 1] = old[people[group[k]].rank].score;
            }
            if (k >= forcedPrefix[d] && next.lo[d + 1] <= next.hi[d + 1] &&
                (d == 0 || next.lo[d + 1] <= next.hi[d])) {
                selectActors(d + 1, next);
            }
        }
        actors = keep;
    }

  public:
    GeneralSolver(const std::vector<Record> &records, const std::vector<Player> &players, int lastTime)
        : old(records), people(players), T(lastTime), H(MAX_TIME - T), M(int(old.size()) - 1) {}

    int run() {
        for (int r = M; r >= 1; --r) {
            int i = old[r].owner;
            if (people[i].rank != r) {
                continue;
            }
            int d = people[i].target - r;
            if (d < 0) {
                initiallyFail.push_back(i);
            } else if (d < H && int(groups[d].size()) < H - d) {
                groups[d].push_back(i);
            }
        }
        if (initiallyFail.empty()) {
            return 0;
        }
        for (L = forcedCount(); L <= H; ++L) {
            Quantiles q;
            for (int j = 1; j <= L; ++j) {
                q.lo[j] = 1;
                q.hi[j] = MAX_SCORE;
            }
            actors = int(initiallyFail.size());
            std::copy(initiallyFail.begin(), initiallyFail.end(), ids.begin());
            full = prefix(L);
            selectActors(0, q);
        }
        assert(hasAnswer);
        return answer.residue();
    }
};
int main() {
    Input in;
    int n = in.read();
    int M = in.read();
    int type = in.read();
    std::vector<Player> players(n + 1);
    for (int i = 1; i <= n; ++i) {
        players[i].target = in.read();
    }
    std::vector<Record> records(M + 1);
    int latest = 0;
    for (int i = 1; i <= M; ++i) {
        records[i] = {in.read(), in.read(), in.read()};
        latest = std::max(latest, records[i].time);
    }
    if (type == 0) {
        if (latest == MAX_TIME) {
            std::puts("0");
            return 0;
        }
        sortHistory(records, players);
        auto solver=std::make_unique<GeneralSolver>(records,players,latest);
        std::printf("%d\n", solver->run());
    } else {
        int L = in.read();
        std::vector<int> owner(L + 1), rank(L + 1);
        for (int j = 1; j <= L; ++j) {
            owner[j] = in.read();
            rank[j] = in.read();
        }
        std::vector<Record> original(n + 1);
        for (int k = 1; k <= M; ++k) {
            const auto& r = records[k];
            if (historicalOrder(r, original[r.owner])) {
                original[r.owner] = r;
            }
        }
        std::printf("%d\n", fixedLeaderboard(records, original, players, latest, owner, rank));
    }
}
