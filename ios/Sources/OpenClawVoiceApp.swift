import SwiftUI

@main
struct OpenClawVoiceApp: App {
    @StateObject private var settings = AppSettings()
    @StateObject private var conversation: ConversationController

    init() {
        let settings = AppSettings()
        _settings = StateObject(wrappedValue: settings)
        _conversation = StateObject(wrappedValue: ConversationController(settings: settings))
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(settings)
                .environmentObject(conversation)
        }
        #if os(visionOS)
        .defaultSize(width: 560, height: 720)
        #endif
    }
}
