import SwiftUI

struct VocabularyView: View {
    @Environment(Store.self) private var store

    // a row as edited, with variants as typed, and an id that survives saves
    struct Row: Identifiable, Hashable {
        let id = UUID()
        var term: String
        var variants: String

        init(_ entry: VocabEntry) {
            term = entry.term
            variants = entry.variants.joined(separator: ", ")
        }

        var entry: VocabEntry {
            VocabEntry(term: term.trimmingCharacters(in: .whitespaces), variants: VocabularyView.split(variants))
        }
    }

    @State private var rows: [Row] = []
    @State private var used = 0
    @State private var max = 0
    @State private var loaded = false
    @State private var selection: Set<Row.ID> = []
    @State private var newTerm = ""
    @State private var newVariants = ""
    @State private var saveTask: Task<Void, Never>?
    @State private var lastSent: [VocabEntry] = []
    @FocusState private var addFocused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 14) {
                Text("Terms Whisper should spell your way, such as acronyms, project names and jargon. Each term is a spelling hint, and anything it mishears the same way every time can be listed under Also Heard As to be replaced with the term. Changes apply to recordings transcribed from now on. People's names are added as hints on their own.")
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                addBar
                usage
            }
            .padding(20)
            Divider()
            table
        }
        .navigationTitle("Vocabulary")
        .navigationSubtitle(loaded ? "\(rows.count) term\(rows.count == 1 ? "" : "s")" : "")
        .toolbar {
            ToolbarItem {
                Button { addFocused = true } label: { Label("Add Term", systemImage: "plus") }
                    .help("Add a term")
            }
            ToolbarItem {
                Button { remove(selection) } label: { Label("Remove", systemImage: "minus") }
                    .disabled(selection.isEmpty)
                    .help("Remove the selected terms")
            }
        }
        .task { await load() }
        .onChange(of: rows) { scheduleSave() }
        .onDisappear { saveNow() }
    }

    private var addBar: some View {
        HStack(alignment: .firstTextBaseline) {
            TextField("Term", text: $newTerm, prompt: Text("Term, such as GMS"))
                .focused($addFocused)
                .frame(maxWidth: 240)
                .onSubmit(add)
            TextField("Also heard as", text: $newVariants, prompt: Text("Also heard as, such as GMs, G.M.S."))
                .frame(maxWidth: 340)
                .onSubmit(add)
            Button("Add", action: add)
                .disabled(newTerm.trimmingCharacters(in: .whitespaces).isEmpty)
        }
        .textFieldStyle(.roundedBorder)
    }

    private var usage: some View {
        VStack(alignment: .leading, spacing: 4) {
            Gauge(value: Double(Swift.min(used, Swift.max(max, 1))), in: 0...Double(Swift.max(max, 1))) {}
                .gaugeStyle(.accessoryLinearCapacity)
                .tint(used > max ? .red : .accentColor)
                .frame(maxWidth: 420)
            Text("Spelling hints use \(used) of \(max) characters\(used > max ? ", so the last terms are left out. Fixes still apply to them" : "").")
                .font(.caption)
                .foregroundStyle(used > max ? .red : .secondary)
        }
    }

    private var table: some View {
        Table($rows, selection: $selection) {
            TableColumn("Term") { $row in
                TextField("Term", text: $row.term)
                    .textFieldStyle(.plain)
                    .fontWeight(.medium)
            }
            .width(min: 140, ideal: 220)
            TableColumn("Also Heard As") { $row in
                TextField("Also heard as", text: $row.variants, prompt: Text("none"))
                    .textFieldStyle(.plain)
            }
        }
        .contextMenu(forSelectionType: Row.ID.self) { ids in
            Button("Remove") { remove(ids) }
                .disabled(ids.isEmpty)
        }
        .onDeleteCommand { remove(selection) }
        .overlay {
            if loaded && rows.isEmpty {
                ContentUnavailableView("No Terms Yet", systemImage: "character.book.closed",
                                       description: Text("Add acronyms and names Whisper gets wrong."))
            } else if !loaded {
                ProgressView()
            }
        }
    }

    // editing

    private func add() {
        let term = newTerm.trimmingCharacters(in: .whitespaces)
        guard !term.isEmpty else { return }
        // adding a term already listed replaces it
        rows.removeAll { $0.term.trimmingCharacters(in: .whitespaces).caseInsensitiveCompare(term) == .orderedSame }
        rows.append(Row(VocabEntry(term: term, variants: Self.split(newVariants))))
        newTerm = ""
        newVariants = ""
        addFocused = true
        saveNow()
    }

    private func remove(_ ids: Set<Row.ID>) {
        rows.removeAll { ids.contains($0.id) }
        selection.subtract(ids)
        saveNow()
    }

    static func split(_ text: String) -> [String] {
        text.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
    }

    // saving, a moment after typing stops, like the note

    private var entries: [VocabEntry] { rows.map(\.entry).filter { !$0.term.isEmpty } }

    private func scheduleSave() {
        guard loaded, entries != lastSent else { return }
        saveTask?.cancel()
        saveTask = Task {
            try? await Task.sleep(for: .milliseconds(800))
            guard !Task.isCancelled else { return }
            await save()
        }
    }

    private func saveNow() {
        saveTask?.cancel()
        guard loaded, entries != lastSent else { return }
        Task { await save() }
    }

    private func load() async {
        guard let state: Vocabulary = try? await API.get(["api", "vocabulary"]) else { return }
        apply(state)
        loaded = true
    }

    private func apply(_ state: Vocabulary) {
        rows = state.entries.map(Row.init)
        lastSent = state.entries
        used = state.used
        max = state.max
    }

    private func save() async {
        let sending = entries
        lastSent = sending
        do {
            let state: Vocabulary = try await API.put(["api", "vocabulary"], ["entries": sending])
            used = state.used
            max = state.max
            // only take the server's list if it changed what was sent, so typing is not interrupted
            if state.entries != sending { apply(state) }
        } catch {
            store.show(error.localizedDescription)
            await load()
        }
    }
}
