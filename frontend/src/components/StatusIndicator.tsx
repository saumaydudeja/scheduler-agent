import React from 'react';
import styles from './StatusIndicator.module.css';

type StatusIndicatorProps = {
  currentMessage: string;
};

export default function StatusIndicator({ currentMessage }: StatusIndicatorProps) {
  if (!currentMessage) return null;
  return (
    <div className={styles.container}>
      <div className={styles.dot} />
      <span>{currentMessage}</span>
    </div>
  );
}
