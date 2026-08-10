import AppKit
import SwiftUI

final class ChronosAppDelegate: NSObject, NSApplicationDelegate {
    func applicationWillFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(
        _ sender: NSApplication
    ) -> Bool {
        true
    }
}

@main
struct ChronosApp: App {
    @NSApplicationDelegateAdaptor(ChronosAppDelegate.self)
    private var appDelegate

    var body: some Scene {
        WindowGroup {
            ChronosWebView()
                .frame(minWidth: 900, minHeight: 620)
                .ignoresSafeArea()
        }
        .defaultSize(width: 1360, height: 840)
        .windowStyle(.hiddenTitleBar)
        .windowResizability(.contentMinSize)
    }
}
