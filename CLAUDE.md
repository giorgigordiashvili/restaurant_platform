# Restaurant Platform — Django backend

DRF backend that powers `aimenu.ge` (customers), `pos.aimenu.ge` (staff POS),
and the Django admin at `admin.aimenu.ge`.

- **Framework:** Django 4.2, DRF, drf-spectacular, SimpleJWT, django-parler for translations.
- **Tenant isolation:** `apps/core/middleware/tenant.py` reads `X-Restaurant`
  header (or subdomain) into `request.restaurant`. Dashboard views are gated
  with `@require_restaurant` + `IsTenantManager`.
- **Translations:** many models use parler's `TranslatableModel`. Serializers
  use `safe_translation_getter("name", any_language=True)` when reading.

## Environment

- `.venv/` — Python virtualenv. Activate with `source .venv/bin/activate`.
- `docker-compose.yml` — db + web + redis + flower + minio. Port 5432 may
  collide with other local postgres instances; change the host port in
  `docker-compose.override.yml` (gitignored) or stop the other one.
- `DJANGO_SETTINGS_MODULE=config.settings.dev` for local, `prod` on DO.

## Deploying

- DigitalOcean app id: `3076a3a7-33de-4587-949c-1cf87ac5fbed` (Telos team).
- Spec has `deploy_on_push: true` on `main` → a push auto-rolls out. DO runs
  the `migrate` pre-deploy job with the new image before swapping traffic.
  If that job fails, DO **auto-rolls-back** to the previous active deploy.
- Tail a failed deploy:
  ```bash
  doctl apps logs 3076a3a7-33de-4587-949c-1cf87ac5fbed --deployment <id> --type deploy
  doctl apps logs 3076a3a7-33de-4587-949c-1cf87ac5fbed --deployment <id> --type build
  ```
- Direct push to `main` is blocked by the Claude Code sandbox unless the
  user explicitly says "merge it". Create a branch, push it, wait for the
  go-ahead.
- **The user's preferred workflow is to push directly to `main`** once
  they've approved a change. Don't silo every small edit in its own PR if
  it's already been discussed and approved — commit directly to `main`
  and push. Branch + PR is only the default when the user hasn't said
  "merge it" yet, or for large/risky changes that deserve review.

## CI checks — run BEFORE pushing

CI (`.github/workflows/ci.yml`) runs these gates. A push that trips any of
them round-trips through a failed Actions run, so run them locally first:

```bash
source .venv/bin/activate
pip install --upgrade 'black' 'isort'   # pick up the latest; CI pins `black>=24.0` so it resolves latest
black --check apps/ tests/ config/
isort --check-only apps/ tests/ config/
python manage.py check
```

**Version matters for both black AND isort.** `requirements/dev.txt` pins
`black>=24.0` / `isort>=5.0`, which pip resolves to the latest major
(currently `black==26.x`, `isort==6.x`). Older locals accept files that
newer CI flags. When CI says "would reformat X.py" but your local check
passes, upgrade:

```bash
pip install --upgrade black isort && black --version && isort --version
```

If a linter reformats files owned by other PRs (pre-existing drift under a
newer major), fix them in the same PR — there's no way to get CI green
without touching them.

## Known CI drift on `main`

As of 2026-04-21 the **Test** job on `main` has been red for weeks due to
a missing MinIO service container in GitHub Actions (boto3 fails with
`NameResolutionError` against `minio:9000`). A handful of individual
tests (`tests/core/test_admin.py::TestNestedTenantField`, etc.) are also
pre-existing. Don't feel obligated to fix these in an unrelated PR — open
a separate infra PR for the MinIO fixture or mock `DEFAULT_FILE_STORAGE`
in tests. Merges are currently going in with the Test job red; the Lint
+ Security jobs are the real gates.

## CRITICAL: migration gotcha

`makemigrations <app>` scans ALL installed apps, and several of the
translation-related models have permanent drift (Meta options on
`menucategorytranslation`, `citytranslation`, etc.). Running it will emit
migrations for those apps too, and the `dependencies` list of your new
migration will point at those freshly-generated parents.

If you delete the unrelated migrations (because you didn't intend to commit
them), your new migration's `dependencies = [...]` still references them and
the deploy fails with `NodeNotFoundError`.

**Safe workflow for a new app:**

1. `source .venv/bin/activate`
2. `python manage.py makemigrations <newapp>`
3. `git status` — note any unrelated migrations that got created.
4. Open `apps/<newapp>/migrations/0001_initial.py` and **rewrite the
   `dependencies` list** to point at the last migrations on `origin/main`
   (check `ls apps/<other>/migrations/`).
5. Delete the unrelated migrations that shouldn't ship with this PR.
6. Verify the graph is consistent:
   ```bash
   DATABASE_URL=sqlite:///t.db python manage.py migrate --plan | grep -i '<newapp>\|error'
   rm -f t.db
   ```
   `--plan` just confirms the graph; actual apply against sqlite may fail on
   unrelated pg-specific schema. For a real apply, spin up the docker db and
   run `migrate` against it.

`manage.py check` does **NOT** catch this.

## API schema + regeneration

drf-spectacular serves the spec at `/api/schema/`. Downstream clients
(`restaurant-frontend`, `aimenu-pos`) regenerate via `@gordela/api-generator`
(`npm run generate:api`) from `.env` vars.

**Always add `@extend_schema(request=<YourSerializer>)`** on APIViews that
read `request.data`. Without it, the generator emits a no-arg function
(`Promise<any>`) which is useless — downstream clients have to hand-write a
wrapper. Check `apps/loyalty/views.py` for the pattern.

Tags matter for grouping: `@extend_schema(tags=["Dashboard - Loyalty"])` etc.

## Auth endpoints

`apps/accounts/urls.py`:

- `POST /api/v1/auth/login/` — JWT pair (NOT `/token/`; that's a common
  false friend).
- `POST /api/v1/auth/token/refresh/` — refresh the access token.
- `POST /api/v1/auth/register/` — new customer signup.

## CORS

`config/settings/base.py` defines `CORS_ALLOW_HEADERS`. **Every custom
header** the frontends send must be in that list or the browser preflight
rejects the request with a generic CORS error.

Current list includes `x-restaurant` (POS tenant-scope). If you add any more
custom headers, add them here too.

`config/settings/prod.py` allowlist regex `^https://.*\.aimenu\.ge$` covers
`pos.aimenu.ge` and `aimenu.ge`. DO's default `*.ondigitalocean.app` URLs are
not covered — users must use the custom domain.

## Multi-tenant admin patterns

Use `apps/core/admin.py::TenantAwareModelAdmin` as the base for any
restaurant-scoped admin. Set `tenant_field = "restaurant"` (or
`"program__restaurant"` etc). Staff see only their own restaurant's rows;
superadmins see all and can simulate a restaurant via
`/admin/simulate-restaurant/`.

## Signals over view mutation

When something should happen on a status change (e.g. loyalty punches on
order completion), prefer `@receiver(post_save, sender=Order)` in an app's
`signals.py` connected in `apps.py::ready()`. Keeps business logic out of
the view layer.

Examples: `apps/loyalty/signals.py`, `apps/accounts/signals.py`.

## Local DB tips

- If port 5432 is taken (other projects' docker compose), either change the
  exposed port in docker-compose.override.yml or run the tests against
  sqlite for a quick smoke check:
  ```
  DATABASE_URL=sqlite:///t.db python manage.py test apps.<newapp>
  ```
- For pg-specific tests use `docker compose up -d db` then
  `DATABASE_URL=postgres://postgres:postgres@localhost:<port>/postgres`.

## Common failure modes seen in production

- **Migration NodeNotFoundError** — see the migration gotcha above.
- **TypeError: Cannot filter a query once a slice has been taken** —
  `[:20]` inside a `get_queryset()` breaks DRF's `PageNumberPagination.count()`.
  Drop the slice, use `page_size` instead.
- **permission_denied "You must be a staff member of this restaurant"** —
  The logged-in user isn't `Restaurant.owner` and has no active
  `StaffMember` row. Fix via Django admin `/admin/tenants/restaurant/` or
  `/admin/staff/staffmember/`.
