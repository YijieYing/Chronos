import Foundation

final class ObservationEmitter {
    private let deviceID: String
    private let lock = NSLock()
    private let formatter: ISO8601DateFormatter

    init(deviceID: String) {
        self.deviceID = deviceID
        formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    }

    func emit(kind: String, at date: Date = Date(), payload: [String: Any]) {
        guard let data = encode(kind: kind, at: date, payload: payload) else {
            FileHandle.standardError.write(Data("failed to encode observation\n".utf8))
            return
        }
        lock.lock()
        defer { lock.unlock() }
        FileHandle.standardOutput.write(data)
    }

    func encode(kind: String, at date: Date, payload: [String: Any]) -> Data? {
        let object: [String: Any] = [
            "observation_id": UUID().uuidString.lowercased(),
            "device_id": deviceID,
            "kind": kind,
            "observed_at": formatter.string(from: date),
            "confidence": 1.0,
            "payload": payload,
        ]

        guard JSONSerialization.isValidJSONObject(object),
              let data = try? JSONSerialization.data(withJSONObject: object),
              var line = String(data: data, encoding: .utf8)
        else { return nil }
        line.append("\n")
        return Data(line.utf8)
    }
}
