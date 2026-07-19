// swift-tools-version: 5.10

import PackageDescription

let package = Package(
    name: "ChronosMacAgent",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "chronos-mac-agent", targets: ["ChronosMacAgent"]),
    ],
    targets: [
        .executableTarget(
            name: "ChronosMacAgent",
            path: "Sources/ChronosMacAgent"
        ),
    ]
)
