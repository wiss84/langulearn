// LanguLearn - Live session WebSocket connection + reconnect handling.

// Model id -> display label, for the top-bar model lamp/name text (see
// setModelLampState in transcript.js). Fetched once at page load - if a
// session_status arrives before this resolves, the lamp just shows the raw
// model id until the next status update relabels it.
fetch('/api/models').then((r) => r.json()).then((data) => {
  (data.models || []).forEach((m) => { modelLabels[m.id] = m.label; });
}).catch(() => {});

// Plain reconnects (session time limit, model switch, a genuine blip) keep
// a flat 2s retry - that's deliberately snappy. A classified error kind
// ('network': no internet/DNS failure, or 'rate_limit': 429 quota
// exhausted after every retry - see live_session.py's
// _connect_failure_payload) is different: retrying every 2s just hammers
// a connection that's guaranteed to keep failing the same way until
// connectivity/quota actually recovers. backoffMs grows on each
// consecutive classified failure (capped) and resets the moment a
// connection actually succeeds. For 'network' specifically, a browser
// 'online' event also short-circuits the wait and retries immediately,
// since that's a far more reliable "connectivity is back" signal than a
// timer guess - there's no equivalent browser signal for quota
// recovering, so 'rate_limit' just rides out the backoff.
let lastCloseKind = null; // null | 'network' | 'rate_limit'
let backoffMs = 3000;
const BACKOFF_CAP_MS = 30000;
let onlineListenerAttached = false;

function connectWebSocket() {
  setConnectionState('connecting');
  setModelLampState('connecting');

  // Each socket tracks its own manualClose flag and is compared against the
  // current `ws` before acting on any event, instead of relying on one
  // shared `manualClose` boolean - a stale socket that's already been
  // superseded by a newer connection must never act on its own onclose or
  // schedule a competing reconnect.
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
    // A reconnect opens a brand-new server-side session with fresh
    // hands-free state (see live_session.py's hf_state) - if the client
    // was mid hands-free listening when the old socket dropped, tell the
    // new session so it doesn't silently stay muted server-side while the
    // button still shows "live" in the UI.
    if (handsFreeActive) {
      socket.send(JSON.stringify({ type: 'handsfree_start' }));
    }
  };

  socket.onclose = () => {
    if (socket !== ws) return; // stale socket already superseded - don't double-reconnect
    setConnectionState('error');
    setModelLampState('connecting');
    if (socket.manualClose) return;

    if (lastCloseKind) {
      reconnectTimer = setTimeout(connectWebSocket, backoffMs);
      backoffMs = Math.min(backoffMs * 2, BACKOFF_CAP_MS);
      if (lastCloseKind === 'network' && !onlineListenerAttached) {
        onlineListenerAttached = true;
        window.addEventListener('online', () => {
          clearTimeout(reconnectTimer);
          connectWebSocket();
        });
      }
    } else {
      reconnectTimer = setTimeout(connectWebSocket, 2000);
    }
  };

  socket.onerror = () => {
    if (socket === ws) { setConnectionState('error'); setModelLampState('connecting'); }
  };

  // console.log('WS message:', msg.type, msg);
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
      noteConversationActivity();
    } else if (msg.type === 'mood_change') {
      if (window.setAvatarMood) window.setAvatarMood(msg.mood);
    } else if (msg.type === 'session_status') {
      showSessionStatus(msg.resumed);
      setModelLampState(msg.unavailable ? 'unavailable' : 'connected', msg.model_name);
      // A real, available session_status means the server actually
      // connected to a model - even if we were mid-backoff a moment ago,
      // we're demonstrably past whatever was failing now.
      if (!msg.unavailable) { lastCloseKind = null; backoffMs = 3000; }
    } else if (msg.type === 'error') {
      showError(msg.message);
      // Purely a UI banner (see transcript.js's showError) - this is never
      // written to the transcript or memory.py.
      lastCloseKind = (msg.kind === 'network' || msg.kind === 'rate_limit') ? msg.kind : null;
    }
  };
}
