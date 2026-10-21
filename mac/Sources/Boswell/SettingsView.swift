import SwiftUI

struct SettingsView: View {
    @Environment(Store.self) private var store
    @State private var settings: SettingsState?
    @AppStorage(askTodosKey) private var askTodos = true

    var body: some View {
        Group {
            if let settings { form(settings) } else { ProgressView() }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .navigationTitle("Settings")
        .task { settings = try? await API.get(["api", "settings"]) }
    }

    private func form(_ s: SettingsState) -> some View {
        Form {
            Section("Appearance") {
                LabeledContent {
                    HStack(spacing: 10) {
                        ForEach(s.accents, id: \.self) { name in
                            Button { Task { await save(SettingsChange(accent: name)) } } label: {
                                Circle()
                                    .fill(accentTint(name).gradient)
                                    .frame(width: 20, height: 20)
                                    .overlay {
                                        if name == s.accent {
                                            Circle().fill(.white).frame(width: 7, height: 7)
                                        }
                                    }
                            }
                            .buttonStyle(.plain)
                            .help(name.capitalized)
                            .accessibilityLabel(name.capitalized)
                        }
                    }
                } label: {
                    Text("Accent color")
                    Text("Buttons, links, highlights and charts, here and in the web version.")
                }
            }

            Section("Notes") {
                Toggle(isOn: $askTodos) {
                    Text("Ask to find to-dos before sending")
                    Text("When a note goes to Obsidian before its meeting was checked for to-dos.")
                }
            }

            Section("Claude") {
                LabeledContent {
                    Text(s.key ?? "not set")
                        .font(.system(.body, design: .monospaced))
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                } label: {
                    Text("Anthropic API key")
                    Text(s.key != nil ? "Read from ANTHROPIC_API_KEY in .env." : "Add ANTHROPIC_API_KEY to .env and restart the app.")
                }
                Picker(selection: Binding(get: { s.claudeModel }, set: { id in
                    Task { await save(SettingsChange(claudeModel: id)) }
                })) {
                    ForEach(s.models, id: \.id) { Text($0.label).tag($0.id) }
                } label: {
                    Text("Model")
                    Text(modelHint(s))
                }
            }

            Section {
                HStack(spacing: 12) {
                    tile(money(s.usage.today), "today")
                    tile(money(s.usage.total), s.usage.since.map { "in total since \($0.prefix(10))" } ?? "in total")
                    tile("\(Int(s.usage.requests ?? 0))", "requests to Claude")
                }
                .padding(.vertical, 4)
            } header: {
                Text("Usage")
            } footer: {
                Text("Estimated from list prices for the requests this app makes, counted from when usage tracking was added. Anything else using the same key isn't included, and the Anthropic Console has the exact bill.")
                    .foregroundStyle(.secondary)
            }

            Section("About") {
                LabeledContent("Project folder") {
                    Button(Server.shared.projectDir.path) { NSWorkspace.shared.open(Server.shared.projectDir) }
                        .buttonStyle(.link)
                }
                LabeledContent("Server", value: Server.shared.baseURL.absoluteString)
                LabeledContent("Log") {
                    Button(Server.shared.logFile.lastPathComponent) {
                        NSWorkspace.shared.activateFileViewerSelecting([Server.shared.logFile])
                    }
                    .buttonStyle(.link)
                    .help(Server.shared.logFile.path)
                }
            }
        }
        .formStyle(.grouped)
        .frame(maxWidth: 680)
        .frame(maxWidth: .infinity)
    }

    private func tile(_ value: String, _ label: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value).font(.title2.weight(.semibold)).monospacedDigit()
            Text(label).font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
    }

    private func modelHint(_ s: SettingsState) -> String {
        var hint = "Answers questions on the Ask page and writes notes with Claude."
        if let m = s.models.first(where: { $0.id == s.claudeModel }) {
            hint += " \(price(m.input)) per million tokens in, \(price(m.output)) out."
        }
        return hint
    }

    private func price(_ n: Double) -> String {
        n == n.rounded() ? "$\(Int(n))" : String(format: "$%g", n)
    }

    private func money(_ n: Double?) -> String { String(format: "$%.2f", n ?? 0) }

    private func save(_ change: SettingsChange) async {
        await store.run {
            let updated: SettingsState = try await API.put(["api", "settings"], change)
            settings = updated
            store.accent = updated.accent
        }
    }
}
