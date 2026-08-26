// ThirtyTutors - TalkingHead/HeadAudio wrapper for the avatar-select preview
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

// Exported so external tooling (see static/UI/avatar_test/speaking_loop.html,
// a recording harness built to capture this exact loop for the marketing
// site's tutor cards) can reach the underlying TalkingHead instance - its
// renderer, in particular, for the same alpha-canvas/background fix
// dance_loop.html needed (see that file's own comment on the same issue).
// Live ES module binding - always reflects the current value even though
// it's reassigned inside initAvatarHead() below, not just its value at
// import time.
export { head };

// Known tuning item: mouth movement reads as subtle/laggy/out-of-sync,
// which turned out to be less about amplitude than about SPEED. setValue()
// (what HeadAudio drives) runs through TalkingHead's exponential smoothing
// - its default acceleration/max-velocity caps are tuned for natural idle
// motion (eyebrows drifting, mouth micro-movement), not for visemes that
// need to swing across their full range within a single ~100-150ms
// phoneme. With the defaults, a viseme is often still easing toward one
// target when the next one arrives, which reads as "barely moving" even
// though the underlying values ARE timed correctly (HeadAudio analyzes
// the real audio graph sample-by-sample as it plays - there's no missing-
// timestamp problem the way there would be for a text-driven approach).
// TalkingHead already solves this exact problem for its own fast shapes -
// eye blinks get a 10x acceleration override (mtAccExceptions) - so
// visemes (and jawOpen) get the same treatment below, applied before
// showAvatar() so it's baked into every mtAvatar entry from the start.
// MOUTH_BOOST stays as a smaller amplitude nudge on top - clamped to 1
// regardless, so it's safe either way.
const MOUTH_BOOST = 1.5;
const VISEME_ACC = 0.15; // vs TalkingHead's mtAccDefault of 0.01
const VISEME_MAXV = 15; // vs TalkingHead's mtMaxVDefault of 5

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

// TalkingHead's vendored source gives each mood its own infinite idle
// pose-cycling animation, installed into head.animQueue by showAvatar()'s
// own internal setMood() call - independent of, and running concurrently
// with, any pose set via setPoseFromTemplate()/playGesture(). Left alone,
// it kept firing its own random pose picks (on a 5-30s timer - see
// animMoods.neutral.anims in the vendored talkinghead.mjs) in between
// playPoseShowcase()'s own deliberate, timed pose changes below - visible
// as extra poses that were never in POSE_SHOWCASE_MALE/FEMALE, most
// noticeably for males (neutral mood's pose template has an 'M'-only
// branch that adds 'wide' as an extra option on top of the usual
// side/hip/straight choices). avatarDrawer.js hit the exact same
// interference on the learning page's fullscreen avatar and fixed it by
// replacing the animation with a restricted safe-pose version, since that
// page has nothing else deliberately driving pose changes; here,
// playPoseShowcase already owns pose changes entirely on its own schedule,
// so the fix is simpler - just remove TalkingHead's own version outright
// rather than replacing it with anything.
function stripAutoPoseLoop() {
  if (!head) return;
  const i = head.animQueue.findIndex((x) => x.template.name === 'pose');
  if (i !== -1) head.animQueue.splice(i, 1);
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

  // Must happen before any showAvatar() call - see the MOUTH_BOOST comment
  // above. showAvatar() (and our own stubMissingBlendShapes) build each
  // mtAvatar entry's acc/maxv from these exception dicts at creation time.
  head.visemeNames.forEach((v) => {
    head.mtAccExceptions['viseme_' + v] = VISEME_ACC;
    head.mtMaxVExceptions['viseme_' + v] = VISEME_MAXV;
  });
  head.mtAccExceptions['jawOpen'] = VISEME_ACC;
  head.mtMaxVExceptions['jawOpen'] = VISEME_MAXV;

  ready = true;
}

export async function loadAvatarAndPlaySample(voiceName, onProgress) {
  if (!ready) throw new Error('Avatar preview not initialized yet.');

  // showAvatar() below fully loads and swaps in the NEW mesh before its
  // own promise resolves (glb fetch+parse happens first, then the scene
  // swap) - but the PREVIOUS avatar's playPoseShowcase() timeout chain
  // isn't invalidated until playPoseShowcase() is called again for this
  // new avatar, which only happens after this whole function returns.
  // That left a window - as long as the glb takes to load - where the new
  // avatar was already visible but a still-pending step() from the OLD
  // avatar's chain could fire, calling setPoseFromTemplate() with the old
  // avatar's gender-list pose but landing on the new avatar's skeleton
  // (setPoseFromTemplate just acts on whatever's currently loaded - it has
  // no idea which avatar "owns" the call). Invalidating right here, before
  // the new glb even starts loading, closes that window entirely.
  stopPoseShowcase();

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
  stripAutoPoseLoop(); // showAvatar() ends by calling setMood() itself, which installs the mood's own (unsafe) pose loop - strip it immediately, before playGreeting()/playPoseShowcase() ever get a chance to compete with it

  // TalkingHead's `poseBase` isn't recreated per avatar - it's one object
  // living on the shared `head` instance for its whole lifetime, and
  // updatePoseBase() mutates it in place every frame via Quaternion.slerp()
  // (which writes its result back into the quaternion it's called on), so
  // by the time a pose transition finishes, poseBase has been dragged into
  // exactly matching it - it's effectively "whatever pose this avatar was
  // last actually in", persisted across avatar swaps. showAvatar() (just
  // above) copies that stale poseBase straight into the NEW avatar's
  // skeleton, so it visually snaps into the PREVIOUS avatar's ending pose
  // the instant it loads, and stays there until playPoseShowcase()'s own
  // delay+transition corrects it seconds later - a male avatar ending on
  // 'sitting' left the very next avatar (even a female one that never uses
  // 'sitting' itself) starting there too. 'straight' exists in both
  // POSE_SHOWCASE_MALE and POSE_SHOWCASE_FEMALE, so it's a safe universal
  // reset regardless of this avatar's gender (not known at this point in
  // the call - only the caller's later playPoseShowcase(gender) call knows
  // that). ms=50 rather than 0: setPoseFromTemplate's transition math
  // divides by this value, and an edge case where the very first frame's
  // timestamp exactly matches the transition's own start timestamp would
  // divide by zero; 50ms is visually instant but leaves enough headroom
  // to never hit that.
  head.setPoseFromTemplate(head.poseTemplates['straight'], 50);

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

export function playGreeting() {
  // "handup" is TalkingHead's built-in gesture template (used by its own
  // 👋 emoji animation); mirror=true flips it from the template's default
  // left hand to the right hand, which reads as a natural wave hello.
  if (!ready || !head) return;
  head.playGesture('handup', 3, true, 700);
}

// --- Pose showcase: cycles through a few named poses so a visitor can see
// the avatar move, right after the greeting wave finishes (started with a
// delay below rather than immediately, since the greeting is itself a hand/
// arm gesture - starting a pose transition on the same shoulder/arm bones
// at the same time would fight the greeting's own animation). Loops
// indefinitely until superseded (see poseLoopGeneration) - by picking a
// different avatar tile, which calls this again and invalidates the
// previous chain, or by the page unloading.
//
// 'namaste' is a *gesture* (gestureTemplates), not one of the 10 built-in
// body poses (poseTemplates) - verified from the vendored source, see
// design_plans/ROLEPLAY_HANDSFREE_AND_GESTURES.md. It needs playGesture(),
// not setPoseFromTemplate() like the poses in the lists below.
//
// Separate lists per gender - all pose names below verified against
// poseTemplates in the vendored talkinghead.mjs (side, hip, turn, bend,
// back, straight, wide, oneknee, kneel, sitting all exist as keys there).
const POSE_SHOWCASE_FEMALE = ['side', 'hip', 'turn', 'back', 'straight'];
const POSE_SHOWCASE_MALE = ['straight', 'wide', 'oneknee', 'sitting'];
const POSE_SHOWCASE_GESTURE = 'namaste';
const POSE_SHOWCASE_GAP_MS = 4000;
const POSE_SHOWCASE_START_DELAY_MS = 3500; // let the greeting gesture finish first

let poseLoopGeneration = 0;

export function stopPoseShowcase() {
  poseLoopGeneration++; // invalidates any pending timeouts from the current loop
}

export function playPoseShowcase(gender) {
  if (!ready || !head) return;
  const myGeneration = ++poseLoopGeneration; // supersedes any loop already in flight
  const poses = gender === 'Male' ? POSE_SHOWCASE_MALE : POSE_SHOWCASE_FEMALE;

  const total = poses.length + 1; // + the namaste gesture at the end
  function step(i) {
    if (myGeneration !== poseLoopGeneration || !head) return; // superseded - stop silently
    if (i < poses.length) {
      head.setPoseFromTemplate(head.poseTemplates[poses[i]]);
    } else {
      head.playGesture(POSE_SHOWCASE_GESTURE, 3);
    }
    setTimeout(() => step((i + 1) % total), POSE_SHOWCASE_GAP_MS);
  }

  setTimeout(() => step(0), POSE_SHOWCASE_START_DELAY_MS);
}
