// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "Boswell",
    platforms: [.macOS(.v26)],
    targets: [
        .executableTarget(name: "Boswell", path: "Sources/Boswell"),
    ],
    swiftLanguageModes: [.v5]
)
