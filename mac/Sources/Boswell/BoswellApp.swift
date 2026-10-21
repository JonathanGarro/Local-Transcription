import SwiftUI

@main
struct BoswellApp: App {
    @NSApplicationDelegateAdaptor private var delegate: AppDelegate
    @State private var store = Store()

    var body: some Scene {
        Window("Boswell", id: "main") {
            ContentView()
                .environment(store)
                .task { await store.start() }
                .onOpenURL { store.open(link: $0) }
        }
        .defaultSize(width: 1200, height: 820)
        .commands {
            // the settings page lives in the sidebar
            CommandGroup(replacing: .appSettings) {
                Button("Settings...") { store.section = .settings }
                    .keyboardShortcut(",")
            }
            CommandGroup(replacing: .newItem) {
                Button("Import Recording...") { store.importing = true }
                    .keyboardShortcut("o")
            }
            CommandGroup(after: .sidebar) {
                Button("Refresh") { Task { await store.refresh(); store.revision += 1 } }
                    .keyboardShortcut("r")
            }
            CommandMenu("Go") {
                ForEach(Array(Place.allCases.enumerated()), id: \.offset) { i, place in
                    Button(place.rawValue) { store.section = place }
                        .keyboardShortcut(KeyEquivalent(Character("\(i + 1)")))
                }
            }
        }

    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        // swift run starts a bare binary, which would otherwise have no dock icon or menu bar
        NSApp.setActivationPolicy(.regular)
        NSApp.activate()
        snapshotIfAsked()
    }

    // BOSWELL_SNAPSHOT=/path/to/file.png saves a picture of the window a few seconds after
    // launch, for checking a layout from the command line without screen recording access
    private func snapshotIfAsked() {
        guard let path = ProcessInfo.processInfo.environment["BOSWELL_SNAPSHOT"] else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 5) {
            guard let view = NSApp.windows.first(where: \.isVisible)?.contentView?.superview,
                  let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
            view.cacheDisplay(in: view.bounds, to: rep)
            try? rep.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: path))
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        Server.shared.stop()
    }
}
