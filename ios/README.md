# OpenClaw Voice — iOS & visionOS app

Standalone SwiftUI app for iPhone, iPad, and Apple Vision Pro that holds a
hands-free voice conversation (ChatGPT-voice style) with your remote agent
through the phone-agent bridge in this repository.

## How it works

```
iPhone / Vision Pro                      Your server (LAN or VPN)
┌─────────────────────────┐              ┌──────────────────────────────┐
│  SFSpeechRecognizer     │   WebSocket  │  phone-agent (FastAPI)       │
│  (on-device STT)        │──/ws/chat───▶│  ChatEngine                  │
│  AVSpeechSynthesizer    │◀─────────────│   └─ LLM adapter:            │
│  (TTS)                  │              │      OpenClaw / Ollama / CLI │
└─────────────────────────┘              └──────────────────────────────┘
```

- Speech is transcribed **on-device** (Apple Speech framework); only text goes
  over the network.
- The reply comes back as text and is spoken with AVSpeechSynthesizer.
- With **Hands-free** enabled the loop keeps going: listen → think → speak →
  listen again, until the agent says goodbye (`continue_dialog: false`) or you
  tap the orb.
- The same WebSocket session id is reused across app launches, so the remote
  agent (e.g. an OpenClaw session) keeps conversation context.

## Requirements

- macOS with Xcode 16 or newer (visionOS SDK included)
- [XcodeGen](https://github.com/yonaskolb/XcodeGen): `brew install xcodegen`
- The phone-agent server running somewhere reachable from the device
  (`uvicorn app.main:app --host 0.0.0.0 --port 8080`)

## Build

```bash
cd ios
xcodegen generate
open OpenClawVoice.xcodeproj
```

Two app targets are generated from the same sources:

- **OpenClawVoice-iOS** — iPhone & iPad (iOS 17+)
- **OpenClawVoice-visionOS** — Apple Vision Pro (visionOS 1.0+)

Select a target, set your signing team, and run.

## Configure

In the app, open **Settings** (gear icon):

- **Server URL** — base URL of the phone-agent, e.g. `http://192.168.1.10:8080`.
  The app derives `ws://…/ws/chat` automatically (`https` → `wss`).
- **API token** — only needed if `MOBILE_API_TOKEN` is set on the server.
- **Language** — locale used for both speech recognition and TTS.
- **Hands-free** — keep listening after each reply.

The generated Info.plist enables `NSAllowsLocalNetworking`, so plain
`ws://` on your LAN works out of the box. For access outside your network,
put the bridge behind TLS (then use `https://…` as the server URL) or a VPN
such as Tailscale, and set `MOBILE_API_TOKEN`.

## Server-side setting

```bash
# optional shared secret checked by /chat and /ws/chat
export MOBILE_API_TOKEN="change-me"
```
