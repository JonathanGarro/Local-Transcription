import Foundation

// models, matching the json the python server sends

struct Meeting: Codable, Identifiable, Hashable {
    let meetingId: String
    let title: String?
    let date: String?
    let sourceFile: String?
    let sourceType: String?
    let minutes: Double?
    let status: String?
    let archived: String?
    let sentPath: String?
    let project: String?
    let todosSent: Double?
    let todosChecked: String?
    let note: Bool?

    var id: String { meetingId }
    var name: String { title ?? meetingId }
    var needsReview: Bool { status != "done" }
}

struct Job: Codable, Identifiable, Hashable {
    let jobId: String
    let kind: String?
    let file: String?
    let status: String
    let step: String?
    let progress: Double?
    let error: String?
    let meetingId: String?
    let plaudId: String?

    var id: String { jobId }
    var running: Bool { status == "queued" || status == "processing" }
}

struct AppState: Codable {
    let jobs: [Job]
    let meetings: [Meeting]
    let owner: String?
}

struct Clip: Codable, Hashable {
    let start: Double
    let end: Double
    let text: String?
}

struct Suggestion: Codable, Hashable {
    let name: String
    let score: Double
    let source: String?
}

struct Speaker: Codable, Identifiable, Hashable {
    let speaker: String
    let name: String?
    let status: String
    let score: Double?
    let bestName: String?
    let bestScore: Double?
    let minutes: Double?
    let label: String
    let resolved: Bool
    let clips: [Clip]
    let suggestions: [Suggestion]

    var id: String { speaker }
    var isMic: Bool { speaker == "MIC" }
}

struct MeetingDetail: Codable {
    let meetingId: String
    let title: String?
    let date: String?
    let status: String?
    let minutes: Double?
    let split: Bool?
    let archived: String?
    let sentPath: String?
    let note: Bool?
    let owner: String?
    let speakers: [Speaker]
}

struct Turn: Codable, Identifiable, Hashable {
    let speaker: String
    let name: String?
    let start: Double
    let end: Double
    let text: String

    var id: Double { start }
}

struct Transcript: Codable {
    let turns: [Turn]
    let markdown: String
    let path: String
}

struct Note: Codable {
    let exists: Bool
    let markdown: String
    let path: String
    let project: String?
    let backend: String?
    let model: String?
    let created: String?
    // the note template it was written in
    let template: String?
    let edited: String?
    let sentPath: String?
    let archived: String?
    let notesDir: String?
    let vault: String?
    let job: Job?
    let todosSent: [SentTodo]?
    let todosChecked: String?
}

struct SentTodo: Codable, Hashable {
    // its id in the app it went to
    let thingsId: String
    let title: String
    let added: String?
    let source: String?
    let app: String?
    // opens it in that app
    let link: String?
}

struct Project: Codable, Hashable {
    let name: String
    let status: String?
}

struct Lookups: Codable {
    let projects: [Project]
    let organizations: [String]?
    let teams: [String]?
    let defaultBackend: String?
    let claudeReady: Bool?
    // the to-do app chosen in settings, nil when the to-do features are off
    let todoApp: String?
    // whether claude can find to-dos
    let todosReady: Bool?
    let templates: [String]?
    // the note template chosen in settings
    let noteTemplate: String?
}

struct Person: Codable, Hashable, Identifiable {
    let personId: String
    let name: String
    let organizations: String?
    let teams: String?
    let entries: Double?
    let profileSeconds: Double?
    let meetings: Double?
    let lastHeard: String?

    var id: String { personId }
    // the server joins lists with ", ", and sends "" for none
    var organizationList: [String] { Person.split(organizations) }
    var teamList: [String] { Person.split(teams) }
    var subtitle: String { (organizationList + teamList).joined(separator: " · ") }

    private static func split(_ joined: String?) -> [String] {
        (joined ?? "").components(separatedBy: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
    }
}

struct PersonMeeting: Codable, Hashable, Identifiable {
    let meetingId: String
    let title: String?
    let date: String?
    let sourceType: String?
    let meetingStatus: String?
    let project: String?
    let status: String?
    let minutes: Double?

    var id: String { meetingId }
    // tables sort by these, optionals are not comparable
    var sortDate: String { date ?? "" }
    var sortTitle: String { title ?? meetingId }
}

struct DetailsRequest: Encodable {
    let organizations: [String]
    let teams: [String]
}

struct ImportedMeeting: Codable {
    let title: String?
    let date: String?
    let project: String?
    let people: [String]
    let markdown: String?
    let processed: Bool
}

// a diff part is sent as [text, changed]
struct DiffPart: Decodable, Hashable {
    let text: String
    let changed: Bool

    init(from decoder: Decoder) throws {
        var container = try decoder.unkeyedContainer()
        text = try container.decode(String.self)
        changed = try container.decode(Bool.self)
    }
}

struct DiffRow: Decodable, Hashable {
    let kind: String
    let old: Int?
    let new: Int?
    let parts: [DiffPart]?
}

struct NoteDiff: Decodable {
    let rows: [DiffRow]
    let base: String?
    let added: Int
    let removed: Int
}

struct SendResult: Codable {
    let path: String
    let meetingId: String
}

struct UploadResult: Codable {
    struct Skipped: Codable { let file: String; let reason: String }
    let queued: [String]
    let skipped: [Skipped]
}

struct Ok: Codable {}

struct VocabEntry: Codable, Hashable {
    var term: String
    var variants: [String]
}

struct Vocabulary: Codable {
    let entries: [VocabEntry]
    let used: Int
    let max: Int
}

struct ClaudeModel: Codable, Hashable {
    let id: String
    let label: String
    let input: Double
    let output: Double
}

struct Usage: Codable {
    let total: Double?
    let requests: Double?
    let since: String?
    let today: Double?
}

struct SettingsState: Codable {
    let accent: String
    let claudeModel: String
    let accents: [String]
    let models: [ClaudeModel]
    let key: String?
    // "keychain", "env", or nil when there is no key
    let keySource: String?
    let usage: Usage
    // whether the plaud cli is on the path, without which the plaud page is hidden
    let plaudInstalled: Bool
    // "things", "todoist", "reminders", or "none"
    let todoApp: String
    let thingsInstalled: Bool
    // only ever masked
    let todoistToken: String?
    let todoistSource: String?
    // obsidian folders, with "settings" or "env" for where each was set
    let meetingNotesDir: String?
    let meetingNotesDirSource: String?
    let peopleDir: String?
    let peopleDirSource: String?
    let noteTemplate: String
    // your name, and whether it was set here ("settings") or in .env
    let owner: String?
    let ownerSource: String?
    // "left" or "right"
    let micChannel: String
    // "local" or "claude"
    let summaryBackend: String
    // no name anywhere and setup not finished, so it's shown when the app opens
    let setupNeeded: Bool
    let people: [String]
    // only ever masked
    let hfToken: String?
    let hfSource: String?
    // whether the speaker detection model is downloaded, so no token is needed
    let modelReady: Bool
    let hfTermsUrl: String
}

struct NoteTemplate: Codable, Hashable {
    let name: String
    let builtIn: Bool
    let text: String
}

struct Templates: Codable {
    let templates: [NoteTemplate]
    let chosen: String
}

struct KeyCheck: Codable {
    let ok: Bool
    let error: String?
}

struct SettingsChange: Encodable {
    var accent: String?
    var claudeModel: String?
    var todoApp: String?
    // "" goes back to the folder in .env
    var meetingNotesDir: String?
    var peopleDir: String?
    var noteTemplate: String?
    // "" goes back to MIC_OWNER in .env
    var owner: String?
    var micChannel: String?
    var summaryBackend: String?
    var setupDone: Bool?
}

struct Count: Codable, Hashable {
    let label: String
    let value: Double
    let minutes: Double?
}

struct HomeStats: Codable {
    let meetings: Double
    let people: Double
    let hours: Double
    let voices: Double
    let review: Double?
    let ownerShare: Double?
    let byMonth: [Count]
    let byWeekday: [Count]
    let peopleTop: [Count]
    let projectsTop: [Count]
    let teamsTop: [Count]
}

struct Conversation: Codable, Identifiable, Hashable {
    let conversationId: String
    let title: String?
    let updated: String?
    let questions: Double?

    var id: String { conversationId }
}

struct ConversationPage: Codable {
    let items: [Conversation]
    let page: Int
    let pages: Int
    let total: Int
}

struct SavedTurn: Codable {
    let question: String
    let answer: String?
    let links: [String: String]?
}

struct SavedConversation: Codable {
    let title: String?
    let turns: [SavedTurn]
}

struct AskStarted: Codable {
    let askId: String
    let conversationId: String
}

struct AskStatus: Codable {
    let status: String
    let steps: [String]?
    let answer: String?
    let links: [String: String]?
    let title: String?
    let error: String?
}

// a project in the to-do app, or a list in reminders
struct ThingsProject: Codable, Hashable {
    let id: String
    let name: String
    let area: String?
}

// a to-do claude found, checked and adjusted before it goes to the to-do app
struct Todo: Codable, Hashable, Identifiable {
    var id = UUID()
    var keep = true
    var title: String
    var notes: String
    var meetingId: String
    var seconds: Int
    var projectId: String
    var projectSource: String?
    var when: String
    var deadline: String

    enum CodingKeys: String, CodingKey {
        case title, notes, meetingId, seconds, projectId, projectSource, when, deadline
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
        notes = try c.decodeIfPresent(String.self, forKey: .notes) ?? ""
        meetingId = try c.decodeIfPresent(String.self, forKey: .meetingId) ?? ""
        seconds = try c.decodeIfPresent(Int.self, forKey: .seconds) ?? -1
        projectId = try c.decodeIfPresent(String.self, forKey: .projectId) ?? ""
        projectSource = try c.decodeIfPresent(String.self, forKey: .projectSource)
        // a to-do with no date starts as today
        let found = try c.decodeIfPresent(String.self, forKey: .when) ?? ""
        when = found.isEmpty ? "today" : found
        deadline = try c.decodeIfPresent(String.self, forKey: .deadline) ?? ""
    }
}

struct MeetingRef: Codable, Hashable {
    let title: String?
    let date: String?
}

struct FoundTodos: Codable {
    var todos: [Todo]
    let projects: [ThingsProject]
    let areas: [String]
    let meetings: [String: MeetingRef]
    let appName: String?
    // where a to-do with no project goes, such as the inbox
    let inbox: String?
}

struct AddTodosRequest: Encodable {
    let todos: [Todo]
    // a meeting whose note's action items become the to-dos added
    var noteMeetingId: String?
}

struct Added: Codable {
    let added: Int
    let vault: String?
}

struct PlaudRecording: Codable, Identifiable, Hashable {
    let plaudId: String
    let name: String
    let date: String?
    let duration: String?
    let meetingId: String?
    // new, queued, processing, done or imported
    let status: String
    let hidden: Bool?

    var id: String { plaudId }
}

struct PlaudRecent: Codable {
    let days: Int
    let recordings: [PlaudRecording]
}

struct PlaudAccount: Codable {
    let installed: Bool
    let signedIn: Bool
    let email: String?
    let name: String?
    // the browser is open on plaud's sign in page
    let signingIn: Bool
    let error: String?
}

// requests

struct ApplyRequest: Encodable {
    var names: [String: String] = [:]
    var noEnroll: [String] = []
    var skip: [String] = []
    var merges: [[String]] = []
}

// client

struct APIError: LocalizedError {
    let status: Int
    let message: String
    var errorDescription: String? { message }
}

enum API {
    private static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    private static let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        return e
    }()

    // each part is one path component, so meeting names with slashes or spaces stay whole
    static func url(_ parts: [String], query: [URLQueryItem] = []) -> URL {
        var url = Server.shared.baseURL
        for part in parts { url.append(component: part) }
        return query.isEmpty ? url : url.appending(queryItems: query)
    }

    static func meeting(_ id: String, _ rest: String...) -> [String] {
        ["api", "meetings", id] + rest
    }

    static func get<T: Decodable>(_ parts: [String], query: [URLQueryItem] = []) async throws -> T {
        try await send(URLRequest(url: url(parts, query: query)))
    }

    static func post<T: Decodable>(_ parts: [String], _ body: some Encodable = [String: String]()) async throws -> T {
        var request = URLRequest(url: url(parts))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try encoder.encode(body)
        return try await send(request)
    }

    static func put<T: Decodable>(_ parts: [String], _ body: some Encodable) async throws -> T {
        var request = URLRequest(url: url(parts))
        request.httpMethod = "PUT"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try encoder.encode(body)
        return try await send(request)
    }

    static func delete<T: Decodable>(_ parts: [String]) async throws -> T {
        var request = URLRequest(url: url(parts))
        request.httpMethod = "DELETE"
        return try await send(request)
    }

    static func data(_ parts: [String], query: [URLQueryItem] = []) async throws -> Data {
        let (data, response) = try await URLSession.shared.data(from: url(parts, query: query))
        try check(data, response)
        return data
    }

    static func upload(_ files: [URL]) async throws -> UploadResult {
        let boundary = "boswell-\(UUID().uuidString)"
        var body = Data()
        func field(_ name: String, _ value: String) {
            body.append("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(name)\"\r\n\r\n\(value)\r\n")
        }
        field("source_type", "auto")
        for file in files {
            let scoped = file.startAccessingSecurityScopedResource()
            defer { if scoped { file.stopAccessingSecurityScopedResource() } }
            body.append("--\(boundary)\r\nContent-Disposition: form-data; name=\"files\"; filename=\"\(file.lastPathComponent)\"\r\n")
            body.append("Content-Type: application/octet-stream\r\n\r\n")
            body.append(try Data(contentsOf: file))
            body.append("\r\n")
        }
        body.append("--\(boundary)--\r\n")
        var request = URLRequest(url: url(["api", "upload"]))
        request.httpMethod = "POST"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.httpBody = body
        return try await send(request)
    }

    private static func send<T: Decodable>(_ request: URLRequest) async throws -> T {
        let (data, response) = try await URLSession.shared.data(for: request)
        try check(data, response)
        return try decoder.decode(T.self, from: data)
    }

    private static func check(_ data: Data, _ response: URLResponse) throws {
        guard let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) else { return }
        // fastapi puts the reason in detail
        let detail = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String
        throw APIError(status: http.statusCode, message: detail ?? "The server answered \(http.statusCode)")
    }
}

private extension Data {
    mutating func append(_ string: String) { append(Data(string.utf8)) }
}
