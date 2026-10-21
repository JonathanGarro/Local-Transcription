import AppKit
import AVFoundation
import Observation
import SwiftUI

enum Place: String, Hashable, CaseIterable {
    case inbox = "Inbox"
    case archive = "Archive"
    case home = "Home"
    case ask = "Ask"
    case people = "People"
    case vocabulary = "Vocabulary"
    case settings = "Settings"

    static let meetings: [Place] = [.inbox, .archive]
    // pages with no list column
    var fullWidth: Bool { [.home, .vocabulary, .settings].contains(self) }
    static let library: [Place] = [.home, .ask, .people, .vocabulary, .settings]

    var icon: String {
        switch self {
        case .inbox: "tray"
        case .archive: "archivebox"
        case .home: "house"
        case .ask: "bubble.left.and.text.bubble.right"
        case .people: "person.2"
        case .vocabulary: "character.book.closed"
        case .settings: "gearshape"
        }
    }

    // the same page in the web version
    var route: String { "#/\(rawValue.lowercased())" }
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
    var section: Place = .inbox
    var selection: String?
    var personSelection: String?
    var people: [Person] = []
    let chat = Chat()
    // a moment to show in the transcript of the meeting being opened
    var focusTime: Double?
    // the accent chosen in settings, shared with the web version
    var accent = "blue"
    var meetings: [Meeting] = []
    var jobs: [Job] = []
    var owner: String?
    var connected = false
    var startError: String?
    var message: String?
    var importing = false
    // bumped when something outside a view changes its meeting, so the view reloads
    var revision = 0

    private var polling: Task<Void, Never>?

    var inbox: [Meeting] { meetings.filter { $0.archived == nil } }

    var archive: [Meeting] {
        meetings.filter { $0.archived != nil }
            .sorted { ($0.date ?? "", $0.archived ?? "") > ($1.date ?? "", $1.archived ?? "") }
    }

    var reviewCount: Int { inbox.filter(\.needsReview).count }

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
        do {
            try await Server.shared.start()
            connected = true
            startError = nil
        } catch {
            startError = error.localizedDescription
            return
        }
        await refresh()
        if let settings: SettingsState = try? await API.get(["api", "settings"]) { accent = settings.accent }
        polling = Task {
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(2))
                await refresh()
            }
        }
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

    func openWebVersion(_ path: String = "") {
        // appending(path:) would escape the # of the page's routes
        if let url = URL(string: Server.shared.baseURL.absoluteString + "/" + path) { NSWorkspace.shared.open(url) }
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
