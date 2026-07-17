// public/elements/TimerStop.jsx
// Invisible element the app mounts when a model turn ends. On mount it sets a
// window stop-flag that any running <TurnTimer> with the same id polls, so the
// timer halts even if Chainlit does not unmount the timer element on update.
//
// Props: { id: string }

export default function TimerStop() {
  const id = (props && props.id) || "default";
  const mark = (el) => {
    if (!el) return;
    window.__cxrStop = window.__cxrStop || {};
    window.__cxrStop[id] = true;
  };
  return <span ref={mark} style={{ display: "none" }} aria-hidden="true" />;
}