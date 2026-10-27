import Foundation

// whether a newer release is on github, checked at most once a day. it only asks github's
// api for the latest release's tag, and sends nothing about the mac or the library
@MainActor @Observable
final class Updates {
    struct Release: Decodable {
        let tagName: String
        let htmlUrl: URL
        var version: String { tagName.hasPrefix("v") ? String(tagName.dropFirst()) : tagName }

        enum CodingKeys: String, CodingKey {
            case tagName = "tag_name"
            case htmlUrl = "html_url"
        }
    }

    static let latest = URL(string: "https://api.github.com/repos/JonathanGarro/wemmick/releases/latest")!
    private static let lastCheck = "lastUpdateCheck"

    // a newer release than this one, when there is one
    var available: Release?
    var checking = false
    var lastError: String?

    // the version in the app's Info.plist. swift run has none, so it never checks
    let current = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String

    // on launch, unless switched off in settings or checked in the last day
    func checkIfDue() async {
        let defaults = UserDefaults.standard
        guard defaults.object(forKey: Prefs.checkUpdates) as? Bool ?? true else { return }
        if let last = defaults.object(forKey: Self.lastCheck) as? Date, last.timeIntervalSinceNow > -86_400 { return }
        await check()
    }

    func check() async {
        guard let current, !checking else { return }
        checking = true
        defer { checking = false }
        var request = URLRequest(url: Self.latest)
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            // 404 is no release published yet
            if (response as? HTTPURLResponse)?.statusCode == 404 {
                available = nil
            } else {
                let release = try JSONDecoder().decode(Release.self, from: data)
                available = Self.isNewer(release.version, than: current) ? release : nil
            }
            lastError = nil
            UserDefaults.standard.set(Date(), forKey: Self.lastCheck)
        } catch {
            lastError = "Couldn't reach GitHub"
        }
    }

    // compares 0.10.0 and 0.9.1 by number, a missing part counting as 0
    static func isNewer(_ version: String, than current: String) -> Bool {
        let a = version.split(separator: ".").map { Int($0) ?? 0 }
        let b = current.split(separator: ".").map { Int($0) ?? 0 }
        for i in 0..<max(a.count, b.count) {
            let x = i < a.count ? a[i] : 0, y = i < b.count ? b[i] : 0
            if x != y { return x > y }
        }
        return false
    }
}
