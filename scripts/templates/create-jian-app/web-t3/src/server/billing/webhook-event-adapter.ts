import { db } from "~/server/db";

export async function isStripeWebhookEventProcessed(eventId: string): Promise<boolean> {
  const existing = await db.stripeWebhookEvent.findUnique({
    where: { eventId },
    select: { id: true },
  });

  return Boolean(existing);
}

export async function recordStripeWebhookEvent(input: {
  eventId: string;
  eventType: string;
  customerId: string | null;
}): Promise<void> {
  await db.stripeWebhookEvent.create({
    data: {
      eventId: input.eventId,
      eventType: input.eventType,
      customerId: input.customerId,
    },
  });
}
