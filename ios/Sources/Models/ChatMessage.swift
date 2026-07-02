import Foundation

struct ChatMessage: Identifiable, Equatable {
    enum Role {
        case user
        case assistant
        case system
    }

    let id = UUID()
    let role: Role
    var text: String
    let date = Date()
}

/// Messages received from the phone-agent bridge over /ws/chat.
enum ServerEvent {
    case ready(sessionID: String)
    case thinking
    case assistantMessage(text: String, continueDialog: Bool)
    case resetOK
    case pong
    case serverError(message: String)

    init?(json: [String: Any]) {
        switch json["type"] as? String {
        case "ready":
            self = .ready(sessionID: json["session_id"] as? String ?? "")
        case "thinking":
            self = .thinking
        case "assistant_message":
            self = .assistantMessage(
                text: json["text"] as? String ?? "",
                continueDialog: json["continue_dialog"] as? Bool ?? true
            )
        case "reset_ok":
            self = .resetOK
        case "pong":
            self = .pong
        case "error":
            self = .serverError(message: json["message"] as? String ?? "Unknown server error")
        default:
            return nil
        }
    }
}
