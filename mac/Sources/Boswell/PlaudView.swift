import SwiftUI

// recordings uploaded to plaud lately, imported by downloading their audio and running them
// here, so plaud's own transcription minutes are not used
struct PlaudList: View {
    @Environment(Store.self) private var store
    @State private var recent: PlaudRecent?
    @State private var error: String?
    @State private var importing: Set<String> = []
    @State private var showHidden = false
    @AppStorage(Prefs.hideImported) private var hideImported = true

    private var hiddenCount: Int { (recent?.recordings ?? []).filter { $0.hidden == true }.count }

    private var shown: [PlaudRecording] {
        (recent?.recordings ?? []).filter { (showHidden || $0.hidden != true) && !(hideImported && $0.status == "imported") }
    }

    // a plaud import that starts or finishes changes the list
    private var jobKey: String {
        store.jobs.filter { $0.plaudId != nil }.map { "\($0.jobId):\($0.status)" }.joined(separator: ",")
    }

    private var fresh: [PlaudRecording] {
        shown.filter { $0.status == "new" && $0.hidden != true && !importing.contains($0.plaudId) }
    }

    var body: some View {
        List(shown) { r in
            HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(r.name).fontWeight(.medium).lineLimit(2)
                        .foregroundStyle(r.hidden == true ? .secondary : .primary)
                    Text([r.date, r.duration].compactMap { $0 }.joined(separator: " · "))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer(minLength: 8)
                status(r)
            }
            .padding(.vertical, 3)
            .contentShape(Rectangle())
            .onTapGesture { if let id = r.meetingId { store.openMeeting(id) } }
            .help(r.meetingId != nil ? "Open the meeting" : "")
            .contextMenu {
                if r.hidden == true {
                    Button("Show in List") { Task { await hide(r, false) } }
                } else if r.status == "new" {
                    Button("Hide") { Task { await hide(r, true) } }
                }
            }
        }
        .safeAreaInset(edge: .top, spacing: 0) {
            // the switch first, with its label on the right
            HStack(spacing: 8) {
                Toggle("Hide Processed", isOn: $hideImported)
                    .toggleStyle(.switch)
                    .controlSize(.small)
                    .labelsHidden()
                Text("Hide Processed")
                    .onTapGesture { hideImported.toggle() }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .overlay {
            if let error {
                ContentUnavailableView("Could not reach Plaud", systemImage: "icloud.slash", description: Text(error))
            } else if recent == nil {
                ProgressView()
            } else if shown.isEmpty {
                ContentUnavailableView("Nothing New", systemImage: "icloud",
                                       description: Text(recent?.recordings.isEmpty == false
                                           ? "Everything uploaded to Plaud in the last \(recent?.days ?? 14) days is in the app."
                                           : "Nothing uploaded to Plaud in the last \(recent?.days ?? 14) days."))
            }
        }
        .navigationTitle("Plaud")
        .navigationSubtitle(recent.map { "Last \($0.days) days" } ?? "")
        .navigationSplitViewColumnWidth(min: 280, ideal: 340)
        .toolbar {
            if hiddenCount > 0 || showHidden {
                ToolbarItem {
                    Toggle(isOn: $showHidden) { Label("Show Hidden", systemImage: showHidden ? "eye" : "eye.slash") }
                        .help(showHidden ? "Hide the recordings you hid" : "Show \(hiddenCount) hidden recording\(hiddenCount == 1 ? "" : "s")")
                }
            }
            ToolbarItem {
                Button { Task { await load() } } label: { Label("Refresh", systemImage: "arrow.clockwise") }
                    .help("Check Plaud again")
            }
            if fresh.count > 1 {
                ToolbarItem {
                    Button("Import All \(fresh.count)") { Task { await importRecordings(fresh.map(\.plaudId)) } }
                }
            }
        }
        .task(id: jobKey) { await load() }
    }

    @ViewBuilder
    private func status(_ r: PlaudRecording) -> some View {
        if r.hidden == true {
            StatusText(text: "Hidden", color: .secondary)
        } else if r.status == "new" && !importing.contains(r.plaudId) {
            Button("Import") { Task { await importRecordings([r.plaudId]) } }
                .controlSize(.small)
        } else if let job = store.jobs.first(where: { $0.plaudId == r.plaudId }), job.status == "processing" {
            Text("\(job.step ?? "importing") \(Int(((job.progress ?? 0) * 100).rounded()))%")
                .font(.caption2.weight(.medium))
                .foregroundStyle(.blue)
                .lineLimit(1)
        } else if r.status == "queued" || r.status == "processing" || importing.contains(r.plaudId) {
            StatusText(text: r.status == "processing" ? "Importing" : "Queued", color: .blue)
        } else {
            StatusText(text: "In the app", color: .green)
        }
    }

    private func load() async {
        do {
            recent = try await API.get(["api", "plaud", "recent"])
            error = nil
            // once the server lists an import as queued, it no longer needs holding here
            importing.subtract(recent?.recordings.filter { $0.status != "new" }.map(\.plaudId) ?? [])
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func hide(_ r: PlaudRecording, _ hidden: Bool) async {
        await store.run {
            let _: Ok = try await API.post(["api", "plaud", r.plaudId, "hide"], ["hidden": hidden])
        }
        await load()
    }

    private func importRecordings(_ ids: [String]) async {
        importing.formUnion(ids)
        await store.run {
            let _: [String: [String]] = try await API.post(["api", "plaud", "import"], ["plaud_ids": ids])
            await store.refresh()
            store.show("Importing \(ids.count) recording\(ids.count == 1 ? "" : "s") from Plaud")
        }
        await load()
    }
}

struct StatusText: View {
    let text: String
    let color: Color

    var body: some View {
        Text(text)
            .font(.caption2.weight(.medium))
            .padding(.horizontal, 7)
            .padding(.vertical, 2)
            .foregroundStyle(color)
            .background(color.opacity(0.15), in: Capsule())
    }
}
