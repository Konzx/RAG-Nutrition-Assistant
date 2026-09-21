import { createHandler as rawHandler, normalizeEmbedding } from "./index.ts";

const quotaResult = (allowed = true, remaining = 9) => ({
  allowed, remaining, limit: 10, resets_at: "2026-09-21T00:00:00+00:00", retry_after_seconds: 3600,
});
// Existing pipeline tests still exercise their original upstream stages.
// Intercept the new quota stage and verify server-side identity/credentials.
const createHandler: typeof rawHandler = (env, fetcher = fetch) => rawHandler(env, (url, init) => {
  if (url.endsWith("/rpc/consume_nutrition_daily_quota")) {
    assert(JSON.parse(String(init?.body)).p_user_id === "user-1");
    assert(new Headers(init?.headers).get("authorization") === "Bearer test-service-secret");
    return Promise.resolve(Response.json(quotaResult()));
  }
  return fetcher(url, init);
});

function assert(value: unknown, message = "Assertion failed"): asserts value {
  if (!value) throw new Error(message);
}
const settings: Record<string, string> = {
  SUPABASE_URL: "https://example.supabase.co",
  SUPABASE_SERVICE_ROLE_KEY: "test-service-secret",
  HF_TOKEN: "test-hf-secret", GROQ_API_KEY: "test-groq-secret",
};
const env = (name: string) => settings[name];
const request = (body: unknown = { question: "Why is fiber important?" }) =>
  new Request("https://example.supabase.co/functions/v1/nutrition-chat", {
    method: "POST", headers: { Authorization: "Bearer test-user-session", "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
const row = { id: 1, doc_id: "human-nutrition-text.pdf", chunk_index: 2,
  content: "Fiber passage. ".repeat(100), metadata: { pages: [3, 4] }, similarity: 0.8 };

Deno.test("embeddings match shape and normalization, reject invalid data", () => {
  const vector = normalizeEmbedding([Array(384).fill(2)]);
  assert(Math.abs(Math.hypot(...vector) - 1) < 1e-12);
  for (const bad of [[], Array(384).fill(0), Array(383).fill(1), Array(384).fill(NaN), [[1], [2]]]) {
    let failed = false;
    try { normalizeEmbedding(bad); } catch { failed = true; }
    assert(failed, "Invalid vector accepted");
  }
});

Deno.test("full pipeline preserves passages, authenticates caller, maps citations", async () => {
  let calls = 0;
  const handler = createHandler(env, async (url, init) => {
    calls++;
    const headers = new Headers(init?.headers);
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    if (calls === 1) {
      assert(url.endsWith("/auth/v1/user"));
      assert(headers.get("authorization") === "Bearer test-user-session");
      return Response.json({ id: "user-1" });
    }
    if (calls === 2) {
      assert(url.endsWith("/models/BAAI/bge-small-en-v1.5/pipeline/feature-extraction"));
      assert(body.inputs[0] === "Represent this sentence for searching relevant passages: Why is fiber important?");
      assert(body.truncate === false);
      return Response.json([Array(384).fill(1)]);
    }
    if (calls === 3) {
      assert(url.endsWith("/rpc/match_chunks_bge"));
      assert(headers.get("authorization") === "Bearer test-service-secret");
      assert(body.match_count === 4 && body.filter.source === "human-nutrition-text.pdf");
      assert(Math.abs(Math.hypot(...body.query_embedding) - 1) < 1e-12);
      return Response.json([row, row]);
    }
    assert(url === "https://api.groq.com/openai/v1/chat/completions");
    assert(headers.get("authorization") === "Bearer test-groq-secret");
    assert(body.model === "qwen/qwen3.8-27b" && body.reasoning_effort === "none");
    assert(body.messages[1].content.includes(row.content));
    return Response.json({ choices: [{ message: { content: "Claim [S1]. Unknown [S9]." }, finish_reason: "length" }] });
  });
  const result = await handler(request());
  const body = await result.json();
  assert(result.status === 200 && calls === 4);
  assert(body.sources.length === 1 && body.sources[0].pdf_pages[0] === 3);
  assert(body.review_notes.length === 2);
  assert(body.usage.limit === 10 && body.usage.remaining === 9);
});

Deno.test("daily exhaustion blocks inference and returns reset delay", async () => {
  let calls = 0;
  const handler = rawHandler(env, async (url) => {
    calls++;
    if (url.endsWith("/auth/v1/user")) return Response.json({ id: "user-1" });
    assert(url.endsWith("/rpc/consume_nutrition_daily_quota"));
    return Response.json(quotaResult(false, 0));
  });
  const result = await handler(request());
  assert(result.status === 429 && calls === 2);
  assert(result.headers.get("retry-after") === "3600");
  assert((await result.json()).error.code === "DAILY_LIMIT_REACHED");
});

Deno.test("quota failure or invalid response fails closed before inference", async () => {
  for (const response of [new Response("missing RPC", { status: 404 }), Response.json({}), Response.json(null)]) {
    let calls = 0;
    const handler = rawHandler(env, async () => {
      calls++;
      return calls === 1 ? Response.json({ id: "user-1" }) : response;
    });
    const result = await handler(request());
    assert(result.status >= 500 && calls === 2);
  }
});

Deno.test("tenth accepted request can generate and reports zero remaining", async () => {
  const handler = rawHandler(env, async (url) => {
    if (url.endsWith("/auth/v1/user")) return Response.json({ id: "user-1" });
    if (url.endsWith("/rpc/consume_nutrition_daily_quota")) return Response.json(quotaResult(true, 0));
    if (url.includes("huggingface.co")) return Response.json(Array(384).fill(1));
    if (url.endsWith("/rpc/match_chunks_bge")) return Response.json([row]);
    return Response.json({ choices: [{ message: { content: "Answer [S1]" }, finish_reason: "stop" }] });
  });
  const result = await handler(request());
  const body = await result.json();
  assert(result.status === 200 && body.usage.remaining === 0 && body.status === "answered");
});

Deno.test("no sources never calls Groq", async () => {
  let calls = 0;
  const handler = createHandler(env, async () => {
    calls++;
    if (calls === 1) return Response.json({ id: "user-1" });
    if (calls === 2) return Response.json(Array(384).fill(1));
    assert(calls === 3);
    return Response.json([]);
  });
  const result = await handler(request());
  assert((await result.json()).status === "no_sources" && calls === 3);
});

Deno.test("missing/invalid auth prevents inference", async () => {
  let calls = 0;
  const handler = createHandler(env, async () => {
    calls++;
    return new Response("private auth error", { status: 401 });
  });
  const unauthenticated = new Request(request());
  unauthenticated.headers.delete("authorization");
  assert((await handler(unauthenticated)).status === 401 && calls === 0);
  assert((await handler(request())).status === 401);
  assert(Number(calls) === 1);
});

Deno.test("input, CORS preflight, and method validation make no upstream calls", async () => {
  const handler = createHandler((name) => name === "ALLOWED_ORIGINS" ? "https://app.example" : env(name),
    () => { throw new Error("Unexpected network call"); });
  const preflight = await handler(new Request("https://example.com", { method: "OPTIONS", headers: { Origin: "https://app.example" } }));
  assert(preflight.status === 204 && preflight.headers.get("access-control-allow-origin") === "https://app.example");
  assert((await handler(new Request("https://example.com", { headers: { Origin: "https://other.example" } }))).status === 403);
  assert((await handler(new Request("https://example.com"))).status === 405);
  assert((await handler(request({ question: "  " }))).status === 400);
  assert((await handler(request({ question: "a".repeat(1001) }))).status === 400);
  assert((await handler(request({ question: "a".repeat(9000) }))).status === 413);
});

Deno.test("provider limits and payment errors are safe and never retried", async () => {
  for (const status of [429, 402]) {
    let calls = 0;
    const handler = createHandler(env, async () => {
      calls++;
      return calls === 1 ? Response.json({ id: "user-1" }) :
        new Response("secret-token private-passage", { status, headers: { "Retry-After": "12" } });
    });
    const result = await handler(request());
    assert(result.status === (status === 429 ? 429 : 503));
    if (status === 429) assert(result.headers.get("retry-after") === "12");
    assert(!(await result.text()).includes("secret-token"));
    assert(calls === 2);
  }
});

Deno.test("missing server secret prevents upstream requests", async () => {
  const handler = createHandler(() => undefined, () => { throw new Error("Unexpected call"); });
  const result = await handler(request());
  assert(result.status === 503 && (await result.json()).error.code === "MISSING_CONFIGURATION");
});
