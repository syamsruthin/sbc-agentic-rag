import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { warmUp } from "@sbc/chat-ui";

import { App } from "./App";
import "./index.css";

warmUp(import.meta.env.VITE_API_BASE_URL || "/api");
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
