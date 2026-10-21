import Foundation

// the python server holds all of the app's logic. this starts it from the project folder,
// or uses one already running, such as one started by hand with uv run app.py.
final class Server {
    static let shared = Server()

    let baseURL: URL
    let projectDir: URL
    private var process: Process?

    private init() {
        let env = ProcessInfo.processInfo.environment
        baseURL = URL(string: env["BOSWELL_URL"] ?? "http://127.0.0.1:8765")!
        if let dir = Bundle.main.object(forInfoDictionaryKey: "BoswellProjectDir") as? String {
            projectDir = URL(fileURLWithPath: dir)
        } else if let dir = env["BOSWELL_PROJECT_DIR"] {
            projectDir = URL(fileURLWithPath: dir)
        } else {
            // swift run, from mac/Sources/Boswell/Server.swift up to the project folder
            var dir = URL(fileURLWithPath: #filePath)
            for _ in 0..<4 { dir.deleteLastPathComponent() }
            projectDir = dir
        }
    }

    var logFile: URL {
        FileManager.default.homeDirectoryForCurrentUser.appending(path: "Library/Logs/Boswell.log")
    }

    func isUp() async -> Bool {
        var request = URLRequest(url: baseURL.appending(path: "api/state"))
        request.timeoutInterval = 1
        guard let (_, response) = try? await URLSession.shared.data(for: request) else { return false }
        return (response as? HTTPURLResponse)?.statusCode == 200
    }

    func start() async throws {
        if await isUp() { return }
        let python = projectDir.appending(path: ".venv/bin/python")
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            throw ServerError("No Python environment at \(python.path). Run uv sync in the project folder.")
        }
        if !FileManager.default.fileExists(atPath: logFile.path) {
            FileManager.default.createFile(atPath: logFile.path, contents: nil)
        }
        let log = try FileHandle(forWritingTo: logFile)
        log.seekToEndOfFile()

        let p = Process()
        p.executableURL = python
        p.arguments = ["app.py"]
        p.currentDirectoryURL = projectDir
        // apps opened from finder get a bare PATH, so add homebrew for ffmpeg and ffprobe
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + (env["PATH"] ?? "/usr/bin:/bin")
        // the server stops itself when this process goes away
        env["BOSWELL_APP_PID"] = String(ProcessInfo.processInfo.processIdentifier)
        p.environment = env
        p.standardOutput = log
        p.standardError = log
        try p.run()
        process = p

        // loading the models' libraries takes a few seconds
        for _ in 0..<300 {
            if await isUp() { return }
            if !p.isRunning { break }
            try await Task.sleep(for: .milliseconds(200))
        }
        throw ServerError("The server did not start. See \(logFile.path).")
    }

    // only stops a server this app started
    func stop() {
        guard let p = process, p.isRunning else { return }
        p.terminate()
        p.waitUntilExit()
    }
}

struct ServerError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}
