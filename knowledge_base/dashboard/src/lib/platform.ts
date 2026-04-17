export interface PlatformConfig {
  label: string;
  term: string;
  termPlural: string;
  badgeClasses: string;
  accentColor: string;
}

const platforms: Record<string, PlatformConfig> = {
  talend: {
    label: "Talend",
    term: "Job",
    termPlural: "Jobs",
    badgeClasses: "platform-tag border-[#4285F4] text-[#4285F4]/80",
    accentColor: "border-l-[#4285F4]",
  },
  workato: {
    label: "Workato",
    term: "Recipe",
    termPlural: "Recipes",
    badgeClasses: "platform-tag border-[#F97316] text-[#F97316]/80",
    accentColor: "border-l-[#F97316]",
  },
  lobster: {
    label: "Lobster",
    term: "Profile",
    termPlural: "Profiles",
    badgeClasses: "platform-tag border-[#10B981] text-[#10B981]/80",
    accentColor: "border-l-[#10B981]",
  },
  generic: {
    label: "Generic",
    term: "Component",
    termPlural: "Components",
    badgeClasses: "platform-tag border-[#64748B] text-[#64748B]/80",
    accentColor: "border-l-[#64748B]",
  },
};

const fallback: PlatformConfig = {
  label: "Unknown",
  term: "Item",
  termPlural: "Items",
  badgeClasses: "platform-tag border-[#64748B] text-[#64748B]/80",
  accentColor: "border-l-[#64748B]",
};

export function getPlatform(platform: string): PlatformConfig {
  return platforms[platform] ?? fallback;
}
