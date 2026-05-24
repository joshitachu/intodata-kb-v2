import { z } from "zod";

export const TenantEnvironmentSchema = z.enum([
  "production",
  "staging",
  "development",
  "preview",
]);
export type TenantEnvironment = z.infer<typeof TenantEnvironmentSchema>;

export const TenantConfigSchema = z.object({
  name: z.string(),
  slug: z.string(),
  environment: z.string(),
});
export type TenantConfig = z.infer<typeof TenantConfigSchema>;

export const FALLBACK_TENANT: TenantConfig = {
  name: "Nexus",
  slug: "nexus",
  environment: "development",
};
