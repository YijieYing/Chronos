import AppKit
import Foundation

final class SessionStateCollector {
    private let emitter: ObservationEmitter
    private var observers: [NSObjectProtocol] = []

    init(emitter: ObservationEmitter) {
        self.emitter = emitter
    }

    func start() {
        emitter.emit(
            kind: "collector.status",
            payload: ["module": "session_state", "status": "available"]
        )
        let center = NSWorkspace.shared.notificationCenter
        observe(center, NSWorkspace.willSleepNotification) { [weak self] in
            self?.emitDevice("unavailable", reason: "system_sleep")
        }
        observe(center, NSWorkspace.didWakeNotification) { [weak self] in
            self?.emitDevice("available", reason: "system_wake")
        }
        observe(center, NSWorkspace.screensDidSleepNotification) { [weak self] in
            self?.emitScreen("asleep")
        }
        observe(center, NSWorkspace.screensDidWakeNotification) { [weak self] in
            self?.emitScreen("awake")
        }
        observe(center, NSWorkspace.sessionDidResignActiveNotification) { [weak self] in
            self?.emitDevice("inactive", reason: "session_resigned")
        }
        observe(center, NSWorkspace.sessionDidBecomeActiveNotification) { [weak self] in
            self?.emitDevice("active", reason: "session_became_active")
        }
        emitDevice("active", reason: "collector_started")
        emitScreen("awake")
    }

    func stop() {
        let center = NSWorkspace.shared.notificationCenter
        observers.forEach(center.removeObserver)
        observers.removeAll()
    }

    private func observe(
        _ center: NotificationCenter,
        _ name: NSNotification.Name,
        action: @escaping () -> Void
    ) {
        observers.append(
            center.addObserver(forName: name, object: nil, queue: .main) { _ in action() }
        )
    }

    private func emitDevice(_ state: String, reason: String) {
        emitter.emit(
            kind: "device.presence",
            payload: ["state": state, "reason": reason]
        )
    }

    private func emitScreen(_ state: String) {
        emitter.emit(kind: "screen.state", payload: ["state": state])
    }
}
