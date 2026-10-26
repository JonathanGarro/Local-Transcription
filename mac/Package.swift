// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "Heron",
    platforms: [.macOS(.v26)],
    targets: [
        .executableTarget(name: "Heron", path: "Sources/Heron"),
    ],
    swiftLanguageModes: [.v5]
)
