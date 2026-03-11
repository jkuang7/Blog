import { getCurrentUserEntitlement } from "~/server/billing/entitlements";
import { createTRPCRouter, protectedProcedure } from "~/server/api/trpc";

export const billingRouter = createTRPCRouter({
  entitlement: protectedProcedure.query(({ ctx }) => getCurrentUserEntitlement(ctx.auth.id)),
});
