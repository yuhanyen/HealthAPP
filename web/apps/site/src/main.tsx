import "@healthapp/ui/styles.css";

import { I18nProvider, Spinner } from "@healthapp/ui";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <I18nProvider loading={<Spinner />}>
      <App />
    </I18nProvider>
  </StrictMode>,
);
