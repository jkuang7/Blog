import { createTRPCRouter, protectedProcedure, publicProcedure } from "~/server/api/trpc";

export const userRouter = createTRPCRouter({
  viewer: publicProcedure.query(({ ctx }) => ctx.auth ?? null),
  me: protectedProcedure.query(({ ctx }) => ctx.auth),
});
