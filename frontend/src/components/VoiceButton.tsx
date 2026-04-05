import React from 'react';
import styles from './VoiceButton.module.css';
import type { VoiceState } from '@/hooks/useWebSocket';

type VoiceButtonProps = {
  state: VoiceState;
  onTap: () => void;
};

export default function VoiceButton({ state, onTap }: VoiceButtonProps) {
  // Map internal constraints to clean semantic icons mapping
  const getIcon = () => {
    switch (state) {
      case 'idle': return '🎤';
      case 'listening': return '🎙️';
      case 'processing': return '⏳';
      case 'speaking': return '🔊';
      case 'error': return '🔄';
      default: return '🎤';
    }
  };

  const getLabel = () => {
    switch (state) {
      case 'idle': return 'Tap to Speak';
      case 'listening': return 'Listening...';
      case 'processing': return 'Thinking...';
      case 'speaking': return 'Speaking';
      case 'error': return 'Connection Error (Tap to Reconnect)';
      default: return '';
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
      <button 
        className={`${styles.button} ${styles[state]}`} 
        onClick={onTap}
        aria-label={getLabel()}
      >
        {getIcon()}
      </button>
      <div className={styles.label}>
        {getLabel()}
      </div>
    </div>
  );
}
