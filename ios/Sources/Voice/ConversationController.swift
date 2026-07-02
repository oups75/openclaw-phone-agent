import Foundation
import SwiftUI

/// Drives the hands-free voice conversation loop:
/// listen -> transcribe -> send to remote agent -> speak reply -> listen again.
@MainActor
final class ConversationController: ObservableObject {
    enum VoiceState: Equatable {
        case idle
        case connecting
        case listening
        case thinking
        case speaking
        case error(String)
    }

    @Published private(set) var voiceState: VoiceState = .idle
    @Published private(set) var messages: [ChatMessage] = []
    @Published var liveTranscript = ""

    let client = AgentClient()
    let recognizer = SpeechRecognizer()
    let speaker = Speaker()

    private let settings: AppSettings
    private var eventPump: Task<Void, Never>?
    private var voiceLoop: Task<Void, Never>?
    private var pendingReply: CheckedContinuation<(text: String, continueDialog: Bool), Error>?

    init(settings: AppSettings) {
        self.settings = settings
    }

    var isConnected: Bool {
        if case .connected = client.state { return true }
        return false
    }

    // MARK: - Connection

    func connect() {
        guard let url = settings.webSocketURL else {
            voiceState = .error("Invalid server URL")
            return
        }
        voiceState = .connecting
        client.connect(to: url)
        startEventPump()
    }

    func disconnect() {
        stopVoiceLoop()
        client.disconnect()
        voiceState = .idle
    }

    private func startEventPump() {
        guard eventPump == nil else { return }
        eventPump = Task { [weak self] in
            guard let self else { return }
            for await event in self.client.events {
                self.handle(event)
            }
        }
    }

    private func handle(_ event: ServerEvent) {
        switch event {
        case .ready:
            if voiceState == .connecting { voiceState = .idle }
        case .thinking:
            voiceState = .thinking
        case .assistantMessage(let text, let continueDialog):
            pendingReply?.resume(returning: (text, continueDialog))
            pendingReply = nil
        case .serverError(let message):
            pendingReply?.resume(throwing: NSError(
                domain: "AgentClient", code: 1,
                userInfo: [NSLocalizedDescriptionKey: message]
            ))
            pendingReply = nil
            if voiceState != .idle { voiceState = .error(message) }
        case .resetOK, .pong:
            break
        }
    }

    // MARK: - Voice loop

    func toggleVoiceLoop() {
        if voiceLoop != nil {
            stopVoiceLoop()
        } else {
            startVoiceLoop()
        }
    }

    func startVoiceLoop() {
        guard voiceLoop == nil else { return }
        if !isConnected { connect() }

        voiceLoop = Task { [weak self] in
            guard let self else { return }
            repeat {
                do {
                    try await self.runSingleVoiceTurn()
                } catch is CancellationError {
                    break
                } catch {
                    self.voiceState = .error(error.localizedDescription)
                    break
                }
            } while !Task.isCancelled && self.settings.autoListen
            if case .error = self.voiceState {
                // Keep the error visible until the user acts again.
            } else {
                self.voiceState = .idle
            }
            self.voiceLoop = nil
        }
    }

    func stopVoiceLoop() {
        voiceLoop?.cancel()
        voiceLoop = nil
        recognizer.stop()
        speaker.stop()
        if pendingReply == nil {
            voiceState = .idle
        }
    }

    private func runSingleVoiceTurn() async throws {
        voiceState = .listening
        liveTranscript = ""

        let observation = observeTranscript()
        defer { observation.cancel() }

        let transcript = try await recognizer.listenForTurn(locale: Locale(identifier: settings.speechLocale))
        try Task.checkCancellation()
        guard !transcript.isEmpty else { return }

        let reply = try await sendAndAwaitReply(userText: transcript)
        try Task.checkCancellation()

        if !reply.text.isEmpty {
            voiceState = .speaking
            await speaker.speak(reply.text, localeIdentifier: settings.speechLocale, rate: settings.speechRate)
        }
        if !reply.continueDialog {
            voiceLoop?.cancel()
        }
    }

    private func observeTranscript() -> Task<Void, Never> {
        Task { [weak self] in
            guard let self else { return }
            while !Task.isCancelled {
                self.liveTranscript = self.recognizer.partialTranscript
                try? await Task.sleep(nanoseconds: 100_000_000)
            }
        }
    }

    // MARK: - Text path (typed messages share the same agent session)

    func sendTypedMessage(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        if !isConnected { connect() }
        Task { [weak self] in
            guard let self else { return }
            do {
                _ = try await self.sendAndAwaitReply(userText: trimmed)
                self.voiceState = .idle
            } catch {
                self.voiceState = .error(error.localizedDescription)
            }
        }
    }

    private func sendAndAwaitReply(userText: String) async throws -> (text: String, continueDialog: Bool) {
        messages.append(ChatMessage(role: .user, text: userText))
        voiceState = .thinking
        try await client.sendUserMessage(userText)

        let reply = try await withCheckedThrowingContinuation { continuation in
            pendingReply = continuation
        }
        if !reply.text.isEmpty {
            messages.append(ChatMessage(role: .assistant, text: reply.text))
        }
        return reply
    }

    // MARK: - Session management

    func resetConversation() {
        stopVoiceLoop()
        messages.removeAll()
        Task { try? await client.sendReset() }
    }
}
