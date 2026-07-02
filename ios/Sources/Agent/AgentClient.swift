import Foundation

/// WebSocket client for the phone-agent bridge (/ws/chat).
/// Delivers ServerEvents through an AsyncStream and reconnects on demand.
@MainActor
final class AgentClient: ObservableObject {
    enum ConnectionState: Equatable {
        case disconnected
        case connecting
        case connected(sessionID: String)
        case failed(String)
    }

    @Published private(set) var state: ConnectionState = .disconnected

    private var task: URLSessionWebSocketTask?
    private var receiveLoop: Task<Void, Never>?
    private var eventContinuation: AsyncStream<ServerEvent>.Continuation?

    /// Events arrive here; the conversation controller consumes this stream.
    private(set) lazy var events: AsyncStream<ServerEvent> = AsyncStream { continuation in
        self.eventContinuation = continuation
    }

    func connect(to url: URL) {
        disconnect()
        state = .connecting

        let task = URLSession.shared.webSocketTask(with: url)
        self.task = task
        task.resume()

        receiveLoop = Task { [weak self] in
            await self?.runReceiveLoop(task: task)
        }
    }

    func disconnect() {
        receiveLoop?.cancel()
        receiveLoop = nil
        task?.cancel(with: .normalClosure, reason: nil)
        task = nil
        if case .connected = state {
            state = .disconnected
        }
    }

    func sendUserMessage(_ text: String) async throws {
        try await send(json: ["type": "user_message", "text": text])
    }

    func sendReset() async throws {
        try await send(json: ["type": "reset"])
    }

    private func send(json: [String: Any]) async throws {
        guard let task else {
            throw URLError(.notConnectedToInternet)
        }
        let data = try JSONSerialization.data(withJSONObject: json)
        guard let string = String(data: data, encoding: .utf8) else {
            throw URLError(.cannotDecodeContentData)
        }
        try await task.send(.string(string))
    }

    private func runReceiveLoop(task: URLSessionWebSocketTask) async {
        while !Task.isCancelled {
            do {
                let message = try await task.receive()
                guard let event = Self.parse(message) else { continue }
                if case .ready(let sessionID) = event {
                    state = .connected(sessionID: sessionID)
                }
                eventContinuation?.yield(event)
            } catch {
                if !Task.isCancelled {
                    state = .failed(error.localizedDescription)
                    eventContinuation?.yield(.serverError(message: error.localizedDescription))
                }
                return
            }
        }
    }

    private nonisolated static func parse(_ message: URLSessionWebSocketTask.Message) -> ServerEvent? {
        let data: Data
        switch message {
        case .string(let string):
            guard let encoded = string.data(using: .utf8) else { return nil }
            data = encoded
        case .data(let raw):
            data = raw
        @unknown default:
            return nil
        }
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return nil
        }
        return ServerEvent(json: json)
    }
}
