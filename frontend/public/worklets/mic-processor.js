class MicProcessor extends AudioWorkletProcessor {
  process(inputs, outputs, parameters) {
    const input = inputs[0];
    if (input && input.length > 0) {
      const channelData = input[0];
      if (channelData && channelData.length > 0) {
        // Post Float32Array chunk transferring ownership avoiding memory pressure drift
        this.port.postMessage(channelData.buffer, [channelData.buffer]);
      }
    }
    // Return true to keep processor alive
    return true;
  }
}

registerProcessor('mic-processor', MicProcessor);
