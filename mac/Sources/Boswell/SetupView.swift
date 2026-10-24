import SwiftUI

// the setup shown when the app is first opened, a step at a time: your name, the speaker
// detection model, what writes notes, where they go, and the to-do app. each step saves as
// you leave it, everything but your name can be skipped, and all of it is in settings later
struct SetupView: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var settings: SettingsState?
    // BOSWELL_SETUP_STEP=notes opens setup on that step, for checking a page from the command line
    @State private var step = ProcessInfo.processInfo.environment["BOSWELL_SETUP_STEP"]
        .flatMap { name in Step.allCases.first { "\($0)" == name } } ?? .welcome
    @State private var name = ""
    @State private var hfToken = ""
    @State private var claudeKey = ""
    @State private var backend = "local"
    @State private var check: KeyCheck?
    @State private var working = false
    @State private var error: String?

    enum Step: Int, CaseIterable {
        case welcome, name, speakers, notes, folder, todos, done
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Group {
                if let settings {
                    VStack(alignment: .leading, spacing: 0) { page(settings) }
                } else {
                    ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .padding(28)
            Divider()
            footer
                .padding(.horizontal, 28)
                .padding(.vertical, 14)
        }
        .frame(width: 600, height: 470)
        .interactiveDismissDisabled()
        .task {
            settings = try? await API.get(["api", "settings"])
            name = settings?.owner ?? ""
            backend = settings?.summaryBackend ?? "local"
        }
    }

    // pages

    @ViewBuilder
    private func page(_ s: SettingsState) -> some View {
        switch step {
        case .welcome:
            header("Welcome to Boswell", "waveform")
            Text("Boswell transcribes your meetings on this Mac, works out who said what, and writes a note for each one. Recordings, transcripts and voice profiles never leave it.")
            Text("A few questions get it ready. Everything here can be changed later in Settings.")
                .foregroundStyle(.secondary)
                .padding(.top, 8)
        case .name:
            header("What's your name?", "person.crop.circle")
            Text("Notes are written for you, and your to-dos are the ones Boswell looks for. Use the name people will be confirmed under, so your own voice is matched to you.")
                .foregroundStyle(.secondary)
            TextField("Your full name", text: $name)
                .textFieldStyle(.roundedBorder)
                .frame(maxWidth: 320)
                .padding(.top, 12)
                .onSubmit { Task { await next() } }
            let matches = s.people.filter { !name.isEmpty && $0.localizedCaseInsensitiveContains(name) && $0 != name }.prefix(4)
            if !matches.isEmpty {
                HStack {
                    Text("Already in Boswell:").foregroundStyle(.secondary)
                    ForEach(Array(matches), id: \.self) { person in
                        Button(person) { name = person }.buttonStyle(.link)
                    }
                }
                .font(.callout)
                .padding(.top, 4)
            }
        case .speakers:
            header("Speaker detection", "person.2.wave.2")
            if s.modelReady {
                Label("The speaker detection model is already on this Mac.", systemImage: "checkmark.circle.fill")
                    .foregroundStyle(.green)
            } else {
                Text("Telling voices apart uses a model from Hugging Face, downloaded once the first time you transcribe. Its makers ask you to accept their terms first, which takes a free account.")
                    .foregroundStyle(.secondary)
                VStack(alignment: .leading, spacing: 6) {
                    Link("1. Accept the model's terms", destination: URL(string: s.hfTermsUrl)!)
                    Link("2. Create a read token", destination: URL(string: "https://huggingface.co/settings/tokens")!)
                    Text("3. Paste the token here")
                }
                .padding(.top, 10)
                HStack {
                    SecureField(s.hfToken == nil ? "hf_..." : "Saved. Paste another to replace it", text: $hfToken)
                        .textFieldStyle(.roundedBorder)
                        .frame(maxWidth: 320)
                    Button(working ? "Checking" : "Save and Check") { Task { await saveHFToken() } }
                        .disabled(working || (hfToken.isEmpty && s.hfToken == nil))
                }
                .padding(.top, 4)
                if let check {
                    Label(check.ok ? "The token works, and the model will download the first time you transcribe."
                          : check.error ?? "The token didn't work",
                          systemImage: check.ok ? "checkmark.circle.fill" : "xmark.octagon.fill")
                        .foregroundStyle(check.ok ? .green : .red)
                }
            }
        case .notes:
            header("Writing notes", "doc.text")
            Picker("Write notes with", selection: $backend) {
                Text("A model on this Mac").tag("local")
                Text("Claude").tag("claude")
            }
            .pickerStyle(.radioGroup)
            .labelsHidden()
            Text(backend == "local"
                 ? "Nothing leaves this Mac. The model is a 17 GB download the first time, and it works best with 32 GB of memory or more."
                 : "Claude writes better notes, and Ask and finding to-dos use it too. It reads the transcript text, never the audio, with your Anthropic API key.")
                .foregroundStyle(.secondary)
                .padding(.top, 6)
            if backend == "claude" {
                if s.key != nil {
                    Label("Your Anthropic API key is saved.", systemImage: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                        .padding(.top, 8)
                } else {
                    SecureField("sk-ant-...", text: $claudeKey)
                        .textFieldStyle(.roundedBorder)
                        .frame(maxWidth: 320)
                        .padding(.top, 8)
                    Link("Create a key in the Anthropic Console", destination: URL(string: "https://console.anthropic.com/settings/keys")!)
                        .font(.callout)
                }
            }
        case .folder:
            header("Where notes go", "folder")
            Text("If you use Obsidian, Send to Obsidian copies each finished note into your meeting notes folder. Skip this if you don't.")
                .foregroundStyle(.secondary)
            HStack {
                Text(s.meetingNotesDir ?? "No folder chosen")
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .foregroundStyle(s.meetingNotesDir == nil ? .secondary : .primary)
                Spacer()
                Button("Choose...") { chooseFolder(s) }
            }
            .padding(.top, 12)
        case .todos:
            header("To-dos", "checklist")
            Text("Boswell can find the things you were asked to do or offered to do in a meeting, and add them to your to-do app after you check them. This uses Claude.")
                .foregroundStyle(.secondary)
            Picker("To-do app", selection: Binding(get: { s.todoApp }, set: { app in
                Task { await save(SettingsChange(todoApp: app)) }
            })) {
                Text("None").tag("none")
                Text("Things 3").tag("things")
                Text("Todoist").tag("todoist")
                Text("Reminders").tag("reminders")
            }
            .labelsHidden()
            .fixedSize()
            .padding(.top, 12)
            if s.todoApp == "todoist" {
                Text("Add your Todoist token in Settings after this.")
                    .foregroundStyle(.secondary)
                    .padding(.top, 4)
            }
        case .done:
            header("You're set", "checkmark.seal")
            Text("Drop a recording on the window, or press Cmd+O, to transcribe your first meeting. The first time you confirm who's who, Boswell learns their voices, so later meetings name them on their own.")
            Text("Everything you chose is in Settings.")
                .foregroundStyle(.secondary)
                .padding(.top, 8)
        }
    }

    private func header(_ title: String, _ icon: String) -> some View {
        Label(title, systemImage: icon)
            .font(.title2.weight(.semibold))
            .padding(.bottom, 12)
    }

    private var footer: some View {
        HStack {
            if let error {
                Label(error, systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.red)
                    .lineLimit(2)
            } else {
                Text("\(step.rawValue + 1) of \(Step.allCases.count)").foregroundStyle(.secondary)
            }
            Spacer()
            if step != .welcome && step != .done {
                Button("Back") { move(-1) }
            }
            if [.speakers, .folder].contains(step) {
                Button("Skip") { move(1) }
            }
            Button(step == .done ? "Start Using Boswell" : "Continue") { Task { await next() } }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
                .disabled(working || (step == .name && name.trimmingCharacters(in: .whitespaces).isEmpty))
        }
    }

    // saving

    private func move(_ by: Int) {
        error = nil
        check = nil
        step = Step(rawValue: step.rawValue + by) ?? step
    }

    // saves what this step asked for, then moves on
    private func next() async {
        error = nil
        working = true
        defer { working = false }
        do {
            switch step {
            case .name:
                settings = try await API.put(["api", "settings"], SettingsChange(owner: name.trimmingCharacters(in: .whitespaces)))
            case .speakers where !hfToken.isEmpty:
                settings = try await API.put(["api", "settings", "hf"], ["key": hfToken])
                hfToken = ""
            case .notes:
                if backend == "claude", !claudeKey.isEmpty {
                    settings = try await API.put(["api", "settings", "key"], ["key": claudeKey])
                    claudeKey = ""
                    store.chat.claudeReady = true
                }
                settings = try await API.put(["api", "settings"], SettingsChange(summaryBackend: backend))
            case .done:
                let finished: SettingsState = try await API.put(["api", "settings"], SettingsChange(setupDone: true))
                store.apply(finished)
                store.showSetup = false
                store.revision += 1
                dismiss()
                return
            default:
                break
            }
            if let settings { store.apply(settings) }
            move(1)
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func save(_ change: SettingsChange) async {
        do {
            let updated: SettingsState = try await API.put(["api", "settings"], change)
            settings = updated
            store.apply(updated)
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func saveHFToken() async {
        working = true
        defer { working = false }
        do {
            if !hfToken.isEmpty {
                settings = try await API.put(["api", "settings", "hf"], ["key": hfToken])
                hfToken = ""
            }
            check = try await API.post(["api", "settings", "hf", "check"])
        } catch {
            check = KeyCheck(ok: false, error: error.localizedDescription)
        }
    }

    private func chooseFolder(_ s: SettingsState) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = true
        panel.prompt = "Choose"
        panel.message = "Your Obsidian meeting notes folder"
        if let path = s.meetingNotesDir { panel.directoryURL = URL(fileURLWithPath: path) }
        if panel.runModal() == .OK, let url = panel.url {
            Task { await save(SettingsChange(meetingNotesDir: url.path)) }
        }
    }
}
