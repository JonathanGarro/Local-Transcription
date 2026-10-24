import SwiftUI

struct SettingsView: View {
    @Environment(Store.self) private var store
    @State private var settings: SettingsState?
    @State private var enteringKey = false
    @State private var confirmRemove = false
    @State private var check: KeyCheck?
    @State private var checking = false
    @State private var width: CGFloat = 0
    @State private var enteringToken = false
    @State private var confirmRemoveToken = false
    @State private var tokenCheck: KeyCheck?
    @State private var templates: Templates?
    // the template open in the editor, a copy of a built-in one to start from when new
    @State private var editing: TemplateDraft?
    @AppStorage(Prefs.askTodos) private var askTodos = true
    @AppStorage(Prefs.openAfterSend) private var openAfterSend = false
    @AppStorage(Prefs.plaud) private var plaudEnabled = true
    @State private var plaud: PlaudAccount?

    var body: some View {
        Group {
            if let settings { form(settings) } else { ProgressView() }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .navigationTitle("Settings")
        .task {
            settings = try? await API.get(["api", "settings"])
            templates = try? await API.get(["api", "templates"])
            if settings?.plaudInstalled == true { await watchPlaud(try? await API.get(["api", "plaud", "account"])) }
        }
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

            Section("To-dos") {
                Picker(selection: Binding(get: { s.todoApp }, set: { app in
                    Task { await save(SettingsChange(todoApp: app)) }
                })) {
                    Text("None").tag("none")
                    Text("Things 3").tag("things")
                    Text("Todoist").tag("todoist")
                    Text("Reminders").tag("reminders")
                } label: {
                    Text("To-do app")
                    Text(todoAppHint(s))
                }
                if s.todoApp == "todoist" {
                    todoistToken(s)
                }
                Toggle(isOn: $askTodos) {
                    Text("Ask to find to-dos before sending")
                    Text("When a note goes to Obsidian before its meeting was checked for to-dos.")
                }
                .disabled(s.todoApp == "none")
            }

            Section("Notes") {
                Picker(selection: Binding(get: { s.noteTemplate }, set: { name in
                    Task { await save(SettingsChange(noteTemplate: name)) }
                })) {
                    ForEach(templates?.templates ?? [], id: \.name) { t in
                        Text(t.builtIn ? t.name : "\(t.name) (yours)").tag(t.name)
                    }
                } label: {
                    Text("Template")
                    Text("The layout new notes are written in. Notes already written keep theirs, and Rewrite Note can switch one.")
                }
                HStack {
                    if let chosen = templates?.templates.first(where: { $0.name == s.noteTemplate }) {
                        Button(chosen.builtIn ? "View..." : "Edit...") {
                            editing = TemplateDraft(name: chosen.name, text: chosen.text, builtIn: chosen.builtIn, original: chosen.name)
                        }
                        Button("Duplicate...") {
                            editing = TemplateDraft(name: "\(chosen.name) Copy", text: chosen.text, builtIn: false, original: nil)
                        }
                    }
                    Spacer()
                }
            }

            Section("Obsidian") {
                folderRow("Meeting notes folder", path: s.meetingNotesDir, source: s.meetingNotesDirSource,
                          purpose: "Where Send to Obsidian puts notes, and where imported notes are read from.") {
                    SettingsChange(meetingNotesDir: $0)
                }
                folderRow("People folder", path: s.peopleDir, source: s.peopleDirSource,
                          purpose: "One note per person, where their teams and organizations are read and saved.") {
                    SettingsChange(peopleDir: $0)
                }
                Toggle(isOn: $openAfterSend) {
                    Text("Open notes in Obsidian after sending")
                    Text("Shows the note in Obsidian as soon as it's sent.")
                }
            }

            Section("Plaud") {
                if s.plaudInstalled {
                    Toggle(isOn: $plaudEnabled) {
                        Text("Use Plaud")
                        Text("Import recordings uploaded to Plaud and transcribe them here. Off hides the Plaud page.")
                    }
                    plaudAccount
                        .disabled(!plaudEnabled)
                } else {
                    LabeledContent {
                        Button("Copy Command") {
                            NSPasteboard.general.clearContents()
                            NSPasteboard.general.setString("npm install -g @plaud-ai/cli", forType: .string)
                        }
                    } label: {
                        Text("Plaud CLI not installed")
                        Text("Importing from Plaud needs its command line tool. Install it with npm install -g @plaud-ai/cli in Terminal, then restart Boswell.")
                    }
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
        .sheet(item: $editing) { draft in
            TemplateEditor(draft: draft) { updated in
                templates = updated
                if let fresh: SettingsState = try? await API.get(["api", "settings"]) {
                    settings = fresh
                    store.apply(fresh)
                }
            }
        }
        .sheet(isPresented: $enteringToken) {
            KeySheet(title: "Todoist API Token",
                     message: "Copy your token from Todoist's Settings, Integrations, Developer. It's saved in the macOS Keychain and only shown masked after this.",
                     placeholder: "Token", linkTitle: "Open Todoist's Developer Settings",
                     link: URL(string: "https://app.todoist.com/app/settings/integrations/developer")!) { token in
                await tokenAction { try await API.put(["api", "settings", "todoist"], ["key": token]) }
            }
        }
        .confirmationDialog("Remove the Todoist token?", isPresented: $confirmRemoveToken) {
            Button("Remove Token", role: .destructive) { Task { await tokenAction { try await API.delete(["api", "settings", "todoist"]) } } }
        } message: {
            Text("It's deleted from the Keychain, and to-dos can't be sent to Todoist until you add one again.")
        }
        .confirmationDialog("Remove the API key?", isPresented: $confirmRemove) {
            Button("Remove Key", role: .destructive) { Task { await keyAction { try await API.delete(["api", "settings", "key"]) } } }
        } message: {
            Text("It's deleted from the Keychain, and Ask and Claude notes stop working until you add one again.")
        }
    }

    @ViewBuilder
    private var plaudAccount: some View {
        LabeledContent {
            if let plaud, plaud.signingIn {
                ProgressView().controlSize(.small)
            } else if plaud?.signedIn == true {
                Button("Sign Out") { Task { await plaudAction(["api", "plaud", "logout"]) } }
            } else if plaud != nil {
                Button("Sign In...") { Task { await plaudAction(["api", "plaud", "login"]) } }
                    .buttonStyle(.borderedProminent)
                    .help("Open Plaud's sign in page in your browser")
            } else {
                ProgressView().controlSize(.small)
            }
        } label: {
            Text("Account")
            Text(plaudHint)
        }
        if let error = plaud?.error {
            Label(error, systemImage: "xmark.octagon.fill")
                .foregroundStyle(.red)
                .lineLimit(2)
        }
    }

    private var plaudHint: String {
        guard let plaud else { return "Checking..." }
        if plaud.signingIn { return "Finish signing in in your browser. This page updates when you're done." }
        if plaud.signedIn { return "Signed in as \(plaud.email ?? plaud.name ?? "your Plaud account")." }
        return "Sign in to see your recordings on the Plaud page. It opens in your browser."
    }

    private func plaudAction(_ parts: [String]) async {
        do {
            await watchPlaud(try await API.post(parts))
        } catch {
            store.show(error.localizedDescription)
        }
    }

    // follows a sign in until the browser is done with it
    private func watchPlaud(_ account: PlaudAccount?) async {
        plaud = account
        while plaud?.signingIn == true, !Task.isCancelled {
            try? await Task.sleep(for: .seconds(1.5))
            if let next: PlaudAccount = try? await API.get(["api", "plaud", "account"]) { plaud = next }
        }
    }

    // a folder in the vault, picked in an open panel. one chosen here wins over .env
    private func folderRow(_ title: String, path: String?, source: String?, purpose: String,
                           change: @escaping (String) -> SettingsChange) -> some View {
        LabeledContent {
            HStack {
                if source == "settings" {
                    Button("Reset") { Task { await save(change("")) } }
                        .help("Go back to the folder in .env, if it names one")
                }
                Button("Choose...") {
                    let panel = NSOpenPanel()
                    panel.canChooseFiles = false
                    panel.canChooseDirectories = true
                    panel.canCreateDirectories = true
                    panel.prompt = "Choose"
                    panel.message = purpose
                    if let path { panel.directoryURL = URL(fileURLWithPath: path) }
                    if panel.runModal() == .OK, let url = panel.url {
                        Task { await save(change(url.path)) }
                    }
                }
            }
        } label: {
            Text(title)
            if let path {
                Text(path)
                    .truncationMode(.middle)
                    .lineLimit(1)
                    .help(path)
            }
            Text(path == nil ? "\(purpose) Not chosen yet."
                 : source == "env" ? "\(purpose) Set in .env." : purpose)
        }
    }

    @ViewBuilder
    private func todoistToken(_ s: SettingsState) -> some View {
        LabeledContent {
            Text(s.todoistToken.map { "••••" + $0.suffix(3) } ?? "not set")
                .font(.system(.body, design: .monospaced))
                .foregroundStyle(.secondary)
        } label: {
            Text("Todoist API token")
            Text(s.todoistSource == "env" ? "Read from TODOIST_API_TOKEN in .env."
                 : s.todoistToken == nil ? "Needed to add to-dos and read your projects. Find it in Todoist's Settings, Integrations, Developer."
                 : "Kept in the macOS Keychain.")
        }
        HStack {
            Button(s.todoistToken == nil ? "Add Token..." : "Replace Token...") { enteringToken = true }
            if s.todoistToken != nil {
                Button("Check") { Task { tokenCheck = try? await API.post(["api", "settings", "todoist", "check"]) } }
                    .help("Ask Todoist for your projects to see that the token works")
            }
            if s.todoistSource == "keychain" {
                Button("Remove Token...", role: .destructive) { confirmRemoveToken = true }
            }
            Spacer()
            if let tokenCheck {
                Label(tokenCheck.ok ? "The token works" : tokenCheck.error ?? "The token didn't work",
                      systemImage: tokenCheck.ok ? "checkmark.circle.fill" : "xmark.octagon.fill")
                    .foregroundStyle(tokenCheck.ok ? .green : .red)
                    .lineLimit(2)
            }
        }
    }

    private func todoAppHint(_ s: SettingsState) -> String {
        switch s.todoApp {
        case "things":
            return s.thingsInstalled ? "Find your to-dos in a meeting or an answer and add them to Things, with a start date and a deadline."
                : "Things 3 isn't in your Applications folder."
        case "todoist":
            return "Add your to-dos to Todoist. A to-do's date becomes its due date, and its deadline Todoist's deadline."
        case "reminders":
            return "Add your to-dos to Reminders, in a list you pick. The deadline becomes the due date, or the date when there's no deadline. The first one asks for permission to control Reminders."
        default:
            return "Off hides the To-dos tab, sending to-dos and the to-do badges."
        }
    }

    // a token change, then the page shows the new state
    @discardableResult
    private func tokenAction(_ request: () async throws -> SettingsState) async -> Bool {
        do {
            settings = try await request()
            tokenCheck = nil
            return true
        } catch {
            store.show(error.localizedDescription)
            return false
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
            store.apply(updated)
        }
    }
}

// pastes a new key in a password field, so it is never shown
struct KeySheet: View {
    @Environment(\.dismiss) private var dismiss
    var title = "Anthropic API Key"
    var message = "Paste a key from the Anthropic Console. It's saved in the macOS Keychain and only shown masked after this."
    var placeholder = "sk-ant-..."
    var linkTitle = "Open the Anthropic Console"
    var link = URL(string: "https://console.anthropic.com/settings/keys")!
    let save: (String) async -> Bool
    @State private var key = ""
    @State private var saving = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(title).font(.headline)
            Text(message)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            SecureField(placeholder, text: $key)
                .textFieldStyle(.roundedBorder)
                .onSubmit { Task { await submit() } }
            HStack {
                Link(linkTitle, destination: link)
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

struct TemplateDraft: Identifiable {
    var name: String
    var text: String
    let builtIn: Bool
    // the saved name it was opened as, nil for a new one
    let original: String?
    var id: String { original ?? "new" }
}

// a note template as markdown with placeholders, with a list of them beside it. built-in
// templates are read only, and duplicating one is how yours start
struct TemplateEditor: View {
    @Environment(\.dismiss) private var dismiss
    @State var draft: TemplateDraft
    let saved: (Templates) async -> Void
    @State private var error: String?
    @State private var saving = false
    @State private var confirmDelete = false

    private static let placeholders: [(String, String)] = [
        ("{{date}}", "the meeting's date"),
        ("{{title}}", "the meeting's name"),
        ("{{people}}", "who was there, other than you, as links"),
        ("{{teams}}", "their teams"),
        ("{{organizations}}", "their organizations"),
        ("{{project}}", "the meeting's project, as a link"),
        ("{{transcript_link}}", "a link that opens the transcript in Boswell"),
        ("{{transcript}}", "the transcript, on a line of its own"),
        ("{{action_items}}", "links to to-dos sent, on a line of its own"),
        ("{{paragraph: ...}}", "a paragraph the model writes, as you describe it"),
        ("{{bullets: ...}}", "a list the model writes, on a line of its own"),
        ("{{topics: ...}}", "topics with points under each, on a line of its own"),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                if draft.builtIn {
                    Text(draft.name).font(.headline)
                    Text("Built in, so duplicate it to make changes").foregroundStyle(.secondary)
                } else {
                    TextField("Template name", text: $draft.name)
                        .textFieldStyle(.roundedBorder)
                        .frame(maxWidth: 280)
                }
                Spacer()
            }
            HStack(alignment: .top, spacing: 14) {
                TextEditor(text: $draft.text)
                    .font(.system(.body, design: .monospaced))
                    .disabled(draft.builtIn)
                    .scrollContentBackground(.hidden)
                    .padding(6)
                    .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 6))
                ScrollView {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Placeholders").font(.headline)
                        ForEach(Self.placeholders, id: \.0) { code, meaning in
                            VStack(alignment: .leading, spacing: 1) {
                                Text(code).font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                                Text(meaning).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        Text("A list after a property, as in people: {{people}}, becomes an Obsidian list. A section whose placeholders all come out empty is left out. The first # heading names the note's file.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .padding(.top, 4)
                    }
                }
                .frame(width: 230)
            }
            if let error {
                Label(error, systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.red)
            }
            HStack {
                if !draft.builtIn, draft.original != nil {
                    Button("Delete...", role: .destructive) { confirmDelete = true }
                }
                Spacer()
                Button(draft.builtIn ? "Done" : "Cancel", role: .cancel) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                if !draft.builtIn {
                    Button(saving ? "Saving" : "Save") { Task { await save() } }
                        .keyboardShortcut(.defaultAction)
                        .disabled(saving || draft.name.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
        }
        .padding(20)
        .frame(width: 820, height: 560)
        .confirmationDialog("Delete \(draft.original ?? "")?", isPresented: $confirmDelete) {
            Button("Delete Template", role: .destructive) { Task { await delete() } }
        } message: {
            Text("Notes already written in it keep their layout.")
        }
    }

    private func save() async {
        saving = true
        defer { saving = false }
        let name = draft.name.trimmingCharacters(in: .whitespaces)
        do {
            var updated: Templates = try await API.put(["api", "templates", name], ["text": draft.text])
            // a renamed template replaces the old one
            if let original = draft.original, original != name {
                updated = try await API.delete(["api", "templates", original])
            }
            // the editor opens on the chosen template or a copy of it, so what's saved is the choice
            let _: SettingsState = try await API.put(["api", "settings"], SettingsChange(noteTemplate: name))
            await saved(updated)
            dismiss()
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func delete() async {
        guard let original = draft.original else { return }
        do {
            let updated: Templates = try await API.delete(["api", "templates", original])
            await saved(updated)
            dismiss()
        } catch {
            self.error = error.localizedDescription
        }
    }
}
