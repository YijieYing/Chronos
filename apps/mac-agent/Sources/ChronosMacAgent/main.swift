import AppKit
import Foundation

private func argumentValue(_ name: String) -> String? {
    guard let index = CommandLine.arguments.firstIndex(of: name),
          CommandLine.arguments.indices.contains(index + 1)
    else {
        return nil
    }
    return CommandLine.arguments[index + 1]
}

let deviceID = argumentValue("--device-id") ?? Host.current().localizedName ?? "mac"
let emitter = ObservationEmitter(deviceID: deviceID)

if CommandLine.arguments.contains("--self-test") {
    emitter.emit(
        kind: "input.activity",
        at: Date(timeIntervalSince1970: 1_700_000_000),
        payload: [
            "key_count": 12,
            "click_count": 2,
            "pointer_distance": 100.0,
            "scroll_distance": 30.0,
            "active_seconds": 4.5,
            "interval_seconds": 10.0,
        ]
    )
} else {
    let inputCollector = InputActivityCollector(emitter: emitter)
    let foregroundCollector = ForegroundContextCollector(emitter: emitter)
    let sessionCollector = SessionStateCollector(emitter: emitter)

    FileHandle.standardError.write(Data("Chronos mac-agent started for \(deviceID).\n".utf8))
    sessionCollector.start()
    foregroundCollector.start()
    inputCollector.start()

    signal(SIGINT) { _ in
        CFRunLoopStop(CFRunLoopGetMain())
    }
    signal(SIGTERM) { _ in
        CFRunLoopStop(CFRunLoopGetMain())
    }

    RunLoop.main.run()

    inputCollector.stop()
    foregroundCollector.stop()
    sessionCollector.stop()
}
