import Foundation
import WebKit

final class WebBridge: NSObject, WKScriptMessageHandler {
    static let channel = "chronosNative"

    private weak var webView: WKWebView?

    func attach(_ webView: WKWebView) {
        self.webView = webView
    }

    func userContentController(
        _ userContentController: WKUserContentController,
        didReceive message: WKScriptMessage
    ) {
        guard message.name == Self.channel,
              let envelope = message.body as? [String: Any],
              let type = envelope["type"] as? String
        else {
            return
        }

        switch type {
        case "web.ready":
            send(
                event: "native.ready",
                payload: runtimeInfo
            )
        case "runtime.info":
            send(
                event: "native.runtime-info",
                payload: runtimeInfo
            )
        default:
            send(
                event: "native.bridge-error",
                payload: ["message": "Unsupported bridge message"]
            )
        }
    }

    private var runtimeInfo: [String: Any] {
        [
            "platform": "macOS",
            "shellVersion": "0.1.0",
            "monitorBridgeAvailable": false,
        ]
    }

    private func send(event: String, payload: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: payload),
              let json = String(data: data, encoding: .utf8)
        else {
            return
        }

        let eventJSON = String(reflecting: event)
        let script = """
        window.dispatchEvent(new CustomEvent(\(eventJSON), { detail: \(json) }));
        """
        DispatchQueue.main.async { [weak webView] in
            webView?.evaluateJavaScript(script)
        }
    }
}
