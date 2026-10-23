import { useEffect, useState } from "react";

/** Report a real wait, without inventing progress or a completion estimate. */
export default function PendingFeedback({
  active,
  message = "仍在等待服务确认。请保留本页；超时后先核对状态，再重试原操作。",
}: {
  active: boolean;
  message?: string;
}) {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    setVisible(false);
    if (!active) return;
    const timer = window.setTimeout(() => setVisible(true), 10000);
    return () => window.clearTimeout(timer);
  }, [active]);
  return active && visible ? (
    <p className="info-banner" role="status" aria-live="polite">
      {message}
    </p>
  ) : null;
}
