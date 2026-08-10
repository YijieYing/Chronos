import AppKit
import SwiftUI
import WebKit

struct ChronosWebView: NSViewRepresentable {
    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    func makeNSView(context: Context) -> WKWebView {
        let controller = WKUserContentController()
        controller.add(context.coordinator.bridge, name: WebBridge.channel)
        controller.addUserScript(Self.runtimeScript)

        let configuration = WKWebViewConfiguration()
        configuration.userContentController = controller
        configuration.websiteDataStore = .default()
        configuration.preferences.setValue(true, forKey: "developerExtrasEnabled")

        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.uiDelegate = context.coordinator
        webView.allowsBackForwardNavigationGestures = false
        webView.allowsMagnification = false
        webView.customUserAgent = "ChronosMacApp/0.1 WKWebView"
        context.coordinator.attach(webView)
        context.coordinator.loadInitialPage()
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {}

    static let runtimeScript = WKUserScript(
        source: """
        (() => {
          document.documentElement.style.overscrollBehavior = "none";
          document.body && (document.body.style.overscrollBehavior = "none");

          const post = (type, payload = {}) => {
            window.webkit.messageHandlers.\(WebBridge.channel).postMessage({
              type,
              payload
            });
          };

          Object.defineProperty(window, "chronosNative", {
            value: Object.freeze({
              platform: "macOS",
              postMessage: post,
              getRuntimeInfo: () => post("runtime.info")
            }),
            configurable: false,
            writable: false
          });

          window.addEventListener("DOMContentLoaded", () => post("web.ready"));
        })();
        """,
        injectionTime: .atDocumentStart,
        forMainFrameOnly: true
    )
}

extension ChronosWebView {
    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate {
        private weak var webView: WKWebView?
        private var didTryBundledPage = false
        let bridge = WebBridge()

        func attach(_ webView: WKWebView) {
            self.webView = webView
            bridge.attach(webView)
        }

        func loadInitialPage() {
            guard let webView else { return }
            webView.load(URLRequest(url: RuntimeLocation.remoteURL))
        }

        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationAction: WKNavigationAction,
            decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
        ) {
            guard let url = navigationAction.request.url else {
                decisionHandler(.cancel)
                return
            }

            if RuntimeLocation.isAllowedInWebView(url) {
                decisionHandler(.allow)
                return
            }

            if navigationAction.navigationType == .linkActivated {
                NSWorkspace.shared.open(url)
            }
            decisionHandler(.cancel)
        }

        func webView(
            _ webView: WKWebView,
            didFailProvisionalNavigation navigation: WKNavigation?,
            withError error: Error
        ) {
            loadBundledPageIfNeeded()
        }

        func webView(
            _ webView: WKWebView,
            createWebViewWith configuration: WKWebViewConfiguration,
            for navigationAction: WKNavigationAction,
            windowFeatures: WKWindowFeatures
        ) -> WKWebView? {
            if let url = navigationAction.request.url,
               !RuntimeLocation.isAllowedInWebView(url)
            {
                NSWorkspace.shared.open(url)
            }
            return nil
        }

        private func loadBundledPageIfNeeded() {
            guard !didTryBundledPage, let webView else { return }
            didTryBundledPage = true

            if let indexURL = Bundle.module.url(
                forResource: "index",
                withExtension: "html",
                subdirectory: "Web"
            ) {
                webView.loadFileURL(
                    indexURL,
                    allowingReadAccessTo: indexURL.deletingLastPathComponent()
                )
                return
            }

            webView.loadHTMLString(Self.missingBundlePage, baseURL: nil)
        }

        private static let missingBundlePage = """
        <!doctype html>
        <html>
        <meta charset="utf-8">
        <meta name="color-scheme" content="light">
        <style>
          body {
            margin: 0; min-height: 100vh; display: grid; place-items: center;
            color: #32473f; background: #f0f4f0;
            font: 14px -apple-system, BlinkMacSystemFont, sans-serif;
          }
          main { max-width: 520px; padding: 36px; }
          code { color: #24815f; font-family: ui-monospace, monospace; }
        </style>
        <main>
          <h2>Chronos frontend is unavailable</h2>
          <p>Start the local service or prepare the bundled frontend:</p>
          <code>./scripts/run-schedule.sh</code><br><br>
          <code>./scripts/build-mac-app.sh</code>
        </main>
        </html>
        """
    }
}
