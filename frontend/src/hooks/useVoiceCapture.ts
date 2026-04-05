import { useRef } from 'react';
import { float32ToInt16 } from '@/lib/audio';

type UseVoiceCaptureProps = {
  // Exclusively accepts one configuration prop emitting extracted audio buffers
  onAudioChunk: (buffer: Int16Array) => void;
};

export function useVoiceCapture({ onAudioChunk }: UseVoiceCaptureProps) {
  const audioContextRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);
  const isMutedRef = useRef<boolean>(false);

  const startCapture = async () => {
    if (workletNodeRef.current) return; // Retain idempotent invocation
    try {
      // Constraint: Trigger exactly via the user gesture `onClick` binding
      // Instantiate context at 16000 specifically for upstream capture feed
      const ctx = new window.AudioContext({ sampleRate: 16000 });
      audioContextRef.current = ctx;

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      streamRef.current = stream;

      // Registration loads the standalone worklet injected securely
      await ctx.audioWorklet.addModule('/worklets/mic-processor.js');

      const source = ctx.createMediaStreamSource(stream);
      const workletNode = new AudioWorkletNode(ctx, 'mic-processor');
      workletNodeRef.current = workletNode;

      // Extract float data and downcast to required Int16 natively guarded
      workletNode.port.onmessage = (e: MessageEvent<ArrayBuffer>) => {
        if (isMutedRef.current) return;
        const floatData = new Float32Array(e.data);
        const int16Chunk = float32ToInt16(floatData);
        onAudioChunk(int16Chunk);
      };

      source.connect(workletNode);
      // No destination connection — the port.onmessage handler is sufficient 

    } catch (err) {
      console.error("Microphone capture access failed:", err);
      throw err;
    }
  };

  const stopCapture = () => {
    if (workletNodeRef.current) {
      workletNodeRef.current.disconnect();
      workletNodeRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(track => track.stop());
      streamRef.current = null;
    }
    if (audioContextRef.current) {
      audioContextRef.current.close();
      audioContextRef.current = null;
    }
  };

  const muteCapture = () => {
    isMutedRef.current = true;
  };

  const unmuteCapture = () => {
    isMutedRef.current = false;
  };

  return { startCapture, stopCapture, muteCapture, unmuteCapture };
}
