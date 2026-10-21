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
    let edited: String?
    let sentPath: String?
    let archived: String?
    let notesDir: String?
    let vault: String?
    let job: Job?
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
