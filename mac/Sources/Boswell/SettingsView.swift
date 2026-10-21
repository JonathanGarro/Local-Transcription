import SwiftUI

struct SettingsView: View {
    @Environment(Store.self) private var store
    @State private var settings: SettingsState?
    @State private var enteringKey = false
    @State private var confirmRemove = false
    @State private var check: KeyCheck?
    @State private var checking = false
    @State private var width: CGFloat = 0
    @AppStorage(Prefs.askTodos) private var askTodos = true
    @AppStorage(Prefs.things) private var thingsEnabled = true
    @AppStorage(Prefs.openAfterSend) private var openAfterSend = false

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
                    Text("Buttons, links, highlights and charts.")
                }
            }

            Section("Things 3") {
                Toggle(isOn: $thingsEnabled) {
                    Text("Use Things 3")
                    Text("Find your to-dos in a meeting or an answer and add them to Things. Off hides the To-dos tab, Send to Things and the to-do badges.")
                }
                Toggle(isOn: $askTodos) {
                    Text("Ask to find to-dos before sending")
                    Text("When a note goes to Obsidian before its meeting was checked for to-dos.")
                }
                .disabled(!thingsEnabled)
            }

            Section("Obsidian") {
                Toggle(isOn: $openAfterSend) {
                    Text("Open notes in Obsidian after sending")
                    Text("Shows the note in Obsidian as soon as it's sent.")
                }
            }

            Section("Claude") {
                LabeledContent {
                    // only ever the masked key, a new one is pasted in rather than shown
                    Text(s.key.map { "••••" + $0.suffix(3) } ?? "not set")
                        .font(.system(.body, design: .monospaced))
                        .foregroundStyle(.secondary)
                } label: {
                    Text("Anthropic API key")
                    Text(keyHint(s))
                }
                HStack {
                    if s.keySource == "env" {
                        Button("Move to Keychain") { Task { await keyAction { try await API.post(["api", "settings", "key", "move"]) } } }
                            .buttonStyle(.borderedProminent)
                            .help("Copy the key into the Keychain and take it out of .env")
                    }
                    Button(s.key == nil ? "Add Key..." : "Replace Key...") { enteringKey = true }
                    if s.key != nil {
                        Button(checking ? "Checking" : "Check") { Task { await checkKey() } }
                            .disabled(checking)
                            .help("Make a free request to Anthropic to see that the key works")
                    }
                    if s.keySource == "keychain" {
                        Button("Remove Key...", role: .destructive) { confirmRemove = true }
                    }
                    Spacer()
                    if let check {
                        Label(check.ok ? "The key works" : check.error ?? "The key didn't work",
                              systemImage: check.ok ? "checkmark.circle.fill" : "xmark.octagon.fill")
                            .foregroundStyle(check.ok ? .green : .red)
                            .lineLimit(2)
                    }
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
        // the form fills the page so its scroll bar sits at the window's edge,
        // with the sections kept to a readable width in the middle
        .contentMargins(.horizontal, max(0, (width - 680) / 2), for: .scrollContent)
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { width = $0 }
        .sheet(isPresented: $enteringKey) {
            KeySheet { key in await keyAction { try await API.put(["api", "settings", "key"], ["key": key]) } }
        }
        .confirmationDialog("Remove the API key?", isPresented: $confirmRemove) {
            Button("Remove Key", role: .destructive) { Task { await keyAction { try await API.delete(["api", "settings", "key"]) } } }
        } message: {
            Text("It's deleted from the Keychain, and Ask and Claude notes stop working until you add one again.")
        }
    }

    private func keyHint(_ s: SettingsState) -> String {
        switch s.keySource {
        case "keychain": return "Kept in the macOS Keychain. Used by this app and the scripts."
        case "env": return "Read from ANTHROPIC_API_KEY in .env. Move it to the Keychain to keep it out of a plain text file."
        default: return "Needed for Ask, finding to-dos and Claude notes. Create one in the Anthropic Console."
        }
    }

    // a key change, then everything that depends on having a key looks again
    @discardableResult
    private func keyAction(_ request: () async throws -> SettingsState) async -> Bool {
        do {
            let updated = try await request()
            settings = updated
            check = nil
            store.chat.claudeReady = updated.key != nil
            store.revision += 1
            return true
        } catch {
            store.show(error.localizedDescription)
            return false
        }
    }

    private func checkKey() async {
        checking = true
        defer { checking = false }
        check = try? await API.post(["api", "settings", "key", "check"])
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

// pastes a new key in a password field, so it is never shown
struct KeySheet: View {
    @Environment(\.dismiss) private var dismiss
    let save: (String) async -> Bool
    @State private var key = ""
    @State private var saving = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Anthropic API Key").font(.headline)
            Text("Paste a key from the Anthropic Console. It's saved in the macOS Keychain and only shown masked after this.")
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            SecureField("sk-ant-...", text: $key)
                .textFieldStyle(.roundedBorder)
                .onSubmit { Task { await submit() } }
            HStack {
                Link("Open the Anthropic Console", destination: URL(string: "https://console.anthropic.com/settings/keys")!)
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button(saving ? "Saving" : "Save") { Task { await submit() } }
                    .keyboardShortcut(.defaultAction)
                    .disabled(saving || key.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(20)
        .frame(width: 460)
    }

    private func submit() async {
        let trimmed = key.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        saving = true
        defer { saving = false }
        if await save(trimmed) {
            key = ""
            dismiss()
        }
    }
}
