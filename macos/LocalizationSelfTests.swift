import Foundation

extension NativeSelfTests {
    @MainActor
    static func runLocalization() throws -> [String] {
        var checks: [String] = []
        func check(_ condition: Bool, _ name: String) throws {
            guard condition else { throw BridgeError.message("Localization self-test failed: " + name) }
            checks.append("localization-" + name)
        }

        let previousLanguage = Localization.shared.language
        let previousResolved = Localization.shared.resolvedLanguage
        let previousPreview = Localization.shared.isPreview
        defer {
            Localization.shared.configure(preview: previousPreview, language: previousLanguage,
                                          preferredLanguages: [previousResolved.rawValue])
        }

        try check(AppLanguage.parse("zh-Hans-TW") == .zhHans && AppLanguage.parse("zh-Hant-CN") == .zhHant,
                  "script-precedes-region")
        try check(["zh-TW", "zh_HK", "zh-MO"].allSatisfy { AppLanguage.parse($0) == .zhHant },
                  "traditional-regions")
        try check(AppLanguage.parse("zh-CN") == .zhHans && AppLanguage.parse("ja-JP") == .ja
                  && AppLanguage.parse("en-GB") == .en, "locale-aliases")
        try check(AppLanguage.resolve(preferredLanguages: ["fr-FR", "ja-JP", "en-US"]) == .ja
                  && AppLanguage.resolve(preferredLanguages: ["fr-FR"]) == .en
                  && AppLanguage.resolve(preferredLanguages: []) == .en, "unsupported-system-fallback")
        try check(AppLanguage.parse("unknown") == nil && AppLanguage.parse("") == nil, "unknown-selection-rejected")

        var reads = 0
        var writes: [(String, String)] = []
        var stored = "zh-Hant"
        let isolated = Localization(preferences: LocalizationPreferenceStore(
            read: { key in reads += 1; return key == Localization.preferenceKey ? stored : nil },
            write: { value, key in writes.append((value, key)); stored = value }
        ))
        isolated.configure(preview: true, preferredLanguages: ["ja-JP"])
        isolated.setLanguage(.en, persist: true)
        try check(reads == 0 && writes.isEmpty && isolated.resolvedLanguage == .en,
                  "preview-never-reads-or-writes-preferences")
        isolated.configure(preview: false, preferredLanguages: ["en-US"])
        try check(reads == 1 && isolated.language == .zhHant && isolated.resolvedLanguage == .zhHant,
                  "stored-selection-restored")
        isolated.setLanguage(.ja)
        try check(writes.count == 1 && writes[0].0 == "ja" && writes[0].1 == Localization.preferenceKey,
                  "only-own-preference-written")
        isolated.setLanguage(.en, persist: false)
        try check(writes.count == 1, "nonpersisting-selection")
        stored = "unsupported-language"
        isolated.configure(preview: false, preferredLanguages: ["zh-HK"])
        try check(isolated.language == .system && isolated.resolvedLanguage == .zhHant, "invalid-preference-system-fallback")
        let beforeExplicit = reads
        isolated.configure(preview: false, language: .en, preferredLanguages: ["ja"])
        try check(reads == beforeExplicit && isolated.resolvedLanguage == .en, "explicit-selection-skips-preference-read")

        let catalog = LocalizationCatalog.load()
        try check(catalog.strings.count >= 500 && catalog.validationErrors().isEmpty,
                  "complete-four-language-catalog")
        let missing = LocalizationCatalog(schemaVersion: 1, sourceLanguage: "zh-Hans",
            strings: ["源": ["en": "Source", "zh-Hans": "源"]])
        try check(missing.validationErrors().count == 2, "missing-translations-detected")
        let invalid = LocalizationCatalog(schemaVersion: 1, sourceLanguage: "zh-Hans",
            strings: ["值 %@": ["en": "Value %d", "zh-Hans": "值 %@", "zh-Hant": "值 %@", "ja": "値 %@"]])
        try check(invalid.validationErrors().count == 1, "format-type-mismatch-detected")
        let invalidTemplate = LocalizationCatalog(schemaVersion: 1, sourceLanguage: "zh-Hans",
            strings: ["值 {value_1}": ["en": "Value {value_2}", "zh-Hans": "值 {value_1}",
                                     "zh-Hant": "值 {value_1}", "ja": "値 {value_1}"]])
        try check(invalidTemplate.validationErrors().count == 1, "template-placeholder-mismatch-detected")
        try check(!LocalizationCatalog.empty.validationErrors().isEmpty, "missing-catalog-rejected")

        for language in [AppLanguage.en, .zhHans, .zhHant, .ja] {
            isolated.configure(preview: true, language: language)
            try check(L("连接设置") == catalog.strings["连接设置"]?[language.rawValue]
                      && !LF("会话 %d", 7).contains("%d") && LF("会话 %d", 7).contains("7"),
                      language.rawValue + "-lookup-and-integer-format")
            let userValue = "My 100% folder · 資料 {value_1}"
            try check(LF("移除“%@”的访问权限？", userValue).contains(userValue),
                      language.rawValue + "-user-content-preserved")
            let expectedNumber = NumberFormatter()
            expectedNumber.locale = language.locale
            expectedNumber.numberStyle = .decimal
            expectedNumber.maximumFractionDigits = 0
            try check(LN(1234567) == expectedNumber.string(from: NSNumber(value: 1234567))
                      && !LT(Date(timeIntervalSince1970: 1700000000), dateStyle: .medium).isEmpty,
                      language.rawValue + "-locale-number-and-date")
        }
        isolated.setLanguage(.en, persist: false)
        try check(LF("会话 %d", "wrong") == L("会话 %d") && LF("会话 %d") == L("会话 %d")
                  && LF("Unsafe %n", 1) == "Unsafe %n", "invalid-format-arguments-do-not-execute")
        try check(L("Untouched user folder 文件") == "Untouched user folder 文件", "unknown-text-stays-unchanged")
        try check(L("Unknown OS error 原文", fallback: "请检查这一项设置。") == "Please check this setting.",
                  "unknown-error-uses-localized-fallback")
        let portMessage = "本机端口 8712 正由其他进程使用。请先停止原来的终端隧道或占用此端口的程序，再打开本应用。"
        try check(L(portMessage).hasPrefix("Local port 8712 is in use"),
                  "known-backend-template-translated")
        try check(L("prefix " + portMessage) == "prefix " + portMessage,
                  "backend-template-requires-whole-message")
        let systemMessage = "系统操作失败：/Users/demo/100% 資料 {value_1}"
        try check(L(systemMessage) == "System operation failed: /Users/demo/100% 資料 {value_1}",
                  "backend-template-captures-never-retranslated")
        isolated.setLanguage(.zhHans, persist: false)
        try check(L("未知系统错误", fallback: "请检查这一项设置。") == "未知系统错误",
                  "source-language-keeps-original-error")
        return checks
    }
}
