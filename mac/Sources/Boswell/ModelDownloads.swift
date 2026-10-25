import SwiftUI

// the models boswell downloads, whether each is on this mac, and a button that downloads the
// rest in the background with progress, from setup and from settings
struct ModelDownloads: View {
    // the speaker model needs a hugging face token, unless it's downloaded already
    let tokenReady: Bool
    @State private var state: ModelsState?
    @State private var error: String?

    private var missing: [ModelInfo] { (state?.models ?? []).filter { !$0.ready } }
    private var running: Bool { state?.download.running == true }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(state?.models ?? [], id: \.repo) { model in
                row(model)
            }
            if let message = error ?? state?.download.error {
                Label(message, systemImage: "xmark.octagon.fill")
                    .foregroundStyle(.red)
                    .lineLimit(3)
            }
            if !missing.isEmpty && !running {
                HStack {
                    Button("Download \(missing.count == 1 ? "It" : "Them")") { Task { await download() } }
                        .disabled(missing.contains(where: \.gated) && !tokenReady)
                    if missing.contains(where: \.gated) && !tokenReady {
                        Text("Add a Hugging Face token first").foregroundStyle(.secondary)
                    }
                }
            }
        }
        .task { await load() }
    }

    private func row(_ model: ModelInfo) -> some View {
        let download = state?.download
        let active = download?.running == true && download?.repo == model.repo
        return VStack(alignment: .leading, spacing: 4) {
            HStack {
                Image(systemName: model.ready ? "checkmark.circle.fill" : active ? "arrow.down.circle" : "circle.dashed")
                    .foregroundStyle(model.ready ? .green : .secondary)
                VStack(alignment: .leading, spacing: 1) {
                    Text(model.label)
                    Text("\(model.detail), \(model.size)").font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Text(model.ready ? "Downloaded" : active ? progressText(download) : running ? "Waiting" : "Not downloaded")
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
            }
            if active, let download, download.total > 0 {
                ProgressView(value: min(download.done, download.total), total: download.total)
            }
        }
    }

    private func progressText(_ download: ModelDownload?) -> String {
        guard let download, download.total > 0 else { return "Starting" }
        return "\(Int((download.done / download.total * 100).rounded()))%"
    }

    private func load() async {
        state = try? await API.get(["api", "models"])
        // a download started earlier, from setup say, is followed until it's done
        while state?.download.running == true, !Task.isCancelled {
            try? await Task.sleep(for: .seconds(0.5))
            if let next: ModelsState = try? await API.get(["api", "models"]) { state = next }
        }
    }

    private func download() async {
        error = nil
        do {
            state = try await API.post(["api", "models", "download"])
            await load()
        } catch {
            self.error = error.localizedDescription
        }
    }
}
