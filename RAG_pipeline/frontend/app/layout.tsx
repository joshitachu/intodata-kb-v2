import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { Providers } from "./providers";
import { Layout } from "@/components/Layout";

const inter = Inter({ subsets: ["latin"] });

const tenantName = process.env.NEXT_PUBLIC_TENANT_NAME ?? "Nexus";

export const metadata: Metadata = {
  title: {
    default: tenantName,
    template: `%s | ${tenantName}`,
  },
  description: "RAG-powered knowledge base for company documents",
  icons: {
    icon: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className={inter.className}>
        <Providers>
          <Layout>{children}</Layout>
        </Providers>
      </body>
    </html>
  );
}
