"use client";

import { useRef, useEffect, useState } from 'react';
import styles from './page.module.css';
import VoiceButton from '@/components/VoiceButton';
import TranscriptPane from '@/components/TranscriptPane';
import StatusIndicator from '@/components/StatusIndicator';
import BookingCard from '@/components/BookingCard';

import { useWebSocket } from '@/hooks/useWebSocket';
import { useStatusStream } from '@/hooks/useStatusStream';
import { useVoiceCapture } from '@/hooks/useVoiceCapture';

export default function Home() {
  const sessionIdRef = useRef<string | null>(null);
  const [isReady, setIsReady] = useState(false);

  useEffect(() => {
    if (!sessionIdRef.current) {
      // Must securely generate UUID exactly once adhering to the global stability constraint
      sessionIdRef.current = crypto.randomUUID();
      setIsReady(true);
    }
  }, []);

  const sessionId = sessionIdRef.current || '';

  // Hooks map flawlessly ensuring separated semantics
  const { startCapture, stopCapture, muteCapture, unmuteCapture } = useVoiceCapture({ onAudioChunk: (chunk) => send(chunk) });
  const { send, sendMuteSignal, reconnect, disconnect, connect, voiceState, setVoiceState } = useWebSocket(sessionId, unmuteCapture);
  const { statuses } = useStatusStream(sessionId);

  const handleTap = async () => {
    if (voiceState === 'error') {
      reconnect();
      return;
    }
    
    // Tap to begin or re-engage interaction seamlessly
    if (voiceState === 'idle') {
      try {
        await connect();
        await startCapture();
        unmuteCapture(); 
        setVoiceState('listening');
      } catch (e) {
        console.error("Initiation sequence failed.", e);
      }
    } 
    // Tap to explicitly conclude speech, defeating generic VAD latency
    else if (voiceState === 'listening') {
      muteCapture();
      sendMuteSignal();
      setVoiceState('processing');
    }
  };

  // Block SSR hydration gaps
  if (!isReady) return null;

  const latestStatusObj = statuses.length > 0 ? statuses[statuses.length - 1] : null;
  const currentMessage = latestStatusObj?.message || '';
  
  // Conditionally extract booking validation seamlessly
  const bookingCompletion = statuses.find(s => s.module === 'booking' && s.event);

  return (
    <main className={styles.main}>
      <header className={styles.header}>
        <h1>Smart Scheduler</h1>
        <p>Dynamic AI Voice Agent Workspace</p>
      </header>

      <StatusIndicator currentMessage={currentMessage} />

      <div className={styles.voiceControls}>
        <VoiceButton state={voiceState} onTap={handleTap} />
      </div>

      {bookingCompletion && bookingCompletion.event && (
        <BookingCard 
          event={bookingCompletion.event} 
          calendarLink={bookingCompletion.calendar_link} 
        />
      )}

      <TranscriptPane statuses={statuses} />
    </main>
  );
}
