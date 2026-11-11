import Foundation

/// Word-level comparison of two transcripts, for checking that transcribing in pieces
/// gives the same words as transcribing in one pass.
public enum WordDiff {
    public enum Op: Equatable {
        case same(Int, Int)
        /// A word of the reference (index into `reference`) that the candidate lacks.
        case missing(Int)
        /// A word of the candidate (index into `candidate`) the reference lacks.
        case extra(Int)
    }

    /// Lower case, letters and digits only, so punctuation and capitals don't count.
    public static func normalize(_ word: String) -> String {
        String(word.lowercased().unicodeScalars.filter { CharacterSet.alphanumerics.contains($0) }.map(Character.init))
    }

    /// Longest-common-subsequence alignment of the normalized words.
    public static func compare(reference: [String], candidate: [String]) -> [Op] {
        let a = reference.map(normalize), b = candidate.map(normalize)
        let n = a.count, m = b.count
        var table = [[Int32]](repeating: [Int32](repeating: 0, count: m + 1), count: n + 1)
        if n > 0, m > 0 {
            for i in stride(from: n - 1, through: 0, by: -1) {
                for j in stride(from: m - 1, through: 0, by: -1) {
                    table[i][j] = a[i] == b[j] ? table[i + 1][j + 1] + 1 : max(table[i + 1][j], table[i][j + 1])
                }
            }
        }
        var ops: [Op] = []
        var i = 0, j = 0
        while i < n || j < m {
            if i < n, j < m, a[i] == b[j] {
                ops.append(.same(i, j)); i += 1; j += 1
            } else if j < m, i == n || table[i][j + 1] >= table[i + 1][j] {
                ops.append(.extra(j)); j += 1
            } else {
                ops.append(.missing(i)); i += 1
            }
        }
        return ops
    }
}
