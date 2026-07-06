"use client";

import { useEffect, useState } from "react";

export type Viewport = {
  isMobile: boolean;
  isTablet: boolean;
  isDesktop: boolean;
};

const MOBILE_BREAKPOINT = 640;
const DESKTOP_BREAKPOINT = 1024;

const TABLET_QUERY = `(min-width: ${MOBILE_BREAKPOINT}px)`;
const DESKTOP_QUERY = `(min-width: ${DESKTOP_BREAKPOINT}px)`;

function computeViewport(width: number): Viewport {
  if (width < MOBILE_BREAKPOINT) {
    return { isMobile: true, isTablet: false, isDesktop: false };
  }
  if (width < DESKTOP_BREAKPOINT) {
    return { isMobile: false, isTablet: true, isDesktop: false };
  }
  return { isMobile: false, isTablet: false, isDesktop: true };
}

const DESKTOP_DEFAULT: Viewport = {
  isMobile: false,
  isTablet: false,
  isDesktop: true,
};

export function useViewport(): Viewport {
  const [viewport, setViewport] = useState<Viewport>(() => {
    if (typeof window === "undefined") return DESKTOP_DEFAULT;
    return computeViewport(window.innerWidth);
  });

  useEffect(() => {
    if (typeof window === "undefined") return;

    function update() {
      setViewport(computeViewport(window.innerWidth));
    }

    const tabletMql = window.matchMedia(TABLET_QUERY);
    const desktopMql = window.matchMedia(DESKTOP_QUERY);
    tabletMql.addEventListener("change", update);
    desktopMql.addEventListener("change", update);
    update();

    return () => {
      tabletMql.removeEventListener("change", update);
      desktopMql.removeEventListener("change", update);
    };
  }, []);

  return viewport;
}
