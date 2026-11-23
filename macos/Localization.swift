import Foundation
import Combine

enum AppLanguage: String, CaseIterable, Identifiable {
    case system = "system"
    case en = "en"
    case zhHans = "zh-Hans"
    case zhHant = "zh-Hant"
    case ja = "ja"

    var id: String { rawValue }
    var nativeName: String {
        switch self {
        case .system: return L("跟随系统")
        case .en: return "English"
        case .zhHans: return "简体中文"
        case .zhHant: return "繁體中文"
        case .ja: return "日本語"
        }
    }
    var title: String { nativeName }
    var locale: Locale { Locale(identifier: self == .system ? "en" : rawValue) }

    static func parse(_ value: String) -> AppLanguage? {
        let parts = value.replacingOccurrences(of: "_", with: "-").lowercased().split(separator: "-").map(String.init)
        guard let first = parts.first else { return nil }
        if first == "system" { return .system }
        if first == "en" { return .en }
        if first == "ja" { return .ja }
        if first == "zhhans" { return .zhHans }
        if first == "zhhant" { return .zhHant }
        guard first == "zh" else { return nil }
        if parts.contains("hans") { return .zhHans }
        if parts.contains("hant") { return .zhHant }
        return parts.contains(where: { ["tw", "hk", "mo"].contains($0) }) ? .zhHant : .zhHans
    }
    static func resolve(preferredLanguages: [String]) -> AppLanguage {
        for value in preferredLanguages {
            if let language = parse(value), language != .system { return language }
        }
        return .en
    }
}

/// Source text is a stable catalog identifier. User content never enters this table.
struct LocalizationCatalog: Decodable {
    let schemaVersion: Int
    let sourceLanguage: String
    let strings: [String: [String: String]]
    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version", sourceLanguage = "source_language", strings
    }
    static let empty = LocalizationCatalog(schemaVersion: 1, sourceLanguage: "zh-Hans", strings: [:])

    static func load() -> LocalizationCatalog {
        let source = URL(fileURLWithPath: #filePath).deletingLastPathComponent().appendingPathComponent("Localizations.json")
        var urls: [URL] = []
        if let resources = Bundle.main.resourceURL {
            urls.append(resources.appendingPathComponent("macos/Localizations.json"))
            urls.append(resources.appendingPathComponent("Localizations.json"))
        }
        // Developer binaries and self-tests can use the adjacent source catalog.
        // Distributed apps never fall back into a developer's checkout.
        if Bundle.main.bundleURL.pathExtension != "app" { urls.append(source) }
        for url in urls {
            guard let data = try? Data(contentsOf: url), data.count <= 2 * 1024 * 1024,
                  let result = try? JSONDecoder().decode(LocalizationCatalog.self, from: data),
                  result.schemaVersion == 1, result.sourceLanguage == "zh-Hans" else { continue }
            return result
        }
        return .empty
    }

    func validationErrors() -> [String] {
        var errors: [String] = []
        if schemaVersion != 1 || sourceLanguage != "zh-Hans" { errors.append("Unsupported localization schema") }
        if strings.isEmpty { errors.append("Localization catalog is empty") }
        for (source, translations) in strings {
            guard let expected = FormatPattern.signature(source) else {
                errors.append("Invalid source format: " + source); continue
            }
            let expectedTokens = MessageTemplate.tokens(in: source)
            for language in [AppLanguage.en, .zhHans, .zhHant, .ja] {
                guard let value = translations[language.rawValue], !value.isEmpty else {
                    errors.append("Missing " + language.rawValue + ": " + source); continue
                }
                if FormatPattern.signature(value) != expected {
                    errors.append("Format mismatch " + language.rawValue + ": " + source)
                }
                if MessageTemplate.tokens(in: value) != expectedTokens {
                    errors.append("Template mismatch " + language.rawValue + ": " + source)
                }
                if language == .zhHans && value != source {
                    errors.append("Source text mismatch: " + source)
                }
            }
        }
        return errors.sorted()
    }
}

private enum FormatPattern {
    static func signature(_ format: String) -> [String]? {
        let characters = Array(format)
        var result: [String] = []
        var index = 0
        while index < characters.count {
            guard characters[index] == "%" else { index += 1; continue }
            index += 1
            guard index < characters.count else { return nil }
            if characters[index] == "%" { index += 1; continue }
            var specifier = "%"
            while index < characters.count && "-+ #0.123456789hlLzjt".contains(characters[index]) {
                specifier.append(characters[index]); index += 1
            }
            guard index < characters.count, "@diuoxXfFeEgG".contains(characters[index]) else { return nil }
            specifier.append(characters[index]); index += 1
            result.append(specifier)
        }
        return result
    }
    static func accepts(_ values: [CVarArg], for signature: [String]) -> Bool {
        guard values.count == signature.count else { return false }
        for (value, specifier) in zip(values, signature) {
            if specifier.hasSuffix("@") {
                guard value is String || value is NSString else { return false }
            } else if let type = specifier.last, "fFeEgG".contains(type) {
                guard value is Float || value is Double else { return false }
            } else {
                guard value is Int || value is Int32 || value is Int64 || value is Int16 ||
                      value is Int8 || value is UInt || value is UInt32 || value is UInt64 ||
                      value is UInt16 || value is UInt8 else { return false }
            }
        }
        return true
    }
}

/// Only catalogued backend templates are matched; this is not a general text translator.
private struct MessageTemplate {
    private static let tokenExpression = try! NSRegularExpression(pattern: #"\{value_[1-9][0-9]*\}"#)
    let source: String
    let names: [String]
    let expression: NSRegularExpression

    static func tokens(in text: String) -> [String] {
        tokenExpression.matches(in: text, range: NSRange(text.startIndex..., in: text)).compactMap {
            Range($0.range, in: text).map { String(text[$0]) }
        }
    }

    init?(_ source: String) {
        let matches = Self.tokenExpression.matches(in: source, range: NSRange(source.startIndex..., in: source))
        guard !matches.isEmpty, matches.count <= 4 else { return nil }
        var pattern = #"\A"#
        var last = source.startIndex
        var names: [String] = []
        for match in matches {
            guard let range = Range(match.range, in: source) else { return nil }
            pattern += NSRegularExpression.escapedPattern(for: String(source[last..<range.lowerBound])) + "(.+?)"
            names.append(String(source[range]))
            last = range.upperBound
        }
        pattern += NSRegularExpression.escapedPattern(for: String(source[last...])) + #"\z"#
        guard Set(names).count == names.count,
              let expression = try? NSRegularExpression(pattern: pattern, options: [.dotMatchesLineSeparators]) else { return nil }
        self.source = source
        self.names = names
        self.expression = expression
    }

    func values(in text: String) -> [String: String]? {
        guard text.utf8.count <= 8192,
              let match = expression.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)) else { return nil }
        var result: [String: String] = [:]
        for (index, name) in names.enumerated() {
            guard let range = Range(match.range(at: index + 1), in: text) else { return nil }
            result[name] = String(text[range])
        }
        return result
    }

    static func render(_ template: String, values: [String: String]) -> String {
        // Append captures once so user text resembling a token is never substituted again.
        let matches = tokenExpression.matches(in: template, range: NSRange(template.startIndex..., in: template))
        var result = ""
        var last = template.startIndex
        for match in matches {
            guard let range = Range(match.range, in: template) else { continue }
            result += template[last..<range.lowerBound]
            result += values[String(template[range])] ?? String(template[range])
            last = range.upperBound
        }
        result += template[last...]
        return result
    }
}

private final class LocalizationRuntime: @unchecked Sendable {
    static let shared = LocalizationRuntime()
    private let lock = NSLock()
    private var language: AppLanguage = .zhHans
    let catalog: LocalizationCatalog
    private let templates: [MessageTemplate]

    private init() {
        let catalog = LocalizationCatalog.load()
        self.catalog = catalog
        templates = catalog.strings.keys.sorted().compactMap(MessageTemplate.init)
    }

    func set(_ value: AppLanguage) {
        lock.lock(); language = value; lock.unlock()
    }
    var resolvedLanguage: AppLanguage {
        lock.lock(); defer { lock.unlock() }; return language
    }

    private func entry(_ source: String, language: AppLanguage) -> String? {
        guard let entry = catalog.strings[source], let signature = FormatPattern.signature(source) else { return nil }
        for key in [language.rawValue, "en"] {
            if let value = entry[key], !value.isEmpty,
               FormatPattern.signature(value) == signature,
               MessageTemplate.tokens(in: value) == MessageTemplate.tokens(in: source) { return value }
        }
        return nil
    }

    private func translated(_ source: String, language: AppLanguage) -> String? {
        if language == .zhHans { return source }
        if let value = entry(source, language: language) { return value }
        for template in templates {
            guard let values = template.values(in: source),
                  let translated = entry(template.source, language: language) else { continue }
            return MessageTemplate.render(translated, values: values)
        }
        return nil
    }

    func text(_ source: String, fallback: String? = nil, language: AppLanguage? = nil) -> String {
        let selected = language ?? resolvedLanguage
        if let value = translated(source, language: selected) { return value }
        if let fallback { return translated(fallback, language: selected) ?? fallback }
        return source
    }

    func format(_ source: String, arguments: [CVarArg]) -> String {
        let selected = resolvedLanguage
        let translated = text(source, language: selected)
        guard let signature = FormatPattern.signature(source),
              FormatPattern.accepts(arguments, for: signature) else { return translated }
        return String(format: translated, locale: selected.locale, arguments: arguments)
    }
}

/// Injected only by isolated self-tests; preview mode never invokes these closures.
struct LocalizationPreferenceStore {
    let read: (String) -> String?
    let write: (String, String) -> Void
    static let standard = LocalizationPreferenceStore(
        read: { UserDefaults.standard.string(forKey: $0) },
        write: { value, key in UserDefaults.standard.set(value, forKey: key) }
    )
}

@MainActor
final class Localization: ObservableObject {
    static let shared = Localization()
    static let preferenceKey = "CodexLocalMCP.interfaceLanguage"
    static let didChangeNotification = Notification.Name("CodexLocalMCPLanguageDidChange")
    @Published private(set) var language: AppLanguage = .system
    @Published private(set) var resolvedLanguage: AppLanguage = .zhHans
    var resolvedLocale: Locale { resolvedLanguage.locale }
    var locale: Locale { resolvedLocale }
    private(set) var isPreview = true
    private var preferredLanguagesOverride: [String]?
    private let preferences: LocalizationPreferenceStore

    init(preferences: LocalizationPreferenceStore = .standard) { self.preferences = preferences }

    func configure(preview: Bool, language explicit: AppLanguage? = nil, preferredLanguages: [String]? = nil) {
        isPreview = preview
        preferredLanguagesOverride = preferredLanguages
        let selected: AppLanguage
        if let explicit { selected = explicit }
        else if !preview, let stored = preferences.read(Self.preferenceKey) {
            selected = AppLanguage.parse(stored) ?? .system
        } else { selected = .system }
        apply(selected)
    }

    func setLanguage(_ selected: AppLanguage, persist: Bool = true) {
        apply(selected)
        if persist && !isPreview { preferences.write(selected.rawValue, Self.preferenceKey) }
    }

    private func apply(_ selected: AppLanguage) {
        let resolved = selected == .system
            ? AppLanguage.resolve(preferredLanguages: preferredLanguagesOverride ?? Locale.preferredLanguages)
            : selected
        LocalizationRuntime.shared.set(resolved)
        language = selected
        resolvedLanguage = resolved
        NotificationCenter.default.post(name: Self.didChangeNotification, object: self)
    }

    var catalogValidationErrors: [String] { LocalizationRuntime.shared.catalog.validationErrors() }
    var catalogEntryCount: Int { LocalizationRuntime.shared.catalog.strings.count }
}

func L(_ source: String) -> String { LocalizationRuntime.shared.text(source) }
func L(_ source: String, fallback: String) -> String {
    LocalizationRuntime.shared.text(source, fallback: fallback)
}
func LF(_ source: String, _ arguments: CVarArg...) -> String {
    LocalizationRuntime.shared.format(source, arguments: arguments)
}
func LN(_ value: Int) -> String {
    let formatter = NumberFormatter()
    formatter.locale = LocalizationRuntime.shared.resolvedLanguage.locale
    formatter.numberStyle = .decimal
    formatter.maximumFractionDigits = 0
    return formatter.string(from: NSNumber(value: value)) ?? String(value)
}
func LT(_ value: Date, dateStyle: DateFormatter.Style = .none,
        timeStyle: DateFormatter.Style = .short) -> String {
    let formatter = DateFormatter()
    formatter.locale = LocalizationRuntime.shared.resolvedLanguage.locale
    formatter.dateStyle = dateStyle
    formatter.timeStyle = timeStyle
    return formatter.string(from: value)
}
