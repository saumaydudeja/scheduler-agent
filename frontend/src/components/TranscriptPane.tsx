import React, { useEffect, useRef } from 'react';
import styles from './TranscriptPane.module.css';
import type { StatusEvent } from '@/hooks/useStatusStream';

type TranscriptPaneProps = {
  statuses: StatusEvent[];
};

export default function TranscriptPane({ statuses }: TranscriptPaneProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [statuses]);

  if (statuses.length === 0) {
    return (
      <div className={styles.transcriptContainer}>
        <div className={styles.emptyState}>Event logs will appear here...</div>
      </div>
    );
  }

  return (
    <div className={styles.transcriptContainer} ref={containerRef}>
      {statuses.map((status, idx) => {
        // Only rendering explicit backend status markers per design requirements
        // Avoid mapping speech-to-text here since SSE sends task traces natively 
        return (
          <div key={idx} className={styles.logEntry}>
            <span className={styles.timestamp}>
              {new Date(status.timestamp).toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            </span>
            <span className={styles.module}>[{status.module}]</span>
            <span className={styles.message}>{status.message}</span>
          </div>
        );
      })}
    </div>
  );
}
