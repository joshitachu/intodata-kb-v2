import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { createQueryClient } from "./lib/queryClient";
import "./index.css";

// Query defaults (including the deliberate absence of a global
// `refetchInterval`) live in `lib/queryClient.ts` so they are documented and
// unit-tested in one place rather than inlined here.
const queryClient = createQueryClient();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>
);
