"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { AuthProvider } from "@/components/AuthProvider";
import { OnboardingProvider } from "@/contexts/OnboardingContext";
import { TenantProvider } from "@/contexts/TenantContext";

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
