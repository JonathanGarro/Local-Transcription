import Foundation
import Sparkle

// updates through sparkle. once a day it reads the appcast attached to the latest github
// release, downloads a newer dmg in the background, checks it against the release signing key,
// and installs it when the app quits. it sends nothing about the mac or the library
@MainActor @Observable
final class Updates: NSObject, SPUStandardUserDriverDelegate {
    // the version in the app's Info.plist. swift run has none, so it never checks
    let current = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String
    // a newer version found with installing left to the user, shown under the sidebar until
    // they look at it
    var available: String?
    var automaticChecks = false
    var automaticInstalls = false

    @ObservationIgnored private var controller: SPUStandardUpdaterController?
    private var updater: SPUUpdater? { controller?.updater }

    override init() {
        super.init()
        guard current != nil else { return }
        let controller = SPUStandardUpdaterController(startingUpdater: true, updaterDelegate: nil, userDriverDelegate: self)
        self.controller = controller
        // checking was switched off before sparkle, and that carries over once
        if UserDefaults.standard.object(forKey: Prefs.checkUpdates) as? Bool == false {
            controller.updater.automaticallyChecksForUpdates = false
        }
        UserDefaults.standard.removeObject(forKey: Prefs.checkUpdates)
        automaticChecks = controller.updater.automaticallyChecksForUpdates
        automaticInstalls = controller.updater.automaticallyDownloadsUpdates
    }

    // sparkle's own window, saying it's up to date or offering the update
    func check() {
        controller?.checkForUpdates(nil)
    }

    func setAutomaticChecks(_ on: Bool) {
        updater?.automaticallyChecksForUpdates = on
        automaticChecks = on
    }

    func setAutomaticInstalls(_ on: Bool) {
        updater?.automaticallyDownloadsUpdates = on
        automaticInstalls = on
    }

    // with automatic installs off, an update found on the daily check shows under the sidebar
    // rather than opening a window over whatever the user is doing

    nonisolated var supportsGentleScheduledUpdateReminders: Bool { true }

    nonisolated func standardUserDriverShouldHandleShowingScheduledUpdate(_ update: SUAppcastItem, andInImmediateFocus immediateFocus: Bool) -> Bool {
        false
    }

    nonisolated func standardUserDriverWillHandleShowingUpdate(_ handleShowingUpdate: Bool, forUpdate update: SUAppcastItem, state: SPUUserUpdateState) {
        guard !handleShowingUpdate else { return }
        let version = update.displayVersionString
        MainActor.assumeIsolated { available = version }
    }

    nonisolated func standardUserDriverDidReceiveUserAttention(forUpdate update: SUAppcastItem) {
        MainActor.assumeIsolated { available = nil }
    }

    nonisolated func standardUserDriverWillFinishUpdateSession() {
        MainActor.assumeIsolated { available = nil }
    }
}
