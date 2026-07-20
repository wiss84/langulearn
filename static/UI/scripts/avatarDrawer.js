// LanguLearn - avatar drawer for the live learning page. Toggled by the
// camera button in the top bar; shows the current conversation's tutor
// avatar and, once loaded, becomes the actual audio output path so its
// mouth moves in sync with the real conversation (not a canned sample -
// see registerAvatarAudioSink in audio.js for why that requires routing
// playback through this avatar's own AudioContext).
//
// This is an ES module (needs bare imports for three/talkinghead), unlike
// every other learning-page script - so it can't see their `let`/`const`
// globals directly. It reaches them via `window.X`, which only works for
// values those scripts expose that way on purpose (currentVoiceName is
// declared with `var` in state.js specifically for this; functions like
// registerAvatarAudioSink are plain top-level declarations, which are
// `window` properties automatically).

import { TalkingHead } from 'talkinghead';
import { HeadAudio } from '/vendor/headaudio/modules/headaudio.mjs';

const cameraToggleBtn = document.getElementById('cameraToggleBtn');
const bodyEl = document.getElementById('body');
const avatarDrawer = document.getElementById('avatarDrawer');
const avatarDrawerPreview = document.getElementById('avatarDrawerPreview');
const avatarDrawerHint = document.getElementById('avatarDrawerHint');
const avatarMaximizeBtn = document.getElementById('avatarMaximizeBtn');

const MOUTH_BOOST = 1.4; // see avatarPreview.js for why this exists

let head = null;
let headaudio = null;
let avatarReady = false;
let isMaximized = false;

// Same VRoid/ARKit blend-shape gap workaround as avatarPreview.js - see
// that file for the full explanation. Duplicated rather than shared since
// these are two separate pages/documents with no module loader connecting
// them.
const ARKIT_BLEND_SHAPES = [
  'browDownLeft', 'browDownRight', 'browInnerUp', 'browOuterUpLeft', 'browOuterUpRight',
  'cheekPuff', 'cheekSquintLeft', 'cheekSquintRight',
  'eyeBlinkLeft', 'eyeBlinkRight',
  'eyeLookDownLeft', 'eyeLookDownRight', 'eyeLookInLeft', 'eyeLookInRight',
  'eyeLookOutLeft', 'eyeLookOutRight', 'eyeLookUpLeft', 'eyeLookUpRight',
  'eyeSquintLeft', 'eyeSquintRight', 'eyeWideLeft', 'eyeWideRight',
  'jawForward', 'jawLeft', 'jawOpen', 'jawRight',
  'mouthClose', 'mouthDimpleLeft', 'mouthDimpleRight', 'mouthFrownLeft', 'mouthFrownRight',
  'mouthFunnel', 'mouthLeft', 'mouthLowerDownLeft', 'mouthLowerDownRight',
  'mouthPressLeft', 'mouthPressRight', 'mouthPucker', 'mouthRight',
  'mouthRollLower', 'mouthRollUpper', 'mouthShrugLower', 'mouthShrugUpper',
  'mouthSmileLeft', 'mouthSmileRight', 'mouthStretchLeft', 'mouthStretchRight',
  'mouthUpperUpLeft', 'mouthUpperUpRight', 'noseSneerLeft', 'noseSneerRight', 'tongueOut',
  'eyesLookDown', 'eyesLookUp',
];

function stubMissingBlendShapes() {
  ARKIT_BLEND_SHAPES.forEach((name) => {
    if (!(name in head.mtAvatar)) {
      const entry = {
        fixed: null, realtime: null, system: null, systemd: null, newvalue: null, ref: null,
        min: head.mtMinExceptions.hasOwnProperty(name) ? head.mtMinExceptions[name] : head.mtMinDefault,
        max: head.mtMaxExceptions.hasOwnProperty(name) ? head.mtMaxExceptions[name] : head.mtMaxDefault,
        easing: head.mtEasingDefault, base: null, v: 0, needsUpdate: false,
        acc: (head.mtAccExceptions.hasOwnProperty(name) ? head.mtAccExceptions[name] : head.mtAccDefault) / 1000,
        maxv: (head.mtMaxVExceptions.hasOwnProperty(name) ? head.mtMaxVExceptions[name] : head.mtMaxVDefault) / 1000,
        limit: head.mtLimits.hasOwnProperty(name) ? head.mtLimits[name] : null,
        onchange: head.mtOnchange.hasOwnProperty(name) ? head.mtOnchange[name] : null,
        baseline: head.mtBaselineExceptions.hasOwnProperty(name) ? head.mtBaselineExceptions[name] : head.mtBaselineDefault,
        ms: [], is: [],
      };
      entry.value = entry.baseline;
      entry.applied = entry.baseline;
      head.mtAvatar[name] = entry;
    }
  });
}

async function ensureAvatarReady() {
  if (avatarReady) return;
  avatarDrawerHint.textContent = 'Loading avatar...';

  head = new TalkingHead(avatarDrawerPreview, {
    cameraView: 'upper',
    dracoEnabled: true,
    dracoDecoderPath: '/vendor/three/jsm/libs/draco/',
    lipsyncLang: 'en',
    lipsyncModules: ['en'],
    avatarMood: 'neutral',
  });

  await head.audioCtx.audioWorklet.addModule('/vendor/headaudio/modules/headworklet.mjs');
  headaudio = new HeadAudio(head.audioCtx, {
    processorOptions: {},
    parameterData: { vadGateActiveDb: -40, vadGateInactiveDb: -60 },
  });
  await headaudio.loadModel('/vendor/headaudio/dist/model-en-mixed.bin');
  headaudio.onvalue = (key, value) => {
    if (key.startsWith('viseme_') && key !== 'viseme_sil') {
      value = Math.min(1, value * MOUTH_BOOST);
    }
    head.setValue(key, value);
  };
  head.opt.update = headaudio.update.bind(headaudio);

  const voiceName = window.currentVoiceName || 'Kore';
  try {
    await head.showAvatar({ url: `/avatar/${voiceName}_th.glb` });
    stubMissingBlendShapes();
    avatarDrawerHint.textContent = '';
  } catch (e) {
    avatarDrawerHint.textContent = 'Avatar could not load.';
    console.error(e);
  }

  // From here on, TTS playback routes through this avatar's AudioContext
  // instead of the standalone one audio.js otherwise creates - see
  // registerAvatarAudioSink there for why.
  window.registerAvatarAudioSink(head.audioCtx, headaudio);

  avatarReady = true;
}

cameraToggleBtn.addEventListener('click', async () => {
  const opening = !avatarDrawer.classList.contains('open');
  if (opening) {
    avatarDrawer.classList.add('open');
    avatarMaximizeBtn.style.display = 'inline-flex';
    try {
      await ensureAvatarReady();
      if (head.audioCtx.state === 'suspended') await head.audioCtx.resume();
    } catch (e) {
      avatarDrawerHint.textContent = 'Avatar preview could not start.';
      console.error(e);
    }
  } else {
    avatarDrawer.classList.remove('open');
    avatarMaximizeBtn.style.display = 'none';
    if (isMaximized) exitMaximize();
  }
});

function enterMaximize() {
  isMaximized = true;
  bodyEl.classList.add('avatar-fullscreen');
  avatarMaximizeBtn.textContent = '⤡';
  avatarMaximizeBtn.title = 'Minimize avatar';
  if (head) head.setView('full');
}

function exitMaximize() {
  isMaximized = false;
  bodyEl.classList.remove('avatar-fullscreen');
  avatarMaximizeBtn.textContent = '⤢';
  avatarMaximizeBtn.title = 'Maximize avatar';
  if (head) head.setView('upper');
}

avatarMaximizeBtn.addEventListener('click', () => {
  if (isMaximized) exitMaximize(); else enterMaximize();
});
