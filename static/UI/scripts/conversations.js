// LanguLearn - loads the active conversation and connects the websocket
// for it. Switching between languages or adding a new one happens on
// /profiles now (see profileDetail.js) - this only ever deals with
// "whichever conversation is currently active" for the loaded profile.
// Voice/model/language for that conversation are fixed at creation time
// (via /landing + /avatar-select) - there's no live-editing UI for them
// on this page anymore, so there's nothing here to push config changes
// back to the server either.

async function fetchConversationList(profileId) {
  const res = await fetch(`/api/profiles/${profileId}/conversations`);
  if (!res.ok) throw new Error('Failed to load conversations');
  return res.json();
}

async function fetchConversationDetail(profileId, conversationId) {
  const res = await fetch(`/api/profiles/${profileId}/conversations/${conversationId}`);
  if (!res.ok) throw new Error('Conversation not found');
  return res.json();
}

async function loadConversationsForProfile() {
  if (!currentProfile) return;
  const data = await fetchConversationList(currentProfile.id);
  conversationsCache = data.conversations || [];
  if (conversationsCache.length === 0) {
    // No auto-created "Default" conversation anymore - a profile with none
    // just hasn't picked a language yet, so send them straight to
    // avatar-select to do that instead of showing a stuck, disconnected
    // learning page.
    window.location.href = `/avatar-select?profile_id=${encodeURIComponent(currentProfile.id)}`;
    return;
  }

  const activeId = data.active_conversation_id;
  const target = conversationsCache.find((c) => c.id === activeId) || conversationsCache[0];
  await selectConversation(target.id);
}

async function selectConversation(id) {
  const conv = await fetchConversationDetail(currentProfile.id, id);
  currentConversationId = id;
  currentVoiceName = (conv.config || {}).voice_name || 'Kore';
  renderConversationTranscript(conv.turns || []);

  if (ws) {
    ws.manualClose = true;
    ws.close();
  }
  connectWebSocket();
}
