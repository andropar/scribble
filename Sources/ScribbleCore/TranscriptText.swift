import Foundation

/// Joining transcript pieces in scripts with and without spaces between words.
public enum TranscriptText {
    /// Scripts written without spaces between words. Whisper returns these as small fragments
    /// without leading spaces, so a separator must not be added. Korean uses spaces.
    public static func isUnspaced(_ character: Character) -> Bool {
        guard let scalar = character.unicodeScalars.first?.value else { return false }
        switch scalar {
        case 0x0E00...0x0EFF, // Thai, Lao
             0x0F00...0x0FFF, // Tibetan
             0x1000...0x109F, // Myanmar
             0x1780...0x17FF, // Khmer
             0x3000...0x312F, // CJK punctuation, Hiragana, Katakana, Bopomofo
             0x31F0...0x31FF, // Katakana extensions
             0x3400...0x4DBF, // CJK extension A
             0x4E00...0x9FFF, // CJK ideographs
             0xF900...0xFAFF, // CJK compatibility ideographs
             0xFF00...0xFFEF, // Fullwidth forms
             0x20000...0x3FFFF: // CJK supplementary planes
            return true
        default:
            return false
        }
    }

    /// `piece` as it should be appended to `text`.
    public static func spaced(_ piece: String, after text: String) -> String {
        if piece.hasPrefix(" ") { return piece }
        // A mark reference stays set off from the text around it.
        if let first = piece.first, isUnspaced(first), text.last != "]", !text.isEmpty { return piece }
        return " " + piece
    }

    /// Segments as prose.
    public static func joined(_ segments: [String]) -> String {
        segments.reduce("") { $0 + spaced($1, after: $0) }.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// Repairs fragments from Whisper's word timing: a combining mark (Thai vowels and tones)
    /// joins the character it belongs to, so a mark reference can't split them, and a
    /// whitespace-only fragment becomes the leading space of the next word.
    public static func mergedFragments(_ words: [Word]) -> [Word] {
        var merged: [Word] = []
        var pendingSpace = false
        for word in words where !word.text.isEmpty {
            if word.text.allSatisfy(\.isWhitespace) {
                pendingSpace = true
                continue
            }
            if !pendingSpace, let last = merged.last, combines(word.text, with: last.text) {
                merged[merged.count - 1].text += word.text
                merged[merged.count - 1].end = max(last.end, word.end)
            } else {
                var word = word
                if pendingSpace, !word.text.hasPrefix(" ") { word.text = " " + word.text }
                merged.append(word)
            }
            pendingSpace = false
        }
        return merged
    }

    /// Swift counts a base character and its combining marks as one `Character`.
    private static func combines(_ text: String, with previous: String) -> Bool {
        (previous + text).count < previous.count + text.count
    }
}
