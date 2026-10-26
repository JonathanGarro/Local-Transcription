// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "Wemmick",
    platforms: [.macOS(.v26)],
    targets: [
        .executableTarget(name: "Wemmick", path: "Sources/Wemmick"),
    ],
    swiftLanguageModes: [.v5]
)
