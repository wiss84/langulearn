/**
* MIT License
*
* Copyright (c) 2025 Mika Suominen
*
* Permission is hereby granted, free of charge, to any person obtaining a copy
* of this software and associated documentation files (the "Software"), to deal
* in the Software without restriction, including without limitation the rights
* to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
* copies of the Software, and to permit persons to whom the Software is
* furnished to do so, subject to the following conditions:
*
* The above copyright notice and this permission notice shall be included in all
* copies or substantial portions of the Software.
*
* THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
* IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
* FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
* AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
* LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
* OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
* SOFTWARE.
*/

import {
  FaceLandmarker, FilesetResolver, DrawingUtils
} from "mediapipe-vision";


class FaceTracking {

  /**
  * Constructor.
  *
  * @param {Object} head TalkingHead instance
  * @param {Object} [opt=null] Options
  */
  constructor( head, opt = null ) {
    this.opt = Object.assign({
      video: null,
      canvas: null,
      isFaceTracking: true,
      isHeadTracking: true,
      isHeadCoupledPerspective: true,
      isFaceMirror: true,
      isHeadMirror: true,
      headDeltaX: -0.1,
      headDeltaY: 0,
      headDeltaZ: 0,
      modelAssetPath: "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
      wasmPath: "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.22-rc.20250304/wasm"
    }, opt || {});
    this.head = head;
    this.id = 0;

    // Mirror face
    this.mirrorFace = {
      "browDownLeft": "browDownRight", "browDownRight": "browDownLeft",
      "browOuterUpLeft": "browOuterUpRight", "browOuterUpRight": "browOuterUpLeft",
      "cheekSquintLeft": "cheekSquintRight", "cheekSquintRight": "cheekSquintLeft",
      "eyeBlinkLeft": "eyeBlinkRight", "eyeBlinkRight": "eyeBlinkLeft",
      "eyeLookDownLeft": "eyeLookDownRight", "eyeLookDownRight": "eyeLookDownLeft",
      "eyeLookInLeft": "eyeLookInRight", "eyeLookInRight": "eyeLookInLeft",
      "eyeLookOutLeft": "eyeLookOutRight", "eyeLookOutRight": "eyeLookOutLeft",
      "eyeLookUpLeft": "eyeLookUpRight", "eyeLookUpRight": "eyeLookUpLeft",
      "eyeSquintLeft": "eyeSquintRight", "eyeSquintRight": "eyeSquintLeft",
      "eyeWideLeft": "eyeWideRight", "eyeWideRight": "eyeWideLeft",
      "jawLeft": "jawRight", "jawRight": "jawLeft",
      "mouthDimpleLeft": "mouthDimpleRight", "mouthDimpleRight": "mouthDimpleLeft",
      "mouthFrownLeft": "mouthFrownRight", "mouthFrownRight": "mouthFrownLeft",
      "mouthLeft": "mouthRight", "mouthRight": "mouthLeft",
      "mouthLowerDownLeft": "mouthLowerDownRight", "mouthLowerDownRight": "mouthLowerDownLeft",
      "mouthPressLeft": "mouthPressRight", "mouthPressRight": "mouthPressLeft",
      "mouthRollLower": "mouthRollUpper", "mouthRollUpper": "mouthRollLower",
      "mouthShrugLower": "mouthShrugUpper", "mouthShrugUpper": "mouthShrugLower",
      "mouthSmileLeft": "mouthSmileRight", "mouthSmileRight": "mouthSmileLeft",
      "mouthStretchLeft": "mouthStretchRight", "mouthStretchRight": "mouthStretchLeft",
      "mouthUpperUpLeft": "mouthUpperUpRight", "mouthUpperUpRight": "mouthUpperUpLeft",
      "noseSneerLeft": "noseSneerRight", "noseSneerRight": "noseSneerLeft"
    }

  }

  /**
  * Check if getUserMedia is supported.
  * @return True, if supported.
  */
  isGetUserMedia() {
    return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  }


  /**
  * Start face tracking.
  *
  * @param {Object} [opt=null] Options
  */
  async start( opt = null ) {
    this.opt = Object.assign(this.opt, opt || {});

    // If all features all disabled, return
    if ( !this.opt.isFaceTracking && !this.opt.isHeadTracking && !this.opt.isHeadCoupledPerspective ) return;

    if ( !this.faceLandmarker ) {

      if ( !this.isGetUserMedia() ) {
        console.error("FaceTracking: getUserMedia() is not supported by your browser");
        return;
      }

      const filesetResolver = await FilesetResolver.forVisionTasks( this.opt.wasmPath );
      this.faceLandmarker = await FaceLandmarker.createFromOptions(filesetResolver, {
        baseOptions: {
          modelAssetPath: this.opt.modelAssetPath,
          delegate: "GPU"
        },
        outputFaceBlendshapes: true,
        runningMode: "VIDEO",
        numFaces: 1
      });
    }

    // Start video
    if ( this.opt.video ) {

      // Stop and reset
      this.stop();
      this.lastVideoTime = -1;
      this.canvasCtx = null;
      this.drawingUtils = null;

      try {
        const stream = await navigator.mediaDevices.getUserMedia({ video: true });
        this.opt.video.srcObject = stream;
        this.opt.video.addEventListener("loadeddata", () => {
          this.lastVideoTime = -1;
          this.mt = this.head.mtAvatar;
          this.camera = this.head.camera;
          if ( this.opt.canvas ) {
            this.opt.canvas.width = this.opt.video.videoWidth;
            this.opt.canvas.height = this.opt.video.videoHeight;
            this.canvasCtx = this.opt.canvas.getContext("2d");
            this.drawingUtils = new DrawingUtils(this.canvasCtx);
          }
          this.running = true;
          window.requestAnimationFrame( this.update.bind(this) );
        });
      } catch (error) {
        console.error("FaceTracking: getUserMedia() failed, error=",error)
      }
 
    }

  }


  /**
  * Animate dynamic bones.
  * @param {number} dt Delta time in ms.
  */
  async update(dt) {
    
    if ( !this.running ) return;

    let results;
    if ( this.lastVideoTime !== this.opt.video.currentTime) {
      this.lastVideoTime = this.opt.video.currentTime;
      let startTimeMs = performance.now();
      results = this.faceLandmarker.detectForVideo(this.opt.video, startTimeMs);
    }

    if ( results && results.faceBlendshapes && results.faceBlendshapes.length ) {

      // Control points
      const markCenter = results.faceLandmarks[0][8];
      const markTop = results.faceLandmarks[0][8]; // alternative: 10=top-most
      const markBottom = results.faceLandmarks[0][164]; // alternative: 152=chin
      const markLeft = results.faceLandmarks[0][127];
      const markRight = results.faceLandmarks[0][356];

      // Face tracking
      if ( this.opt.isFaceTracking ) {
        results.faceBlendshapes[0].categories.forEach( x => {
          let key = x.displayName || x.categoryName;
          if ( this.opt.isFaceMirror ) {
            key = this.mirrorFace[key] || key;
          }
          const value = x.score;
          if ( this.mt.hasOwnProperty(key) ) {
            Object.assign(this.mt[key],{ realtime: value, needsUpdate: true });
          }
        });
      }

      // Head tracking
      if ( this.opt.isHeadTracking ) {

        // Calculate rotation
        let rotateX = 4 * (markBottom.z - markTop.z) + this.opt.headDeltaX;
        let rotateY = 2 * (markRight.z - markLeft.z) + this.opt.headDeltaY;
        let rotateZ = (markBottom.x - markTop.x) + this.opt.headDeltaZ;
        if ( this.opt.isHeadMirror ) {
          rotateY = -1 * rotateY;
          rotateZ = -1 * rotateZ;
        }
        
        // Rotate
        Object.assign(this.mt["headRotateX"],{ realtime: rotateX, needsUpdate: true });
        Object.assign(this.mt["headRotateY"],{ realtime: rotateY, needsUpdate: true });
        Object.assign(this.mt["headRotateZ"],{ realtime: rotateZ, needsUpdate: true });
        Object.assign(this.mt["bodyRotateX"],{ realtime: rotateX/8, needsUpdate: true });
        Object.assign(this.mt["bodyRotateY"],{ realtime: rotateY/2, needsUpdate: true });
        Object.assign(this.mt["bodyRotateZ"],{ realtime: rotateZ/2, needsUpdate: true });

      }

      // Head-Coupled Perspective using camera control
      if ( this.opt.isHeadCoupledPerspective ) {

        let z = markCenter.z;
        let rotx = (markCenter.y - 0.5) / 2;
        let roty = (0.5 - markCenter.x) / 2;

        const opt = {
          cameraDistance: 2 * z,
          cameraRotateX: rotx,
          cameraRotateY: 1.5 * roty
        };
        this.head.setView( null, opt );
        this.head.cameraClock = 999;

      }

      // Canvas
      if ( this.canvasCtx ) {

        // Clear canvas
        this.canvasCtx.clearRect(0, 0, this.opt.canvas.width, this.opt.canvas.height);

        // Draw canvas
        const landmarks = results.faceLandmarks[0];

        this.drawingUtils.drawLandmarks(
          [landmarks[this.id]],
          { color: "#FF3030" }
        );

        this.drawingUtils.drawConnectors(
          landmarks,
          FaceLandmarker.FACE_LANDMARKS_TESSELATION,
          { color: "#C0C0C070", lineWidth: 1 }
        );
        this.drawingUtils.drawConnectors(
          landmarks,
          FaceLandmarker.FACE_LANDMARKS_RIGHT_EYE,
          { color: "#FF3030" }
        );
        this.drawingUtils.drawConnectors(
          landmarks,
          FaceLandmarker.FACE_LANDMARKS_RIGHT_EYEBROW,
          { color: "#FF3030" }
        );
        this.drawingUtils.drawConnectors(
          landmarks,
          FaceLandmarker.FACE_LANDMARKS_LEFT_EYE,
          { color: "#30FF30" }
        );
        this.drawingUtils.drawConnectors(
          landmarks,
          FaceLandmarker.FACE_LANDMARKS_LEFT_EYEBROW,
          { color: "#30FF30" }
        );
        this.drawingUtils.drawConnectors(
          landmarks,
          FaceLandmarker.FACE_LANDMARKS_FACE_OVAL,
          { color: "#E0E0E0" }
        );
        this.drawingUtils.drawConnectors(
          landmarks,
          FaceLandmarker.FACE_LANDMARKS_LIPS,
          { color: "#E0E0E0" }
        );
        this.drawingUtils.drawConnectors(
          landmarks,
          FaceLandmarker.FACE_LANDMARKS_RIGHT_IRIS,
          { color: "#FF3030" }
        );
        this.drawingUtils.drawConnectors(
          landmarks,
          FaceLandmarker.FACE_LANDMARKS_LEFT_IRIS,
          { color: "#30FF30" }
        );

      }
    }

    // Call this function again
    if ( this.running ) {
      window.requestAnimationFrame(this.update.bind(this));
    }

  }

  /**
  * Stop face tracking.
  */
  stop() {
    this.running = false;

    // Clear morph target values
    if ( this.head.mtAvatar ) {
      for( let mt of Object.keys(this.head.mtAvatar) ) {
        Object.assign(this.head.mtAvatar[mt],{ realtime: null, needsUpdate: true });
      }
    }

    // Stop video
    if ( this.opt.video ) {
      this.opt.video.pause();
      this.opt.video.currentTime = 0;
      if ( this.opt.video?.srcObject ) {
        const videoTrack = this.opt.video.srcObject.getVideoTracks()[0];
        if (videoTrack) videoTrack.stop();
        
        // Clear the displayed frame
        this.opt.video.removeAttribute('src'); // optional but good practice
        this.opt.video.srcObject = null;
        this.opt.video.load(); // clears the frame
        this.opt.canvas.width = this.opt.video.videoWidth;
        this.opt.canvas.height = this.opt.video.videoHeight;
      }
    }

    // Clear canvas
    if ( this.canvasCtx ) {
      this.canvasCtx.clearRect(0, 0, this.opt.canvas.width, this.opt.canvas.height);
    }
  }

}

export { FaceTracking };
