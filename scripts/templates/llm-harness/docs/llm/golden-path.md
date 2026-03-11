# Golden Path

## Layering and Import Direction

- `src/components/ui/**` is presentational-only and cannot import `src/server/**` or `src/infra/**`.
- `src/components/navigation/main-nav.tsx` is a server-rendered navigation component and is explicitly listed in `serverUiFiles`.
- Client providers must not import server modules directly; use shared type facades in `src/types/**` for type-only contracts.
- Cross-feature imports must go through public entrypoints (`index.ts` / `public.ts`).

## Unit Test Policy

- Unit tests are co-located with the source file they validate.
- A changed source module must have a co-located unit test file:
  - `foo.ts` -> `foo.test.ts` or `foo.spec.ts`
  - `foo.tsx` -> `foo.test.tsx` or `foo.spec.tsx`

## Integration/E2E Policy

- Integration tests live under `tests/**`.
- Desktop e2e tests live under `desktop/e2e/**`.
- Integration/e2e tests should include `e2e` or `integration` in path or filename.

## Maintainability Ratchet

- Maintainability rules run at warning severity.
- Existing legacy violations are tracked in `.lint-debt.json`.
- New or worsened violations in changed files fail CI.

## Definition of Done

- `pnpm run verify` (full quality gate)
- `pnpm run lint` is ESLint-only and does not include structure/tests/context checks
