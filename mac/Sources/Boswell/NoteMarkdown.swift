import SwiftUI

// a meeting note as formatted markdown, read only: the frontmatter as properties like
// obsidian's, then headings, nested lists, checkboxes, quotes and paragraphs. bold, italic,
// code and links work inline, and [[wikilinks]] show in the accent color
struct NoteMarkdown: View {
    @Environment(Store.self) private var store
    let text: String
    // the body text size in points, the rest scales from it
    var size: CGFloat = 13
    // the transcript property opens the meeting's transcript tab, since its file is outside
    // the vault and obsidian could not open it
    var openTranscript: (() -> Void)?

    private struct Property: Hashable {
        let key: String
        let values: [String]
    }

    private enum Block: Hashable {
        case heading(Int, String)
        case bullet(Int, String, checked: Bool?)
        case numbered(Int, String, String)
        case quote(String)
        case rule
        case paragraph(String)
        // a fenced block, such as the transcript, with its lines as they are
        case code(String)
    }

    var body: some View {
        let (properties, rest) = Self.split(text)
        let blocks = Self.blocks(rest)
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                if !properties.isEmpty { propertiesBox(properties) }
                ForEach(Array(blocks.enumerated()), id: \.offset) { i, block in
                    view(block)
                        .padding(.top, i == 0 ? 0 : Self.gap(blocks[i - 1], block))
                }
            }
            .font(.system(size: size))
            .textSelection(.enabled)
            .padding(24)
            .frame(maxWidth: 780, alignment: .leading)
            // the note sits in the middle of the pane
            .frame(maxWidth: .infinity)
        }
    }

    // frontmatter

    private func propertiesBox(_ properties: [Property]) -> some View {
        Grid(alignment: .leadingFirstTextBaseline, horizontalSpacing: 16, verticalSpacing: 8) {
            ForEach(properties, id: \.key) { p in
                GridRow {
                    Text(p.key)
                        .foregroundStyle(.secondary)
                        .gridColumnAlignment(.leading)
                    values(p)
                }
            }
        }
        .font(.system(size: size - 1))
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 8))
        .padding(.bottom, 18)
    }

    @ViewBuilder
    private func values(_ p: Property) -> some View {
        if p.values.isEmpty {
            Text("Empty").foregroundStyle(.tertiary)
        } else if p.key == "transcript", let openTranscript, p.values.count == 1, URL(string: p.values[0])?.scheme != nil {
            Button("Show transcript", action: openTranscript)
                .buttonStyle(.link)
                .help("Show the transcript")
        } else if p.values.count == 1, let url = URL(string: p.values[0]), url.scheme == "file" {
            let name = url.deletingLastPathComponent().lastPathComponent.removingPercentEncoding ?? url.lastPathComponent
            if let openTranscript, url.lastPathComponent == "transcript.md" {
                Button(name, action: openTranscript)
                    .buttonStyle(.link)
                    .help("Show the transcript")
            } else {
                Link(name, destination: url)
                    .help(url.path)
            }
        } else {
            FlowLayout(spacing: 6, trailing: false) {
                ForEach(p.values, id: \.self) { value in
                    let name = Self.unlink(value)
                    if name != value {
                        Text(name)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 2)
                            .foregroundStyle(store.accentColor)
                            .background(store.accentColor.opacity(0.12), in: Capsule())
                    } else {
                        Text(value)
                    }
                }
            }
        }
    }

    // '[[Name]]' or '[[Name|alias]]' as the name shown, anything else as it is
    private static func unlink(_ value: String) -> String {
        guard let m = value.firstMatch(of: #/^\[\[([^\]|]+)(?:\|([^\]]+))?\]\]$/#) else { return value }
        return String(m.2 ?? m.1)
    }

    private static func split(_ text: String) -> ([Property], String) {
        let lines = text.components(separatedBy: "\n")
        guard lines.first?.trimmingCharacters(in: .whitespaces) == "---",
              let end = lines.dropFirst().firstIndex(where: { $0.trimmingCharacters(in: .whitespaces) == "---" }) else {
            return ([], text)
        }
        var properties: [Property] = []
        var key: String?
        var values: [String] = []
        func finish() {
            if let key { properties.append(Property(key: key, values: values)) }
            values = []
        }
        for line in lines[1..<end] {
            if let item = line.firstMatch(of: #/^\s*-\s+(.*)$/#) {
                values.append(clean(String(item.1)))
            } else if let pair = line.firstMatch(of: #/^([^:\s][^:]*):\s*(.*)$/#) {
                finish()
                key = String(pair.1)
                let value = clean(String(pair.2))
                if !value.isEmpty { values.append(value) }
            }
        }
        finish()
        return (properties, lines[(end + 1)...].joined(separator: "\n"))
    }

    // yaml quotes around a value
    private static func clean(_ value: String) -> String {
        var v = value.trimmingCharacters(in: .whitespaces)
        if v.count >= 2, let first = v.first, (first == "\"" || first == "'"), v.last == first {
            v = String(v.dropFirst().dropLast())
        }
        return v
    }

    // the body

    private static func blocks(_ body: String) -> [Block] {
        let lines = body.components(separatedBy: "\n")
        // list depth comes from the indents actually used, so two, four spaces or a tab all nest
        let indents = Set(lines.compactMap { line -> Int? in
            line.firstMatch(of: #/^(\s*)(?:[-*+]|\d+[.)])\s/#).map { width(String($0.1)) }
        }).sorted()
        func level(_ space: Substring) -> Int { indents.firstIndex(of: width(String(space))) ?? 0 }

        var blocks: [Block] = []
        var paragraph: [String] = []
        var fence: [String]?
        func flush() {
            if !paragraph.isEmpty { blocks.append(.paragraph(paragraph.joined(separator: " "))) }
            paragraph = []
        }
        for line in lines {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.hasPrefix("```") {
                if let open = fence {
                    blocks.append(.code(open.joined(separator: "\n")))
                    fence = nil
                } else {
                    flush()
                    fence = []
                }
            } else if fence != nil {
                fence?.append(line)
            } else if trimmed.isEmpty {
                flush()
            } else if let m = line.firstMatch(of: #/^(#{1,6})\s+(.*)$/#) {
                flush()
                blocks.append(.heading(m.1.count, String(m.2)))
            } else if let m = line.firstMatch(of: #/^(\s*)[-*+]\s+\[([ xX])\]\s+(.*)$/#) {
                flush()
                blocks.append(.bullet(level(m.1), String(m.3), checked: m.2 != " "))
            } else if let m = line.firstMatch(of: #/^(\s*)[-*+]\s+(.*)$/#) {
                flush()
                blocks.append(.bullet(level(m.1), String(m.2), checked: nil))
            } else if let m = line.firstMatch(of: #/^(\s*)(\d+)[.)]\s+(.*)$/#) {
                flush()
                blocks.append(.numbered(level(m.1), String(m.2), String(m.3)))
            } else if trimmed.firstMatch(of: #/^(-{3,}|\*{3,}|_{3,})$/#) != nil {
                flush()
                blocks.append(.rule)
            } else if let m = line.firstMatch(of: #/^\s*>\s?(.*)$/#) {
                flush()
                blocks.append(.quote(String(m.1)))
            } else {
                paragraph.append(trimmed)
            }
        }
        flush()
        // a fence left open runs to the end, as obsidian shows it
        if let open = fence { blocks.append(.code(open.joined(separator: "\n"))) }
        return blocks
    }

    // items of one list sit close together, other blocks get room
    private static func gap(_ before: Block, _ block: Block) -> CGFloat {
        func listed(_ b: Block) -> Bool {
            switch b {
            case .bullet, .numbered: true
            default: false
            }
        }
        return listed(before) && listed(block) ? 5 : 12
    }

    private static func width(_ space: String) -> Int {
        space.reduce(0) { $0 + ($1 == "\t" ? 4 : 1) }
    }

    @ViewBuilder
    private func view(_ block: Block) -> some View {
        switch block {
        case .heading(let n, let s):
            Text(Self.inline(s, accent: store.accentColor))
                .font(.system(size: size * (n == 1 ? 1.75 : n == 2 ? 1.35 : n == 3 ? 1.15 : 1),
                              weight: n == 1 ? .bold : .semibold))
                .padding(.top, n <= 2 ? 10 : 4)
        case .bullet(let level, let s, let checked):
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                if let checked {
                    Image(systemName: checked ? "checkmark.square.fill" : "square")
                        .foregroundStyle(checked ? store.accentColor : .secondary)
                } else {
                    Text(["•", "◦", "▪"][level % 3]).foregroundStyle(.secondary)
                }
                Text(Self.inline(s, accent: store.accentColor))
                    .strikethrough(checked == true, color: .secondary)
                    .foregroundStyle(checked == true ? .secondary : .primary)
            }
            .padding(.leading, CGFloat(level) * 20 + 4)
        case .numbered(let level, let n, let s):
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text("\(n).").foregroundStyle(.secondary).monospacedDigit()
                Text(Self.inline(s, accent: store.accentColor))
            }
            .padding(.leading, CGFloat(level) * 20 + 4)
        case .quote(let s):
            Text(Self.inline(s, accent: store.accentColor))
                .foregroundStyle(.secondary)
                .padding(.leading, 12)
                .overlay(alignment: .leading) { Rectangle().fill(.quaternary).frame(width: 3) }
        case .rule:
            Divider().padding(.vertical, 6)
        case .paragraph(let s):
            Text(Self.inline(s, accent: store.accentColor)).lineSpacing(2)
        case .code(let s):
            Text(s)
                .font(.system(size: size - 1, design: .monospaced))
                .lineSpacing(3)
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 8))
        }
    }

    // [[wikilinks]] become links to a placeholder scheme while markdown is parsed, then plain
    // accent colored text, since they have nowhere to open here
    private static func inline(_ s: String, accent: Color) -> AttributedString {
        let marked = s.replacing(#/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/#) { m in
            let label = String(m.2 ?? m.1)
            return "[\(label)](wikilink:\(label.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? ""))"
        }
        guard var result = try? AttributedString(markdown: marked, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)) else {
            return AttributedString(s)
        }
        for run in result.runs where run.link?.scheme == "wikilink" {
            result[run.range].link = nil
            result[run.range].foregroundColor = accent
        }
        return result
    }
}
