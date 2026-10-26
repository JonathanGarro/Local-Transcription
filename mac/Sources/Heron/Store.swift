import AppKit
import AVFoundation
import Observation
import SwiftUI

enum Place: String, Hashable, CaseIterable {
    case inbox = "Inbox"
    case plaud = "Plaud"
    case archive = "Archive"
    case ask = "Ask"
    case people = "People"
    case vocabulary = "Vocabulary"
    case home = "Data"
    case settings = "Settings"

    static let meetings: [Place] = [.inbox, .plaud, .archive]
    // pages with no list column
    var fullWidth: Bool { [.home, .vocabulary, .settings].contains(self) }
    static let library: [Place] = [.ask, .people, .vocabulary, .home, .settings]

    var icon: String {
        switch self {
        case .inbox: "tray"
        case .archive: "archivebox"
        case .plaud: "icloud.and.arrow.down"
        case .home: "chart.bar.xaxis"
        case .ask: "bubble.left.and.text.bubble.right"
        case .people: "person.2"
        case .vocabulary: "character.book.closed"
        case .settings: "gearshape"
        }
    }
}

func accentTint(_ name: String) -> Color {
    switch name {
    case "red": .red
    case "green": .green
    case "purple": .purple
    case "orange": .orange
    default: .blue
    }
}

enum Stage {
    case needsReview, writing, ready, noNote

    var label: String {
        switch self {
        case .needsReview: "Needs review"
        case .writing: "Writing note"
        case .ready: "Ready to send"
        case .noNote: "No note yet"
        }
    }
}

// the library as the server last reported it, refreshed every couple of seconds
@MainActor @Observable
final class Store {
    // HERON_PAGE=vocabulary opens on that page, for trying one page from the command line
    var section: Place = ProcessInfo.processInfo.environment["HERON_PAGE"]
        .flatMap { page in Place.allCases.first { $0.rawValue.lowercased() == page.lowercased() } } ?? .inbox
    // HERON_MEETING=<meeting id> selects that meeting at launch, with HERON_PAGE=archive say
    var selection: String? = ProcessInfo.processInfo.environment["HERON_MEETING"]
    var personSelection: String?
    var people: [Person] = []
    let chat = Chat()
    // a moment to show in the transcript of the meeting being opened
    var focusTime: Double?
    // a tab for the open meeting to switch to, such as the transcript from its note
    var showTab: Tab?
    private var pendingLink: URL?
    // the accent chosen in settings
    var accent = "blue"
    // Color.accentColor is the system's, so views that draw in the accent use this
    var accentColor: Color { accentTint(accent) }
    var meetings: [Meeting] = []
    var jobs: [Job] = []
    var owner: String?
    // whether the plaud cli is installed, so the plaud page can be shown
    var plaudInstalled = false
    // the setup shown when the app is first opened, or again from settings
    var showSetup = ProcessInfo.processInfo.environment["HERON_SETUP_STEP"] != nil
    // the to-do app chosen in settings, nil when the to-do features are off
    var todoApp: String?
    var todoAppName: String { Self.todoApps[todoApp ?? ""] ?? "your to-do app" }
    static let todoApps = ["things": "Things", "todoist": "Todoist", "reminders": "Reminders"]
    var connected = false
    var startError: String?
    // what installing the python packages is doing, the first time the app opens
    var installing: String?
    var message: String?
    var importing = false
    // bumped when something outside a view changes its meeting, so the view reloads
    var revision = 0

    // the open note saves its typing through this before the app quits
    var saveNote: (meetingId: String, save: () async -> Void)?

    private var polling: Task<Void, Never>?

    var inbox: [Meeting] { meetings.filter { $0.archived == nil } }

    var archive: [Meeting] {
        meetings.filter { $0.archived != nil }
            .sorted { ($0.date ?? "", $0.archived ?? "") > ($1.date ?? "", $1.archived ?? "") }
    }

    var reviewCount: Int { inbox.filter(\.needsReview).count }

    // what the rest of the app follows from settings
    func apply(_ settings: SettingsState) {
        accent = settings.accent
        plaudInstalled = settings.plaudInstalled
        todoApp = settings.todoApp == "none" ? nil : settings.todoApp
        if settings.setupNeeded { showSetup = true }
    }

    // the plaud page needs the cli and the switch in settings on
    func shows(_ place: Place, plaudEnabled: Bool) -> Bool {
        place != .plaud || (plaudInstalled && plaudEnabled)
    }

    func meeting(_ id: String?) -> Meeting? { meetings.first { $0.meetingId == id } }

    func stage(_ m: Meeting) -> Stage {
        if m.needsReview { return .needsReview }
        if noteJob(m.meetingId) != nil { return .writing }
        return m.note == true ? .ready : .noNote
    }

    func noteJob(_ meetingId: String) -> Job? {
        jobs.first { $0.kind == "note" && $0.meetingId == meetingId && $0.running }
    }

    func start() async {
        // the window can close without quitting, and starts this again when it reopens
        guard !connected else { return }
        startError = nil
        do {
            try await Server.shared.start { line in
                Task { @MainActor in self.installing = line }
            }
            connected = true
            installing = nil
        } catch {
            startError = error.localizedDescription
            installing = nil
            return
        }
        await refresh()
        if let link = pendingLink {
            pendingLink = nil
            open(link: link)
        }
        // things used to be a switch here. switched off, it becomes no to-do app on the server
        if UserDefaults.standard.object(forKey: Prefs.things) as? Bool == false {
            let _: SettingsState? = try? await API.put(["api", "settings"], SettingsChange(todoApp: "none"))
            UserDefaults.standard.removeObject(forKey: Prefs.things)
        }
        if let settings: SettingsState = try? await API.get(["api", "settings"]) { apply(settings) }
        await recoverRecordings()
        polling = Task {
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(2))
                await refresh()
            }
        }
    }

    // the server keeps its queue in memory, so quitting stops this work for good
    func unfinishedWork() async -> String? {
        await refresh()
        let running = jobs.filter(\.running)
        let notes = running.filter { $0.kind == "note" }.count
        let transcriptions = running.count - notes
        var parts: [String] = []
        if transcriptions > 0 { parts.append(transcriptions == 1 ? "1 transcription" : "\(transcriptions) transcriptions") }
        if notes > 0 { parts.append(notes == 1 ? "1 note" : "\(notes) notes") }
        if chat.pending { parts.append("an answer in Ask") }
        if recorder.isRecording { parts.append("a recording, which is saved and transcribed when Heron next opens") }
        return parts.isEmpty ? nil : ListFormatter.localizedString(byJoining: parts)
    }

    func refresh() async {
        guard let state: AppState = try? await API.get(["api", "state"]) else { return }
        let finished = jobs.filter(\.running).map(\.id).filter { id in state.jobs.contains { $0.id == id && !$0.running } }
        if state.meetings != meetings { meetings = state.meetings }
        if state.jobs != jobs { jobs = state.jobs }
        owner = state.owner
        if !finished.isEmpty { revision += 1 }
        NSApp.dockTile.badgeLabel = reviewCount > 0 ? "\(reviewCount)" : nil
    }

    func copy(_ text: String, _ what: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
        show("Copied the \(what)")
    }

    func show(_ text: String) {
        message = text
        Task {
            try? await Task.sleep(for: .seconds(4))
            if message == text { message = nil }
        }
    }

    func run(_ work: () async throws -> Void) async {
        do { try await work() } catch { show(error.localizedDescription) }
    }

    // actions

    // recording

    let recorder = Recorder()

    func startRecording() async {
        await run {
            guard await AVCaptureDevice.requestAccess(for: .audio) else {
                throw RecorderError("Heron isn't allowed to use the microphone. Allow it in System Settings, Privacy & Security, Microphone")
            }
            let settings: SettingsState = try await API.get(["api", "settings"])
            try recorder.start(micOnRight: settings.micChannel == "right")
        }
    }

    func stopRecording() async {
        let name = recorder.name.trimmingCharacters(in: .whitespaces)
        guard let caf = recorder.stop() else { return }
        await transcribeRecording(caf, name: name.isEmpty ? "Call" : name)
    }

    // compress a finished recording and send it to be transcribed. the files are deleted once
    // the server has its copy, and kept in the recordings folder if anything goes wrong
    private func transcribeRecording(_ caf: URL, name: String) async {
        await run {
            let date = String(caf.lastPathComponent.prefix(10))
            // a meeting already named this on the same day would make the upload a rerun of it
            var unique = name, n = 2
            while meetings.contains(where: { $0.meetingId == "\(date) - \(unique)" }) {
                unique = "\(name) \(n)"
                n += 1
            }
            let m4a = try await Task.detached { [unique] in try Recorder.compress(caf, name: unique) }.value
            do {
                let result = try await API.upload([m4a], sourceType: "zoom")
                guard !result.queued.isEmpty else {
                    throw RecorderError(result.skipped.map(\.reason).first ?? "The server didn't take the recording")
                }
            } catch {
                throw RecorderError("Couldn't send the recording to be transcribed: \(error.localizedDescription). It's kept in \(Recorder.folder.path)")
            }
            try? FileManager.default.removeItem(at: caf)
            try? FileManager.default.removeItem(at: m4a)
            show("Transcribing \(unique)")
            section = .inbox
            await refresh()
        }
    }

    // a recording that was going when the app quit or crashed is finished and transcribed
    // the next time it opens, named after when it started
    private func recoverRecordings() async {
        let files = (try? FileManager.default.contentsOfDirectory(at: Recorder.folder, includingPropertiesForKeys: [.creationDateKey])) ?? []
        for caf in files where caf.pathExtension == "caf" {
            let started = (try? caf.resourceValues(forKeys: [.creationDateKey]).creationDate) ?? Date()
            let time = started.formatted(date: .omitted, time: .shortened).replacingOccurrences(of: ":", with: ".")
            await transcribeRecording(caf, name: "Call at \(time)")
        }
    }

    func importFiles(_ urls: [URL]) async {
        let audio = urls.filter { ["m4a", "mp3", "wav"].contains($0.pathExtension.lowercased()) }
        guard !audio.isEmpty else { return show("Only m4a, mp3 and wav files can be transcribed") }
        await run {
            let result = try await API.upload(audio)
            let skipped = result.skipped.map { "\($0.file): \($0.reason)" }
            show(result.queued.isEmpty ? skipped.joined(separator: "\n")
                 : "Transcribing \(result.queued.count) recording\(result.queued.count == 1 ? "" : "s")")
            section = .inbox
            await refresh()
        }
    }

    func rename(_ id: String, to name: String) async {
        await run {
            let _: [String: String] = try await API.post(API.meeting(id, "title"), ["name": name])
            await refresh()
            revision += 1
        }
    }

    func reveal(_ id: String) async {
        await run { let _: Ok = try await API.post(API.meeting(id, "reveal")) }
    }

    func openInObsidian(_ id: String) async {
        await run { let _: Ok = try await API.post(API.meeting(id, "open-obsidian")) }
    }

    func moveToInbox(_ id: String) async {
        await run {
            let _: Ok = try await API.post(API.meeting(id, "unarchive"))
            await refresh()
            section = .inbox
            selection = id
        }
    }

    func dismiss(_ job: Job) async {
        await run {
            let _: Ok = try await API.post(["api", "jobs", job.jobId, "dismiss"])
            await refresh()
        }
    }

    func loadPeople() async {
        if let found: [Person] = try? await API.get(["api", "people"]) { people = found }
    }

    func person(_ id: String?) -> Person? { people.first { $0.personId == id } }

    func openMeeting(_ id: String, at seconds: Double? = nil) {
        guard let m = meeting(id) else { return show("That meeting is not in the library") }
        section = m.archived == nil ? .inbox : .archive
        selection = id
        focusTime = seconds
    }

    // heron://meeting?id=ID&at=SECONDS, from answers and from to-dos in things. boswell://
    // links, from notes and to-dos made before the app was renamed, work the same. returns
    // false for a link that is not one of these
    @discardableResult
    func open(link url: URL) -> Bool {
        guard ["heron", "boswell"].contains(url.scheme ?? ""), url.host == "meeting",
              let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
              let id = parts.queryItems?.first(where: { $0.name == "id" })?.value else { return false }
        // a link that launched the app waits for the library to load
        guard connected, !meetings.isEmpty else {
            pendingLink = url
            return true
        }
        openMeeting(id, at: parts.queryItems?.first { $0.name == "at" }?.value.flatMap(Double.init))
        // a note's transcript link names the tab to open on
        if let tab = parts.queryItems?.first(where: { $0.name == "tab" })?.value, let shown = Tab(rawValue: tab.capitalized) {
            showTab = shown
        }
        return true
    }
}

// plays short wav clips the server cuts from the recording
@MainActor @Observable
final class Player: NSObject, AVAudioPlayerDelegate {
    static let shared = Player()

    var playing: String?
    private var player: AVAudioPlayer?

    func toggle(_ key: String, meeting: String, speaker: String, start: Double, end: Double) async {
        if playing == key { return stop() }
        stop()
        playing = key
        let query = [URLQueryItem(name: "speaker", value: speaker),
                     URLQueryItem(name: "start", value: String(start)),
                     URLQueryItem(name: "end", value: String(end))]
        guard let data = try? await API.data(API.meeting(meeting, "clip"), query: query),
              playing == key, let p = try? AVAudioPlayer(data: data) else {
            if playing == key { playing = nil }
            return
        }
        p.delegate = self
        player = p
        p.play()
    }

    func stop() {
        player?.stop()
        player = nil
        playing = nil
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in self.stop() }
    }
}
