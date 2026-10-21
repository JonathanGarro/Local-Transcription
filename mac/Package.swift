// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "Boswell",
    platforms: [.macOS(.v15)],
    targets: [
        .executableTarget(name: "Boswell", path: "Sources/Boswell"),
    ],
    swiftLanguageModes: [.v5]
)
