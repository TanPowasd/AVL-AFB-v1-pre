// 《压缩奇点》（The Compression Singularity）C++20 起步模板。
// 接口：从标准输入读取目标长度 L 与长度为 L 的 0/1 串 S，
//       向标准输出打印一行合法的单带虚拟机程序 P（仅含 > < + . [ ]）。
// 本模板实现最基础的直接翻转输出编码（Guaranteed AC，|P| <= 1.5L），
// 请先对 data/samples/ 中的匿名标本进行深度数据分析与机制破译，
// 再设计通用自动分析与字节码合成算法以大幅压缩 |P|！

#include <iostream>
#include <string>

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);

    std::size_t L = 0;
    std::string S;
    if (!(std::cin >> L >> S)) return 0;

    std::string prog;
    prog.reserve(S.size() + S.size() / 2 + 16);

    char cur = '0';
    for (char bit : S) {
        if (bit != cur) {
            prog.push_back('+');
            cur = bit;
        }
        prog.push_back('.');
    }

    std::cout << prog << '\n';
    return 0;
}
