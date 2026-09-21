# Deploy nutrition-chat

The standalone frontend is in `web/`; see
[`DEPLOY_VERCEL.md`](../../../DEPLOY_VERCEL.md) for frontend publishing,
public key configuration, and authentication redirects.

This is the hosted equivalent of test.py: HF BGE query embedding, the existing
match_chunks_bge RPC, and Groq qwen/qwen3.8-27b. No model downloads or changes
to textbook data. Daily request counts are stored in a separate table.
Questions are independent; chat history is not
used. Four whole passages are sent to Groq, with a 768-token answer limit.

## Supabase Dashboard deployment

**Before deploying this version:** run the complete SQL from
`supabase/migrations/20260920000100_nutrition_daily_limit.sql` in your project's
SQL editor. This creates the usage table and service-role-only quota RPC.
Then replace the deployed function with the updated index.ts and redeploy.
If the migration is missing or the quota database is unavailable, requests
fail closed before calling HF/Groq.

1. Open the existing Supabase project containing the nutrition chunks.
2. Open **Edge Functions**, select **Deploy a new function / Via Editor**
   (the button wording may vary), and name it **nutrition-chat**.
3. Replace the editor's `index.ts` with the complete sibling `index.ts` file.
   No other source files or imports are required.
4. Confirm function secrets `HF_TOKEN` and `GROQ_API_KEY` are present. Supabase
   provides `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` automatically.
   The local ingestion variable `SUPABASE_KEY` is not used here.
5. Deploy. Keep platform JWT verification enabled. The function additionally
   verifies the caller with Supabase Auth before calling either model API.
6. Invoke from a signed-in Supabase user session as shown below. A bare anon
   key, publishable key, or service-role key is not a user session and will not
   pass the function's user check. Anonymous Auth users are also rejected.

If you use the dashboard Test panel, send POST with Content-Type application/json,
Authorization `Bearer <a signed-in user's access_token>`, and this body:

```json
{"question":"Why is dietary fiber important?"}
```

Do not disable the function's user check to make a dashboard test pass.
The browser frontend must sign in through Supabase Auth before submitting a question.

Optional secret: `ALLOWED_ORIGINS=https://your-app.vercel.app,https://your-preview-origin`
using exact origins without trailing slashes. If absent, CORS permits all origins
but valid registered-user authentication is still required. CORS does not replace
authentication or usage limits.

## Browser client

Use the existing Supabase browser client configured with a publishable/anon key.
Never use a service-role, Groq, or HF key in browser code.

```typescript
const { data: { session } } = await supabase.auth.getSession();
if (!session) throw new Error("Please sign in first.");

const { data, error } = await supabase.functions.invoke("nutrition-chat", {
  body: { question },
});
if (error) {
  // FunctionsHttpError carries the response, including our safe error message.
  const details = error.context instanceof Response
    ? await error.context.json().catch(() => null)
    : null;
  throw new Error(details?.error?.message ?? "Unable to answer. Please try again.");
}
// Render data.answer as safe Markdown (raw HTML disabled).
// [S1] opens data.sources.find(source => source.label === "S1").
// Show source.passage and source.pdf_pages in the citation panel.
// Surface data.review_notes when present. Similarity is NOT answer confidence.
```

Success returns `{status, answer, sources, review_notes, finish_reason, model,
request_id, usage}`. `usage` contains `{limit: 10, remaining, resets_at}`;
the frontend can display the remaining allowance. Each source has `{label, id, doc_id, chunk_index, pdf_pages,
passage, similarity}`. With no sources, status is `no_sources`, and Groq is not
called. Errors return `{error: {code, message}, request_id}` with a non-2xx status.

## Troubleshooting and launch limits

- 401: sign in again and send the user's access token.
- 503 MISSING_CONFIGURATION: add the named secret.
- 503 PROVIDER_CREDITS_REQUIRED: check HF/Groq account allowance.
- 429 RATE_LIMITED: wait for Retry-After; the function does not automatically
  retry inference or switch providers.
- 429 DAILY_LIMIT_REACHED: 10 questions have been submitted today. Resets at
  midnight UTC (05:30 in India); Retry-After gives seconds until reset.
- 502 Source retrieval: check match_chunks_bge in the same project and its
  service_role permission. The existing supabase_schema.sql defines this RPC.
- 502 Answer generation: check Groq model availability and account permissions.
  This Qwen model is a preview; no silent fallback is configured.
- Requests are limited to 8 KB, questions to 1000 characters, and the upstream
  pipeline to 100 seconds. HF can still reject a question above its token limit;
  input is never silently truncated.
- Each verified user gets 10 accepted question attempts per UTC calendar day,
  across sessions and devices. Invalid input and failed authentication do not
  count. Once quota is reserved, empty retrieval and provider errors still
  count; manual retries are new attempts. An atomic database UPSERT prevents
  parallel requests from exceeding the allowance. There is no per-minute limit.
- Counters are per account, not per person/IP; multiple accounts have separate
  allowances. HF/Groq account limits remain shared by all users.
- Citation-label checks do not verify factual support; inspect real passages.

## CLI alternative and offline tests

From the project root, with Supabase CLI installed and logged in:

```powershell
supabase functions deploy nutrition-chat --project-ref YOUR_PROJECT_REF
```

With Deno installed:

```powershell
deno check supabase/functions/nutrition-chat/index.ts
deno test supabase/functions/nutrition-chat/index_test.ts
```

Tests use injected fake credentials and fetch responses; no network or real
secrets are needed.

References:
- https://supabase.com/docs/guides/functions/quickstart-dashboard
- https://supabase.com/docs/guides/functions/auth
- https://huggingface.co/docs/inference-providers/tasks/feature-extraction
