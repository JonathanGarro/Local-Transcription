import SwiftUI
import WebKit

// a page of the web version, shown in the window without its own sidebar, for the parts
// not rebuilt natively yet
struct WebPane: NSViewRepresentable {
    @Environment(Store.self) private var store
    let page: String

    private static let script = """
    const style = document.createElement("style");
    style.textContent = "nav.sidebar { display: none !important; } main { padding-top: 20px !important; }";
    document.documentElement.appendChild(style);
    window.addEventListener("hashchange", () => webkit.messageHandlers.route.postMessage(location.hash));
    """

    func makeCoordinator() -> Coordinator { Coordinator(store: store) }

    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.userContentController.addUserScript(
            WKUserScript(source: Self.script, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        config.userContentController.add(context.coordinator, name: "route")
        let view = WKWebView(frame: .zero, configuration: config)
        view.uiDelegate = context.coordinator
        view.navigationDelegate = context.coordinator
        // the page's own background shows until it has loaded
        view.underPageBackgroundColor = .windowBackgroundColor
        if let url = URL(string: Server.shared.baseURL.absoluteString + "/#/" + page) { view.load(URLRequest(url: url)) }
        return view
    }

    func updateNSView(_ view: WKWebView, context: Context) {}

    static func dismantleNSView(_ view: WKWebView, coordinator: Coordinator) {
        view.configuration.userContentController.removeScriptMessageHandler(forName: "route")
    }

    final class Coordinator: NSObject, WKScriptMessageHandler, WKUIDelegate, WKNavigationDelegate {
        let store: Store

        init(store: Store) { self.store = store }

        func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
            guard let hash = message.body as? String else { return }
            Task { @MainActor in self.store.openRoute(hash) }
        }

        // links that open a new window, and ones off the local server, go to their own app
        func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                     for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
            if let url = action.request.url { NSWorkspace.shared.open(url) }
            return nil
        }

        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                     decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void) {
            if let url = action.request.url, url.host != Server.shared.baseURL.host || url.port != Server.shared.baseURL.port {
                NSWorkspace.shared.open(url)
                return decisionHandler(.cancel)
            }
            decisionHandler(.allow)
        }
    }
}
