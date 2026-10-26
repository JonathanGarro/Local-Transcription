import AppKit
import CoreAudio
import UserNotifications

// notices a call starting, when an app like zoom or a browser starts using the microphone, and
// offers to record it in a notification. when that app stops using the mic while wemmick is
// recording, it offers to stop. core audio lists each process using audio and whether it's
// taking input, which is checked every couple of seconds, without recording anything
@MainActor
final class CallDetector: NSObject, UNUserNotificationCenterDelegate {
    private let store: Store
    private var timer: Timer?
    // the call apps using the mic at the last check, by bundle id
    private var inCall: Set<String> = []
    // the app whose call is being recorded, to offer stopping when it hangs up
    private var recordingFor: String?

    private static let recordAction = "record"
    private static let stopAction = "stop"
    private static let callCategory = "call"
    private static let endedCategory = "ended"

    // apps that make calls, and browsers, for meet and the others that run in one. a bundle
    // id matches when it starts with one of these, which takes in their helper processes
    private static let callApps: [(prefix: String, name: String)] = [
        ("us.zoom", "Zoom"), ("com.microsoft.teams", "Teams"), ("com.cisco.webex", "Webex"),
        ("Cisco-Systems.Spark", "Webex"), ("com.tinyspeck.slackmacgap", "Slack"), ("com.apple.FaceTime", "FaceTime"),
        // facetime's audio runs in this system service rather than in the app
        ("com.apple.avconferenced", "FaceTime"),
        ("com.hnc.Discord", "Discord"), ("net.whatsapp.WhatsApp", "WhatsApp"), ("com.google.Chrome", "Chrome"),
        ("com.apple.Safari", "Safari"), ("com.apple.WebKit", "Safari"), ("company.thebrowser", "Arc"),
        ("org.mozilla.firefox", "Firefox"), ("com.microsoft.edgemac", "Edge"), ("com.brave.Browser", "Brave"),
    ]

    init(store: Store) {
        self.store = store
        super.init()
    }

    // notifications need the app's bundle, so this does nothing under swift run
    func start() {
        guard Bundle.main.bundleIdentifier != nil, timer == nil else { return }
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        center.setNotificationCategories([
            UNNotificationCategory(identifier: Self.callCategory,
                                   actions: [UNNotificationAction(identifier: Self.recordAction, title: "Record")],
                                   intentIdentifiers: []),
            UNNotificationCategory(identifier: Self.endedCategory,
                                   actions: [UNNotificationAction(identifier: Self.stopAction, title: "Stop Recording")],
                                   intentIdentifiers: []),
        ])
        center.requestAuthorization(options: [.alert, .sound]) { _, _ in }
        inCall = Self.callAppsUsingMic()
        timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.check() }
        }
    }

    func stop() {
        timer?.invalidate()
        timer = nil
        inCall = []
    }

    private func check() {
        let now = Self.callAppsUsingMic()
        // switched off in settings, it only keeps up with what's using the mic
        guard UserDefaults.standard.object(forKey: Prefs.offerToRecord) as? Bool ?? true else {
            inCall = now
            return
        }
        // a recording started by hand during a call offers to stop when that call ends too
        if store.recorder.isRecording, recordingFor == nil { recordingFor = now.first }
        let started = now.subtracting(inCall)
        let ended = inCall.subtracting(now)
        inCall = now
        if let app = started.first, !store.recorder.isRecording {
            recordingFor = app
            notify(category: Self.callCategory, title: "\(Self.name(of: app)) is using the microphone",
                   body: "Record this call to transcribe it with Wemmick?")
        }
        if let app = recordingFor, ended.contains(app) {
            if store.recorder.isRecording {
                notify(category: Self.endedCategory, title: "\(Self.name(of: app)) stopped using the microphone",
                       body: "Stop recording and transcribe the call?")
            }
            recordingFor = nil
        }
    }

    private func notify(category: String, title: String, body: String) {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.categoryIdentifier = category
        // a newer offer replaces the last one rather than stacking up
        let request = UNNotificationRequest(identifier: "wemmick.call", content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request)
    }

    // shown even while wemmick is in front
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification) async
        -> UNNotificationPresentationOptions {
        [.banner, .sound]
    }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse) async {
        let action = response.actionIdentifier
        await MainActor.run {
            switch action {
            case Self.recordAction:
                let app = recordingFor.map(Self.name(of:))
                Task {
                    await store.startRecording()
                    if let app, store.recorder.isRecording { store.recorder.name = "\(app) \(store.recorder.name)" }
                }
            case Self.stopAction:
                Task { await store.stopRecording() }
            default:
                break
            }
        }
    }

    private static func name(of bundleID: String) -> String {
        callApps.first { bundleID.hasPrefix($0.prefix) }?.name ?? bundleID
    }

    // the call apps with a process taking microphone input, as their main app's bundle id
    // prefix, so an app's helper processes count as the app
    private static func callAppsUsingMic() -> Set<String> {
        var found: Set<String> = []
        for process in processObjects() where isRunningInput(process) {
            guard let id = bundleID(of: process), let app = callApps.first(where: { id.hasPrefix($0.prefix) }) else { continue }
            found.insert(app.prefix)
        }
        return found
    }

    private static func processObjects() -> [AudioObjectID] {
        var address = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyProcessObjectList,
                                                 mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var size = UInt32(0)
        let system = AudioObjectID(kAudioObjectSystemObject)
        guard AudioObjectGetPropertyDataSize(system, &address, 0, nil, &size) == noErr, size > 0 else { return [] }
        var objects = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
        guard AudioObjectGetPropertyData(system, &address, 0, nil, &size, &objects) == noErr else { return [] }
        return objects
    }

    private static func isRunningInput(_ process: AudioObjectID) -> Bool {
        var address = AudioObjectPropertyAddress(mSelector: kAudioProcessPropertyIsRunningInput,
                                                 mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var running = UInt32(0)
        var size = UInt32(MemoryLayout<UInt32>.size)
        return AudioObjectGetPropertyData(process, &address, 0, nil, &size, &running) == noErr && running != 0
    }

    private static func bundleID(of process: AudioObjectID) -> String? {
        var address = AudioObjectPropertyAddress(mSelector: kAudioProcessPropertyBundleID,
                                                 mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var id: Unmanaged<CFString>?
        var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        guard AudioObjectGetPropertyData(process, &address, 0, nil, &size, &id) == noErr, let id else { return nil }
        return id.takeRetainedValue() as String
    }
}
