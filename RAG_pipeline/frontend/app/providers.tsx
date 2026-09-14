"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { AuthProvider } from "@/components/AuthProvider";
import { OnboardingProvider } from "@/contexts/OnboardingContext";
import { TenantProvider } from "@/contexts/TenantContext";
import { retryTransientOnly } from "@/lib/authedFetch";

interface ProvidersProps {
  children: ReactNode;
}

export function Providers({ children }: ProvidersProps) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 60 * 1000, // 1 minute
            refetchOnWindowFocus: false,
            // An expired access token is resolved by rotating it — which
            // `lib/authedFetch.ts` does inline, once, before the query ever
            // sees a failure — not by repeating the request with the same
            // dead credential. Same for any other deterministic verdict
            // (403/404/409). Transient failures keep the library's backoff.
            retry: retryTransientOnly,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <TenantProvider>
        <AuthProvider>
          <OnboardingProvider>{children}</OnboardingProvider>
        </AuthProvider>
      </TenantProvider>
    </QueryClientProvider>
  );
}
