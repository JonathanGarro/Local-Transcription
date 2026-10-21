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
        }
        .defaultSize(width: 1200, height: 820)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("Import Recording...") { store.importing = true }
                    .keyboardShortcut("o")
            }
            CommandGroup(after: .sidebar) {
                Button("Inbox") { store.section = .inbox }
                    .keyboardShortcut("1")
                Button("Archive") { store.section = .archive }
                    .keyboardShortcut("2")
                Divider()
                Button("Refresh") { Task { await store.refresh(); store.revision += 1 } }
                    .keyboardShortcut("r")
            }
            CommandMenu("Library") {
                // the parts of the app not yet rebuilt natively open the web version
                Button("Ask...") { store.openWebVersion("#/ask") }
                Button("People") { store.openWebVersion("#/people") }
                Button("Vocabulary") { store.openWebVersion("#/vocabulary") }
                Button("Home") { store.openWebVersion("#/home") }
                Divider()
                Button("Open Web Version") { store.openWebVersion() }
            }
        }

        Settings {
            SettingsView()
                .environment(store)
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        // swift run starts a bare binary, which would otherwise have no dock icon or menu bar
        NSApp.setActivationPolicy(.regular)
        NSApp.activate()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        Server.shared.stop()
    }
}
