import SwiftUI

struct NoteView: View {
    @Environment(Store.self) private var store
    let meetingId: String
    @Binding var tab: Tab
    @State private var note: Note?
    @State private var lookups: Lookups?
    @State private var text = ""
    @State private var saved = ""
    @State private var saveTask: Task<Void, Never>?
    @State private var project = ""
    @State private var backend = "local"
    @State private var template = ""
    @State private var showChanges = false
    @State private var inspectorTab = InspectorTab.changes
    @State private var todoState = TodoState.idle
    @State private var todosReady = false
    @State private var diff: NoteDiff?
    @State private var conflict: String?
    @State private var confirmDiscard = false
    @State private var confirmRewrite = false
    @State private var confirmPull = false
    @State private var confirmTodos = false
    @AppStorage(Prefs.askTodos) private var askTodos = true
    @AppStorage(Prefs.openAfterSend) private var openAfterSend = false
    @AppStorage(Prefs.noteMode) private var mode = NoteMode.preview
    @AppStorage(Prefs.noteTextSize) private var textSize = 13.0

    private var job: Job? { store.noteJob(meetingId) ?? note?.job.flatMap { $0.running ? $0 : nil } }
    private var sent: Bool { note?.sentPath != nil }
    // with no meeting notes folder, a meeting is marked done rather than sent to obsidian
    private var obsidian: Bool { !(note?.notesDir ?? "").isEmpty }

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
        .toolbar {
            ToolbarItem { TabPicker(tab: $tab) }
            ToolbarSpacer(.flexible)
            if note?.exists == true && job == nil { toolbar }
        }
        // cmd+s saves right away, autosave does it anyway
        .background {
            Button("Save") { Task { await flush() } }
                .keyboardShortcut("s")
                .opacity(0)
                .allowsHitTesting(false)
        }
        .inspector(isPresented: $showChanges) {
            // the toolbar's Changes and To-dos buttons pick what it shows. with no to-do app,
            // it's only the changes
            VStack(spacing: 0) {
                switch store.todoApp != nil ? inspectorTab : .changes {
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
        .onAppear { store.saveNote = (meetingId, { await flush() }) }
        .onDisappear {
            if store.saveNote?.meetingId == meetingId { store.saveNote = nil }
            Task { await flush() }
        }
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
            Text((note?.edited != nil ? "This replaces your edits with a new note" : "This replaces the note with a new one")
                 + (template == note?.template ? "." : ", in the \(template) template."))
        }
        .confirmationDialog(obsidian ? "Find your to-dos before sending?" : "Find your to-dos first?", isPresented: $confirmTodos) {
            Button("Find To-dos First") { Task { await findTodosFirst() } }
            Button(obsidian ? "Send Anyway" : "Mark Done Anyway") { Task { await send(replace: false) } }
            Button("Cancel", role: .cancel) {}
                .dialogSuppressionToggle("Don't ask again", isSuppressed: Binding(get: { !askTodos }, set: { askTodos = !$0 }))
        } message: {
            Text("This meeting hasn't been checked for to-dos. Sending them to \(store.todoAppName) first puts their links in the note's action items\(obsidian ? ", so Obsidian gets everything at once" : "").")
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
                textSizeSlider
                Text(status)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .frame(maxWidth: 240, alignment: .trailing)
                    .help(status)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            Divider()
            switch mode {
            case .preview:
                NoteMarkdown(text: text, size: textSize, openTranscript: { store.showTab = .transcript })
            case .source:
                TextEditor(text: $text)
                    .font(.system(size: textSize, design: .monospaced))
                    .scrollContentBackground(.hidden)
                    .contentMargins(16, for: .scrollContent)
            }
        }
    }

    // eight sizes, two points apart, with the original 13 among them
    private static let textSizes: ClosedRange<Double> = 11...25
    private static let textStep = 2.0

    // the note's text size, for both the formatted view and the markdown. cmd+= and cmd+- step it
    private var textSizeSlider: some View {
        HStack(spacing: 6) {
            Image(systemName: "textformat.size.smaller").foregroundStyle(.secondary)
            Slider(value: $textSize, in: Self.textSizes, step: Self.textStep)
                .controlSize(.mini)
                .frame(width: 90)
            Image(systemName: "textformat.size.larger").foregroundStyle(.secondary)
        }
        .help("Text size")
        // a size picked when the stops were one point apart moves to the nearest stop
        .onAppear {
            let lower = Self.textSizes.lowerBound
            textSize = min(max(lower + ((textSize - lower) / Self.textStep).rounded() * Self.textStep, lower), Self.textSizes.upperBound)
        }
        .background {
            Button("Bigger") { textSize = min(textSize + Self.textStep, Self.textSizes.upperBound) }
                .keyboardShortcut("=")
                .opacity(0)
                .allowsHitTesting(false)
            Button("Smaller") { textSize = max(textSize - Self.textStep, Self.textSizes.lowerBound) }
                .keyboardShortcut("-")
                .opacity(0)
                .allowsHitTesting(false)
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
            if obsidian || sent {
                Button { Task { await store.openInObsidian(meetingId) } } label: {
                    Label("Open in Obsidian", systemImage: "arrow.up.forward.app")
                }
                .disabled(!sent)
                .help(sent ? "Open the note in Obsidian" : "Send the note first")
            }
        }

        // gaps, so these are separate buttons rather than one control
        ToolbarSpacer(.fixed)

        ToolbarItemGroup {
            // toggles, so the one whose pane is open shows as pressed
            Toggle(isOn: inspectorShowing(.changes)) {
                Label("Changes", systemImage: "plus.forwardslash.minus")
            }
            .toggleStyle(.button)
            .help("Show what changed")
        }

        ToolbarSpacer(.fixed)

        ToolbarItemGroup {
            if store.todoApp != nil {
                Toggle(isOn: inspectorShowing(.todos)) {
                    Label("To-dos", systemImage: "checklist")
                }
                .toggleStyle(.button)
                .help("Find your to-dos in the transcript and send them to \(store.todoAppName)")
            }

            Menu {
                // rewriting can switch the note to another template
                Menu("Rewrite Note") {
                    ForEach(lookups?.templates ?? [], id: \.self) { name in
                        Button(name == note?.template ? "\(name) (current)" : name) {
                            template = name
                            confirmRewrite = true
                        }
                    }
                }
                if note?.edited != nil && !sent {
                    Button("Discard My Edits...") { confirmDiscard = true }
                }
                Divider()
                Button("Copy Note") { store.copy(text, "note") }
                Button("Export Note...") { export() }
                Button("Show in Finder") { Task { await store.reveal(meetingId) } }
            } label: {
                Label("More", systemImage: "ellipsis.circle")
            }

            if obsidian {
                Button { requestSend() } label: {
                    Label(sent ? "Send Again" : "Send to Obsidian", systemImage: "paperplane")
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.return, modifiers: [.command, .shift])
                .help("Save the note into the meeting notes folder")
            } else if note?.archived == nil {
                Button { requestSend() } label: {
                    Label("Mark Done", systemImage: "checkmark.circle")
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.return, modifiers: [.command, .shift])
                .help("Move the meeting to the Archive. Export Note in the More menu saves the note as a file")
            }
        }
    }

    // on when the pane is open on that tab. turning one on switches the pane to it, and
    // turning it off closes the pane
    private func inspectorShowing(_ tab: InspectorTab) -> Binding<Bool> {
        Binding(get: { showChanges && inspectorTab == tab }, set: { on in
            if on { inspectorTab = tab }
            showChanges = on
        })
    }

    // to-dos. claude reads the transcript for the owner's to-dos, and once they are added
    // to the to-do app the note's action items become links to them

    private var todos: some View {
        let sentTodos = note?.todosSent ?? []
        let looking: Bool = { if case .idle = todoState { return false }; return true }()
        return ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                if !looking {
                    if !sentTodos.isEmpty {
                        Text("Sent to \(store.todoAppName)").font(.headline)
                        VStack(alignment: .leading, spacing: 8) {
                            ForEach(sentTodos, id: \.thingsId) { t in
                                let app = Store.todoApps[t.app ?? "things"] ?? "the to-do app"
                                VStack(alignment: .leading, spacing: 1) {
                                    if let url = t.link.flatMap(URL.init(string:)) {
                                        Link(t.title, destination: url)
                                            .help("Open in \(app)")
                                    } else {
                                        Text(t.title)
                                    }
                                    Text("\(t.added?.prefix(10) ?? "")\(t.source == "ask" ? ", from Ask" : "")")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                            }
                        }
                    } else if let checked = note?.todosChecked {
                        Text("Checked on \(checked.prefix(10)), and nothing was sent to \(store.todoAppName).")
                            .foregroundStyle(.secondary)
                    } else {
                        Text("Claude reads the transcript for tasks you were asked to do or offered to do, to add to \(store.todoAppName). The note's action items then become links to them.")
                            .foregroundStyle(.secondary)
                    }
                    if !todosReady {
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
                              return try await API.post(["api", "todos", "add"],
                                                        AddTodosRequest(todos: todos, noteMeetingId: meetingId))
                          },
                          added: { result in
                              todoState = .idle
                              await load(replaceText: true)
                              let vault = result.vault == "same" || result.vault == "pulled"
                              store.show("Added \(result.added) to \(store.todoAppName)\(vault ? ", and updated the note in Obsidian" : "")")
                          },
                          enabled: todosReady)
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
                Picker("Template", selection: $template) {
                    ForEach(lookups?.templates ?? [], id: \.self) { Text($0).tag($0) }
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
                    Label(p.name, systemImage: p.active == true ? "circle.fill" : "circle")
                        .textInputCompletion(p.name)
                }
            }
            .onSubmit { Task { await saveProject() } }
    }

    private var matchingProjects: [Project] {
        let all = lookups?.projects ?? []
        let typed = project.trimmingCharacters(in: .whitespaces)
        if typed.isEmpty { return Array(all.filter { $0.active == true }.prefix(12)) }
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
            todosReady = l.todosReady == true
            backend = l.defaultBackend ?? "local"
            // a rewrite keeps the note's template, a first note uses the one from settings
            template = fetched.template ?? l.noteTemplate ?? "Standard"
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
                                            ["backend": backend, "project": name.isEmpty ? nil : name,
                                             "template": template.isEmpty ? nil : template])
            await store.refresh()
        }
    }

    // a first send asks about to-dos when the meeting was never checked and claude can look
    private func requestSend() {
        let looking: Bool = { if case .idle = todoState { return false }; return true }()
        let unchecked = (note?.todosSent ?? []).isEmpty && note?.todosChecked == nil
        if store.todoApp != nil && askTodos && todosReady && !(obsidian && sent) && unchecked && !looking {
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

    // sends the note to obsidian, or with no meeting notes folder marks the meeting done
    private func send(replace: Bool) async {
        await flush()
        do {
            let result: SendResult = obsidian
                ? try await API.post(API.meeting(meetingId, "note", "send"), ["replace": replace])
                : try await API.post(API.meeting(meetingId, "note", "finish"))
            // like archiving in mail, move on to the next meeting in the inbox
            let inbox = store.inbox.map(\.meetingId)
            let next = inbox.firstIndex(of: meetingId).flatMap { i in inbox.indices.contains(i + 1) ? inbox[i + 1] : i > 0 ? inbox[i - 1] : nil }
            await store.refresh()
            store.selection = store.section == .inbox ? next : result.meetingId
            if obsidian {
                store.show("Sent to Obsidian as \(URL(fileURLWithPath: result.path).lastPathComponent)")
                if openAfterSend { await store.openInObsidian(result.meetingId) }
            } else {
                store.show("Moved to the Archive")
            }
        } catch let error as APIError where error.status == 409 {
            conflict = error.message
        } catch {
            store.show(error.localizedDescription)
        }
    }

    // saves the note as a markdown file, named the way sending names it
    private func export() {
        let heading = text.split(separator: "\n").first { $0.hasPrefix("# ") }.map { String($0.dropFirst(2)) } ?? meetingId
        let panel = NSSavePanel()
        panel.nameFieldStringValue = heading.replacingOccurrences(of: #"[\\/:*?"<>|#^\[\]]"#, with: "-", options: .regularExpression)
            .trimmingCharacters(in: CharacterSet(charactersIn: " .")) + ".md"
        panel.allowedContentTypes = [.init(filenameExtension: "md") ?? .plainText]
        panel.canCreateDirectories = true
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            try text.write(to: url, atomically: true, encoding: .utf8)
            store.show("Exported \(url.lastPathComponent)")
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
    // things 3 on or off, from before there was a choice of to-do app. read once to carry
    // a switched off things over to the server's setting
    static let things = "thingsEnabled"
    // whether sending a note asks to find its to-dos first
    static let askTodos = "askTodosBeforeSend"
    static let openAfterSend = "openInObsidianAfterSend"
    // whether a note shows formatted or as markdown to edit
    static let noteMode = "noteMode"
    static let noteTextSize = "noteTextSize"
    // whether the plaud list leaves out recordings already imported
    static let hideImported = "plaudHideImported"
    // the plaud page, shown only when the plaud cli is installed too
    static let plaud = "plaudEnabled"
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
