import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import "./index.css";
import App from "./App";
import { connect, useStore } from "./store";

connect();
// handy for debugging from the browser console
(window as unknown as { __botbattle: typeof useStore }).__botbattle = useStore;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
