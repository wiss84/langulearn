// LanguLearn - shared DOM references + cross-concern state.
//
// This file must load first (every other script reads these at
// top-level). Variables declared here with `let`/`const` are ordinary
// top-level script declarations - browsers share one global scope across
// sibling <script src> tags, so every other file can reference these
// directly with no import/export wiring needed. Keep names unique across
// the whole scripts/ directory.
//
// currentVoiceName is deliberately `var`, not `let` - top-level `var`
// (and function declarations) in classic scripts attach to `window`,
// which is what lets avatarDrawer.js (an ES module, with its own isolated
// module scope) read it as `window.currentVoiceName`. `let`/`const`
// bindings are NOT reachable that way, so this one exception is load-bearing.

// --- DOM references ---

const talkBtn = document.getElementById('talkBtn');
const talkHint = document.getElementById('talkHint');
const connectionDot = document.getElementById('connectionDot');
const errorBanner = document.getElementById('errorBanner');
const transcriptArea = document.getElementById('transcriptArea');
const emptyState = document.getElementById('emptyState');
const waveformCanvas = document.getElementById('waveform');
const waveformCtx = waveformCanvas.getContext('2d');
const sessionStatusText = document.getElementById('sessionStatusText');

// --- Shared mutable state ---
// (profile/conversation identity and the live socket - referenced across
// profiles.js, conversations.js, audio.js, websocket.js, and, via
// window.currentVoiceName, avatarDrawer.js)

let currentProfile = null;
let conversationsCache = [];
let currentConversationId = null;
var currentVoiceName = null;

let ws = null;
let reconnectTimer = null;
