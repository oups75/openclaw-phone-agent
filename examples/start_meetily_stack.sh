#!/bin/bash
# Bring up the local Meetily backend stack for the phone-agent, reusing the
# existing faster-whisper install (no whisper.cpp build, no ggml download).
#
#   examples/start_meetily_stack.sh
#
# Starts:
#   - faster-whisper /inference shim on :8178  (existing whisper venv + CT2 model)
#   - Meetily FastAPI backend on :5167         (summaries via local ollama)
#
# Then run the phone-agent with MEETILY_ENABLED=true (see below) and, optionally,
# the Meetily desktop frontend which reads the same :5167 backend.

set -euo pipefail

WHISPER_VENV=/home/soloway/.local/share/whisper-venv/bin/python3
HF_CACHE=/run/media/soloway/workspace/Devel/Tools/ai/hf-cache
MEETILY_BACKEND=/srv/share/private/Devel/Projects/DMS/Projets/K/meeting-minutes/backend
AGENT_DIR=/srv/share/private/Devel/Projects/DMS/Projets/K/openclaw-phone-agent

echo "▶ faster-whisper /inference shim on :8178 (reusing existing model)"
HF_HOME="$HF_CACHE" WHISPER_MODEL="${WHISPER_MODEL:-base}" \
  "$WHISPER_VENV" "$AGENT_DIR/examples/faster_whisper_server.py" \
  --host 127.0.0.1 --port 8178 &
WHISPER_PID=$!

echo "▶ Meetily backend on :5167 (ollama summaries)"
cd "$MEETILY_BACKEND/app"
OLLAMA_HOST=http://127.0.0.1:11434 OPENAI_API_KEY="${OPENAI_API_KEY:-ollama}" \
  "$MEETILY_BACKEND/.venv/bin/python" -m uvicorn main:app \
  --host 0.0.0.0 --port 5167 --log-level warning &
BACKEND_PID=$!

trap 'kill $WHISPER_PID $BACKEND_PID 2>/dev/null || true' EXIT INT TERM

echo
echo "✅ stack up. Point the phone-agent at it:"
echo "   MEETILY_ENABLED=true MEETILY_BACKEND_URL=http://127.0.0.1:5167 \\"
echo "   MEETILY_WHISPER_URL=http://127.0.0.1:8178 MEETILY_SUMMARY_PROVIDER=ollama \\"
echo "   MEETILY_SUMMARY_MODEL=llama3 uvicorn app.main:app --port 8080"
echo
echo "   Frontend (optional): meeting-minutes/target/release/meetily  (reads :5167)"
echo "   Ctrl-C to stop both services."
wait
