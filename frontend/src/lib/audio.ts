/**
 * audio.ts - Provides strict audio conversions and cursor-driven gapless playback scheduling.
 */

// Convert 32-bit float to 16-bit PCM for Gemini
export function float32ToInt16(float32Array: Float32Array): Int16Array {
  const length = float32Array.length;
  const int16Array = new Int16Array(length);
  for (let i = 0; i < length; i++) {
    const s = Math.max(-1, Math.min(1, float32Array[i]));
    int16Array[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return int16Array;
}

// Convert 16-bit PCM (from Gemini) to 32-bit float for playback
export function int16ToFloat32(int16Array: Int16Array): Float32Array {
  const length = int16Array.length;
  const float32Array = new Float32Array(length);
  for (let i = 0; i < length; i++) {
    const s = int16Array[i];
    float32Array[i] = s < 0 ? s / 0x8000 : s / 0x7fff;
  }
  return float32Array;
}

/**
 * Handles seamless sequential audio piece rendering based on strict playback cursor parameters.
 */
export class GaplessPlaybackQueue {
  private audioContext: AudioContext | null = null;
  private nextStartTime: number = 0;
  
  private activeSources: number = 0;
  private completionTimeout: number | undefined;
  public onAllPlaybackComplete?: () => void;

  init(context: AudioContext) {
    this.audioContext = context;
    this.nextStartTime = this.audioContext.currentTime;
  }

  scheduleChunk(buffer: AudioBuffer) {
    if (!this.audioContext) return;
    
    // Rule: Each chunk's start time = max(nextStartTime, audioContext.currentTime)
    this.nextStartTime = Math.max(this.nextStartTime, this.audioContext.currentTime);

    const source = this.audioContext.createBufferSource();
    source.buffer = buffer;
    source.connect(this.audioContext.destination);
    
    source.start(this.nextStartTime);
    
    // Track bounds exclusively natively for playback state changes
    this.activeSources++;
    source.onended = () => {
      this.activeSources--;
      if (this.activeSources === 0) {
        if (this.completionTimeout) window.clearTimeout(this.completionTimeout);
        this.completionTimeout = window.setTimeout(() => {
          if (this.activeSources === 0 && this.onAllPlaybackComplete) {
            this.onAllPlaybackComplete();
          }
        }, 500); // 500ms safety debounce protecting rapid network delivery jitter 
      }
    };
    
    // Rule: nextStartTime advances by buffer.duration
    this.nextStartTime += buffer.duration;
  }
  
  resetSession() {
      if (this.audioContext) {
          this.nextStartTime = this.audioContext.currentTime;
      } else {
          this.nextStartTime = 0;
      }
  }
}
