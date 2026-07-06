"use client";

import { useEffect, useState } from "react";

export type VisualViewportInfo = {
  keyboardHeight: number;
  viewportHeight: number;
};

const ZERO: VisualViewportInfo = { keyboardHeight: 0, viewportHeight: 0 };

export function useVisualViewport(): VisualViewportInfo {
  const [info, setInfo] = useState<VisualViewportInfo>(ZERO);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!window.visualViewport) return;

    const layoutHeight = window.visualViewport.height;
    let frame = 0;

    function update() {
      if (frame) cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const current = window.visualViewport;
        if (!current) return;
        const keyboardHeight = Math.max(0, layoutHeight - current.height);
        setInfo({ keyboardHeight, viewportHeight: current.height });
      });
    }

    window.visualViewport.addEventListener("resize", update);
    window.visualViewport.addEventListener("scroll", update);
    update();

    return () => {
      if (frame) cancelAnimationFrame(frame);
      window.visualViewport?.removeEventListener("resize", update);
      window.visualViewport?.removeEventListener("scroll", update);
    };
  }, []);

  return info;
}
