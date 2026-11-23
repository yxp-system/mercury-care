# Contributing

Use Python 3.12, Node.js 24 and the committed `uv.lock` / `package-lock.json`. Start from the documented synthetic demo; use independent test databases.

Explain the user-visible problem, final behavior and verification in each PR. Add meaningful tests for changes to authorization, money, stock, versioning, idempotency or recovery. Preserve the eight after-sales branches and the five-role boundary.

Before submitting, run the checks in README. Changes involving PostgreSQL must pass PostgreSQL CI, including non-owner RLS checks. Never attach real credentials, customer information or full model prompts containing personal data to issues.

PRs are welcome for the explicitly tracked integration and production-hardening gaps. An adapter interface or screenshot alone is not integration evidence.
