import SwiftUI

enum PeopleSort: String, CaseIterable {
    case name = "Name"
    case meetings = "Most Meetings"
    case lastHeard = "Last Heard"
}

struct PeopleList: View {
    @Environment(Store.self) private var store
    @State private var search = ""
    @AppStorage("peopleSort") private var sort = PeopleSort.name

    private var shown: [Person] {
        // every word has to match the name, an organization or a team
        let words = search.lowercased().split(separator: " ")
        let matching = store.people.filter { p in
            let text = "\(p.name) \(p.organizations ?? "") \(p.teams ?? "")".lowercased()
            return words.allSatisfy { text.contains($0) }
        }
        switch sort {
        case .name: return matching.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
        case .meetings: return matching.sorted { ($0.meetings ?? 0, $1.name) > ($1.meetings ?? 0, $0.name) }
        case .lastHeard: return matching.sorted { ($0.lastHeard ?? "", $1.name) > ($1.lastHeard ?? "", $0.name) }
        }
    }

    var body: some View {
        @Bindable var store = store
        List(shown, selection: $store.personSelection) { p in
            VStack(alignment: .leading, spacing: 2) {
                HStack {
                    Text(p.name).fontWeight(.medium)
                    Spacer()
                    Text(count(p.meetings, "meeting"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if !p.subtitle.isEmpty {
                    Text(p.subtitle)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
            .padding(.vertical, 2)
            .tag(p.personId)
        }
        .overlay {
            if shown.isEmpty {
                if !search.isEmpty { ContentUnavailableView.search(text: search) }
                else { ContentUnavailableView("No People Yet", systemImage: "person.2",
                                             description: Text("People are added when you name a speaker.")) }
            }
        }
        .searchable(text: $search, placement: .sidebar, prompt: "Name, organization or team")
        .navigationTitle("People")
        .navigationSubtitle("\(store.people.count) people")
        .navigationSplitViewColumnWidth(min: 240, ideal: 290)
        .toolbar {
            ToolbarItem {
                Menu {
                    Picker("Sort By", selection: $sort) {
                        ForEach(PeopleSort.allCases, id: \.self) { Text($0.rawValue).tag($0) }
                    }
                    .pickerStyle(.inline)
                } label: {
                    Label("Sort", systemImage: "arrow.up.arrow.down")
                }
                .help("Sort people")
            }
        }
        .task { await store.loadPeople() }
    }
}

struct PersonView: View {
    @Environment(Store.self) private var store
    let personId: String
    @State private var meetings: [PersonMeeting]?
    @State private var selected: Set<String> = []
    @State private var sortOrder = [KeyPathComparator(\PersonMeeting.sortDate, order: .reverse)]
    @State private var editing = false

    var body: some View {
        if let person = store.person(personId) {
            VStack(alignment: .leading, spacing: 0) {
                header(person)
                    .padding(20)
                Divider()
                meetingsTable
            }
            .navigationTitle(person.name)
            .toolbar {
                ToolbarItem {
                    Button { editing = true } label: { Label("Edit", systemImage: "pencil") }
                        .help("Edit the name, organizations and teams")
                        .keyboardShortcut("e")
                }
            }
            .sheet(isPresented: $editing) {
                EditPersonSheet(person: person)
            }
            .task(id: store.revision) {
                meetings = try? await API.get(["api", "people", personId, "meetings"])
            }
        }
    }

    private func header(_ person: Person) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 14) {
                Text(initials(person.name))
                    .font(.title2.weight(.semibold))
                    .foregroundStyle(.white)
                    .frame(width: 52, height: 52)
                    .background(Color.accentColor.gradient, in: Circle())
                VStack(alignment: .leading, spacing: 3) {
                    Text(person.name).font(.title.weight(.semibold))
                    if !person.subtitle.isEmpty {
                        Text(person.subtitle).foregroundStyle(.secondary)
                    }
                }
            }
            Grid(alignment: .leading, horizontalSpacing: 28, verticalSpacing: 4) {
                GridRow {
                    stat("Meetings", count(person.meetings, "meeting"))
                    stat("Voice profile", voice(person))
                    stat("Last heard", person.lastHeard ?? "never")
                }
            }
        }
    }

    private func stat(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            Text(value)
        }
    }

    private var meetingsTable: some View {
        Table(sortedMeetings, selection: $selected, sortOrder: $sortOrder) {
            TableColumn("Date", value: \.sortDate) { Text($0.date ?? "").monospacedDigit() }
                .width(min: 80, ideal: 90, max: 110)
            TableColumn("Meeting", value: \.sortTitle) { m in
                HStack {
                    Text(m.title ?? m.meetingId)
                    if m.meetingStatus == "needs review" {
                        StageBadge(stage: .needsReview)
                    }
                }
            }
            TableColumn("Project") { Text($0.project ?? "").foregroundStyle(.secondary) }
            TableColumn("Tagged By") { Text($0.taggedBy).foregroundStyle(.secondary) }
                .width(min: 80, ideal: 100)
            TableColumn("Minutes") { m in
                Text(m.minutes.map { String(format: "%.1f", $0) } ?? "").monospacedDigit()
                    .frame(maxWidth: .infinity, alignment: .trailing)
            }
            .width(min: 50, ideal: 60, max: 70)
        }
        .contextMenu(forSelectionType: String.self) { ids in
            if let id = ids.first {
                Button("Open Meeting") { store.openMeeting(id) }
            }
        } primaryAction: { ids in
            // a double click opens the meeting
            if let id = ids.first { store.openMeeting(id) }
        }
        .overlay {
            if meetings == nil { ProgressView() }
            else if meetings?.isEmpty == true {
                ContentUnavailableView("Not tagged in any meetings yet", systemImage: "calendar")
            }
        }
    }

    private var sortedMeetings: [PersonMeeting] {
        (meetings ?? []).sorted(using: sortOrder)
    }

    private func voice(_ person: Person) -> String {
        let clips = Int(person.entries ?? 0)
        guard clips > 0 else { return "none" }
        let minutes = (person.profileSeconds ?? 0) / 60
        return "\(count(Double(clips), "clip")), \(String(format: "%.1f", minutes)) min"
    }

    private func initials(_ name: String) -> String {
        name.split(separator: " ").prefix(2).compactMap(\.first).map(String.init).joined()
    }
}

struct EditPersonSheet: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    let person: Person
    @State private var name = ""
    @State private var organizations: [String] = []
    @State private var teams: [String] = []
    @State private var lookups: Lookups?
    @State private var saving = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Form {
                TextField("Name", text: $name)
                ChipsField(label: "Organizations", values: $organizations, options: lookups?.organizations ?? [],
                           prompt: "Add an organization")
                ChipsField(label: "Teams", values: $teams, options: lookups?.teams ?? [], prompt: "Add a team")
            }
            .formStyle(.grouped)
            Text("The name shows in voice matching, transcripts and notes, where it is a link. Organizations and teams are written to the person's note in Obsidian. Saving updates every past transcript and note.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 20)
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button(saving ? "Saving" : "Save") { Task { await save() } }
                    .keyboardShortcut(.defaultAction)
                    .disabled(saving || name.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            .padding(20)
        }
        .frame(width: 480)
        .task {
            name = person.name
            organizations = person.organizationList
            teams = person.teamList
            lookups = try? await API.get(["api", "lookups"])
        }
    }

    private func save() async {
        saving = true
        defer { saving = false }
        let newName = name.trimmingCharacters(in: .whitespaces)
        await store.run {
            // details first, so they land in the note under the current name
            let _: Ok = try await API.post(["api", "people", person.personId, "details"],
                                           DetailsRequest(organizations: organizations, teams: teams))
            if newName != person.name {
                let _: Ok = try await API.post(["api", "people", person.personId, "rename"], ["name": newName])
            }
            await store.loadPeople()
            await store.refresh()
            store.revision += 1
            store.show("Saved. Transcripts and notes updated.")
            dismiss()
        }
    }
}

// a list of removable chips with a field that adds one, suggesting known values
struct ChipsField: View {
    let label: String
    @Binding var values: [String]
    let options: [String]
    let prompt: String
    @State private var typed = ""

    var body: some View {
        LabeledContent(label) {
            VStack(alignment: .trailing, spacing: 6) {
                if !values.isEmpty {
                    FlowLayout(spacing: 6) {
                        ForEach(values, id: \.self) { value in
                            HStack(spacing: 4) {
                                Text(value)
                                Button { values.removeAll { $0 == value } } label: {
                                    Image(systemName: "xmark.circle.fill")
                                }
                                .buttonStyle(.borderless)
                                .foregroundStyle(.secondary)
                                .help("Remove")
                            }
                            .padding(.horizontal, 8)
                            .padding(.vertical, 3)
                            .background(.quaternary, in: Capsule())
                        }
                    }
                }
                TextField(prompt, text: $typed, prompt: Text(prompt))
                    .labelsHidden()
                    .textInputSuggestions {
                        ForEach(matches, id: \.self) { Text($0).textInputCompletion($0) }
                    }
                    .onSubmit(add)
            }
        }
    }

    private var matches: [String] {
        let query = typed.trimmingCharacters(in: .whitespaces)
        return options.filter { option in
            !values.contains(option) && (query.isEmpty || option.localizedCaseInsensitiveContains(query))
        }
        .prefix(10).map { $0 }
    }

    private func add() {
        let value = typed.trimmingCharacters(in: .whitespaces)
        // a known value keeps its spelling
        let known = options.first { $0.caseInsensitiveCompare(value) == .orderedSame } ?? value
        if !value.isEmpty && !values.contains(where: { $0.caseInsensitiveCompare(value) == .orderedSame }) {
            values.append(known)
        }
        typed = ""
    }
}

// lays chips out in rows, wrapping when a row is full
struct FlowLayout: Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let rows = arrange(subviews, width: proposal.width ?? .infinity)
        return CGSize(width: rows.map(\.width).max() ?? 0, height: rows.map(\.height).reduce(0, +) + spacing * CGFloat(max(rows.count - 1, 0)))
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var y = bounds.minY
        for row in arrange(subviews, width: bounds.width) {
            // right aligned, to sit under the label's trailing edge like the other form fields
            var x = bounds.maxX - row.width
            for index in row.items {
                let size = subviews[index].sizeThatFits(.unspecified)
                subviews[index].place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
                x += size.width + spacing
            }
            y += row.height + spacing
        }
    }

    private func arrange(_ subviews: Subviews, width: CGFloat) -> [(items: [Int], width: CGFloat, height: CGFloat)] {
        var rows: [(items: [Int], width: CGFloat, height: CGFloat)] = []
        for (index, subview) in subviews.enumerated() {
            let size = subview.sizeThatFits(.unspecified)
            if let last = rows.last, last.width + spacing + size.width <= width {
                rows[rows.count - 1].items.append(index)
                rows[rows.count - 1].width += spacing + size.width
                rows[rows.count - 1].height = max(last.height, size.height)
            } else {
                rows.append(([index], size.width, size.height))
            }
        }
        return rows
    }
}

// a meeting imported from obsidian, which has its note in the vault and no recording
struct ImportedView: View {
    @Environment(Store.self) private var store
    let meetingId: String
    @State private var meeting: ImportedMeeting?
    @State private var error: String?

    var body: some View {
        Group {
            if let meeting {
                VStack(alignment: .leading, spacing: 0) {
                    VStack(alignment: .leading, spacing: 6) {
                        Label("Imported from Obsidian, with no recording", systemImage: "doc.text")
                            .foregroundStyle(.secondary)
                        if !meeting.people.isEmpty {
                            Text(meeting.people.joined(separator: ", "))
                        }
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 10)
                    Divider()
                    ScrollView {
                        Text(meeting.markdown ?? "The note is no longer in the vault.")
                            .font(.system(.body, design: .monospaced))
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(16)
                    }
                }
            } else if let error {
                ContentUnavailableView("Could not load this meeting", systemImage: "exclamationmark.triangle",
                                       description: Text(error))
            } else {
                ProgressView()
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .navigationTitle(store.meeting(meetingId)?.name ?? meetingId)
        .navigationSubtitle([meeting?.date, meeting?.project].compactMap { $0 }.joined(separator: " · "))
        .toolbar {
            ToolbarItem {
                Button { Task { await store.openInObsidian(meetingId) } } label: {
                    Label("Open in Obsidian", systemImage: "arrow.up.forward.app")
                }
                .help("Open the note in Obsidian")
            }
        }
        .task {
            do { meeting = try await API.get(API.meeting(meetingId, "imported")) }
            catch { self.error = error.localizedDescription }
        }
    }
}

func count(_ n: Double?, _ noun: String) -> String {
    let value = Int(n ?? 0)
    return "\(value) \(noun)\(value == 1 ? "" : "s")"
}
