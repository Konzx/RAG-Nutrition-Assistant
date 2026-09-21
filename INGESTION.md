The script uses BAAI/bge-small-en-v1.5 through Hugging Face's shared hf-inference
API. It produces 384-dimensional embeddings. No model weights are downloaded
and no dedicated endpoint is required. HF_EMBEDDING_MODEL, HF_PROVIDER and
HF_EMBEDDING_ENDPOINT are not used; the model/provider are fixed together with
the dimensions so configuration cannot accidentally mix incompatible vectors.

1. At https://huggingface.co/settings/tokens, create or edit a fine-grained token
   and enable Inference > Make calls to Inference Providers. Put it in .env as
   HF_TOKEN. HTTP 403 can indicate that this permission is missing.
   Never commit .env or paste the token into chat.
2. Run supabase_schema.sql in the Supabase SQL editor. It creates a new table
   or changes an EMPTY existing chunks table to vector(384). It deliberately
   refuses to convert a populated table with a different vector dimension;
   those documents need re-embedding, not vector padding or truncation.
3. Use a Supabase service-role key in SUPABASE_KEY (or
   SUPABASE_SERVICE_ROLE_KEY) and your project root URL in SUPABASE_URL.
   A trailing /rest/v1/ is handled automatically.
4. Install Python 3.10 or newer. The existing .venv points to a missing Python
   installation, so create a fresh environment:

```powershell
py -3 -m venv .venv-api
.\.venv-api\Scripts\python.exe -m pip install -r requirements.txt
.\.venv-api\Scripts\python.exe -m unittest test_ingest.py
.\.venv-api\Scripts\python.exe -u ingest_new.py
```

Shared API usage is covered only up to your available free inference credits.
It is not an unlimited free service. The script stops on quota/payment errors
and does not provision an endpoint or switch to a paid provider. Existing paid
credits on your HF account remain subject to that account's billing settings.

Chunks are grouped only by SENTENCES_PER_CHUNK, with complete sentences and
no character limit or token-based splitting. Choose the sentence count in
ingest_new.py. The notebook recommends 1 for zero overflow on this PDF; 10
produces 13 oversized chunks (1.04%). Larger sentence counts preserve source
text but can exceed the model limit. API truncation is disabled, so oversized requests should fail rather
than silently shorten text. Search uses BGE's recommended query prefix.
The schema supplies match_chunks_bge for the optional similarity_search helper.

Each batch is validated, inserted, and counted back from Supabase using a unique
run identifier. Old document rows are removed only after every new batch is
verified. Failed runs may leave partial rows; the next successful replacement
cleans them up. Do not run concurrent replacements of the same document.
The expected table has no unique constraint on (doc_id, chunk_index), because
old and new runs coexist during replacement. Existing tables must provide an
identity/default for id. The SQL handles pgvector installed in public or extensions.

Historical Public AI validation (before the switch to Groq): the terminal RAG test read 831 stored chunks and generated
answers for all five questions through Public AI, across two runs after a
connection failure. The final answers contain valid source labels and did not
hit the output limit. Review their factual support in rag_test_results/review.md
and the linked source records. Offline RAG checks pass. A portable Python runtime
is available in .notebook-runtime even though the original .venv is broken.

References:
- https://huggingface.co/BAAI/bge-small-en-v1.5
- https://huggingface.co/docs/inference-providers/providers/hf-inference
- https://huggingface.co/docs/inference-providers/pricing

The executed chunk-size comparison is in chunk_size_analysis.ipynb. Open it in
VS Code/Jupyter to view tables, charts and the PDF-specific recommendation.
To reproduce it with the portable runtime created for the analysis:

```powershell
.\.notebook-runtime\python.exe run_chunk_analysis.py
```

For another Python environment, install requirements-notebook.txt and run all
notebook cells from the project directory. The notebook downloads only BGE's
tokenizer/configuration, makes no embedding API requests, and does not write
to Supabase. Outputs are also saved under chunk_analysis_results/.

To test retrieval and answer generation before building a frontend:

Create a Groq API key at https://console.groq.com/keys and add it to `.env`:

```dotenv
GROQ_API_KEY=your_actual_groq_key
```

Keep the existing HF_TOKEN and Supabase credentials. No additional Python
dependency or model download is needed; the script uses the existing httpx package.

```powershell
.\.notebook-runtime\python.exe -u test.py
# Or, with your normal Python environment:
python -u test.py
# One custom question, printing full retrieved passages:
python -u test.py --question "Why is dietary fiber important?" --show-context
```

The default fixed question set covers macronutrients, fiber, calcium/vitamin D,
iron absorption, and water. The script uses the existing BGE query encoder and
match_chunks_bge RPC, filtering to the nutrition PDF. It sends whole retrieved
chunks to qwen/qwen3.8-27b through Groq's direct API, asks for
source citations, and prints the augmented answer. Missing evidence should
produce an explicit limitation instead of an unsupported answer.

No Supabase rows are changed. Full passages and results are saved in timestamped
JSON files under rag_test_results/, with a readable terminal transcript beside
each. Citation-label checks catch unknown labels; they do not establish whether
claims are supported. Inspect the source passages to judge answer quality.

Groq account plan and rate limits apply. Use a Groq free-plan account for free-tier
usage; the script does not enforce account billing settings. This Qwen model is
a preview and availability can change. Thinking is disabled for shorter responses.
See https://console.groq.com/docs/model/qwen/qwen3.8-27b and
https://console.groq.com/docs/rate-limits.
On HTTP 429 the script retries up to twice, honoring numeric Retry-After values
up to 60 seconds. Longer waits fail visibly; retry later. Reduce --top-k or
--max-new-tokens if a request exceeds token limits. Whole passages are preserved.
BGE query embeddings still use the account's HF inference allowance. No other
LLM provider is selected automatically. HF_TOKEN needs inference permission.
If match_chunks_bge is missing, apply supabase_schema.sql in the SQL editor.
Missing GROQ_API_KEY fails before retrieval. Authentication/payment errors are
not retried. No alternative LLM provider is selected automatically.
