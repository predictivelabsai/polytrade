import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import App from "./RoutedApp";
import StandaloneAuthentication from "./clerk-auth";
import TemplatesPage from "./TemplatesPage";
import TrackRecordPage from "./TrackRecord";
import "./styles.css";

const root = createRoot(document.getElementById("root")!);

// /u/:token is a signed-out public page: it mounts outside Clerk entirely so
// a visitor without an account never triggers authentication. Every other
// path renders the authed workspace exactly as before.
root.render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/u/:token" element={<TrackRecordPage />} />
        <Route path="/templates" element={<TemplatesPage />} />
        <Route path="*" element={<StandaloneAuthentication><App /></StandaloneAuthentication>} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);