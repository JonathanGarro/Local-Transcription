import SwiftUI

enum Tab: String, CaseIterable {
    case speakers = "Speakers"
    case transcript = "Transcript"
    case note = "Note"
}

struct MeetingView: View {
    @Environment(Store.self) private var store
    let meetingId: String
    @State private var detail: MeetingDetail?
    @State private var tab: Tab?
    @State private var error: String?

    var body: some View {
        if store.meeting(meetingId)?.sourceType == "obsidian" {
            ImportedView(meetingId: meetingId)
        } else {
            processed
        }
    }

    private var processed: some View {
        Group {
            if let detail, let tab {
                switch tab {
                case .speakers: SpeakersView(detail: detail) { self.detail = $0 }
                case .transcript: TranscriptView(meetingId: meetingId)
                case .note: NoteView(meetingId: meetingId)
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
        .navigationSubtitle(subtitle)
        .toolbar {
            ToolbarItem(placement: .principal) {
                Picker("View", selection: Binding(get: { tab ?? .note }, set: { tab = $0 })) {
                    ForEach(Tab.allCases, id: \.self) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented)
                .fixedSize()
            }
        }
        .task(id: store.revision) { await load() }
        .onChange(of: store.focusTime) { if store.focusTime != nil { tab = .transcript } }
        .onDisappear { Player.shared.stop() }
    }

    private var subtitle: String {
        guard let detail else { return "" }
        var parts = [detail.date, detail.minutes.map { "\(Int($0.rounded())) min" },
                     "\(detail.speakers.count) speakers"].compactMap { $0 }
        if detail.split == true, let owner = detail.owner { parts.append("\(owner) on their own channel") }
        return parts.joined(separator: " · ")
    }

    private func load() async {
        do {
            let loaded: MeetingDetail = try await API.get(API.meeting(meetingId))
            detail = loaded
            // open where the meeting is in its workflow, as the inbox does
            if tab == nil {
                tab = store.focusTime != nil ? .transcript
                    : loaded.status != "done" ? .speakers : loaded.note == true ? .note : .transcript
            }
        } catch {
            self.error = error.localizedDescription
        }
    }
}

// speakers

enum Decision: Equatable {
    case name(String, enroll: Bool)
    case skip
    case same(String)
}

struct SpeakersView: View {
    @Environment(Store.self) private var store
    let detail: MeetingDetail
    let updated: (MeetingDetail) -> Void
    @State private var decisions: [String: Decision] = [:]
    @State private var expanded: Set<String> = []
    @State private var people: [String] = []
    @State private var saving = false

    var body: some View {
        let unresolved = detail.speakers.filter { !$0.resolved }
        let resolved = detail.speakers.filter(\.resolved)
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                if unresolved.isEmpty {
                    Label("Every speaker is named. Nothing to review.", systemImage: "checkmark.circle")
                        .foregroundStyle(.secondary)
                } else {
                    Text("Needs a Name").font(.headline)
                    ForEach(unresolved) { card($0) }
                }
                if !resolved.isEmpty {
                    Text("Identified").font(.headline).padding(.top, 8)
                    ForEach(resolved) { card($0) }
                }
            }
            .padding(20)
            .frame(maxWidth: 760, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button(saving ? "Saving" : "Save Names") { Task { await apply() } }
                    .buttonStyle(.borderedProminent)
                    .disabled(decisions.isEmpty || saving)
                    .keyboardShortcut(.return, modifiers: .command)
                    .help("Name the speakers and update the transcript")
            }
        }
        .task {
            let found: [Person] = (try? await API.get(["api", "people"])) ?? []
            people = found.map(\.name).sorted()
        }
    }

    @ViewBuilder
    private func card(_ spk: Speaker) -> some View {
        let open = !spk.resolved || expanded.contains(spk.speaker)
        GroupBox {
            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .firstTextBaseline) {
                    Text(spk.label).font(.title3.weight(.semibold))
                    Text("\(status(spk)) · \(Int((spk.minutes ?? 0).rounded())) min")
                        .foregroundStyle(.secondary)
                    Spacer()
                    if spk.resolved {
                        Button(open ? "Done" : "Wrong?") {
                            if open { expanded.remove(spk.speaker) } else { expanded.insert(spk.speaker) }
                        }
                        .buttonStyle(.link)
                    }
                }
                if open { editor(spk) }
                if let d = decisions[spk.speaker] {
                    Label(describe(d), systemImage: "arrow.turn.down.right")
                        .font(.callout)
                        .foregroundStyle(Color.accentColor)
                }
            }
            .padding(6)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    @ViewBuilder
    private func editor(_ spk: Speaker) -> some View {
        let player = Player.shared
        VStack(alignment: .leading, spacing: 6) {
            ForEach(Array(spk.clips.enumerated()), id: \.offset) { i, clip in
                let key = "\(spk.speaker)-\(i)"
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Button {
                        Task { await player.toggle(key, meeting: detail.meetingId, speaker: spk.speaker, start: clip.start, end: clip.end) }
                    } label: {
                        Image(systemName: player.playing == key ? "stop.fill" : "play.fill")
                            .frame(width: 14)
                    }
                    .buttonStyle(.borderless)
                    Text(timestamp(clip.start))
                        .monospacedDigit()
                        .foregroundStyle(.secondary)
                    Text(clip.text?.isEmpty == false ? clip.text! : "no words")
                        .italic(clip.text?.isEmpty != false)
                        .lineLimit(2)
                }
            }
        }

        let suggestions = spk.suggestions.filter { $0.score > 0.3 }
        if !suggestions.isEmpty {
            HStack {
                ForEach(suggestions, id: \.name) { s in
                    let chosen = decisions[spk.speaker] == .name(s.name, enroll: enroll(spk))
                    Button {
                        decisions[spk.speaker] = chosen ? nil : .name(s.name, enroll: enroll(spk))
                    } label: {
                        Text(s.name) + Text("  \(percent(s.score))").foregroundStyle(.secondary)
                    }
                    .buttonStyle(.bordered)
                    .tint(chosen ? .accentColor : nil)
                }
            }
        }

        HStack {
            TextField("Search people or type a new name", text: nameBinding(spk))
                .textFieldStyle(.roundedBorder)
                .frame(maxWidth: 320)
                .textInputSuggestions {
                    let typed = typedName(spk)
                    if typed.count > 1 {
                        ForEach(people.filter { $0.localizedCaseInsensitiveContains(typed) && $0 != typed }.prefix(8), id: \.self) {
                            Text($0).textInputCompletion($0)
                        }
                    }
                }
            Toggle("Add voice to profile", isOn: enrollBinding(spk))
                .disabled(typedName(spk).isEmpty)
        }

        HStack {
            let others = detail.speakers.filter { $0.speaker != spk.speaker && !$0.isMic }
            if !spk.isMic && !others.isEmpty {
                Picker("Same person as", selection: sameBinding(spk)) {
                    Text("Choose").tag(String?.none)
                    ForEach(others) { Text($0.label).tag(Optional($0.speaker)) }
                }
                .fixedSize()
            }
            Button(decisions[spk.speaker] == .skip ? "Will Stay Unnamed" : "Leave Unnamed") {
                decisions[spk.speaker] = decisions[spk.speaker] == .skip ? nil : .skip
            }
            if decisions[spk.speaker] != nil {
                Button("Undo") { decisions[spk.speaker] = nil }
                    .buttonStyle(.link)
            }
        }
    }

    // bindings onto the decisions

    private func typedName(_ spk: Speaker) -> String {
        if case .name(let name, _) = decisions[spk.speaker] { return name }
        return ""
    }

    private func enroll(_ spk: Speaker) -> Bool {
        if case .name(_, let enroll) = decisions[spk.speaker] { return enroll }
        return true
    }

    private func nameBinding(_ spk: Speaker) -> Binding<String> {
        Binding(get: { typedName(spk) }, set: { name in
            decisions[spk.speaker] = name.isEmpty ? nil : .name(name, enroll: enroll(spk))
        })
    }

    private func enrollBinding(_ spk: Speaker) -> Binding<Bool> {
        Binding(get: { enroll(spk) }, set: { on in
            if case .name(let name, _) = decisions[spk.speaker] { decisions[spk.speaker] = .name(name, enroll: on) }
        })
    }

    private func sameBinding(_ spk: Speaker) -> Binding<String?> {
        Binding(get: {
            if case .same(let target) = decisions[spk.speaker] { return target }
            return nil
        }, set: { target in
            decisions[spk.speaker] = target.map { .same($0) }
        })
    }

    private func status(_ spk: Speaker) -> String {
        switch spk.status {
        case "mic": return "your mic channel"
        case "matched": return "matched \(percent(spk.score ?? 0))"
        case "confirmed": return "confirmed"
        case "skipped": return "left unnamed"
        default:
            if let best = spk.bestName { return "unknown, closest \(best) \(percent(spk.bestScore ?? 0))" }
            return "unknown"
        }
    }

    private func describe(_ d: Decision) -> String {
        switch d {
        case .skip: return "Will be left unnamed"
        case .same(let target):
            return "Will be merged into \(detail.speakers.first { $0.speaker == target }?.label ?? target)"
        case .name(let name, let enroll):
            let known = people.contains { $0.caseInsensitiveCompare(name) == .orderedSame }
            return "Will be named \(name)\(known ? "" : " (new person)")\(enroll ? "" : ", voice not added to profile")"
        }
    }

    private func apply() async {
        var body = ApplyRequest()
        for (speaker, d) in decisions {
            switch d {
            case .name(let name, let enroll):
                body.names[speaker] = name.trimmingCharacters(in: .whitespaces)
                if !enroll { body.noEnroll.append(speaker) }
            case .skip:
                body.skip.append(speaker)
            case .same(let target):
                if decisions[target] == .same(speaker) {
                    return store.show("Two speakers are each marked as the same as the other, keep only one")
                }
                body.merges.append([speaker, target])
            }
        }
        // merge chains resolve in order, so a speaker merged into one that is itself merged goes first
        let sources = Set(body.merges.map { $0[0] })
        body.merges.sort { sources.contains($0[1]) && !sources.contains($1[1]) }
        saving = true
        defer { saving = false }
        Player.shared.stop()
        await store.run {
            let result: MeetingDetail = try await API.post(API.meeting(detail.meetingId, "apply"), body)
            decisions = [:]
            expanded = []
            updated(result)
            store.show(result.status == "done" ? "Saved. The transcript is updated." : "Saved. Some speakers still need a name.")
            await store.refresh()
        }
    }
}

// transcript

struct TranscriptView: View {
    @Environment(Store.self) private var store
    let meetingId: String
    @State private var transcript: Transcript?
    @State private var filter = ""
    @State private var highlighted: Double?

    private var turns: [Turn] {
        let all = transcript?.turns ?? []
        guard !filter.isEmpty else { return all }
        return all.filter { $0.text.localizedCaseInsensitiveContains(filter) || ($0.name ?? "").localizedCaseInsensitiveContains(filter) }
    }

    var body: some View {
        let player = Player.shared
        ScrollViewReader { proxy in
        List(turns) { turn in
            let key = "turn-\(turn.start)"
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(turn.name ?? turn.speaker)
                        .fontWeight(.semibold)
                        .foregroundStyle(color(turn.speaker))
                    Button {
                        Task { await player.toggle(key, meeting: meetingId, speaker: turn.speaker, start: turn.start, end: turn.end) }
                    } label: {
                        Label(timestamp(turn.start), systemImage: player.playing == key ? "stop.fill" : "play.fill")
                            .monospacedDigit()
                    }
                    .buttonStyle(.borderless)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .help("Play this turn")
                }
                .frame(width: 150, alignment: .leading)
                Text(turn.text)
                    .textSelection(.enabled)
                    .lineSpacing(3)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.vertical, 4)
            .listRowBackground(highlighted == turn.id ? Color.accentColor.opacity(0.15) : nil)
        }
        .onChange(of: transcript?.turns.count) { focus(proxy) }
        .onChange(of: store.focusTime) { focus(proxy) }
        }
        .overlay {
            if transcript == nil { ProgressView() }
            else if turns.isEmpty { ContentUnavailableView.search(text: filter) }
        }
        .safeAreaInset(edge: .top) {
            HStack {
                Image(systemName: "line.3.horizontal.decrease")
                    .foregroundStyle(.secondary)
                TextField("Filter the transcript", text: $filter)
                    .textFieldStyle(.plain)
                if !filter.isEmpty {
                    Text("\(turns.count) of \(transcript?.turns.count ?? 0)")
                        .foregroundStyle(.secondary)
                        .font(.caption)
                }
            }
            .padding(8)
            .background(.bar)
        }
        .toolbar {
            ToolbarItem {
                Button { Task { await store.reveal(meetingId) } } label: { Label("Show in Finder", systemImage: "folder") }
                    .help("Show the transcript file in Finder")
            }
        }
        .task(id: store.revision) { transcript = try? await API.get(API.meeting(meetingId, "transcript")) }
    }

    // a link to a moment opens on the turn it falls in
    private func focus(_ proxy: ScrollViewProxy) {
        guard let time = store.focusTime, let turns = transcript?.turns, !turns.isEmpty else { return }
        let turn = turns.last { $0.start <= time + 0.5 } ?? turns[0]
        filter = ""
        highlighted = turn.id
        store.focusTime = nil
        Task {
            // let the list lay out before scrolling
            try? await Task.sleep(for: .milliseconds(100))
            withAnimation { proxy.scrollTo(turn.id, anchor: .center) }
        }
    }

    // a steady color per speaker, so a conversation reads at a glance
    private func color(_ speaker: String) -> Color {
        let colors: [Color] = [.blue, .orange, .purple, .teal, .pink, .indigo, .brown, .mint]
        if speaker == "MIC" { return .green }
        // hashValue changes between launches, so sum the characters instead
        return colors[speaker.unicodeScalars.reduce(0) { $0 + Int($1.value) } % colors.count]
    }
}

// helpers

func timestamp(_ seconds: Double) -> String {
    let s = Int(seconds)
    return s >= 3600 ? String(format: "%d:%02d:%02d", s / 3600, s / 60 % 60, s % 60) : String(format: "%d:%02d", s / 60, s % 60)
}

func percent(_ score: Double) -> String { "\(Int((score * 100).rounded()))%" }
