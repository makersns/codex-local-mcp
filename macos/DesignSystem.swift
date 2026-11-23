import SwiftUI
import AppKit

enum Palette {
    static let accent = Color(red: 0.39, green: 0.35, blue: 0.91)
    static let ink = Color(red: 0.10, green: 0.12, blue: 0.16)
    static let secondary = Color(red: 0.43, green: 0.46, blue: 0.52)
    static let sidebar = Color(red: 0.96, green: 0.965, blue: 0.98)
    static let line = Color(red: 0.88, green: 0.89, blue: 0.92)
    static let tint = Color(red: 0.95, green: 0.94, blue: 0.995)
    static let success = Color(red: 0.20, green: 0.55, blue: 0.32)
}

struct PrimaryActionStyle: ButtonStyle {
    @Environment(\.isEnabled) var enabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.font(.system(size: 13, weight: .semibold))
            .padding(.horizontal, 17).frame(minHeight: 38)
            .foregroundColor(enabled ? .white : Palette.secondary)
            .background(enabled ? Palette.accent.opacity(configuration.isPressed ? 0.83 : 1) : Palette.line.opacity(0.7))
            .clipShape(RoundedRectangle(cornerRadius: 9))
    }
}
struct SecondaryActionStyle: ButtonStyle {
    @Environment(\.isEnabled) var enabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.font(.system(size: 13, weight: .medium))
            .padding(.horizontal, 13).frame(minHeight: 36)
            .foregroundColor(enabled ? Palette.ink : Palette.secondary.opacity(0.55))
            .background(configuration.isPressed ? Palette.sidebar : Color.white)
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Palette.line, lineWidth: 1))
    }
}
struct QuietActionStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.font(.system(size: 13, weight: .medium))
            .foregroundColor(Palette.accent.opacity(configuration.isPressed ? 0.6 : 1))
            .padding(.vertical, 5)
    }
}
struct FieldStyle: ViewModifier {
    var error = false
    func body(content: Content) -> some View {
        content.textFieldStyle(.plain).font(.system(size: 14))
            .padding(.horizontal, 12).frame(minHeight: 42)
            .background(Color.white).clipShape(RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(error ? Color.red.opacity(0.7) : Palette.line, lineWidth: 1))
    }
}
extension View {
    func appField(error: Bool = false) -> some View { modifier(FieldStyle(error: error)) }
    func panel(padding: CGFloat = 22) -> some View {
        self.frame(maxWidth: .infinity, alignment: .leading).padding(padding).background(Color.white).clipShape(RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(Palette.line, lineWidth: 1))
    }
}

struct PageTitle: View {
    let title: String
    let subtitle: String
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.system(size: 27, weight: .bold)).tracking(-0.5).foregroundColor(Palette.ink)
            Text(subtitle).font(.system(size: 13)).foregroundColor(Palette.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }
}
struct FieldBlock<Content: View>: View {
    let title: String
    let hint: String?
    let error: String?
    let content: Content
    init(_ title: String, hint: String? = nil, error: String? = nil, @ViewBuilder content: () -> Content) {
        self.title = title; self.hint = hint; self.error = error; self.content = content()
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.system(size: 13, weight: .semibold)).foregroundColor(Palette.ink)
            content
            if let error, !error.isEmpty { Text(error).font(.system(size: 12)).foregroundColor(.red).fixedSize(horizontal: false, vertical: true) }
            else if let hint, !hint.isEmpty { Text(hint).font(.system(size: 12)).foregroundColor(Palette.secondary).fixedSize(horizontal: false, vertical: true) }
        }
    }
}
struct AccessPill: View {
    let text: String
    var enabled = true
    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: enabled ? "checkmark" : "minus").font(.system(size: 9, weight: .semibold))
            Text(text).font(.system(size: 11, weight: .medium))
        }.padding(.horizontal, 8).padding(.vertical, 5)
            .foregroundColor(enabled ? Palette.success : Palette.secondary)
            .background(enabled ? Palette.success.opacity(0.08) : Palette.sidebar)
            .clipShape(RoundedRectangle(cornerRadius: 6))
    }
}
struct PermissionToggle: View {
    let title: String
    let detail: String
    @Binding var value: Bool
    var identifier: String = ""
    var body: some View {
        HStack(alignment: .center, spacing: 18) {
            VStack(alignment: .leading, spacing: 5) {
                Text(title).font(.system(size: 13, weight: .semibold)).foregroundColor(Palette.ink)
                Text(detail).font(.system(size: 12)).foregroundColor(Palette.secondary).fixedSize(horizontal: false, vertical: true)
            }.frame(maxWidth: .infinity, alignment: .leading)
            Toggle(title, isOn: $value).labelsHidden().toggleStyle(.switch).tint(Palette.accent)
                .accessibilityLabel(title).accessibilityIdentifier(identifier)
        }.padding(.vertical, 11)
    }
}
struct InformationLine: View {
    let text: String
    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "info.circle").foregroundColor(Palette.accent)
            Text(text).font(.system(size: 12)).foregroundColor(Palette.secondary).fixedSize(horizontal: false, vertical: true)
        }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
            .background(Palette.tint).clipShape(RoundedRectangle(cornerRadius: 8))
    }
}
struct EmptyState: View {
    let symbol: String
    let title: String
    let detail: String
    var body: some View {
        VStack(spacing: 13) {
            Image(systemName: symbol).font(.system(size: 32, weight: .light)).foregroundColor(Palette.accent)
            Text(title).font(.system(size: 17, weight: .semibold)).foregroundColor(Palette.ink)
            Text(detail).font(.system(size: 13)).foregroundColor(Palette.secondary)
                .multilineTextAlignment(.center).frame(maxWidth: 340).fixedSize(horizontal: false, vertical: true)
        }.padding(28).frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
