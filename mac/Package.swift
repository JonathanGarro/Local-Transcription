// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "Boswell",
    platforms: [.macOS(.v26)],
    targets: [
        .executableTarget(name: "Boswell", path: "Sources/Boswell"),
        // runs apple intelligence for the python server, which can't reach it
        .executableTarget(name: "BoswellAI", path: "Sources/BoswellAI"),
    ],
    swiftLanguageModes: [.v5]
)
