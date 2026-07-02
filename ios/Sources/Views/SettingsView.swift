import AVFoundation
import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var settings: AppSettings
    @EnvironmentObject private var conversation: ConversationController
    @Environment(\.dismiss) private var dismiss

    private var availableLocales: [String] {
        let speech = Set(AVSpeechSynthesisVoice.speechVoices().map(\.language))
        return speech.sorted()
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Remote agent") {
                    TextField("Server URL (http://host:8080)", text: $settings.serverBaseURL)
                        .textContentType(.URL)
                        .autocorrectionDisabled()
                        #if os(iOS)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        #endif
                    SecureField("API token (optional)", text: $settings.apiToken)
                    Button("Reconnect") {
                        conversation.disconnect()
                        conversation.connect()
                    }
                }

                Section("Voice") {
                    Toggle("Hands-free (keep listening)", isOn: $settings.autoListen)
                    Picker("Language", selection: $settings.speechLocale) {
                        ForEach(availableLocales, id: \.self) { locale in
                            Text(Locale.current.localizedString(forIdentifier: locale) ?? locale)
                                .tag(locale)
                        }
                    }
                    VStack(alignment: .leading) {
                        Text("Speaking rate")
                        Slider(value: $settings.speechRate, in: 0.3...0.7)
                    }
                }

                Section("Conversation") {
                    Button("Clear conversation", role: .destructive) {
                        conversation.resetConversation()
                    }
                    Button("New agent session") {
                        conversation.disconnect()
                        settings.resetSession()
                        conversation.connect()
                    }
                }
            }
            .navigationTitle("Settings")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}
