export type Entitlement = {
  customerId: string;
  level: "free" | "premium" | "lifetime";
  premium: boolean;
  lifetime: boolean;
  source: "db" | "cache";
  updatedAt: string;
};
