# RAG nutrition assistant

Presented by Ankit Kondilkar.

A minimal nutrition question-answering app with a light theme, source citations,
Supabase authentication, and an optional completion sound.

The knowledge source is **Human Nutrition: 2020 Edition by University of Hawai‘i
at Mānoa Food Science and Human Nutrition Program**.

## Architecture

- `web/`: static HTML/CSS/JavaScript frontend, ready for Vercel.
- `supabase/functions/nutrition-chat/`: authenticated retrieval and answer generation.
- Supabase: document chunks, 384-dimensional embeddings, and daily request quotas.
- Hugging Face: BGE query embeddings; Groq: answer generation.
- Python scripts: PDF ingestion, chunk analysis, and retrieval experiments.

## Deploy to Vercel

Import this repository, select **Other** as the framework, set **Root Directory**
to `web`, leave the build command empty, and use `.` as the output directory.
See [DEPLOY_VERCEL.md](DEPLOY_VERCEL.md) for Supabase setup, final domain settings,
authentication redirects, and launch verification.

`web/config.js` contains only public Supabase connection settings. Model API keys
and the service-role key belong in Supabase Edge Function secrets, never browser
code. Local `.env`, PDFs, runtime folders, and generated results are excluded.

## Local development

From the repository root with Python installed:

```sh
python -m http.server 8000 --bind 127.0.0.1 --directory web
```

Open http://127.0.0.1:8000. To configure a different Supabase project, set
`SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` (or `SUPABASE_ANON_KEY`) in local
`.env`, install `python-dotenv`, and run `python configure_web.py`.

For ingestion setup, see [INGESTION.md](INGESTION.md). The PDF is not included.
The `.notebook-runtime` commands in the guides refer to an optional local Python
installation; substitute `python` when using your own environment.

## Tests

```sh
deno test supabase/functions/nutrition-chat/index_test.ts
python test_web.py
```

Browser tests require Playwright and either installed Microsoft Edge on Windows
or Playwright Chromium. Auth and inference responses are mocked; these tests do
not create accounts or consume model quotas. Test a real signed-in question
against your deployed backend before launch.
