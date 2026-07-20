// LanguLearn - TalkingHead/HeadAudio wrapper for the avatar-select preview
// panel. Adapted from the working prototype (static/avatar_test/test.html)
// - see that file for the from-scratch exploration; this is the same
// pipeline trimmed to just "load an avatar and play its voice sample".

import { TalkingHead } from 'talkinghead';
import { HeadAudio } from '/vendor/headaudio/modules/headaudio.mjs';

let head = null;
let headaudio = null;
let ready = false;
let currentAudioEl = null;
let currentSourceNode = null;

// Known tuning item: HeadAudio's audio-texture-based viseme detection is
// conservative by design, and VRoid's Fcl_MTH_* mouth shapes have a
// smaller geometric range than RPM's defaults - both combine to make
// mouth movement read as subtle rather than a clear open-close motion.
// Boosting mouth-related (viseme_*) values compensates; kept as one
// tunable constant rather than hardcoded inline. mtAvatar clamps applied
// values to [0,1] regardless, so boosting past 1 here is safe.
const MOUTH_BOOST = 1.4;

// TalkingHead's animation code assumes every avatar has the full Ready
// Player Me/ARKit blend shape set (52 names). VRoid's set is coarser, so
// any name our mesh doesn't have needs an inert stub or animate() throws
// the first time it references it - see static/avatar_test/test.html for
// the full explanation of why this mirrors TalkingHead's own "phantom
// entry" pattern rather than being a workaround.
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
  // mtRandomized is left as TalkingHead's original list, not filtered down -
  // every name in it now resolves to a valid stub above, so picking any of
  // them at random for idle variation is a safe no-op even with no mesh
  // backing. Filtering it down to only mesh-backed shapes empties the array
  // for a VRoid avatar and crashes idle animation (Math.random() * 0).
}

export async function initAvatarHead(containerEl) {
  if (ready) return;
  head = new TalkingHead(containerEl, {
    cameraView: 'full',
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

  ready = true;
}

export async function loadAvatarAndPlaySample(voiceName, onProgress) {
  if (!ready) throw new Error('Avatar preview not initialized yet.');

  if (currentAudioEl) {
    currentAudioEl.pause();
    currentAudioEl.remove();
    currentAudioEl = null;
    currentSourceNode = null;
  }

  await head.showAvatar({ url: `/avatar/${voiceName}_th.glb` }, (ev) => {
    if (onProgress && ev.lengthComputable) {
      onProgress(Math.round((ev.loaded / ev.total) * 100));
    }
  });
  stubMissingBlendShapes();

  if (head.audioCtx.state === 'suspended') {
    await head.audioCtx.resume();
  }

  currentAudioEl = new Audio(`/voices/${voiceName}.wav`);
  currentAudioEl.crossOrigin = 'anonymous';
  currentSourceNode = head.audioCtx.createMediaElementSource(currentAudioEl);
  currentSourceNode.connect(headaudio); // for viseme analysis
  currentSourceNode.connect(head.audioCtx.destination); // so it's actually audible
  await currentAudioEl.play();
}
