import React from "react";
import ReactDOM from "react-dom/client";

import App from "./App";
import ErrorBoundary from "./components/ui/ErrorBoundary";
import "./styles/index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {/* Outside App, so a throw from any part of the tree — the layout included — still
        lands on something readable with a way out. */}
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
);
