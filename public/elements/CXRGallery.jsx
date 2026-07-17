// public/elements/CXRGallery.jsx
// Example gallery for the CXR Agent — a paged carousel that shows 3 cards at a
// time (more are reachable via the arrows, which wrap around at either end).
//
// Theme handling: an earlier version referenced Chainlit's shadcn tokens
// (hsl(var(--card)) etc.), but those didn't resolve in the custom-element
// context, so the gallery stayed dark in light mode. Instead we detect the
// active theme directly from the DOM and set our own namespaced --cxr-* CSS
// variables on the root; a MutationObserver keeps them in sync when the theme
// is toggled. Every child reads var(--cxr-*, <dark fallback>), so the worst
// case is the previous dark look. The thumbnail backdrop stays black on
// purpose: radiographs are grayscale-on-black and read poorly on a light strip.
//
// Sizing/layout uses inline styles (Chainlit's custom-element renderer doesn't
// reliably emit Tailwind sizing utilities). Clicking a card:
//   1. fires `arm_example` so the backend uses that image on the next send;
//   2. prefills the case's prompt into the composer and focuses it;
//   3. marks the card selected and shows an "attached to your message" banner.
// The agent runs only when the user presses send. "Clear" disarms it.
//
// Props: examples: [{ id, title, prompt, src }]
// Chainlit runtime globals: `props`, `callAction`.

export default function CXRGallery() {
  const items = (props && props.examples) || [];
  if (!items.length) return null;
  const paged = items.length > 3;

  const DARK = {
    panel: "#262626", card: "#1b1b1b",
    border: "rgba(255,255,255,0.14)", cardborder: "rgba(255,255,255,0.08)",
    fg: "#f5f5f5", muted: "rgba(245,245,245,0.6)",
    chip: "rgba(255,255,255,0.07)", chipborder: "rgba(255,255,255,0.18)",
    ring: "rgba(255,255,255,0.9)",
  };
  const LIGHT = {
    panel: "#f1f2f4", card: "#ffffff",
    border: "rgba(0,0,0,0.12)", cardborder: "rgba(0,0,0,0.08)",
    fg: "#111827", muted: "rgba(17,24,39,0.6)",
    chip: "rgba(0,0,0,0.05)", chipborder: "rgba(0,0,0,0.18)",
    ring: "rgba(0,0,0,0.72)",
  };

  const detectDark = () => {
    try {
      const de = document.documentElement;
      const b = document.body;
      // Explicit class / attribute signals first.
      for (const el of [de, b]) {
        if (!el) continue;
        if (el.classList.contains("dark")) return true;
        if (el.classList.contains("light")) return false;
        const dm = (el.getAttribute("data-theme") ||
                    el.getAttribute("data-mode") || "").toLowerCase();
        if (dm.includes("dark")) return true;
        if (dm.includes("light")) return false;
      }
      const cs = getComputedStyle(de).colorScheme || "";
      if (cs.includes("dark") && !cs.includes("light")) return true;
      if (cs.includes("light") && !cs.includes("dark")) return false;
      // Fall back to background luminance of the nearest painted ancestor.
      let node = b || de;
      let bg = "";
      for (let i = 0; node && i < 6; i++) {
        bg = getComputedStyle(node).backgroundColor;
        const m = bg && bg.match(/[\d.]+/g);
        if (m && m.length >= 3 && !(m.length >= 4 && Number(m[3]) === 0)) {
          const lum = (0.2126 * +m[0] + 0.7152 * +m[1] + 0.0722 * +m[2]) / 255;
          return lum < 0.5;
        }
        node = node.parentElement;
      }
      if (window.matchMedia)
        return window.matchMedia("(prefers-color-scheme: dark)").matches;
    } catch (e) { /* ignore */ }
    return true; // default to dark
  };

  const applyTheme = (root) => {
    const p = detectDark() ? DARK : LIGHT;
    for (const k in p) root.style.setProperty("--cxr-" + k, p[k]);
  };

  const rootRef = (el) => {
    if (!el || el.__cxrThemed) return;
    el.__cxrThemed = true;
    applyTheme(el);
    const rerun = () => applyTheme(el);
    try {
      const mo = new MutationObserver(rerun);
      mo.observe(document.documentElement, {
        attributes: true,
        attributeFilter: ["class", "style", "data-theme", "data-mode"],
      });
      if (document.body)
        mo.observe(document.body, {
          attributes: true,
          attributeFilter: ["class", "style", "data-theme", "data-mode"],
        });
    } catch (e) { /* ignore */ }
    try {
      window.matchMedia("(prefers-color-scheme: dark)")
        .addEventListener("change", rerun);
    } catch (e) { /* ignore */ }
  };

  const setComposerText = (text) => {
    const ta =
      document.querySelector("#chat-input") ||
      document.querySelector('textarea[placeholder*="essage"]') ||
      document.querySelector('[data-testid="chat-input"] textarea') ||
      document.querySelector("textarea");
    if (!ta) return;
    const desc = Object.getOwnPropertyDescriptor(
      window.HTMLTextAreaElement.prototype, "value");
    if (desc && desc.set) desc.set.call(ta, text);
    else ta.value = text;
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    ta.focus();
    ta.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const onPick = (e, ex) => {
    callAction({ name: "arm_example", payload: { example_id: ex.id } });
    setComposerText(ex.prompt || "");
    document.querySelectorAll("[data-ex-card]").forEach((el) => {
      el.style.boxShadow = "none";
    });
    e.currentTarget.style.boxShadow =
      "0 0 0 2px var(--cxr-ring, rgba(255,255,255,0.9))";
    const b = document.getElementById("cxr-attached-banner");
    const t = document.getElementById("cxr-attached-title");
    const im = document.getElementById("cxr-attached-thumb");
    if (im) im.src = ex.src;
    if (t) t.textContent = ex.title;
    if (b) b.style.display = "flex";
  };

  const onClear = () => {
    callAction({ name: "clear_example", payload: {} });
    setComposerText("");
    document.querySelectorAll("[data-ex-card]").forEach((el) => {
      el.style.boxShadow = "none";
    });
    const b = document.getElementById("cxr-attached-banner");
    if (b) b.style.display = "none";
  };

  const scroll = (dir) => {
    const t = document.getElementById("cxr-gallery-track");
    if (!t) return;
    const atEnd = t.scrollLeft + t.clientWidth >= t.scrollWidth - 4;
    const atStart = t.scrollLeft <= 4;
    if (dir > 0 && atEnd) t.scrollTo({ left: 0, behavior: "smooth" });
    else if (dir < 0 && atStart) t.scrollTo({ left: t.scrollWidth, behavior: "smooth" });
    else t.scrollBy({ left: dir * t.clientWidth, behavior: "smooth" });
  };

  const arrowStyle = {
    flex: "0 0 auto",
    height: "28px",
    width: "28px",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    borderRadius: "9999px",
    border: "1px solid var(--cxr-chipborder, rgba(255,255,255,0.18))",
    background: "var(--cxr-chip, rgba(255,255,255,0.07))",
    color: "var(--cxr-fg, #f5f5f5)",
    fontSize: "18px",
    lineHeight: 1,
    cursor: "pointer",
  };

  return (
    <div
      ref={rootRef}
      style={{
        margin: "6px 0",
        padding: "10px",
        borderRadius: "12px",
        border: "1px solid var(--cxr-border, rgba(255,255,255,0.14))",
        background: "var(--cxr-panel, #262626)",
        color: "var(--cxr-fg, #f5f5f5)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
        {paged && (
          <button type="button" aria-label="Previous"
            onClick={() => scroll(-1)} style={arrowStyle}>
            ‹
          </button>
        )}

        <div
          id="cxr-gallery-track"
          style={{
            flex: 1,
            display: "flex",
            gap: "8px",
            overflowX: "auto",
            scrollSnapType: "x mandatory",
            scrollbarWidth: "none",
            paddingBottom: "2px",
          }}
        >
          {items.map((ex) => (
            <div
              key={ex.id}
              style={{
                flex: "0 0 calc((100% - 16px) / 3)",
                scrollSnapAlign: "start",
                borderRadius: "8px",
                overflow: "hidden",
                border: "1px solid var(--cxr-cardborder, rgba(255,255,255,0.08))",
                background: "var(--cxr-card, #1b1b1b)",
              }}
            >
              <button
                type="button"
                data-ex-card
                onClick={(e) => onPick(e, ex)}
                title={`Use this case: ${ex.title}`}
                style={{
                  display: "block",
                  width: "100%",
                  padding: 0,
                  margin: 0,
                  border: "none",
                  borderRadius: "8px",
                  background: "transparent",
                  textAlign: "left",
                  cursor: "pointer",
                  color: "inherit",
                }}
              >
                <div
                  style={{
                    height: "64px",
                    width: "100%",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    overflow: "hidden",
                    background: "#000", // radiographs read best on black, any theme
                  }}
                >
                  <img
                    src={ex.src}
                    alt={ex.title}
                    loading="lazy"
                    style={{ height: "100%", width: "100%", objectFit: "contain" }}
                  />
                </div>
                <div style={{ padding: "5px 7px" }}>
                  <div
                    style={{
                      fontSize: "11px",
                      fontWeight: 600,
                      color: "var(--cxr-fg, #f5f5f5)",
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {ex.title}
                  </div>
                  <div
                    style={{
                      marginTop: "2px",
                      fontSize: "10px",
                      lineHeight: "13px",
                      height: "26px",
                      overflow: "hidden",
                      color: "var(--cxr-muted, rgba(245,245,245,0.6))",
                      display: "-webkit-box",
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: "vertical",
                    }}
                  >
                    {ex.prompt}
                  </div>
                </div>
              </button>
            </div>
          ))}
        </div>

        {paged && (
          <button type="button" aria-label="Next"
            onClick={() => scroll(1)} style={arrowStyle}>
            ›
          </button>
        )}
      </div>

      {/* "Attached" indicator — shown after a card is picked, until send/clear. */}
      <div
        id="cxr-attached-banner"
        style={{
          display: "none",
          alignItems: "center",
          gap: "8px",
          marginTop: "9px",
          padding: "6px 8px",
          borderRadius: "8px",
          border: "1px solid var(--cxr-chipborder, rgba(255,255,255,0.18))",
          background: "var(--cxr-chip, rgba(255,255,255,0.07))",
          color: "var(--cxr-fg, #f5f5f5)",
        }}
      >
        <img
          id="cxr-attached-thumb"
          alt=""
          style={{ height: "30px", width: "30px", objectFit: "cover",
                   borderRadius: "4px", background: "#000" }}
        />
        <span style={{ fontSize: "12px" }}>
          📎 Attached to your message: <strong id="cxr-attached-title"></strong>
        </span>
        <button
          type="button"
          onClick={onClear}
          style={{
            marginLeft: "auto",
            fontSize: "11px",
            padding: "2px 9px",
            borderRadius: "6px",
            border: "1px solid var(--cxr-chipborder, rgba(255,255,255,0.18))",
            background: "transparent",
            color: "var(--cxr-fg, #f5f5f5)",
            cursor: "pointer",
          }}
        >
          Clear
        </button>
      </div>
    </div>
  );
}