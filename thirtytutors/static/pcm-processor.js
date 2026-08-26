// Runs on the audio rendering thread. Just grabs raw Float32 samples from
// the mic and posts them to the main thread - no resampling here, that
// happens because the AudioContext is created with { sampleRate: 16000 },
// so the browser resamples the mic stream for us before it ever reaches
// this processor.
class PCMCaptureProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0];
    if (input && input[0]) {
      // Copy because the underlying buffer gets reused by the audio thread.
      this.port.postMessage(input[0].slice());
    }
    return true;
  }
}

registerProcessor('pcm-capture-processor', PCMCaptureProcessor);
