// swift-tools-version: 5.10

import PackageDescription

let package = Package(
    name: "ChronosMacApp",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "chronos-mac-app", targets: ["ChronosMacApp"]),
    ],
    targets: [
        .executableTarget(
            name: "ChronosMacApp",
            path: "Sources/ChronosMacApp",
            resources: [
                .copy("Resources/Web"),
            ]
        ),
    ]
)
