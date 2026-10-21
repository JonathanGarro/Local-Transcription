import SwiftUI

// the open conversation, kept by the store so it survives switching pages
@MainActor @Observable
final class Chat {
    struct Turn: Identifiable {
        let id = UUID()
        var question: String
        var answer: String?
        var links: [String: String] = [:]
        var steps: [String] = []
        var error: String?
        var things: TodoState = .idle
    }

    var conversationId: String?
    var title: String?
    var turns: [Turn] = []
    var pendingAsk: String?
    var conversations: [Conversation] = []
    var claudeReady = true

    var pending: Bool { pendingAsk != nil }

    func loadList() async {
        // a handful of pages at most, so take them all for one scrolling list
        var found: [Conversation] = []
        var page = 1
        while let result: ConversationPage = try? await API.get(["api", "conversations"],
                                                               query: [URLQueryItem(name: "page", value: "\(page)")]) {
            found += result.items
            if page >= result.pages { break }
            page += 1
        }
        conversations = found
    }

    func open(_ id: String?, store: Store) async {
        guard id != conversationId else { return }
        if pending { return store.show("Wait for the answer first") }
        guard let id else { return reset() }
        do {
            let saved: SavedConversation = try await API.get(["api", "conversations", id])
            conversationId = id
            title = saved.title
            turns = saved.turns.map { Turn(question: $0.question, answer: $0.answer, links: $0.links ?? [:]) }
        } catch {
            store.show(error.localizedDescription)
        }
    }

    func reset() {
        conversationId = nil
        title = nil
        turns = []
    }

    func ask(_ question: String, store: Store) async {
        guard !pending else { return }
        turns.append(Turn(question: question))
        let index = turns.count - 1
        do {
            let body: [String: String?] = ["question": question, "conversation_id": conversationId]
            let started: AskStarted = try await API.post(["api", "ask"], body)
            conversationId = started.conversationId
            pendingAsk = started.askId
            await poll(started.askId, index)
        } catch {
            turns[index].error = error.localizedDescription
            pendingAsk = nil
        }
    }

    private func poll(_ askId: String, _ index: Int) async {
        while pendingAsk == askId {
            do {
                let state: AskStatus = try await API.get(["api", "ask", askId])
                turns[index].steps = state.steps ?? []
                if state.status == "done" {
                    turns[index].answer = state.answer ?? ""
                    turns[index].links = state.links ?? [:]
                    title = state.title ?? title
                } else if state.status == "failed" {
                    turns[index].error = state.error ?? "Failed"
                }
                if state.status != "working" {
                    pendingAsk = nil
                    // saved now, so the list shows it and its name
                    await loadList()
                    return
                }
            } catch {
                turns[index].error = error.localizedDescription
                pendingAsk = nil
                return
            }
            try? await Task.sleep(for: .milliseconds(800))
        }
    }

    func rename(_ id: String, to name: String, store: Store) async {
        await store.run {
            let _: Ok = try await API.post(["api", "conversations", id, "rename"], ["name": name])
            if id == conversationId { title = name }
            await loadList()
        }
    }

    func delete(_ id: String, store: Store) async {
        if pending && id == conversationId { return store.show("Wait for the answer first") }
        await store.run {
            var request = URLRequest(url: API.url(["api", "conversations", id]))
            request.httpMethod = "DELETE"
            _ = try await URLSession.shared.data(for: request)
            if id == conversationId { reset() }
            await loadList()
        }
    }
}

struct ConversationList: View {
    @Environment(Store.self) private var store
    @State private var renaming: Conversation?
    @State private var newName = ""
    @State private var deleting: Conversation?

    var body: some View {
        let chat = store.chat
        List(selection: Binding(get: { chat.conversationId }, set: { id in Task { await chat.open(id, store: store) } })) {
            ForEach(chat.conversations) { c in
                VStack(alignment: .leading, spacing: 2) {
                    Text(c.title ?? "Naming...")
                        .fontWeight(.medium)
                        .lineLimit(2)
                    Text([when(c.updated), count(c.questions, "question")].filter { !$0.isEmpty }.joined(separator: " · "))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .padding(.vertical, 2)
                .tag(c.conversationId)
                .contextMenu {
                    Button("Rename...") { newName = c.title ?? ""; renaming = c }
                    Button("Delete...", role: .destructive) { deleting = c }
                }
            }
        }
        .overlay {
            if chat.conversations.isEmpty {
                ContentUnavailableView("No Conversations", systemImage: "bubble.left.and.text.bubble.right",
                                       description: Text("Ask a question to start one."))
            }
        }
        .navigationTitle("Ask")
        .navigationSplitViewColumnWidth(min: 240, ideal: 280)
        .toolbar {
            ToolbarItem {
                Button { Task { await chat.open(nil, store: store) } } label: {
                    Label("New Conversation", systemImage: "square.and.pencil")
                }
                .help("Start a new conversation")
                .keyboardShortcut("n")
            }
        }
        .alert("Rename Conversation", isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
            TextField("Name", text: $newName)
            Button("Rename") {
                let name = newName.trimmingCharacters(in: .whitespaces)
                if let c = renaming, !name.isEmpty { Task { await chat.rename(c.conversationId, to: name, store: store) } }
            }
            Button("Cancel", role: .cancel) {}
        }
        .confirmationDialog("Delete this conversation?", isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } })) {
            Button("Delete", role: .destructive) {
                if let c = deleting { Task { await chat.delete(c.conversationId, store: store) } }
            }
        } message: {
            Text("\(deleting?.title ?? "It") is deleted for good. There is no undo.")
        }
        .task {
            await chat.loadList()
            let lookups: Lookups? = try? await API.get(["api", "lookups"])
            chat.claudeReady = lookups?.claudeReady == true
        }
    }

    private func when(_ at: String?) -> String {
        guard let at, at.count >= 16 else { return at ?? "" }
        return "\(at.prefix(10)) \(at.dropFirst(11).prefix(5))"
    }
}

struct ChatView: View {
    @Environment(Store.self) private var store
    @State private var question = ""
    @FocusState private var focused: Bool

    var body: some View {
        let chat = store.chat
        VStack(spacing: 0) {
            if chat.turns.isEmpty {
                intro
            } else {
                thread
            }
            Divider()
            composer
        }
        .navigationTitle(chat.turns.isEmpty ? "New Conversation" : chat.title ?? "New Conversation")
        .environment(\.openURL, OpenURLAction { url in
            // meeting links in answers open the meeting here, at the moment when given
            guard url.scheme == "boswell", let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
                  let id = parts.queryItems?.first(where: { $0.name == "id" })?.value else { return .systemAction }
            let at = parts.queryItems?.first { $0.name == "at" }?.value.flatMap(Double.init)
            store.openMeeting(id, at: at)
            return .handled
        })
        .onAppear { focused = true }
    }

    private var intro: some View {
        VStack(spacing: 14) {
            Image(systemName: "bubble.left.and.text.bubble.right")
                .font(.system(size: 40))
                .foregroundStyle(.tertiary)
            Text("Ask about your meetings").font(.title2.weight(.semibold))
            Text("Such as what you agreed with someone or where a project stands. Claude looks through your notes and transcripts to answer, so the parts it reads are sent to Anthropic. Audio and voice profiles never are.")
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
                .frame(maxWidth: 460)
            if !store.chat.claudeReady {
                Label("Add ANTHROPIC_API_KEY to .env and restart the app to ask questions.", systemImage: "key")
                    .foregroundStyle(.orange)
            }
        }
        .padding(30)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var thread: some View {
        let chat = store.chat
        return ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 18) {
                    ForEach(Array(chat.turns.enumerated()), id: \.element.id) { index, turn in
                        turnView(turn, index: index)
                            .id(turn.id)
                    }
                }
                .padding(24)
                .frame(maxWidth: 820, alignment: .leading)
                .frame(maxWidth: .infinity)
            }
            .onChange(of: chat.turns.count) { scrollToEnd(proxy) }
            .onChange(of: chat.turns.last?.answer) { scrollToEnd(proxy) }
            .onAppear { scrollToEnd(proxy) }
        }
    }

    private func scrollToEnd(_ proxy: ScrollViewProxy) {
        guard let last = store.chat.turns.last else { return }
        withAnimation { proxy.scrollTo(last.id, anchor: .top) }
    }

    @ViewBuilder
    private func turnView(_ turn: Chat.Turn, index: Int) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            // the question as a bubble on the right, like messages
            HStack {
                Spacer(minLength: 80)
                Text(turn.question)
                    .textSelection(.enabled)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                    .foregroundStyle(.white)
                    .background(Color.accentColor, in: RoundedRectangle(cornerRadius: 14))
            }
            if let error = turn.error {
                Label("Something went wrong: \(error)", systemImage: "exclamationmark.triangle")
                    .foregroundStyle(.red)
            } else if let answer = turn.answer {
                AnswerText(text: answer, links: turn.links)
                TodoPanel(state: Binding(get: { store.chat.turns[index].things },
                                         set: { store.chat.turns[index].things = $0 }),
                          find: {
                              guard let id = store.chat.conversationId else { throw ServerError("The conversation is not saved yet") }
                              return try await API.post(["api", "conversations", id, "todos"], ["turn": index])
                          })
            } else {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(Array((["Thinking"] + turn.steps).enumerated()), id: \.offset) { i, step in
                        HStack(spacing: 6) {
                            if i == turn.steps.count { ProgressView().controlSize(.small) }
                            Text(step.prefix(1).uppercased() + step.dropFirst())
                        }
                    }
                }
                .font(.callout)
                .foregroundStyle(.secondary)
            }
        }
    }

    private var composer: some View {
        let chat = store.chat
        let ready = chat.claudeReady && !chat.pending
        return HStack(alignment: .bottom, spacing: 10) {
            TextField(chat.turns.isEmpty ? "What did Daniel and I agree to in our last meeting?" : "Ask a follow-up",
                      text: $question, axis: .vertical)
                .textFieldStyle(.plain)
                .lineLimit(1...6)
                .focused($focused)
                .disabled(!ready)
                // return asks, shift return starts a new line
                .onKeyPress(keys: [.return]) { press in
                    if press.modifiers.contains(.shift) { return .ignored }
                    send()
                    return .handled
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(.background, in: RoundedRectangle(cornerRadius: 16))
                .overlay(RoundedRectangle(cornerRadius: 16).strokeBorder(.quaternary))
            Button(action: send) {
                Image(systemName: "arrow.up.circle.fill").font(.system(size: 24))
            }
            .buttonStyle(.borderless)
            .disabled(!ready || question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            .help("Ask")
        }
        .padding(12)
        .background(.bar)
    }

    private func send() {
        let text = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !store.chat.pending, store.chat.claudeReady else { return }
        question = ""
        Task { await store.chat.ask(text, store: store) }
    }
}

// a small markdown renderer for answers: headings, lists, bold, code and links, where
// meeting:ID and meeting:ID@SECONDS open the meeting, at that moment when given
struct AnswerText: View {
    let text: String
    let links: [String: String]

    private enum Block: Hashable {
        case heading(String), paragraph(String), bullet(String), numbered(String, String)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                switch block {
                case .heading(let s):
                    Text(inline(s)).font(.headline).padding(.top, 4)
                case .paragraph(let s):
                    Text(inline(s))
                case .bullet(let s):
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text("•").foregroundStyle(.secondary)
                        Text(inline(s))
                    }
                    .padding(.leading, 4)
                case .numbered(let n, let s):
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text("\(n).").foregroundStyle(.secondary).monospacedDigit()
                        Text(inline(s))
                    }
                    .padding(.leading, 4)
                }
            }
        }
        .textSelection(.enabled)
        .lineSpacing(2)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var blocks: [Block] {
        text.components(separatedBy: "\n").compactMap { line in
            if let m = line.firstMatch(of: #/^\s*[-*] (.*)$/#) { return .bullet(String(m.1)) }
            if let m = line.firstMatch(of: #/^\s*(\d+)[.)] (.*)$/#) { return .numbered(String(m.1), String(m.2)) }
            if let m = line.firstMatch(of: #/^#{1,4} (.*)$/#) { return .heading(String(m.1)) }
            return line.trimmingCharacters(in: .whitespaces).isEmpty ? nil : .paragraph(line)
        }
    }

    private func inline(_ s: String) -> AttributedString {
        // meeting links become boswell: links the chat view opens, unknown meetings plain text
        let linked = s.replacing(#/\[([^\]]+)\]\(meeting:([^)@]+?)(?:@(\d+))?\)/#) { m in
            let id = String(m.2)
            guard links[id] != nil, let encoded = id.addingPercentEncoding(withAllowedCharacters: .alphanumerics) else {
                return String(m.1)
            }
            return "[\(m.1)](boswell://meeting?id=\(encoded)\(m.3.map { "&at=\($0)" } ?? ""))"
        }
        return (try? AttributedString(markdown: linked, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
            ?? AttributedString(s)
    }
}

enum TodoState {
    case idle, loading, error(String), ready(FoundTodos), added(Int)
}

// claude picks out the owner's to-dos, which are checked and adjusted here before they
// are added to things
struct TodoPanel: View {
    @Environment(Store.self) private var store
    @Binding var state: TodoState
    var label = "Send to Things"
    let find: () async throws -> FoundTodos
    // sends the kept to-dos, by default straight to things
    var add: ([Todo]) async throws -> Added = { todos in
        try await API.post(["api", "things", "add"], AddTodosRequest(todos: todos))
    }
    // runs after adding, in place of the added message
    var added: ((Added) async -> Void)?
    @State private var adding = false
    var enabled = true

    var body: some View {
        switch state {
        case .idle:
            Button(label) { Task { await start() } }
                .buttonStyle(.link)
                .disabled(!enabled)
        case .loading:
            HStack(spacing: 6) {
                ProgressView().controlSize(.small)
                Text("Finding your to-dos").foregroundStyle(.secondary)
            }
        case .error(let message):
            HStack {
                Text(message).foregroundStyle(.secondary)
                Button("Try Again") { Task { await start() } }.buttonStyle(.link)
            }
        case .added(let n):
            Label("Added \(n) to-do\(n == 1 ? "" : "s") to Things.", systemImage: "checkmark.circle")
                .foregroundStyle(.secondary)
        case .ready(let found):
            if found.todos.isEmpty {
                Text("No to-dos for you here.").foregroundStyle(.secondary)
            } else {
                panel(found)
            }
        }
    }

    private func panel(_ found: FoundTodos) -> some View {
        let chosen = found.todos.filter(\.keep).count
        return GroupBox {
            VStack(alignment: .leading, spacing: 14) {
                ForEach(found.todos.indices, id: \.self) { i in
                    row(found, i)
                    if i < found.todos.count - 1 { Divider() }
                }
                HStack {
                    Spacer()
                    Button("Cancel") { state = .idle }
                    Button(adding ? "Adding" : "Add \(chosen) to Things") { Task { await add(found) } }
                        .buttonStyle(.borderedProminent)
                        .disabled(chosen == 0 || adding)
                }
            }
            .padding(6)
        } label: {
            Label("To-dos for Things", systemImage: "checklist")
        }
    }

    private func todo(_ i: Int, _ current: Todo) -> Binding<Todo> {
        // swiftui can read a row's binding once the panel has moved on, so fall back to the row's value
        Binding(get: {
            if case .ready(let found) = state, found.todos.indices.contains(i) { return found.todos[i] }
            return current
        }, set: { value in
            if case .ready(var found) = state, found.todos.indices.contains(i) {
                found.todos[i] = value
                state = .ready(found)
            }
        })
    }

    private func row(_ found: FoundTodos, _ i: Int) -> some View {
        let item = todo(i, found.todos[i])
        let t = item.wrappedValue
        return HStack(alignment: .top, spacing: 10) {
            Toggle("", isOn: item.keep).labelsHidden()
            VStack(alignment: .leading, spacing: 6) {
                TextField("Title", text: item.title)
                    .textFieldStyle(.roundedBorder)
                Text(detail(found, t))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                // side by side when there is room, as in a chat, stacked in a narrow inspector
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 14) { fields(found, item, t) }
                    VStack(alignment: .leading, spacing: 6) { fields(found, item, t) }
                }
            }
            .opacity(t.keep ? 1 : 0.45)
        }
    }

    @ViewBuilder
    private func fields(_ found: FoundTodos, _ item: Binding<Todo>, _ t: Todo) -> some View {
                    Picker("Project", selection: Binding(get: { t.projectId }, set: { id in
                        item.wrappedValue.projectId = id
                        item.wrappedValue.projectSource = "you"
                    })) {
                        Text("Inbox").tag("")
                        ForEach(found.areas, id: \.self) { area in
                            SwiftUI.Section(area) {
                                ForEach(found.projects.filter { $0.area == area }, id: \.id) { Text($0.name).tag($0.id) }
                            }
                        }
                    }
                    .fixedSize()
                    if let source = ["charter": "from the project charter", "claude": "Claude's pick"][t.projectSource ?? ""] {
                        Text(source).font(.caption).foregroundStyle(.tertiary)
                    }
                    Picker("When", selection: item.when) {
                        Text("No date").tag("")
                        Text("Today").tag("today")
                        Text("Tomorrow").tag("tomorrow")
                        if !["", "today", "tomorrow"].contains(t.when) { Text(t.when).tag(t.when) }
                    }
                    .fixedSize()
                    DeadlineField(deadline: item.deadline)
    }

    private func detail(_ found: FoundTodos, _ t: Todo) -> String {
        var parts = [t.notes]
        if let m = found.meetings[t.meetingId], let title = m.title { parts.append("From \(title).") }
        if t.seconds >= 0 { parts.append("(\(timestamp(Double(t.seconds))))") }
        return parts.filter { !$0.isEmpty }.joined(separator: " ")
    }

    private func start() async {
        state = .loading
        do { state = .ready(try await find()) } catch { state = .error(error.localizedDescription) }
    }

    private func add(_ found: FoundTodos) async {
        adding = true
        defer { adding = false }
        do {
            let result = try await add(found.todos.filter(\.keep))
            await store.refresh()
            if let added { await added(result) } else { state = .added(result.added) }
        } catch {
            store.show(error.localizedDescription)
        }
    }
}

// an optional date, kept as yyyy-mm-dd like the server wants
struct DeadlineField: View {
    @Binding var deadline: String

    private static let format: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.locale = Locale(identifier: "en_US_POSIX")
        return f
    }()

    var body: some View {
        HStack(spacing: 6) {
            Toggle("Deadline", isOn: Binding(get: { !deadline.isEmpty }, set: { on in
                deadline = on ? Self.format.string(from: Calendar.current.date(byAdding: .day, value: 7, to: .now) ?? .now) : ""
            }))
            if let date = Self.format.date(from: deadline) {
                DatePicker("", selection: Binding(get: { date }, set: { deadline = Self.format.string(from: $0) }),
                           displayedComponents: .date)
                    .labelsHidden()
                    .datePickerStyle(.field)
            }
        }
        .fixedSize()
    }
}
