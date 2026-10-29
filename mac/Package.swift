// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "Wemmick",
    platforms: [.macOS(.v26)],
    dependencies: [
        // installs updates from the appcast on github, checked against the release's signing key
        .package(url: "https://github.com/sparkle-project/Sparkle", from: "2.6.0"),
    ],
    targets: [
        .executableTarget(
            name: "Wemmick",
            dependencies: [.product(name: "Sparkle", package: "Sparkle")],
            path: "Sources/Wemmick",
            // make_app.py puts Sparkle.framework in Contents/Frameworks, and swift run finds it beside the binary
            linkerSettings: [.unsafeFlags(["-Xlinker", "-rpath", "-Xlinker", "@executable_path/../Frameworks",
                                           "-Xlinker", "-rpath", "-Xlinker", "@executable_path"])]
        ),
    ],
    swiftLanguageModes: [.v5]
)
