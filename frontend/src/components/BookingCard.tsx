import React from 'react';
import styles from './BookingCard.module.css';

type EventData = {
  id?: string;
  summary?: string;
  description?: string;
  start?: { dateTime: string };
  end?: { dateTime: string };
};

type Props = {
  event: EventData;
  calendarLink?: string;
};

export default function BookingCard({ event, calendarLink }: Props) {
  if (!event) return null;
  
  return (
    <div className={styles.card}>
      <div className={styles.header}>
        <div className={styles.icon}>📅</div>
        <h3>Meeting Booked!</h3>
      </div>
      {calendarLink && (
        <a href={calendarLink} target="_blank" rel="noopener noreferrer" className={styles.btn}>
          View on Google Calendar
        </a>
      )}
    </div>
  );
}
