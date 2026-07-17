// public/elements/NarrationPreview.jsx
// A compact, collapsible preview of the NV-Reason-CXR-3B narration ("thinking").
// Collapsed it shows a one-line teaser; click to expand the full reasoning.
// Uses a native <details> element — no component state, no exotic CSS needed.
//
// Props: { preview: string, text: string }

export default function NarrationPreview() {
  const text = (props && props.text) || "";
  const preview = (props && props.preview) || text.slice(0, 160);
  if (!text) return null;

  return (
    <details className="my-1 rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm">
      <summary className="cursor-pointer select-none">
        <span className="text-xs font-semibold uppercase tracking-wide opacity-70">
          Model narration
        </span>
        <span className="ml-2 opacity-60">{preview}</span>
      </summary>
      <div className="mt-2 whitespace-pre-wrap border-t border-white/10 pt-2 leading-relaxed opacity-90">
        {text}
      </div>
    </details>
  );
}