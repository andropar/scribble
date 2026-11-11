// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "Scribble",
    platforms: [.macOS(.v15)],
    products: [
        .executable(name: "Scribble", targets: ["Scribble"]),
    ],
    dependencies: [
        .package(url: "https://github.com/argmaxinc/WhisperKit.git", exact: "1.1.1"),
    ],
    targets: [
        .target(
            name: "ScribbleCore",
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
        .executableTarget(
            name: "Scribble",
            dependencies: [
                "ScribbleCore",
                .product(name: "WhisperKit", package: "WhisperKit"),
            ],
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
        .testTarget(
            name: "ScribbleCoreTests",
            dependencies: [
                "ScribbleCore",
                .product(name: "WhisperKit", package: "WhisperKit"),
            ],
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
    ]
)
