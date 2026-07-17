// public/elements/OverlayFrame.jsx
// Renders a self-contained overlay HTML document inside the right-hand
// ElementSidebar (and inline). The whole document — markup, styles, and the
// finding-toggle / opacity scripts — is injected via an iframe `srcDoc`.
//
// Why an iframe: the overlay is a full <html> document with its own <script>s
// and CSS. An iframe isolates it from the chat page (no style bleed) and lets
// its scripts run. The two things that make the panel render *blank* otherwise:
//   1. no explicit height  -> the frame collapses to 0px inside the flex panel;
//   2. no sandbox allow-scripts -> the interactive controls never initialise.
// Both are handled below.
//
// Props: { html: string, title?: string }

export default function OverlayFrame() {
  const html = (props && props.html) || "";
  const title = (props && props.title) || "overlay";

  if (!html) {
    return (
      <div style={{ padding: "1rem", opacity: 0.6 }}>
        No overlay to display.
      </div>
    );
  }

  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        minHeight: "80vh",
        display: "flex",
      }}
    >
      <iframe
        title={title}
        srcDoc={html}
        // allow-scripts: run the overlay's toggle/opacity JS.
        // allow-same-origin: let that JS read its own DOM/inline data.
        sandbox="allow-scripts allow-same-origin allow-popups"
        style={{
          flex: 1,
          width: "100%",
          height: "100%",
          minHeight: "80vh",
          border: "none",
          borderRadius: "8px",
          background: "#0b0b0b",
        }}
      />
    </div>
  );
}