// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "VoiceAnnotate",
    platforms: [.macOS(.v15)],
    products: [
        .executable(name: "VoiceAnnotate", targets: ["VoiceAnnotate"]),
    ],
    dependencies: [
        .package(url: "https://github.com/argmaxinc/WhisperKit.git", exact: "1.1.1"),
    ],
    targets: [
        .target(
            name: "VoiceAnnotateCore",
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
        .executableTarget(
            name: "VoiceAnnotate",
            dependencies: [
                "VoiceAnnotateCore",
                .product(name: "WhisperKit", package: "WhisperKit"),
            ],
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
        .testTarget(
            name: "VoiceAnnotateCoreTests",
            dependencies: [
                "VoiceAnnotateCore",
                .product(name: "WhisperKit", package: "WhisperKit"),
            ],
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
    ]
)
