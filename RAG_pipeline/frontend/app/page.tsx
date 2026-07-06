import { Suspense } from "react";
import { ChatLayout } from "@/components/ChatLayout";

export default function HomePage() {
  return (
    <Suspense fallback={null}>
      <ChatLayout />
    </Suspense>
  );
}
