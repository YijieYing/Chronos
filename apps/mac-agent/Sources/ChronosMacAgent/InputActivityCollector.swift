import ApplicationServices
import Foundation

private let anyInputEventType = CGEventType(rawValue: UInt32.max)!

final class InputActivityCollector {
    private struct Counters {
        var keyCount = 0
        var clickCount = 0
        var pointerDistance = 0.0
        var scrollDistance = 0.0
        var activeSeconds = 0.0
        var lastEventAt: Date?
        var lastPointerLocation: CGPoint?
    }

    private let emitter: ObservationEmitter
    private let interval: TimeInterval
    private let lock = NSLock()
    private var counters = Counters()
    private var eventTap: CFMachPort?
    private var runLoopSource: CFRunLoopSource?
    private var timer: Timer?

    init(emitter: ObservationEmitter, interval: TimeInterval = 10) {
        self.emitter = emitter
        self.interval = interval
    }

    func start() {
        if !CGPreflightListenEventAccess() {
            emitter.emit(
                kind: "collector.status",
                payload: [
                    "module": "input_activity",
                    "status": "permission_required",
                    "missing_capabilities": ["global_input_events"],
                ]
            )
            FileHandle.standardError.write(
                Data("Chronos needs Input Monitoring permission; requesting access.\n".utf8)
            )
            _ = CGRequestListenEventAccess()
        }

        let types: [CGEventType] = [
            .keyDown,
            .leftMouseDown,
            .rightMouseDown,
            .otherMouseDown,
            .mouseMoved,
            .leftMouseDragged,
            .rightMouseDragged,
            .otherMouseDragged,
            .scrollWheel,
        ]
        let mask = types.reduce(CGEventMask(0)) { result, type in
            result | (CGEventMask(1) << type.rawValue)
        }
        let callback: CGEventTapCallBack = { _, type, event, userInfo in
            guard let userInfo else { return Unmanaged.passUnretained(event) }
            let collector = Unmanaged<InputActivityCollector>
                .fromOpaque(userInfo)
                .takeUnretainedValue()
            collector.record(type: type, event: event)
            return Unmanaged.passUnretained(event)
        }
        eventTap = CGEvent.tapCreate(
            tap: .cgSessionEventTap,
            place: .headInsertEventTap,
            options: .listenOnly,
            eventsOfInterest: mask,
            callback: callback,
            userInfo: Unmanaged.passUnretained(self).toOpaque()
        )
        guard let eventTap else {
            emitter.emit(
                kind: "collector.status",
                payload: [
                    "module": "input_activity",
                    "status": "permission_denied",
                    "missing_capabilities": ["global_input_events"],
                ]
            )
            FileHandle.standardError.write(
                Data("Input collector unavailable; grant Input Monitoring and restart.\n".utf8)
            )
            startTimer()
            return
        }
        runLoopSource = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, eventTap, 0)
        if let runLoopSource {
            CFRunLoopAddSource(CFRunLoopGetMain(), runLoopSource, .commonModes)
        }
        CGEvent.tapEnable(tap: eventTap, enable: true)
        emitter.emit(
            kind: "collector.status",
            payload: ["module": "input_activity", "status": "available"]
        )
        startTimer()
    }

    func stop() {
        timer?.invalidate()
        timer = nil
        if let runLoopSource {
            CFRunLoopRemoveSource(CFRunLoopGetMain(), runLoopSource, .commonModes)
        }
        if let eventTap {
            CGEvent.tapEnable(tap: eventTap, enable: false)
        }
        self.runLoopSource = nil
        self.eventTap = nil
    }

    private func startTimer() {
        timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            self?.flush()
        }
    }

    private func record(type: CGEventType, event: CGEvent) {
        if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
            if let eventTap {
                CGEvent.tapEnable(tap: eventTap, enable: true)
            }
            return
        }
        let now = Date()
        lock.lock()
        defer { lock.unlock() }
        if let lastEventAt = counters.lastEventAt {
            counters.activeSeconds += min(now.timeIntervalSince(lastEventAt), 1.0)
        } else {
            counters.activeSeconds += 0.1
        }
        counters.lastEventAt = now

        switch type {
        case .keyDown:
            counters.keyCount += 1
        case .leftMouseDown, .rightMouseDown, .otherMouseDown:
            counters.clickCount += 1
        case .mouseMoved, .leftMouseDragged, .rightMouseDragged, .otherMouseDragged:
            let location = event.location
            if let previous = counters.lastPointerLocation {
                counters.pointerDistance += hypot(location.x - previous.x, location.y - previous.y)
            }
            counters.lastPointerLocation = location
        case .scrollWheel:
            counters.scrollDistance += abs(event.getDoubleValueField(.scrollWheelEventDeltaAxis1))
            counters.scrollDistance += abs(event.getDoubleValueField(.scrollWheelEventDeltaAxis2))
        default:
            break
        }
    }

    private func flush() {
        lock.lock()
        let snapshot = counters
        counters = Counters(lastPointerLocation: counters.lastPointerLocation)
        lock.unlock()

        let idleSeconds = CGEventSource.secondsSinceLastEventType(
            .combinedSessionState,
            eventType: anyInputEventType
        )
        emitter.emit(
            kind: "input.activity",
            payload: [
                "key_count": snapshot.keyCount,
                "click_count": snapshot.clickCount,
                "pointer_distance": snapshot.pointerDistance,
                "scroll_distance": snapshot.scrollDistance,
                "active_seconds": min(snapshot.activeSeconds, interval),
                "idle_seconds": idleSeconds,
                "interval_seconds": interval,
            ]
        )
    }
}
