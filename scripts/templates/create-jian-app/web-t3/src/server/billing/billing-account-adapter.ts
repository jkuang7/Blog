import { db } from "~/server/db";

export async function findStripeCustomerIdForUser(userId: string): Promise<string | null> {
  const account = await db.billingAccount.findUnique({
    where: { userId },
    select: { stripeCustomerId: true },
  });

  return account?.stripeCustomerId ?? null;
}

export async function upsertStripeCustomerIdForUser(input: {
  userId: string;
  stripeCustomerId: string;
}): Promise<void> {
  await db.billingAccount.upsert({
    where: { userId: input.userId },
    create: {
      userId: input.userId,
      stripeCustomerId: input.stripeCustomerId,
    },
    update: {
      stripeCustomerId: input.stripeCustomerId,
    },
  });
}
