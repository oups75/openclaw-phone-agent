import AVFoundation
import Foundation
import Speech

/// On-device streaming speech-to-text built on SFSpeechRecognizer + AVAudioEngine.
/// Listening ends automatically after a short silence and returns the final transcript.
@MainActor
final class SpeechRecognizer: ObservableObject {
    enum SpeechError: LocalizedError {
        case notAuthorized
        case recognizerUnavailable

        var errorDescription: String? {
            switch self {
            case .notAuthorized:
                return "Speech recognition or microphone permission was denied."
            case .recognizerUnavailable:
                return "Speech recognition is unavailable for the selected language."
            }
        }
    }

    @Published private(set) var isListening = false
    @Published private(set) var partialTranscript = ""

    /// Seconds of silence after the last recognized words before the turn ends.
    var silenceTimeout: TimeInterval = 1.6
    /// Hard cap on a single listening turn.
    var maxTurnDuration: TimeInterval = 30

    private let audioEngine = AVAudioEngine()
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    private var recognitionTask: SFSpeechRecognitionTask?
    private var silenceTimer: Timer?
    private var turnTimer: Timer?
    private var finishContinuation: CheckedContinuation<String, Error>?

    static func requestPermissions() async -> Bool {
        let speechGranted = await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { status in
                continuation.resume(returning: status == .authorized)
            }
        }
        guard speechGranted else { return false }
        return await AVAudioApplication.requestRecordPermission()
    }

    /// Listens until silence and returns the final transcript for the turn.
    func listenForTurn(locale: Locale) async throws -> String {
        guard await Self.requestPermissions() else {
            throw SpeechError.notAuthorized
        }
        guard let recognizer = SFSpeechRecognizer(locale: locale), recognizer.isAvailable else {
            throw SpeechError.recognizerUnavailable
        }

        stop()

        let session = AVAudioSession.sharedInstance()
        #if os(iOS)
        try session.setCategory(.playAndRecord, mode: .voiceChat, options: [.duckOthers, .defaultToSpeaker, .allowBluetooth])
        #else
        try session.setCategory(.playAndRecord, mode: .voiceChat, options: [.duckOthers])
        #endif
        try session.setActive(true, options: .notifyOthersOnDeactivation)

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        if recognizer.supportsOnDeviceRecognition {
            request.requiresOnDeviceRecognition = true
        }
        recognitionRequest = request

        let inputNode = audioEngine.inputNode
        let format = inputNode.outputFormat(forBus: 0)
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
            request.append(buffer)
        }
        audioEngine.prepare()
        try audioEngine.start()

        isListening = true
        partialTranscript = ""

        return try await withCheckedThrowingContinuation { continuation in
            finishContinuation = continuation

            recognitionTask = recognizer.recognitionTask(with: request) { [weak self] result, error in
                Task { @MainActor [weak self] in
                    guard let self else { return }
                    if let result {
                        self.partialTranscript = result.bestTranscription.formattedString
                        self.restartSilenceTimer()
                        if result.isFinal {
                            self.finish(with: .success(result.bestTranscription.formattedString))
                        }
                    }
                    if let error {
                        // A cancelled task after we already finished is expected; ignore it.
                        if self.finishContinuation != nil, self.partialTranscript.isEmpty {
                            self.finish(with: .failure(error))
                        } else {
                            self.finish(with: .success(self.partialTranscript))
                        }
                    }
                }
            }

            restartSilenceTimer()
            turnTimer = Timer.scheduledTimer(withTimeInterval: maxTurnDuration, repeats: false) { [weak self] _ in
                Task { @MainActor [weak self] in
                    guard let self else { return }
                    self.finish(with: .success(self.partialTranscript))
                }
            }
        }
    }

    func stop() {
        finish(with: .success(partialTranscript))
    }

    private func restartSilenceTimer() {
        silenceTimer?.invalidate()
        // Only start counting silence once some speech has been recognized.
        guard !partialTranscript.isEmpty else { return }
        silenceTimer = Timer.scheduledTimer(withTimeInterval: silenceTimeout, repeats: false) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.finish(with: .success(self.partialTranscript))
            }
        }
    }

    private func finish(with result: Result<String, Error>) {
        silenceTimer?.invalidate()
        silenceTimer = nil
        turnTimer?.invalidate()
        turnTimer = nil

        if audioEngine.isRunning {
            audioEngine.stop()
            audioEngine.inputNode.removeTap(onBus: 0)
        }
        recognitionRequest?.endAudio()
        recognitionRequest = nil
        recognitionTask?.cancel()
        recognitionTask = nil
        isListening = false

        guard let continuation = finishContinuation else { return }
        finishContinuation = nil
        switch result {
        case .success(let text):
            continuation.resume(returning: text.trimmingCharacters(in: .whitespacesAndNewlines))
        case .failure(let error):
            continuation.resume(throwing: error)
        }
    }
}
