# Deploy RAG nutrition assistant to Vercel

Vercel serves `web/`. Supabase runs Auth, vector retrieval, and the
`nutrition-chat` Edge Function. No Python process is needed on Vercel.

## 1. Public frontend settings

Add `SUPABASE_PUBLISHABLE_KEY` (or legacy `SUPABASE_ANON_KEY`) to local `.env`,
alongside `SUPABASE_URL`. Then run from the project root:

```powershell
.notebook-runtime/python.exe configure_web.py
```

The generator writes only the project URL and browser-safe key to
`web/config.js`. It rejects service-role/secret keys and never falls back to
`SUPABASE_KEY`. Public configuration may be committed. This static site does
not automatically read Vercel environment variables.

Preview locally (serve only `web`, never the project containing `.env`):

```powershell
.notebook-runtime/python.exe -m http.server 8000 --bind 127.0.0.1 --directory web
```

Open http://127.0.0.1:8000. If CORS restrictions are enabled, add this exact
origin to the backend's `ALLOWED_ORIGINS` while testing.

## 2. Backend prerequisites

Use the existing Supabase project containing the nutrition embeddings.
If not already applied, run
`supabase/migrations/20260920000100_nutrition_daily_limit.sql` in SQL Editor.
Confirm the `match_chunks_bge` RPC exists and returns document passages.

In Supabase Edge Function secrets, configure `HF_TOKEN` and `GROQ_API_KEY`.
Hosted functions receive `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`
automatically. Never copy these secrets to `web/`.

Deploy `supabase/functions/nutrition-chat/index.ts` as `nutrition-chat` via
the Supabase dashboard, or with an installed Supabase CLI:

```powershell
supabase login
supabase functions deploy nutrition-chat --project-ref YOUR_PROJECT_REF
```

The checked-in configuration enables gateway JWT verification; the handler
also validates registered users through Supabase Auth. Test with a signed-in
user's token, not an API key in place of a user token.

## 3. Publish the prepared frontend

Option A: upload the **contents of `web/` only** to a GitHub repository.
Import that repository with Vercel → Add New → Project.

Option B: commit this full project with the included `.gitignore`, import it,
and set Root Directory to `web`. Inspect staged files before pushing; do not
commit `.env`, runtimes, PDFs, or result files. `.gitignore` does not remove
files already tracked by Git.

| Vercel setting | Value |
| --- | --- |
| Framework | Other |
| Root directory | `.` for frontend-only repo; `web` for full project |
| Build command | Override, leave empty |
| Output directory | `.` |

`web/vercel.json` already specifies the static output and response headers.
There is no npm install or build needed.

If Node.js and the Vercel CLI are available, deployment can also be done from
`web/` with `npx vercel login`, then `npx vercel --prod`. Use the web directory
so local server files and secrets are not included in the deployment.

## 4. Configure the final URL

After Vercel gives you `https://YOUR_APP.vercel.app`:

1. Supabase → Authentication → URL Configuration: set Site URL to that URL
   and add `https://YOUR_APP.vercel.app/` to Redirect URLs. The signup flow
   redirects to the root after email confirmation.
2. Supabase → Edge Functions → Secrets: set `ALLOWED_ORIGINS` to
   `https://YOUR_APP.vercel.app` (no trailing slash). Multiple exact origins
   are comma-separated. Add a custom domain or preview URL only when used.
3. Confirm the Email auth provider is enabled. Email confirmation follows
   your Supabase settings; configure production email delivery as needed.

Do not use broad preview wildcards in ALLOWED_ORIGINS: this handler matches
exact origins. Auth redirect settings are separate from CORS settings.

## 5. Launch verification

- Sign up, follow confirmation if required, sign in, and sign out.
- Ask a nutrition question; check answer, citation passage, and PDF pages.
- Verify the returned daily allowance. Accepted requests can count even when
  an upstream provider fails; the client does not retry automatically.
- Verify a signed-out user cannot submit questions.
- Check desktop and phone layouts.
- If a question fails, inspect Supabase function logs using the displayed
  request reference. 401 indicates authentication, 403 can indicate an origin
  restriction, 429 indicates daily/provider limits, and 502/503 can indicate
  missing RPCs, backend secrets, or provider availability.

The frontend sends only the current question. Conversation history is kept
in page memory, cleared on account changes, and not sent as model context.
The response sound is synthesized locally with Web Audio after an answer
arrives. The Sound on/off control remembers the preference in this browser.
Audio is unlocked by the question submission gesture; browser audio policies
may silence background tabs. Errors and empty retrieval do not play a chime.

## Local UI tests

Install Playwright into your Python environment (`pip install playwright`).
The test runner uses installed Microsoft Edge on Windows; on other systems
run `playwright install chromium`. Then:

```powershell
.notebook-runtime/python.exe test_web.py
```

Tests mock auth and chat responses and do not create accounts or consume
inference quota. Screenshots are written to `web-test-results/`. A real
signed-in test against the deployed function is still required before launch.

References: [Vercel static build settings](https://vercel.com/docs/builds/configure-a-build),
[Supabase deployment](https://supabase.com/docs/guides/functions/deploy),
[Auth redirect URLs](https://supabase.com/docs/guides/auth/redirect-urls).
