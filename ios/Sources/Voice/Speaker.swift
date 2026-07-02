import AVFoundation
import Foundation

/// Text-to-speech wrapper around AVSpeechSynthesizer with async completion.
@MainActor
final class Speaker: NSObject, ObservableObject, AVSpeechSynthesizerDelegate {
    @Published private(set) var isSpeaking = false

    private let synthesizer = AVSpeechSynthesizer()
    private var finishContinuation: CheckedContinuation<Void, Never>?

    override init() {
        super.init()
        synthesizer.delegate = self
    }

    /// Speaks the text and returns once playback finishes (or is cancelled).
    func speak(_ text: String, localeIdentifier: String, rate: Double) async {
        stop()
        guard !text.isEmpty else { return }

        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(language: localeIdentifier)
            ?? AVSpeechSynthesisVoice(language: Locale.current.identifier)
        utterance.rate = Float(max(0.0, min(rate, 1.0)))

        isSpeaking = true
        await withCheckedContinuation { continuation in
            finishContinuation = continuation
            synthesizer.speak(utterance)
        }
        isSpeaking = false
    }

    func stop() {
        synthesizer.stopSpeaking(at: .immediate)
        resumeIfNeeded()
        isSpeaking = false
    }

    private func resumeIfNeeded() {
        finishContinuation?.resume()
        finishContinuation = nil
    }

    // MARK: - AVSpeechSynthesizerDelegate

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        Task { @MainActor in self.resumeIfNeeded() }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        Task { @MainActor in self.resumeIfNeeded() }
    }
}
