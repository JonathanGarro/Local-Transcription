import AppKit
import CryptoKit

// the python server holds all of the app's logic. the app starts its own copy of the code,
// in Contents/Resources/server, with the python environment make_app.py installs, or uses a
// server already running, such as one started by hand with uv run app.py.
final class Server {
    static let shared = Server()

    let baseURL: URL
    // the server's code: the copy in the app, or the project folder under swift run
    let serverDir: URL
    // the python that runs it: the app's environment, or the project folder's .venv under swift run
    let python: URL
    private var process: Process?

    static let appSupport = FileManager.default.homeDirectoryForCurrentUser.appending(path: "Library/Application Support/Heron")
    // the app was called boswell, with its library and log under that name
    private static let boswellSupport = FileManager.default.homeDirectoryForCurrentUser.appending(path: "Library/Application Support/Boswell")
    private static let boswellBundleID = "io.github.jonathangarro.boswell"

    private init() {
        let env = ProcessInfo.processInfo.environment
        baseURL = URL(string: env["HERON_URL"] ?? "http://127.0.0.1:8765")!
        if let bundled = Bundle.main.resourceURL?.appending(path: "server"),
           FileManager.default.fileExists(atPath: bundled.appending(path: "app.py").path) {
            serverDir = bundled
            python = Self.appSupport.appending(path: "env/bin/python")
        } else {
            if let dir = env["HERON_PROJECT_DIR"] {
                serverDir = URL(fileURLWithPath: dir)
            } else {
                // swift run, from mac/Sources/Heron/Server.swift up to the project folder
                var dir = URL(fileURLWithPath: #filePath)
                for _ in 0..<4 { dir.deleteLastPathComponent() }
                serverDir = dir
            }
            python = serverDir.appending(path: ".venv/bin/python")
        }
    }

    var logFile: URL {
        FileManager.default.homeDirectoryForCurrentUser.appending(path: "Library/Logs/Heron.log")
    }

    func isUp() async -> Bool {
        var request = URLRequest(url: baseURL.appending(path: "api/state"))
        request.timeoutInterval = 1
        guard let (_, response) = try? await URLSession.shared.data(for: request) else { return false }
        return (response as? HTTPURLResponse)?.statusCode == 200
    }

    // status is told what's happening while the python packages are installed, the first time
    // or after an update changes them
    func start(status: @escaping @Sendable (String) -> Void = { _ in }) async throws {
        try await adoptBoswellLibrary()
        if await isUp() { return }
        try await installPython(status: status)
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            throw ServerError("Heron's Python environment isn't installed at \(python.path).")
        }
        // a long log starts over, keeping the one before
        if let size = try? FileManager.default.attributesOfItem(atPath: logFile.path)[.size] as? Int, size > 5_000_000 {
            let old = logFile.deletingLastPathComponent().appending(path: "Heron.old.log")
            try? FileManager.default.removeItem(at: old)
            try? FileManager.default.moveItem(at: logFile, to: old)
        }
        if !FileManager.default.fileExists(atPath: logFile.path) {
            FileManager.default.createFile(atPath: logFile.path, contents: nil)
        }
        let log = try FileHandle(forWritingTo: logFile)
        log.seekToEndOfFile()

        let p = Process()
        p.executableURL = python
        p.arguments = ["app.py"]
        p.currentDirectoryURL = serverDir
        // the app's own ffmpeg and ffprobe come first. apps opened from finder get a bare PATH, so
        // homebrew's are added after, for swift run, and for the plaud cli
        var env = ProcessInfo.processInfo.environment
        let helpers = Bundle.main.executableURL?.deletingLastPathComponent().path ?? ""
        env["PATH"] = "\(helpers):/opt/homebrew/bin:/usr/local/bin:" + (env["PATH"] ?? "/usr/bin:/bin")
        // each line reaches the log as it's printed, rather than when a buffer fills
        env["PYTHONUNBUFFERED"] = "1"
        // compiled python goes to the caches folder, since writing it inside the app would break its signature
        env["PYTHONPYCACHEPREFIX"] = FileManager.default.homeDirectoryForCurrentUser.appending(path: "Library/Caches/Heron/pycache").path
        // the server stops itself when this process goes away
        env["HERON_APP_PID"] = String(ProcessInfo.processInfo.processIdentifier)
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

    // boswell's library folder becomes heron's the first time heron opens. boswell runs from
    // that folder and its server answers on the same port, so it's asked to quit first, and
    // nothing moves while it's still open. it's a rename, so nothing is copied
    private func adoptBoswellLibrary() async throws {
        // only the installed app takes it over, never a copy run from the project folder
        let files = FileManager.default
        guard Bundle.main.resourceURL.map({ files.fileExists(atPath: $0.appending(path: "server/app.py").path) }) == true,
              files.fileExists(atPath: Self.boswellSupport.path), !files.fileExists(atPath: Self.appSupport.path) else { return }
        let running = { NSRunningApplication.runningApplications(withBundleIdentifier: Self.boswellBundleID) }
        running().forEach { $0.terminate() }
        // quitting waits on boswell's own question when work is running, then its server stops
        for _ in 0..<150 {
            if running().isEmpty, !(await isUp()) { break }
            try await Task.sleep(for: .milliseconds(200))
        }
        guard running().isEmpty, !(await isUp()) else {
            throw ServerError("Quit Boswell, then open Heron again. Heron takes over Boswell's meetings and settings once it's closed.")
        }
        try files.moveItem(at: Self.boswellSupport, to: Self.appSupport)
        let oldLog = files.homeDirectoryForCurrentUser.appending(path: "Library/Logs/Boswell.log")
        if !files.fileExists(atPath: logFile.path) { try? files.moveItem(at: oldLog, to: logFile) }
    }

    // python and the server's packages, installed by the uv inside the app from the app's
    // uv.lock into application support. a marker keeps the lock it was installed from, so it's
    // only done the first time and when an update changes the packages. under swift run the
    // project folder's .venv is used as it is
    private func installPython(status: @escaping @Sendable (String) -> Void) async throws {
        guard let uv = Bundle.main.executableURL?.deletingLastPathComponent().appending(path: "uv"),
              FileManager.default.isExecutableFile(atPath: uv.path),
              let lock = try? Data(contentsOf: serverDir.appending(path: "uv.lock")) else { return }
        let envDir = Self.appSupport.appending(path: "env")
        let marker = envDir.appending(path: ".heron-lock")
        let wanted = SHA256.hash(data: lock).map { String(format: "%02x", $0) }.joined()
        if FileManager.default.isExecutableFile(atPath: python.path),
           (try? String(contentsOf: marker, encoding: .utf8)) == wanted {
            return
        }
        status("Installing Heron's Python packages")
        try FileManager.default.createDirectory(at: Self.appSupport, withIntermediateDirectories: true)

        let p = Process()
        p.executableURL = uv
        p.arguments = ["sync", "--frozen", "--no-install-project", "--project", serverDir.path]
        // uv's own files stay out of the signed app, and it runs from application support
        p.currentDirectoryURL = Self.appSupport
        var env = ProcessInfo.processInfo.environment
        env["UV_PROJECT_ENVIRONMENT"] = envDir.path
        // its own python, never homebrew's or the system's
        env["UV_PYTHON_PREFERENCE"] = "only-managed"
        env["NO_COLOR"] = "1"
        p.environment = env
        let output = Pipe()
        p.standardOutput = output
        p.standardError = output
        let lines = LineBuffer()
        output.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            for line in text.split(whereSeparator: \.isNewline).map({ $0.trimmingCharacters(in: .whitespaces) }) where !line.isEmpty {
                lines.append(line)
                status(line)
            }
        }
        try await withCheckedThrowingContinuation { (done: CheckedContinuation<Void, Error>) in
            p.terminationHandler = { _ in done.resume() }
            do { try p.run() } catch { done.resume(throwing: error) }
        }
        output.fileHandleForReading.readabilityHandler = nil
        guard p.terminationStatus == 0 else {
            throw ServerError("Couldn't install Heron's Python packages. Check the internet connection and try again.\n\n\(lines.last(4))")
        }
        try wanted.write(to: marker, atomically: true, encoding: .utf8)
    }

    // only stops a server this app started
    func stop() {
        guard let p = process, p.isRunning else { return }
        p.terminate()
        p.waitUntilExit()
    }
}

// the last lines a process wrote, from the thread its output arrives on
final class LineBuffer: @unchecked Sendable {
    private var lines: [String] = []
    private let lock = NSLock()

    func append(_ line: String) {
        lock.lock()
        lines = Array((lines + [line]).suffix(20))
        lock.unlock()
    }

    func last(_ n: Int) -> String {
        lock.lock()
        defer { lock.unlock() }
        return lines.suffix(n).joined(separator: "\n")
    }
}

struct ServerError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}
