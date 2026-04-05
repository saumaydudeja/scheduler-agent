import { useEffect, useState, useRef } from "react";

export type StatusEvent = {
  id: string;
  message: string;
  module: string;
  status: string;
  timestamp: string;
  event?: Record<string, any>;
  calendar_link?: string;
};

export function useStatusStream(sessionId: string) {
  const [statuses, setStatuses] = useState<StatusEvent[]>([]);
  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!sessionId) return;
    
    // Connect to SSE stream securely linked via injected sessionId
    const backendUrl = process.env.NEXT_PUBLIC_BACKEND_HTTP_URL || "http://localhost:8000";
    const url = `${backendUrl}/stream/status/${sessionId}`;
    
    eventSourceRef.current = new EventSource(url);
    
    eventSourceRef.current.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data) as StatusEvent;
        setStatuses((prev) => [...prev, data]);
      } catch (err) {
        console.error("Failed to parse SSE packet:", err);
      }
    };
    
    eventSourceRef.current.onerror = () => {
      console.warn("SSE connection error");
    };

    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, [sessionId]);

  return { statuses };
}
