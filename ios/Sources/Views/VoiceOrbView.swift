import SwiftUI

/// Animated orb reflecting the conversation state, ChatGPT-voice style.
struct VoiceOrbView: View {
    let state: ConversationController.VoiceState

    @State private var pulse = false

    private var gradientColors: [Color] {
        switch state {
        case .idle, .connecting:
            return [.blue.opacity(0.6), .purple.opacity(0.4)]
        case .listening:
            return [.green, .teal]
        case .thinking:
            return [.orange, .yellow]
        case .speaking:
            return [.blue, .cyan]
        case .error:
            return [.red, .orange]
        }
    }

    private var isActive: Bool {
        switch state {
        case .listening, .thinking, .speaking:
            return true
        default:
            return false
        }
    }

    var body: some View {
        ZStack {
            Circle()
                .fill(
                    RadialGradient(
                        colors: gradientColors,
                        center: .center,
                        startRadius: 8,
                        endRadius: 90
                    )
                )
                .frame(width: 120, height: 120)
                .scaleEffect(pulse && isActive ? 1.12 : 1.0)
                .shadow(color: gradientColors.first?.opacity(0.5) ?? .clear, radius: pulse && isActive ? 32 : 12)
                .animation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true), value: pulse)

            Image(systemName: iconName)
                .font(.system(size: 40, weight: .semibold))
                .foregroundStyle(.white)
        }
        .onAppear { pulse = true }
        .accessibilityLabel(accessibilityText)
    }

    private var iconName: String {
        switch state {
        case .idle, .connecting: return "mic"
        case .listening: return "waveform"
        case .thinking: return "ellipsis"
        case .speaking: return "speaker.wave.2.fill"
        case .error: return "exclamationmark.triangle.fill"
        }
    }

    private var accessibilityText: String {
        switch state {
        case .idle: return "Start voice conversation"
        case .connecting: return "Connecting"
        case .listening: return "Listening"
        case .thinking: return "Agent is thinking"
        case .speaking: return "Agent is speaking"
        case .error(let message): return "Error: \(message)"
        }
    }
}
