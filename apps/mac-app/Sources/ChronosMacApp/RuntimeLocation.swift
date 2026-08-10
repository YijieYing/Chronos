import Foundation

enum RuntimeLocation {
    static let remoteURL: URL = {
        if let value = ProcessInfo.processInfo.environment["CHRONOS_WEB_URL"],
           let url = URL(string: value),
           isAllowedRemoteURL(url)
        {
            return url
        }
        return URL(string: "http://127.0.0.1:8765")!
    }()

    static func isAllowedInWebView(_ url: URL) -> Bool {
        if url.isFileURL || url.scheme == "about" {
            return true
        }
        return isAllowedRemoteURL(url)
    }

    private static func isAllowedRemoteURL(_ url: URL) -> Bool {
        guard url.scheme == "http" || url.scheme == "https" else { return false }
        return url.host == "127.0.0.1" || url.host == "localhost"
    }
}
