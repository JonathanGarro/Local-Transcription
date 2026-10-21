import SwiftUI

struct NoteView: View {
    @Environment(Store.self) private var store
    let meetingId: String
    @State private var note: Note?
    @State private var lookups: Lookups?
    @State private var text = ""
    @State private var saved = ""
    @State private var saveTask: Task<Void, Never>?
    @State private var project = ""
    @State private var backend = "local"
    @State private var showChanges = false
    @State private var inspectorTab = InspectorTab.changes
    @State private var todoState = TodoState.idle
    @State private var claudeReady = false
    @State private var diff: NoteDiff?
    @State private var conflict: String?
    @State private var confirmDiscard = false
    @State private var confirmRewrite = false
    @State private var confirmPull = false
    @State private var confirmTodos = false
    @AppStorage(Prefs.askTodos) private var askTodos = true
    @AppStorage(Prefs.things) private var thingsEnabled = true
    @AppStorage(Prefs.openAfterSend) private var openAfterSend = false
    @AppStorage(Prefs.noteMode) private var mode = NoteMode.preview

    private var job: Job? { store.noteJob(meetingId) ?? note?.job.flatMap { $0.running ? $0 : nil } }
    private var sent: Bool { note?.sentPath != nil }

    var body: some View {
        Group {
            if let job {
                VStack(spacing: 12) {
                    ProgressView(value: job.status == "processing" ? job.progress : nil)
                        .frame(width: 260)
                    Text(job.step ?? "Waiting to write the note")
                        .foregroundStyle(.secondary)
                }
            } else if let note, !note.exists {
                writeForm
            } else if note != nil {
                editor
            } else {
                ProgressView()
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .toolbar { if note?.exists == true && job == nil { toolbar } }
        .inspector(isPresented: $showChanges) {
            VStack(spacing: 0) {
                // with things off, the inspector is only the changes
                if thingsEnabled {
                    Picker("Show", selection: $inspectorTab) {
                        ForEach(InspectorTab.allCases, id: \.self) { Text($0.rawValue).tag($0) }
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                    .padding(10)
                    Divider()
                }
                switch thingsEnabled ? inspectorTab : .changes {
                case .changes: DiffView(diff: diff)
                case .todos: todos
                }
            }
            .frame(maxHeight: .infinity, alignment: .top)
            .inspectorColumnWidth(min: 320, ideal: 440)
        }
        .task(id: store.revision) { await load() }
        .onChange(of: job == nil) { if job == nil { Task { await load() } } }
        .onChange(of: text) { scheduleSave() }
        .onChange(of: showChanges) { if showChanges { Task { await loadDiff() } } }
        .onChange(of: inspectorTab) { if inspectorTab == .changes { Task { await loadDiff() } } }
        .onDisappear { Task { await flush() } }
        .confirmationDialog("Replace the copy in Obsidian?", isPresented: Binding(get: { conflict != nil }, set: { if !$0 { conflict = nil } })) {
            Button("Replace It", role: .destructive) { Task { await send(replace: true) } }
        } message: {
            Text(conflict ?? "")
        }
        .confirmationDialog("Discard your edits?", isPresented: $confirmDiscard) {
            Button("Discard Edits", role: .destructive) { Task { await discard() } }
        } message: {
            Text("The note goes back to what the model wrote.")
        }
        .confirmationDialog("Rewrite the note?", isPresented: $confirmRewrite) {
            Button("Rewrite", role: .destructive) { Task { await write() } }
        } message: {
            Text(note?.edited != nil ? "This replaces your edits with a new note." : "This replaces the note with a new one.")
        }
        .confirmationDialog("Find your to-dos before sending?", isPresented: $confirmTodos) {
            Button("Find To-dos First") { Task { await findTodosFirst() } }
            Button("Send Anyway") { Task { await send(replace: false) } }
            Button("Cancel", role: .cancel) {}
                .dialogSuppressionToggle("Don't ask again", isSuppressed: Binding(get: { !askTodos }, set: { askTodos = !$0 }))
        } message: {
            Text("This meeting hasn't been checked for to-dos. Sending them to Things first puts their links in the note's action items, so Obsidian gets everything at once.")
        }
        .confirmationDialog("Use the Obsidian version?", isPresented: $confirmPull) {
            Button("Use Obsidian Version", role: .destructive) { Task { await pull() } }
        } message: {
            Text("The note here is replaced by the copy in Obsidian.")
        }
    }

    // the note

    private var editor: some View {
        VStack(spacing: 0) {
            if let banner = vaultBanner {
                HStack {
                    Image(systemName: banner.icon)
                    Text(banner.text)
                    Spacer()
                    if note?.vault == "conflict" {
                        Button("Use Obsidian Version") { confirmPull = true }
                    }
                }
                .font(.callout)
                .padding(.horizontal, 14)
                .padding(.vertical, 8)
                .background(banner.tint.opacity(0.12))
                Divider()
            }
            HStack(spacing: 10) {
                Picker("Show", selection: $mode) {
                    Image(systemName: "eye").help("Formatted").tag(NoteMode.preview)
                    Image(systemName: "chevron.left.forwardslash.chevron.right").help("Markdown, to edit").tag(NoteMode.source)
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .fixedSize()
                .help(mode == .preview ? "Showing the note formatted, switch to markdown to edit it" : "Editing the markdown")
                projectField
                Spacer()
                Text(status)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            Divider()
            switch mode {
            case .preview:
                NoteMarkdown(text: text, openTranscript: { store.showTab = .transcript })
            case .source:
                TextEditor(text: $text)
                    .font(.system(.body, design: .monospaced))
                    .scrollContentBackground(.hidden)
                    .contentMargins(16, for: .scrollContent)
            }
        }
    }

    private var status: String {
        if text != saved { return "Edited" }
        if let edited = note?.edited { return "Saved \(edited.prefix(16).replacingOccurrences(of: "T", with: " "))" }
        if let model = note?.model { return "Written by \(model.split(separator: "/").last ?? "")" }
        return ""
    }

    private var vaultBanner: (text: String, icon: String, tint: Color)? {
        switch note?.vault {
        case "pulled": ("Updated from the copy in Obsidian, which is now the master.", "arrow.down.circle", .blue)
        case "app-ahead": ("Changed here since it was sent. Send again to update Obsidian.", "arrow.up.circle", .orange)
        case "conflict": ("Both this note and the Obsidian copy changed since it was sent.", "exclamationmark.triangle", .red)
        case "missing": ("The copy in Obsidian is gone. Send again to put it back.", "questionmark.circle", .orange)
        default: nil
        }
    }

    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        ToolbarItemGroup {
            Button { Task { await store.openInObsidian(meetingId) } } label: {
                Label("Open in Obsidian", systemImage: "arrow.up.forward.app")
            }
            .disabled(!sent)
            .help(sent ? "Open the note in Obsidian" : "Send the note first")

            Button { toggleInspector(.changes) } label: {
                Label("Changes", systemImage: "plus.forwardslash.minus")
            }
            .help("Show what changed")

            if thingsEnabled {
                Button { toggleInspector(.todos) } label: {
                    Label("To-dos", systemImage: "checklist")
                }
                .help("Find your to-dos in the transcript and send them to Things")
            }

            Menu {
                Button("Rewrite Note...") { confirmRewrite = true }
                if note?.edited != nil && !sent {
                    Button("Discard My Edits...") { confirmDiscard = true }
                }
                Divider()
                Button("Copy Note") { store.copy(text, "note") }
                Button("Show in Finder") { Task { await store.reveal(meetingId) } }
            } label: {
                Label("More", systemImage: "ellipsis.circle")
            }

            Button { requestSend() } label: {
                Label(sent ? "Send Again" : "Send to Obsidian", systemImage: "paperplane")
            }
            .buttonStyle(.borderedProminent)
            .keyboardShortcut(.return, modifiers: [.command, .shift])
            .help("Save the note into the meeting notes folder")
        }
        ToolbarItem {
            // cmd+s saves right away, autosave does it anyway
            Button("Save") { Task { await flush() } }
                .keyboardShortcut("s")
                .opacity(0)
                .frame(width: 0)
        }
    }

    private func toggleInspector(_ tab: InspectorTab) {
        if showChanges && inspectorTab == tab {
            showChanges = false
        } else {
            inspectorTab = tab
            showChanges = true
        }
    }

    // to-dos. claude reads the transcript for the owner's to-dos, and once they are added to
    // things the note's action items become links to them

    private var todos: some View {
        let sentTodos = note?.todosSent ?? []
        let looking: Bool = { if case .idle = todoState { return false }; return true }()
        return ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                if !looking {
                    if !sentTodos.isEmpty {
                        Text("Sent to Things").font(.headline)
                        VStack(alignment: .leading, spacing: 8) {
                            ForEach(sentTodos, id: \.thingsId) { t in
                                VStack(alignment: .leading, spacing: 1) {
                                    if let url = URL(string: "things:///show?id=\(t.thingsId.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? t.thingsId)") {
                                        Link(t.title, destination: url)
                                            .help("Open in Things")
                                    }
                                    Text("\(t.added?.prefix(10) ?? "")\(t.source == "ask" ? ", from Ask" : "")")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                            }
                        }
                    } else if let checked = note?.todosChecked {
                        Text("Checked on \(checked.prefix(10)), and nothing was sent to Things.")
                            .foregroundStyle(.secondary)
                    } else {
                        Text("Claude reads the transcript for tasks you were asked to do or offered to do, to add to Things. The note's action items then become links to them.")
                            .foregroundStyle(.secondary)
                    }
                    if !claudeReady {
                        Text("Add your Anthropic API key in Settings to find to-dos.")
                            .foregroundStyle(.orange)
                    }
                }
                TodoPanel(state: $todoState,
                          label: sentTodos.isEmpty && note?.todosChecked == nil ? "Find my to-dos" : "Look again",
                          find: { try await API.post(API.meeting(meetingId, "todos")) },
                          add: { todos in
                              // the note on disk is rewritten, so unsaved edits go first
                              await flush()
                              return try await API.post(["api", "things", "add"],
                                                        AddTodosRequest(todos: todos, noteMeetingId: meetingId))
                          },
                          added: { result in
                              todoState = .idle
                              await load(replaceText: true)
                              let vault = result.vault == "same" || result.vault == "pulled"
                              store.show("Added \(result.added) to Things\(vault ? ", and updated the note in Obsidian" : "")")
                          },
                          enabled: claudeReady)
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    // writing a note

    private var writeForm: some View {
        Form {
            Section {
                projectField
                Picker("Model", selection: $backend) {
                    Text("Local").tag("local")
                    Text("Claude").tag("claude")
                        .disabled(lookups?.claudeReady != true)
                }
            } footer: {
                Text(lookups?.claudeReady == true ? "Claude reads the transcript text only, never the audio."
                     : "Add your Anthropic API key in Settings to use Claude.")
                    .foregroundStyle(.secondary)
            }
            Button("Write Note") { Task { await write() } }
                .buttonStyle(.borderedProminent)
        }
        .formStyle(.grouped)
        .frame(maxWidth: 520)
    }

    private var projectField: some View {
        TextField("Project", text: $project, prompt: Text("Search projects"))
            .frame(maxWidth: 340)
            .textInputSuggestions {
                ForEach(matchingProjects, id: \.name) { p in
                    Label(p.name, systemImage: p.status == "In-Progress" ? "circle.fill" : "circle")
                        .textInputCompletion(p.name)
                }
            }
            .onSubmit { Task { await saveProject() } }
    }

    private var matchingProjects: [Project] {
        let all = lookups?.projects ?? []
        let typed = project.trimmingCharacters(in: .whitespaces)
        if typed.isEmpty { return Array(all.filter { $0.status == "In-Progress" }.prefix(12)) }
        return Array(all.filter { $0.name.localizedCaseInsensitiveContains(typed) && $0.name != typed }.prefix(12))
    }

    // server calls

    // keeps unsaved typing unless told to take the server's copy
    private func load(replaceText: Bool = false) async {
        async let fetched: Note = API.get(API.meeting(meetingId, "note"))
        async let fetchedLookups: Lookups = API.get(["api", "lookups"])
        guard let fetched = try? await fetched else { return }
        note = fetched
        if replaceText || text == saved {
            saveTask?.cancel()
            text = fetched.markdown
            saved = fetched.markdown
        }
        project = fetched.project ?? ""
        if lookups == nil, let l = try? await fetchedLookups {
            lookups = l
            claudeReady = l.claudeReady == true
            backend = l.defaultBackend ?? "local"
        }
        if showChanges { await loadDiff() }
    }

    private func loadDiff() async {
        diff = try? await API.get(API.meeting(meetingId, "note", "diff"))
    }

    private func scheduleSave() {
        saveTask?.cancel()
        guard text != saved else { return }
        saveTask = Task {
            try? await Task.sleep(for: .milliseconds(800))
            guard !Task.isCancelled else { return }
            await flush()
        }
    }

    private func flush() async {
        guard note?.exists == true, text != saved else { return }
        let markdown = text
        await store.run {
            let _: Ok = try await API.post(API.meeting(meetingId, "note", "text"), ["markdown": markdown])
            saved = markdown
            if showChanges { await loadDiff() }
        }
    }

    private func saveProject() async {
        let name = project.trimmingCharacters(in: .whitespaces)
        guard note?.exists == true, name != (note?.project ?? ""), !name.isEmpty else { return }
        await flush()
        await store.run {
            let _: [String: String?] = try await API.post(API.meeting(meetingId, "project"), ["name": name])
            // the project is written into the note's frontmatter
            await load(replaceText: true)
            await store.refresh()
        }
    }

    private func write() async {
        await flush()
        let name = project.trimmingCharacters(in: .whitespaces)
        await store.run {
            let _: Job = try await API.post(API.meeting(meetingId, "note"),
                                            ["backend": backend, "project": name.isEmpty ? nil : name])
            await store.refresh()
        }
    }

    // a first send asks about to-dos when the meeting was never checked and claude can look
    private func requestSend() {
        let looking: Bool = { if case .idle = todoState { return false }; return true }()
        let unchecked = (note?.todosSent ?? []).isEmpty && note?.todosChecked == nil
        if thingsEnabled && askTodos && claudeReady && !sent && unchecked && !looking {
            confirmTodos = true
        } else {
            Task { await send(replace: false) }
        }
    }

    private func findTodosFirst() async {
        inspectorTab = .todos
        showChanges = true
        todoState = .loading
        do {
            todoState = .ready(try await API.post(API.meeting(meetingId, "todos")))
        } catch {
            todoState = .error(error.localizedDescription)
        }
    }

    private func send(replace: Bool) async {
        await flush()
        do {
            let result: SendResult = try await API.post(API.meeting(meetingId, "note", "send"), ["replace": replace])
            // like archiving in mail, move on to the next meeting in the inbox
            let inbox = store.inbox.map(\.meetingId)
            let next = inbox.firstIndex(of: meetingId).flatMap { i in inbox.indices.contains(i + 1) ? inbox[i + 1] : i > 0 ? inbox[i - 1] : nil }
            await store.refresh()
            store.selection = store.section == .inbox ? next : result.meetingId
            store.show("Sent to Obsidian as \(URL(fileURLWithPath: result.path).lastPathComponent)")
            if openAfterSend { await store.openInObsidian(result.meetingId) }
        } catch let error as APIError where error.status == 409 {
            conflict = error.message
        } catch {
            store.show(error.localizedDescription)
        }
    }

    private func discard() async {
        await store.run {
            let _: Note = try await API.post(API.meeting(meetingId, "note", "discard"))
            await load(replaceText: true)
        }
    }

    private func pull() async {
        await store.run {
            let _: Note = try await API.post(API.meeting(meetingId, "note", "pull"))
            await load(replaceText: true)
        }
    }
}

// preferences shared with settings
enum Prefs {
    // the things 3 features: finding to-dos, send to things, and the to-do badges
    static let things = "thingsEnabled"
    // whether sending a note asks to find its to-dos first
    static let askTodos = "askTodosBeforeSend"
    static let openAfterSend = "openInObsidianAfterSend"
    // whether a note shows formatted or as markdown to edit
    static let noteMode = "noteMode"
}

enum NoteMode: String {
    case preview, source
}

enum InspectorTab: String, CaseIterable {
    case changes = "Changes"
    case todos = "To-dos"
}

struct DiffView: View {
    let diff: NoteDiff?

    var body: some View {
        Group {
            if let diff {
                if diff.rows.isEmpty {
                    ContentUnavailableView("No Changes", systemImage: "checkmark",
                                           description: Text("The note matches \(diff.base ?? "the original")."))
                } else {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 0) {
                            Text("\(diff.added) added, \(diff.removed) removed, compared with \(diff.base ?? "the original")")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .padding(.bottom, 8)
                            ForEach(Array(diff.rows.enumerated()), id: \.offset) { _, row in line(row) }
                        }
                        .padding(12)
                    }
                }
            } else {
                ProgressView()
            }
        }
    }

    @ViewBuilder
    private func line(_ row: DiffRow) -> some View {
        if row.kind == "gap" {
            Text("...")
                .foregroundStyle(.tertiary)
                .padding(.vertical, 4)
        } else {
            let tint: Color = row.kind == "add" ? .green : row.kind == "del" ? .red : .clear
            Text(attributed(row, tint: tint))
                .font(.system(.callout, design: .monospaced))
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 4)
                .padding(.vertical, 1)
                .background(tint.opacity(0.1))
        }
    }

    private func attributed(_ row: DiffRow, tint: Color) -> AttributedString {
        var result = AttributedString(row.kind == "add" ? "+ " : row.kind == "del" ? "- " : "  ")
        for part in row.parts ?? [] {
            var piece = AttributedString(part.text)
            if part.changed { piece.backgroundColor = tint.opacity(0.3) }
            if row.kind == "del" { piece.strikethroughStyle = .single }
            result += piece
        }
        return result
    }
}
