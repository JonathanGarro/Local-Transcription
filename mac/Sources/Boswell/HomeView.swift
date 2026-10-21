import Charts
import SwiftUI

struct HomeView: View {
    @Environment(Store.self) private var store
    @State private var stats: HomeStats?

    var body: some View {
        Group {
            if let stats { content(stats) } else { ProgressView() }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .navigationTitle("Data")
        .navigationSubtitle("Every meeting, processed here or imported from Obsidian")
        .task(id: store.revision) {
            stats = try? await API.get(["api", "home"])
            if store.people.isEmpty { await store.loadPeople() }
        }
    }

    private func content(_ s: HomeStats) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                tiles(s)
                card("Meetings per month") { MonthChart(rows: s.byMonth) }
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 360), spacing: 20, alignment: .top)], spacing: 20) {
                    card("Who you meet with most") {
                        RankedBars(rows: s.peopleTop, note: { $0.minutes.map { "\(Int($0)) min talking" } }) { row in
                            store.people.first { $0.name == row.label }.map { person in
                                { store.section = .people; store.personSelection = person.personId }
                            }
                        }
                    }
                    card("Teams you meet with most") { RankedBars(rows: s.teamsTop) }
                    card("Busiest projects") { RankedBars(rows: s.projectsTop) }
                    card("Meetings by weekday") { WeekdayChart(rows: weekdays(s.byWeekday)) }
                }
            }
            .padding(24)
            .frame(maxWidth: 1100)
            .frame(maxWidth: .infinity)
        }
    }

    private func tiles(_ s: HomeStats) -> some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 140), spacing: 12)], spacing: 12) {
            tile(number(s.meetings), "meetings")
            tile(String(format: "%.1f", s.hours), "hours recorded")
            tile(number(s.people), "people met")
            tile(number(s.voices), "voices known")
            if let share = s.ownerShare { tile("\(Int((share * 100).rounded()))%", "of talk time is you") }
            if let review = s.review, review > 0 {
                Button { store.section = .inbox } label: {
                    tile(number(review), "meetings to review", warn: true)
                }
                .buttonStyle(.plain)
                .help("Open the Inbox")
            }
        }
    }

    private func tile(_ value: String, _ label: String, warn: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                if warn { Image(systemName: "exclamationmark.circle.fill").foregroundStyle(.orange) }
                Text(value).font(.title.weight(.semibold)).monospacedDigit()
            }
            Text(label).font(.callout).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 10))
    }

    private func card(_ title: String, @ViewBuilder content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title).font(.headline)
            content()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 10))
    }

    // weekends show only when there are real weekend meetings, not the odd one
    private func weekdays(_ rows: [Count]) -> [Count] {
        let busiest = rows.map(\.value).max() ?? 1
        return rows.filter { !["Sat", "Sun"].contains($0.label) || $0.value >= busiest / 10 }
    }

    private func number(_ n: Double) -> String { "\(Int(n))" }
}

// columns over time, with the hovered month's count above the chart
struct MonthChart: View {
    @Environment(Store.self) private var store
    let rows: [Count]
    @State private var hovered: String?

    private static let months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    var body: some View {
        if rows.isEmpty {
            Text("Nothing yet").foregroundStyle(.secondary)
        } else {
            // label about a dozen months, so the axis stays readable
            let every = max(1, Int((Double(rows.count) / 12).rounded(.up)))
            let labelled = rows.enumerated().filter { $0.offset % every == 0 }.map(\.element.label)
            VStack(alignment: .leading, spacing: 6) {
                readout
                Chart(rows, id: \.label) { row in
                    BarMark(x: .value("Month", row.label), y: .value("Meetings", row.value), width: .ratio(0.7))
                        .clipShape(UnevenRoundedRectangle(topLeadingRadius: 4, topTrailingRadius: 4))
                        .foregroundStyle(store.accentColor.opacity(hovered == nil || hovered == row.label ? 1 : 0.45))
                }
                .chartXSelection(value: $hovered)
                .chartXAxis {
                    AxisMarks(values: labelled) { value in
                        AxisValueLabel { Text(value.as(String.self).map(Self.short) ?? "") }
                    }
                }
                .chartYAxis {
                    AxisMarks(position: .leading) { _ in
                        AxisGridLine().foregroundStyle(.quaternary)
                        AxisValueLabel()
                    }
                }
                .frame(height: 200)
            }
        }
    }

    private var readout: some View {
        let row = rows.first { $0.label == hovered }
        return Text(row.map { "\(Self.long($0.label)): \(Int($0.value)) meetings" } ?? "Hover a month for its count")
            .font(.callout)
            .foregroundStyle(row == nil ? .tertiary : .secondary)
            .monospacedDigit()
    }

    // "2024-08" as "Aug 24" on the axis and "August 2024" in the readout
    static func short(_ label: String) -> String {
        let parts = label.split(separator: "-")
        guard parts.count == 2, let m = Int(parts[1]), (1...12).contains(m) else { return label }
        return "\(months[m - 1]) \(parts[0].suffix(2))"
    }

    static func long(_ label: String) -> String {
        let parts = label.split(separator: "-")
        guard parts.count == 2, let m = Int(parts[1]), (1...12).contains(m) else { return label }
        return "\(DateFormatter().monthSymbols[m - 1]) \(parts[0])"
    }
}

struct WeekdayChart: View {
    @Environment(Store.self) private var store
    let rows: [Count]
    @State private var hovered: String?

    var body: some View {
        Chart(rows, id: \.label) { row in
            BarMark(x: .value("Day", row.label), y: .value("Meetings", row.value), width: .ratio(0.55))
                .clipShape(UnevenRoundedRectangle(topLeadingRadius: 4, topTrailingRadius: 4))
                .foregroundStyle(store.accentColor.opacity(hovered == nil || hovered == row.label ? 1 : 0.45))
                .annotation(position: .top, spacing: 4) {
                    if hovered == row.label {
                        Text("\(Int(row.value))").font(.caption.weight(.medium)).monospacedDigit()
                    }
                }
        }
        .chartXSelection(value: $hovered)
        .chartYAxis {
            AxisMarks(position: .leading) { _ in
                AxisGridLine().foregroundStyle(.quaternary)
                AxisValueLabel()
            }
        }
        .frame(height: 180)
    }
}

// longest first, a thin bar per row with its count, and an action when a row can open something
struct RankedBars: View {
    @Environment(Store.self) private var store
    let rows: [Count]
    var note: (Count) -> String? = { _ in nil }
    var open: (Count) -> (() -> Void)? = { _ in nil }

    var body: some View {
        if rows.isEmpty {
            Text("Nothing yet").foregroundStyle(.secondary)
        } else {
            let top = rows.map(\.value).max() ?? 1
            VStack(alignment: .leading, spacing: 10) {
                ForEach(rows, id: \.label) { row in
                    if let action = open(row) {
                        Button(action: action) { line(row, top: top, linked: true) }
                            .buttonStyle(.plain)
                            .help("Open \(row.label) in People")
                    } else {
                        line(row, top: top, linked: false)
                    }
                }
            }
        }
    }

    private func line(_ row: Count, top: Double, linked: Bool) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline) {
                Text(row.label)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .foregroundStyle(linked ? store.accentColor : .primary)
                Spacer(minLength: 8)
                Text("\(Int(row.value))").monospacedDigit()
                if let extra = note(row) {
                    Text(extra).foregroundStyle(.secondary).monospacedDigit()
                }
            }
            .font(.callout)
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(.quaternary.opacity(0.6))
                    Capsule().fill(store.accentColor)
                        .frame(width: max(4, geo.size.width * row.value / top))
                }
            }
            .frame(height: 6)
        }
        .contentShape(Rectangle())
    }
}
