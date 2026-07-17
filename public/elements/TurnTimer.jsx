// public/elements/TurnTimer.jsx
// A live, counting-up timer shown while a model turn is in progress.
//
// It stops by EITHER of two mechanisms (so it halts regardless of whether
// Chainlit unmounts removed elements on a message update):
//   1. the element is removed from the DOM (el.isConnected === false), or
//   2. the app mounts a <TimerStop> element that sets window.__cxrStop[id] = true.
// The final value is left frozen on screen when it stops.
//
// The start time is keyed on window by a per-turn id so a React remount during a
// message update does not reset the count.
//
// Props: { id: string }   // unique per turn

export default function TurnTimer() {
  const id = (props && props.id) || "default";

  const fmt = (ms) => {
    const s = Math.floor(ms / 1000);
    const m = Math.floor(s / 60);
    return (m > 0 ? m + "m " : "") + (s % 60) + "s";
  };

  const attach = (el) => {
    if (!el || el._cxrTimerStarted) return;
    el._cxrTimerStarted = true;
    window.__cxrTimers = window.__cxrTimers || {};
    if (!window.__cxrTimers[id]) window.__cxrTimers[id] = Date.now();
    const t0 = window.__cxrTimers[id];
    const render = () => { el.textContent = fmt(Date.now() - t0); };
    render();
    const h = setInterval(() => {
      const stopped = window.__cxrStop && window.__cxrStop[id];
      if (!el.isConnected || stopped) {
        render();                 // freeze on the final value
        clearInterval(h);
        delete window.__cxrTimers[id];
        return;
      }
      render();
    }, 250);
  };

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "6px",
        fontSize: "12px",
        opacity: 0.75,
        padding: "2px 0",
      }}
    >
      <span
        style={{
          width: "7px",
          height: "7px",
          borderRadius: "9999px",
          background: "currentColor",
          display: "inline-block",
          animation: "cxrTimerPulse 1s ease-in-out infinite",
        }}
      />
      <span>⏱ <span ref={attach}>0s</span></span>
      <style>{`@keyframes cxrTimerPulse{0%,100%{opacity:.25}50%{opacity:1}}`}</style>
    </span>
  );
}