import { useRef, useCallback, useState } from 'react';
import { GaplessPlaybackQueue, int16ToFloat32 } from '@/lib/audio';

export type VoiceState = 'idle' | 'listening' | 'processing' | 'speaking' | 'error';

export function useWebSocket(sessionId: string, onPlaybackComplete?: () => void) {
  // Locking the WS state securely
  const wsRef = useRef<WebSocket | null>(null);
  
  // Independent playback structures separated by strict 24kHz sampler constraints
  const playbackCtxRef = useRef<AudioContext | null>(null);
  const playbackQueueRef = useRef<GaplessPlaybackQueue | null>(null);
  
  const [voiceState, setVoiceState] = useState<VoiceState>('idle');

  const connect = useCallback(() => {
    return new Promise<void>((resolve, reject) => {
      if (!sessionId) return reject(new Error("No session ID"));
      if (wsRef.current && (wsRef.current.readyState === WebSocket.CONNECTING || wsRef.current.readyState === WebSocket.OPEN)) {
          return resolve(); 
      }

    const wsUrl = process.env.NEXT_PUBLIC_BACKEND_WS_URL || "ws://localhost:8000";
    const uri = `${wsUrl}/ws/voice?session_id=${sessionId}`;
    
    wsRef.current = new WebSocket(uri);
    wsRef.current.binaryType = "arraybuffer"; 

    wsRef.current.onopen = () => {
      setVoiceState('idle'); // Await user tap sequence locally
      // Prepare dedicated layout 
      if (!playbackCtxRef.current) {
        playbackCtxRef.current = new window.AudioContext({ sampleRate: 24000 });
        playbackQueueRef.current = new GaplessPlaybackQueue();
        playbackQueueRef.current.init(playbackCtxRef.current);
        
        // Link the seamless callback reverting the UI safely mapped to visual expectations
        playbackQueueRef.current.onAllPlaybackComplete = () => {
          setVoiceState('listening');
          if (onPlaybackComplete) {
            onPlaybackComplete();
          }
        };
      }
      resolve();
    };

    wsRef.current.onmessage = async (e) => {
      // Receiving raw binary Int16 PCM frames
      if (e.data instanceof ArrayBuffer) {
        if (!playbackCtxRef.current || !playbackQueueRef.current) return;
        
        // Immediately flag output feedback
        setVoiceState('speaking');

        const int16Array = new Int16Array(e.data);
        const float32Array = int16ToFloat32(int16Array);

        // Frame rendering securely utilizing our gapless layout limits
        const audioBuffer = playbackCtxRef.current.createBuffer(1, float32Array.length, 24000);
        audioBuffer.getChannelData(0).set(float32Array);

        playbackQueueRef.current.scheduleChunk(audioBuffer);
      }
    };

    wsRef.current.onerror = (e) => {
      setVoiceState('error');
      reject(new Error("WebSocket Connection failed"));
    };
    wsRef.current.onclose = () => setVoiceState('error');
    
    }); // End Promise
  }, [sessionId]);

  const send = useCallback((int16Chunk: Int16Array) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(int16Chunk.buffer);
    }
  }, []);

  const sendMuteSignal = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "mic_muted" }));
    }
  }, []);

  const reconnect = useCallback(() => {
    connect();
  }, [connect]);

  const disconnect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    if (playbackCtxRef.current) {
       playbackCtxRef.current.close();
       playbackCtxRef.current = null;
    }
  }, []);

  return { 
    send, 
    sendMuteSignal, 
    reconnect, 
    disconnect, 
    connect, 
    voiceState, 
    setVoiceState 
  };
}
