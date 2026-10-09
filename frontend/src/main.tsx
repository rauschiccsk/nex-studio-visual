import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { startInstallCapture } from "./pwa/installPrompt";
import { registerServiceWorker } from "./pwa/registerServiceWorker";
import "./index.css";

// DEV-21: the FIRST statement on purpose. The browser offers installation exactly once, at a moment of its own
// choosing; an offer nobody captured is gone for good and the install button would have nothing to trigger.
startInstallCapture();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

// DEV-21: what makes the browser treat the cockpit as installable. It never serves the app from a cache
// (public/sw.js) — the cockpit is deployed often and an old version must have nowhere to come from.
registerServiceWorker();
