import Foundation
import SwiftUI

/// User-tunable configuration, persisted with @AppStorage.
final class AppSettings: ObservableObject {
    /// Base URL of the phone-agent bridge, e.g. "http://192.168.1.10:8080".
    @AppStorage("serverBaseURL") var serverBaseURL: String = "http://127.0.0.1:8080"
    /// Optional shared secret; must match MOBILE_API_TOKEN on the server.
    @AppStorage("apiToken") var apiToken: String = ""
    /// Keep listening after the agent finishes speaking (hands-free loop).
    @AppStorage("autoListen") var autoListen: Bool = true
    /// BCP-47 locale used for on-device speech recognition and TTS.
    @AppStorage("speechLocale") var speechLocale: String = Locale.current.identifier
    /// AVSpeechSynthesizer rate, 0.0 ... 1.0.
    @AppStorage("speechRate") var speechRate: Double = 0.5
    /// Stable conversation id so the agent keeps context across app launches.
    @AppStorage("sessionID") var sessionID: String = UUID().uuidString

    /// WebSocket endpoint derived from the base URL.
    var webSocketURL: URL? {
        guard var components = URLComponents(string: serverBaseURL.trimmingCharacters(in: .whitespaces)) else {
            return nil
        }
        components.scheme = components.scheme == "https" ? "wss" : "ws"
        components.path = "/ws/chat"
        var query = [URLQueryItem(name: "session_id", value: sessionID)]
        if !apiToken.isEmpty {
            query.append(URLQueryItem(name: "token", value: apiToken))
        }
        components.queryItems = query
        return components.url
    }

    func resetSession() {
        sessionID = UUID().uuidString
    }
}
