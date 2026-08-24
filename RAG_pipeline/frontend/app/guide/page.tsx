import type { Metadata } from "next";
import { ProductGuide } from "@/components/guide/ProductGuide";

export const metadata: Metadata = {
  title: "Documentation",
  description: "Nexus product documentation, workflows, roles, and page reference.",
};

export default function GuidePage() {
  return <ProductGuide />;
}
