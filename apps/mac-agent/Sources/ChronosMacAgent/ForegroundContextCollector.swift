import AppKit
import ApplicationServices
import Foundation

final class ForegroundContextCollector {
    private struct Signature: Equatable {
        let processID: pid_t
        let appID: String
        let title: String?
        let document: String?
    }

    private let emitter: ObservationEmitter
    private let interval: TimeInterval
    private var timer: Timer?
    private var lastSignature: Signature?

    init(emitter: ObservationEmitter, interval: TimeInterval = 2) {
        self.emitter = emitter
        self.interval = interval
    }

    func start() {
        let promptKey = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
        let trusted = AXIsProcessTrustedWithOptions([promptKey: true] as CFDictionary)
        if !trusted {
            emitter.emit(
                kind: "collector.status",
                payload: [
                    "module": "foreground_context",
                    "status": "degraded",
                    "missing_capabilities": ["window_title", "document"],
                ]
            )
            FileHandle.standardError.write(
                Data("Window titles need Accessibility permission; app names still work.\n".utf8)
            )
        } else {
            emitter.emit(
                kind: "collector.status",
                payload: ["module": "foreground_context", "status": "available"]
            )
        }
        sample()
        timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            self?.sample()
        }
    }

    func stop() {
        timer?.invalidate()
        timer = nil
    }

    private func sample() {
        guard let app = NSWorkspace.shared.frontmostApplication else { return }
        let context = accessibilityContext(processID: app.processIdentifier)
        let appID = app.bundleIdentifier ?? "unknown"
        let signature = Signature(
            processID: app.processIdentifier,
            appID: appID,
            title: context.title,
            document: context.document
        )
        guard signature != lastSignature else { return }
        lastSignature = signature

        var payload: [String: Any] = [
            "app_id": appID,
            "app_name": app.localizedName ?? appID,
            "process_id": Int(app.processIdentifier),
        ]
        if let title = context.title { payload["window_title"] = title }
        if let document = context.document { payload["document"] = document }
        emitter.emit(kind: "foreground.changed", payload: payload)
    }

    private func accessibilityContext(processID: pid_t) -> (title: String?, document: String?) {
        guard AXIsProcessTrusted() else { return (nil, nil) }
        let application = AXUIElementCreateApplication(processID)
        var windowValue: CFTypeRef?
        guard AXUIElementCopyAttributeValue(
            application,
            kAXFocusedWindowAttribute as CFString,
            &windowValue
        ) == .success,
            let windowValue
        else {
            return (nil, nil)
        }
        let window = windowValue as! AXUIElement
        return (
            stringAttribute(kAXTitleAttribute as CFString, element: window),
            stringAttribute(kAXDocumentAttribute as CFString, element: window)
        )
    }

    private func stringAttribute(_ attribute: CFString, element: AXUIElement) -> String? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, attribute, &value) == .success else {
            return nil
        }
        return value as? String
    }
}
