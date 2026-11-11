import Foundation

/// A language Whisper can transcribe, identified by its Whisper token code (`<|ru|>`).
public struct WhisperLanguage: Identifiable, Hashable, Sendable {
    public let code: String
    /// English name, the sort key of the list.
    public let name: String
    /// The name in the language itself, when the system knows it.
    public let nativeName: String?

    public var id: String { code }

    /// Starts with the English name, so typing in an open menu finds the entry.
    public var menuTitle: String {
        nativeName.map { "\(name)  \($0)" } ?? name
    }

    init(_ code: String, _ name: String) {
        self.code = code
        self.name = name
        nativeName = Self.nativeName(of: code, englishName: name)
    }

    private static func nativeName(of code: String, englishName: String) -> String? {
        // Whisper's Javanese code differs from the ISO one the system knows.
        let localeCode = code == "jw" ? "jv" : code
        let locale = Locale(identifier: localeCode)
        guard let native = locale.localizedString(forLanguageCode: localeCode), native != localeCode else { return nil }
        let title = native.capitalized(with: locale)
        return title.caseInsensitiveCompare(englishName) == .orderedSame ? nil : title
    }

    /// Every language of the multilingual Whisper models, sorted by English name.
    public static let all: [WhisperLanguage] = [
        WhisperLanguage("af", "Afrikaans"), WhisperLanguage("sq", "Albanian"), WhisperLanguage("am", "Amharic"),
        WhisperLanguage("ar", "Arabic"), WhisperLanguage("hy", "Armenian"), WhisperLanguage("as", "Assamese"),
        WhisperLanguage("az", "Azerbaijani"), WhisperLanguage("ba", "Bashkir"), WhisperLanguage("eu", "Basque"),
        WhisperLanguage("be", "Belarusian"), WhisperLanguage("bn", "Bengali"), WhisperLanguage("bs", "Bosnian"),
        WhisperLanguage("br", "Breton"), WhisperLanguage("bg", "Bulgarian"), WhisperLanguage("my", "Burmese"),
        WhisperLanguage("yue", "Cantonese"), WhisperLanguage("ca", "Catalan"),
        WhisperLanguage("zh", "Chinese"), WhisperLanguage("hr", "Croatian"), WhisperLanguage("cs", "Czech"),
        WhisperLanguage("da", "Danish"), WhisperLanguage("nl", "Dutch"), WhisperLanguage("en", "English"),
        WhisperLanguage("et", "Estonian"), WhisperLanguage("fo", "Faroese"), WhisperLanguage("fi", "Finnish"),
        WhisperLanguage("fr", "French"), WhisperLanguage("gl", "Galician"), WhisperLanguage("ka", "Georgian"),
        WhisperLanguage("de", "German"), WhisperLanguage("el", "Greek"), WhisperLanguage("gu", "Gujarati"),
        WhisperLanguage("ht", "Haitian Creole"), WhisperLanguage("ha", "Hausa"), WhisperLanguage("haw", "Hawaiian"),
        WhisperLanguage("he", "Hebrew"), WhisperLanguage("hi", "Hindi"), WhisperLanguage("hu", "Hungarian"),
        WhisperLanguage("is", "Icelandic"), WhisperLanguage("id", "Indonesian"), WhisperLanguage("it", "Italian"),
        WhisperLanguage("ja", "Japanese"), WhisperLanguage("jw", "Javanese"), WhisperLanguage("kn", "Kannada"),
        WhisperLanguage("kk", "Kazakh"), WhisperLanguage("km", "Khmer"), WhisperLanguage("ko", "Korean"),
        WhisperLanguage("lo", "Lao"), WhisperLanguage("la", "Latin"), WhisperLanguage("lv", "Latvian"),
        WhisperLanguage("ln", "Lingala"), WhisperLanguage("lt", "Lithuanian"), WhisperLanguage("lb", "Luxembourgish"),
        WhisperLanguage("mk", "Macedonian"), WhisperLanguage("mg", "Malagasy"), WhisperLanguage("ms", "Malay"),
        WhisperLanguage("ml", "Malayalam"), WhisperLanguage("mt", "Maltese"), WhisperLanguage("mi", "Maori"),
        WhisperLanguage("mr", "Marathi"), WhisperLanguage("mn", "Mongolian"), WhisperLanguage("ne", "Nepali"),
        WhisperLanguage("no", "Norwegian"), WhisperLanguage("nn", "Norwegian Nynorsk"), WhisperLanguage("oc", "Occitan"),
        WhisperLanguage("ps", "Pashto"), WhisperLanguage("fa", "Persian"), WhisperLanguage("pl", "Polish"),
        WhisperLanguage("pt", "Portuguese"), WhisperLanguage("pa", "Punjabi"),
        WhisperLanguage("ro", "Romanian"), WhisperLanguage("ru", "Russian"),
        WhisperLanguage("sa", "Sanskrit"), WhisperLanguage("sr", "Serbian"), WhisperLanguage("sn", "Shona"),
        WhisperLanguage("sd", "Sindhi"), WhisperLanguage("si", "Sinhala"), WhisperLanguage("sk", "Slovak"),
        WhisperLanguage("sl", "Slovenian"), WhisperLanguage("so", "Somali"), WhisperLanguage("es", "Spanish"),
        WhisperLanguage("su", "Sundanese"), WhisperLanguage("sw", "Swahili"), WhisperLanguage("sv", "Swedish"),
        WhisperLanguage("tl", "Tagalog"), WhisperLanguage("tg", "Tajik"), WhisperLanguage("ta", "Tamil"),
        WhisperLanguage("tt", "Tatar"), WhisperLanguage("te", "Telugu"), WhisperLanguage("th", "Thai"),
        WhisperLanguage("bo", "Tibetan"), WhisperLanguage("tr", "Turkish"), WhisperLanguage("tk", "Turkmen"),
        WhisperLanguage("uk", "Ukrainian"), WhisperLanguage("ur", "Urdu"), WhisperLanguage("uz", "Uzbek"),
        WhisperLanguage("vi", "Vietnamese"), WhisperLanguage("cy", "Welsh"), WhisperLanguage("yi", "Yiddish"),
        WhisperLanguage("yo", "Yoruba"),
    ]

    private static let byCode = Dictionary(uniqueKeysWithValues: all.map { ($0.code, $0) })

    public static func named(code: String) -> WhisperLanguage? {
        byCode[code]
    }
}

/// The language setting: one fixed language, or Whisper's detection.
public enum TranscriptionLanguage: Hashable, Sendable {
    case auto
    case language(WhisperLanguage)

    public static let english = TranscriptionLanguage.language(WhisperLanguage.named(code: "en")!)

    /// Reads a stored setting. A missing or unknown value is English, the app's original behavior.
    public init(storage: String?) {
        self = storage.flatMap(Self.init(argument:)) ?? .english
    }

    /// Parses "auto" or a Whisper language code.
    public init?(argument: String) {
        let value = argument.lowercased()
        if value == "auto" {
            self = .auto
        } else if let language = WhisperLanguage.named(code: value) {
            self = .language(language)
        } else {
            return nil
        }
    }

    public var storageValue: String {
        switch self {
        case .auto: return "auto"
        case let .language(language): return language.code
        }
    }
}

public enum WhisperModel: String, CaseIterable, Identifiable, Sendable {
    case largeTurbo = "openai_whisper-large-v3-v20240930_turbo_632MB"
    case small = "openai_whisper-small"
    case base = "openai_whisper-base"
    case smallEn = "openai_whisper-small.en"
    case baseEn = "openai_whisper-base.en"

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .largeTurbo: return "Large v3 Turbo (best, multilingual, ~630 MB)"
        case .small: return "Small (multilingual, ~490 MB)"
        case .base: return "Base (multilingual, fastest, ~150 MB)"
        case .smallEn: return "Small English (English only, ~490 MB)"
        case .baseEn: return "Base English (English only, fastest, ~150 MB)"
        }
    }

    public var isMultilingual: Bool {
        switch self {
        case .largeTurbo, .small, .base: return true
        case .smallEn, .baseEn: return false
        }
    }
}

/// How a capture gets decoded for a language setting on a given model.
public struct LanguagePlan: Equatable, Sendable {
    /// Language forced on the decoder; nil lets Whisper detect it for each audio chunk.
    public var language: String?
    /// Why the transcript may disappoint. The model can't be switched for the user (it may
    /// mean a large download), so this has to reach them.
    public var warning: String?

    public static func resolve(selection: TranscriptionLanguage, isMultilingual: Bool) -> LanguagePlan {
        guard isMultilingual else {
            let warning = selection == .english ? nil : "This model only understands English, so other speech comes out translated or garbled. Pick a multilingual model."
            return LanguagePlan(language: "en", warning: warning)
        }
        guard case let .language(language) = selection else { return LanguagePlan(language: nil, warning: nil) }
        // The Cantonese token gives an empty transcript, on large too, and small and base don't
        // have it. As Chinese the speech comes out readable, as it does with Auto-detect.
        return LanguagePlan(language: language.code == "yue" ? "zh" : language.code, warning: nil)
    }

    public static func resolve(selection: TranscriptionLanguage, model: WhisperModel) -> LanguagePlan {
        resolve(selection: selection, isMultilingual: model.isMultilingual)
    }
}
