import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var settings: AppSettings
    @EnvironmentObject private var conversation: ConversationController

    @State private var draft = ""
    @State private var showSettings = false

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                transcriptList
                statusBar
                controls
            }
            .navigationTitle("OpenClaw Voice")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        showSettings = true
                    } label: {
                        Image(systemName: "gearshape")
                    }
                }
            }
            .sheet(isPresented: $showSettings) {
                SettingsView()
            }
            .onAppear {
                if !conversation.isConnected {
                    conversation.connect()
                }
            }
        }
        #if os(visionOS)
        .glassBackgroundEffect()
        #endif
    }

    private var transcriptList: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 10) {
                    if conversation.messages.isEmpty {
                        emptyHint
                    }
                    ForEach(conversation.messages) { message in
                        MessageBubble(message: message)
                            .id(message.id)
                    }
                    if conversation.voiceState == .listening && !conversation.liveTranscript.isEmpty {
                        MessageBubble(message: ChatMessage(role: .user, text: conversation.liveTranscript))
                            .opacity(0.55)
                            .id("live")
                    }
                }
                .padding()
            }
            .onChange(of: conversation.messages) { _, messages in
                if let last = messages.last {
                    withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                }
            }
        }
    }

    private var emptyHint: some View {
        VStack(spacing: 8) {
            Image(systemName: "waveform.circle")
                .font(.system(size: 44))
                .foregroundStyle(.secondary)
            Text("Tap the orb and start talking to your agent.")
                .font(.callout)
                .foregroundStyle(.secondary)
        }
        .padding(.top, 60)
    }

    private var statusBar: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(conversation.isConnected ? Color.green : Color.red)
                .frame(width: 8, height: 8)
            Text(statusText)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
        .padding(.vertical, 4)
    }

    private var statusText: String {
        switch conversation.voiceState {
        case .idle:
            return conversation.isConnected ? "Connected — ready" : "Not connected"
        case .connecting: return "Connecting to agent…"
        case .listening: return "Listening…"
        case .thinking: return "Agent is thinking…"
        case .speaking: return "Agent is speaking…"
        case .error(let message): return message
        }
    }

    private var controls: some View {
        VStack(spacing: 14) {
            Button {
                conversation.toggleVoiceLoop()
            } label: {
                VoiceOrbView(state: conversation.voiceState)
            }
            .buttonStyle(.plain)

            HStack {
                TextField("Type instead…", text: $draft)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit(sendDraft)
                Button {
                    sendDraft()
                } label: {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.title2)
                }
                .disabled(draft.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            .padding(.horizontal)
        }
        .padding(.bottom, 16)
    }

    private func sendDraft() {
        conversation.sendTypedMessage(draft)
        draft = ""
    }
}
