// LanguLearn - Live session WebSocket connection + reconnect handling.

function connectWebSocket() {
  setConnectionState('connecting');

  // Each socket tracks its own manualClose flag and is compared against the
  // current `ws` before acting on any event, instead of relying on one
  // shared `manualClose` boolean. The old code set manualClose = true,
  // called ws.close() (async - it doesn't close immediately), then reset
  // manualClose = false and opened a new socket right away. When the OLD
  // socket's onclose eventually fired, it saw manualClose already back to
  // false and scheduled its own reconnect - racing with the new connection
  // that had already been opened. That produced the connect/disconnect
  // loop (red/green dot flicker) on conversation switches, and could open
  // two Live sessions for the same conversation at once (surfacing
  // upstream as repeated 1008 errors).
  const socket = new WebSocket(`ws://${location.host}/ws/session`);
  socket.manualClose = false;
  ws = socket;

  socket.onopen = () => {
    if (socket !== ws) return; // superseded by a newer connection
    setConnectionState('connected');
    showError('');
    // voice_name/native_language/target_language/model_name are omitted -
    // with a profile_id and conversation_id given, the server always uses
    // its own stored config for that conversation (see ws_session in
    // main.py) rather than anything the client sends here. Those fields
    // only matter for the profile-less ephemeral fallback, which this
    // page's flow never reaches anymore (every session starts from
    // /landing or /avatar-select, both of which always produce a real
    // profile + conversation first).
    socket.send(JSON.stringify({
      type: 'init',
      profile_id: currentProfile.id,
      profile_name: currentProfile.name,
      conversation_id: currentConversationId,
    }));
  };

  socket.onclose = () => {
    if (socket !== ws) return; // stale socket already superseded - don't double-reconnect
    setConnectionState('error');
    if (!socket.manualClose) {
      reconnectTimer = setTimeout(connectWebSocket, 2000);
    }
  };

  socket.onerror = () => {
    if (socket === ws) setConnectionState('error');
  };

  socket.onmessage = (event) => {
    if (socket !== ws) return; // ignore messages from a superseded connection
    const msg = JSON.parse(event.data);
    if (msg.type === 'audio') {
      playAudioChunk(msg.data);
    } else if (msg.type === 'transcript_in') {
      appendOrCreateBubble('mine', msg.text);
    } else if (msg.type === 'transcript_out') {
      appendOrCreateBubble('tutor', msg.text);
    } else if (msg.type === 'turn_complete') {
      finalizeTurnBubbles();
      talkHint.textContent = 'Hold to speak';
    } else if (msg.type === 'session_status') {
      showSessionStatus(msg.resumed);
    } else if (msg.type === 'error') {
      showError(msg.message);
    }
  };
}
