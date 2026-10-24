import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @Environment(Store.self) private var store
    @State private var dropping = false

    var body: some View {
        @Bindable var store = store
        Group {
            if store.connected, store.section.fullWidth {
                NavigationSplitView {
                    Sidebar()
                } detail: {
                    switch store.section {
                    case .home: HomeView()
                    case .settings: SettingsView()
                    default: VocabularyView()
                    }
                }
            } else if store.connected {
                NavigationSplitView {
                    Sidebar()
                } content: {
                    switch store.section {
                    case .people: PeopleList()
                    case .ask: ConversationList()
                    case .plaud: PlaudList()
                    default: MeetingList()
                    }
                } detail: {
                    if store.section == .ask {
                        ChatView()
                    } else if store.section == .plaud {
                        ContentUnavailableView("Plaud", systemImage: "icloud.and.arrow.down",
                                               description: Text("Import a recording to download its audio and transcribe it here, so Plaud's own transcription minutes aren't used. Imported ones open in the Inbox."))
                    } else if store.section == .people {
                        if let id = store.personSelection, store.person(id) != nil {
                            PersonView(personId: id)
                                .id(id)
                        } else {
                            ContentUnavailableView("No Person Selected", systemImage: "person.2")
                        }
                    } else if let id = store.selection, store.meeting(id) != nil {
                        MeetingView(meetingId: id)
                            .id(id)
                    } else {
                        ContentUnavailableView("No Meeting Selected", systemImage: "waveform",
                                               description: Text("Drop a recording here to transcribe it."))
                    }
                }
            } else if let error = store.startError {
                ContentUnavailableView {
                    Label("Boswell could not start", systemImage: "exclamationmark.triangle")
                } description: {
                    Text(error)
                } actions: {
                    Button("Try Again") { Task { await store.start() } }
                }
            } else {
                ProgressView("Starting the transcription server")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .overlay(alignment: .bottom) {
            if let message = store.message {
                Text(message)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 8)
                    .background(.regularMaterial, in: Capsule())
                    .padding(.bottom, 20)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
            }
        }
        .animation(.default, value: store.message)
        .dropDestination(for: URL.self) { urls, _ in
            Task { await store.importFiles(urls) }
            return true
        } isTargeted: { dropping = $0 }
        .overlay {
            if dropping {
                RoundedRectangle(cornerRadius: 12)
                    .strokeBorder(store.accentColor, style: StrokeStyle(lineWidth: 3, dash: [8]))
                    .padding(6)
                    .allowsHitTesting(false)
            }
        }
        .sheet(isPresented: $store.showSetup) { SetupView() }
        .fileImporter(isPresented: $store.importing, allowedContentTypes: [.mpeg4Audio, .mp3, .wav],
                      allowsMultipleSelection: true) { result in
            if case .success(let urls) = result { Task { await store.importFiles(urls) } }
        }
        .tint(accentTint(store.accent))
        .frame(minWidth: 820, minHeight: 560)
    }
}

struct Sidebar: View {
    @Environment(Store.self) private var store
    @AppStorage(Prefs.plaud) private var plaudEnabled = true

    var body: some View {
        List(selection: Binding(get: { store.section }, set: { if let s = $0 { store.section = s } })) {
            SwiftUI.Section("Meetings") {
                ForEach(Place.meetings.filter { store.shows($0, plaudEnabled: plaudEnabled) }, id: \.self) { place in
                    Label(place.rawValue, systemImage: place.icon)
                        .labelStyle(SidebarLabel())
                        .badge(place == .inbox ? store.inbox.count : 0)
                        .tag(place)
                }
            }
            SwiftUI.Section("Library") {
                ForEach(Place.library, id: \.self) { place in
                    Label(place.rawValue, systemImage: place.icon)
                        .labelStyle(SidebarLabel())
                        .tag(place)
                }
            }
        }
        .navigationSplitViewColumnWidth(min: 170, ideal: 190)
        .safeAreaInset(edge: .bottom) {
            if !store.jobs.isEmpty {
                JobsView()
            }
        }
    }
}

// a sidebar row, with a little more room between the icon and its name and the name a touch
// bolder. icons sit in boxes of one width, so every name starts at the same place
struct SidebarLabel: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 10) {
            SidebarIcon(icon: configuration.icon)
            configuration.title.fontWeight(.medium)
        }
    }
}

// the accent color, as sidebars show icons, and white on the selected row
private struct SidebarIcon<Icon: View>: View {
    let icon: Icon
    @Environment(\.backgroundProminence) private var prominence

    var body: some View {
        icon
            .foregroundStyle(prominence == .increased ? AnyShapeStyle(.primary) : AnyShapeStyle(.tint))
            .frame(width: 22, alignment: .center)
    }
}

struct JobsView: View {
    @Environment(Store.self) private var store

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(store.jobs) { job in
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text(job.kind == "note" ? "Note: \(store.meeting(job.meetingId)?.name ?? "meeting")" : job.file ?? "Recording")
                            .lineLimit(1)
                            .truncationMode(.middle)
                        Spacer()
                        if !job.running {
                            Button { Task { await store.dismiss(job) } } label: { Image(systemName: "xmark.circle.fill") }
                                .buttonStyle(.borderless)
                                .foregroundStyle(.secondary)
                                .help("Dismiss")
                        }
                    }
                    .font(.caption.weight(.medium))
                    if job.status == "processing" {
                        ProgressView(value: job.progress ?? 0)
                            .controlSize(.small)
                    }
                    Text(job.status == "failed" ? job.error ?? "Failed" : job.step ?? job.status)
                        .font(.caption2)
                        .foregroundStyle(job.status == "failed" ? .red : .secondary)
                        .lineLimit(2)
                }
            }
        }
        .padding(12)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
        .padding(8)
    }
}

struct MeetingList: View {
    @Environment(Store.self) private var store
    @State private var search = ""
    @State private var renaming: Meeting?
    @State private var newName = ""
    @State private var todoFilter = TodoFilter.all

    private var shown: [Meeting] {
        var all = store.section == .inbox ? store.inbox : store.archive
        if store.section == .archive && store.todoApp != nil {
            switch todoFilter {
            case .all: break
            case .sent: all = all.filter { ($0.todosSent ?? 0) > 0 }
            // only notes written here can be checked for to-dos
            case .unchecked: all = all.filter { $0.note == true && ($0.todosSent ?? 0) == 0 && $0.todosChecked == nil }
            }
        }
        guard !search.isEmpty else { return all }
        return all.filter {
            $0.name.localizedCaseInsensitiveContains(search) || ($0.project ?? "").localizedCaseInsensitiveContains(search)
        }
    }

    var body: some View {
        @Bindable var store = store
        List(shown, selection: $store.selection) { m in
            MeetingRow(meeting: m)
                .tag(m.meetingId)
                .contextMenu {
                    Button("Rename...") { newName = m.name; renaming = m }
                    Button("Show in Finder") { Task { await store.reveal(m.meetingId) } }
                    if m.sentPath != nil {
                        Button("Open in Obsidian") { Task { await store.openInObsidian(m.meetingId) } }
                    }
                    if m.archived != nil {
                        Divider()
                        Button("Move to Inbox") { Task { await store.moveToInbox(m.meetingId) } }
                    }
                }
        }
        .overlay {
            if shown.isEmpty {
                if !search.isEmpty {
                    ContentUnavailableView.search(text: search)
                } else if store.section == .inbox {
                    ContentUnavailableView("Inbox Zero", systemImage: "tray",
                                           description: Text("Every meeting is done. Drop a recording to start another."))
                } else {
                    ContentUnavailableView("Nothing Archived", systemImage: "archivebox")
                }
            }
        }
        .safeAreaInset(edge: .top) { ListSearch(text: $search, prompt: "Search \(store.section.rawValue)") }
        .navigationTitle(store.section.rawValue)
        .navigationSubtitle(store.section == .inbox && store.reviewCount > 0 ? "\(store.reviewCount) to review" : "")
        .navigationSplitViewColumnWidth(min: 260, ideal: 320)
        .toolbar {
            if store.section == .archive && store.todoApp != nil {
                ToolbarItem {
                    Menu {
                        Picker("Show", selection: $todoFilter) {
                            ForEach(TodoFilter.allCases, id: \.self) { Text($0.label(store.todoAppName)).tag($0) }
                        }
                        .pickerStyle(.inline)
                    } label: {
                        Label("Filter", systemImage: todoFilter == .all ? "line.3.horizontal.decrease.circle"
                              : "line.3.horizontal.decrease.circle.fill")
                    }
                    .help("Filter by to-dos")
                }
            }
            ToolbarItem {
                Button { store.importing = true } label: { Label("Import Recording", systemImage: "plus") }
                    .help("Import a recording to transcribe")
            }
        }
        .alert("Rename Meeting", isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
            TextField("Name", text: $newName)
            Button("Rename") {
                if let m = renaming, !newName.trimmingCharacters(in: .whitespaces).isEmpty {
                    Task { await store.rename(m.meetingId, to: newName) }
                }
            }
            Button("Cancel", role: .cancel) {}
        }
        .onChange(of: store.section) { search = "" }
    }
}

struct MeetingRow: View {
    @Environment(Store.self) private var store
    let meeting: Meeting

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(meeting.name)
                .fontWeight(.medium)
                .lineLimit(2)
            HStack(spacing: 6) {
                Text(details)
                    .foregroundStyle(.secondary)
                Spacer(minLength: 4)
                TodoBadge(meeting: meeting)
                if meeting.archived == nil {
                    StageBadge(stage: store.stage(meeting))
                }
            }
            .font(.caption)
            if let project = meeting.project {
                Text(project)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
            }
        }
        .padding(.vertical, 3)
    }

    private var details: String {
        [meeting.date, meeting.minutes.map { "\(Int($0.rounded())) min" }].compactMap { $0 }.joined(separator: " · ")
    }
}

// a search field at the top of a list column. the toolbar's own search field would sit at the
// far right of the window, over the meeting's pane, and push its buttons away from the edge
struct ListSearch: View {
    @Binding var text: String
    let prompt: String
    @FocusState private var focused: Bool

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
            TextField(prompt, text: $text)
                .textFieldStyle(.plain)
                .focused($focused)
                .onExitCommand { text = ""; focused = false }
            if !text.isEmpty {
                Button { text = "" } label: { Image(systemName: "xmark.circle.fill") }
                    .buttonStyle(.borderless)
                    .foregroundStyle(.secondary)
                    .help("Clear the search")
            }
        }
        .padding(.horizontal, 9)
        .padding(.vertical, 6)
        .background(.quaternary.opacity(0.6), in: RoundedRectangle(cornerRadius: 8))
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        // cmd+f finds in the list, as the toolbar's search field did
        .background {
            Button("Find") { focused = true }
                .keyboardShortcut("f")
                .opacity(0)
                .allowsHitTesting(false)
        }
    }
}

// whether a meeting's to-dos went to the to-do app
struct TodoBadge: View {
    @Environment(Store.self) private var store
    let meeting: Meeting

    var body: some View {
        if store.todoApp == nil {
            EmptyView()
        } else if let sent = meeting.todosSent, sent > 0 {
            Label("\(Int(sent)) sent", systemImage: "checkmark.circle")
                .font(.caption2.weight(.medium))
                .foregroundStyle(.secondary)
                .help("To-dos sent from this meeting")
        } else if let checked = meeting.todosChecked {
            Text("no to-dos")
                .font(.caption2)
                .foregroundStyle(.tertiary)
                .help("Checked \(checked.prefix(10))")
        }
    }
}

enum TodoFilter: String, CaseIterable {
    case all, sent, unchecked

    func label(_ app: String) -> String {
        switch self {
        case .all: "All Meetings"
        case .sent: "To-dos in \(app)"
        case .unchecked: "Not Checked for To-dos"
        }
    }
}

struct StageBadge: View {
    let stage: Stage

    var body: some View {
        Text(stage.label)
            .font(.caption2.weight(.medium))
            .padding(.horizontal, 7)
            .padding(.vertical, 2)
            .foregroundStyle(color)
            .background(color.opacity(0.15), in: Capsule())
    }

    private var color: Color {
        switch stage {
        case .needsReview: .orange
        case .writing: .blue
        case .ready: .green
        case .noNote: .secondary
        }
    }
}
