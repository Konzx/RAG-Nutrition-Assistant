"""
ingest_new.py

Pipeline:
1. Read human-nutrition-text.pdf
2. Clean extracted PDF text
3. Split into sentences
4. Group whole sentences using SENTENCES_PER_CHUNK only
5. Request 384-dimensional embeddings from the Hugging Face API
6. Insert chunks + metadata + embeddings into Supabase public.chunks

Supabase schema expected:

    public.chunks(
        id bigint,
        doc_id text,
        chunk_index int,
        content text,
        metadata jsonb,
        embedding vector(384)
    )

Environment variables:
    SUPABASE_URL
    SUPABASE_KEY
    HF_TOKEN
    # No dedicated endpoint is required.

Install:
    pip install -r requirements.txt

Usage:
    python -u ingest_new.py
"""

import os
import re
import time
import unicodedata
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from httpx import TransportError
from dotenv import load_dotenv
from huggingface_hub import InferenceClient
from supabase import Client, create_client


# ============================================================
# CONFIG
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
PDF_PATH = BASE_DIR / "human-nutrition-text.pdf"
TABLE_NAME = "chunks"

# This model outputs exactly 384 dimensions, matching:
# embedding vector(384)
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EXPECTED_EMBEDDING_DIM = 384

# Whole sentences per chunk. See the notebook for measured overflow rates.
SENTENCES_PER_CHUNK = 15
EMBEDDING_BATCH_SIZE = 8
DB_BATCH_SIZE = 25

# Remove old rows only after the entire new upload has been verified.
REPLACE_EXISTING_DOCUMENT = True


# ============================================================
# ENVIRONMENT / SUPABASE
# ============================================================

def normalize_supabase_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("SUPABASE_URL must be your project's HTTP(S) URL.")
    path = parts.path.rstrip("/")
    if path.endswith("/rest/v1"):
        path = path[:-len("/rest/v1")]
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def get_supabase_client() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")

    if not url or not key:
        raise RuntimeError(
            "Missing SUPABASE_URL or SUPABASE_KEY.\n"
            "Create a .env file containing:\n"
            "SUPABASE_URL=https://YOUR_PROJECT.supabase.co\n"
            "SUPABASE_KEY=YOUR_SERVICE_ROLE_KEY"
        )

    return create_client(normalize_supabase_url(url), key)


# ============================================================
# PDF EXTRACTION
# ============================================================

def extract_pdf_pages(pdf_path: Path) -> list[dict[str, Any]]:
    """Extract text from the PDF page-by-page."""
    import pymupdf as fitz

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path.resolve()}")

    pages: list[dict[str, Any]] = []

    with fitz.open(pdf_path) as pdf:
        for page_number, page in enumerate(pdf, start=1):
            pages.append(
                {
                    "page_number": page_number,
                    "text": page.get_text("text"),
                }
            )

    if not pages:
        raise ValueError("The PDF contains no pages.")

    return pages


# ============================================================
# CLEANING
# ============================================================

def normalize_line(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip()).lower()


def find_repeated_headers_and_footers(
    pages: list[dict[str, Any]],
    edge_lines: int = 3,
) -> set[str]:
    """
    Find lines repeatedly appearing near the top or bottom of pages.

    These are usually:
    - book titles
    - chapter names
    - page headers
    - footers
    """

    counts: Counter[str] = Counter()

    for page in pages:
        lines = [
            line.strip()
            for line in page["text"].splitlines()
            if line.strip()
        ]

        candidates = lines[:edge_lines] + lines[-edge_lines:]

        # Count at most once per page.
        for line in set(map(normalize_line, candidates)):
            if line:
                counts[line] += 1

    threshold = max(3, round(len(pages) * 0.30))

    return {
        line
        for line, count in counts.items()
        if count >= threshold
    }


def clean_page_text(text: str, repeated_lines: set[str]) -> str:
    """Clean common PDF extraction artifacts."""

    cleaned_lines: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if not line:
            cleaned_lines.append("")
            continue

        normalized = normalize_line(line)

        # Remove repeated page headers / footers.
        if normalized in repeated_lines:
            continue

        # Remove standalone page numbers.
        if re.fullmatch(r"(?:page\s*)?\d+", line, flags=re.IGNORECASE):
            continue

        cleaned_lines.append(line)

    text = "\n".join(cleaned_lines)

    # Normalize Unicode characters.
    text = unicodedata.normalize("NFKC", text)

    # Fix words broken across lines:
    # "nutri-\ntion" -> "nutrition"
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)

    # Remaining newlines are layout artifacts rather than semantic boundaries.
    text = re.sub(r"\n+", " ", text)

    # Collapse repeated whitespace.
    text = re.sub(r"\s+", " ", text)

    # Remove spaces before punctuation.
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)

    return text.strip()


def clean_pdf_pages(
    pages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    repeated_lines = find_repeated_headers_and_footers(pages)

    cleaned_pages: list[dict[str, Any]] = []

    for page in pages:
        text = clean_page_text(page["text"], repeated_lines)

        if text:
            cleaned_pages.append(
                {
                    "page_number": page["page_number"],
                    "text": text,
                }
            )

    return cleaned_pages


# ============================================================
# SENTENCE SPLITTING
# ============================================================

def build_sentence_splitter():
    """
    spaCy's rule-based sentencizer does not require downloading
    a separate English language model.
    """

    import spacy

    nlp = spacy.blank("en")
    nlp.add_pipe("sentencizer")
    nlp.max_length = 10_000_000
    return nlp


def create_sentence_chunks(
    cleaned_pages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Group whole sentences by count only. Never split or truncate a sentence.
    The last chunk can be shorter; page boundaries match the preprocessing rules.
    """

    if not isinstance(SENTENCES_PER_CHUNK, int) or SENTENCES_PER_CHUNK < 1:
        raise ValueError("SENTENCES_PER_CHUNK must be a positive integer.")
    nlp = build_sentence_splitter()

    sentences: list[dict[str, Any]] = []

    for page in cleaned_pages:
        doc = nlp(page["text"])

        for sent in doc.sents:
            sentence = sent.text.strip()

            if sentence:
                sentences.append({"text": sentence, "page_number": page["page_number"]})

    chunks: list[dict[str, Any]] = []

    for start in range(0, len(sentences), SENTENCES_PER_CHUNK):
        group = sentences[start : start + SENTENCES_PER_CHUNK]

        content = " ".join(item["text"] for item in group)
        page_numbers = sorted({item["page_number"] for item in group})

        chunks.append(
            {
                "chunk_index": len(chunks),
                "content": content,
                "metadata": {
                    "pages": page_numbers,
                    "sentence_count": len(group),
                    "start_sentence": start,
                    "end_sentence": start + len(group) - 1,
                },
            }
        )

    return chunks


# ============================================================
# EMBEDDINGS
# ============================================================

def load_embedding_model() -> InferenceClient:
    """Construct an HTTP client; no model weights are downloaded locally."""
    token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACEHUB_API_TOKEN")
    if not token:
        raise RuntimeError("Set HF_TOKEN in .env to a token with inference permission.")
    return InferenceClient(
        model=EMBEDDING_MODEL,
        token=token,
        provider="hf-inference",
        timeout=120,
    )


def request_embeddings(client: InferenceClient, texts: list[str]) -> np.ndarray:
    """Retry transient inference failures and validate pooled sentence vectors."""
    for attempt in range(4):
        try:
            result = client.feature_extraction(texts, truncate=False)
            break
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            transient = status in {429, 500, 502, 503, 504} or isinstance(exc, (TimeoutError, TransportError))
            if not transient or attempt == 3:
                raise RuntimeError(
                    f"Hugging Face embedding request failed (HTTP {status or 'unavailable'}; "
                    f"{type(exc).__name__}). Check HF_TOKEN inference permissions "
                    "and available free credits. HTTP 402 means credits/payment required; "
                    "401/403 means authentication/permission failure. No paid fallback is used."
                ) from None
            delay = 2 ** (attempt + 1)
            print(f"Hugging Face temporarily unavailable; retrying in {delay}s", flush=True)
            time.sleep(delay)
    embeddings = np.asarray(result, dtype=np.float32)
    if embeddings.ndim == 1 and len(texts) == 1:
        embeddings = embeddings.reshape(1, -1)
    expected = (len(texts), EXPECTED_EMBEDDING_DIM)
    if embeddings.shape != expected:
        raise ValueError(
            f"Expected pooled sentence embeddings shaped {expected}; got {embeddings.shape}. "
            "Use a compatible embedding endpoint. Vectors are never padded or truncated."
        )
    if not np.isfinite(embeddings).all():
        raise ValueError("Hugging Face returned non-finite embeddings.")
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    if not np.isfinite(norms).all() or (norms == 0).any():
        raise ValueError("Hugging Face returned invalid or zero-length vectors.")
    return embeddings / norms


def create_embeddings(
    model: InferenceClient,
    chunks: list[dict[str, Any]],
):
    texts = [chunk["content"] for chunk in chunks]

    embeddings = np.concatenate([
        request_embeddings(model, texts[start : start + EMBEDDING_BATCH_SIZE])
        for start in range(0, len(texts), EMBEDDING_BATCH_SIZE)
    ])

    if embeddings.ndim != 2 or embeddings.shape[1] != EXPECTED_EMBEDDING_DIM:
        raise ValueError(
            f"Embedding dimension mismatch. "
            f"Supabase expects {EXPECTED_EMBEDDING_DIM}, "
            f"but the model returned shape {embeddings.shape}."
        )

    return embeddings


# ============================================================
# SUPABASE INGESTION
# ============================================================

def remove_existing_document(
    supabase: Client,
    doc_id: str,
    run_id: str,
) -> None:
    """
    Remove previous runs only after the current run has been verified.
    The expected table has no UNIQUE constraint on (doc_id, chunk_index).
    Do not run two replacements of the same document concurrently.
    """

    supabase.table(TABLE_NAME).delete().eq("doc_id", doc_id).or_(
        f"metadata->>ingestion_run.is.null,metadata->>ingestion_run.neq.{run_id}"
    ).execute()


def prepare_rows(
    doc_id: str,
    chunks: list[dict[str, Any]],
    embeddings,
    run_id: str,
) -> list[dict[str, Any]]:
    if len(chunks) != len(embeddings):
        raise ValueError("Chunk count does not match embedding count.")
    rows: list[dict[str, Any]] = []

    for chunk, embedding in zip(chunks, embeddings):
        metadata = {
            **chunk["metadata"],
            "source": doc_id,
            "embedding_model": EMBEDDING_MODEL,
            "ingestion_run": run_id,
        }

        rows.append(
            {
                "doc_id": doc_id,
                "chunk_index": chunk["chunk_index"],
                "content": chunk["content"],
                "metadata": metadata,
                "embedding": embedding.tolist(),
            }
        )

    return rows


def insert_rows(
    supabase: Client,
    rows: list[dict[str, Any]],
) -> None:
    total = len(rows)

    for start in range(0, total, DB_BATCH_SIZE):
        batch = rows[start : start + DB_BATCH_SIZE]

        try:
            response = supabase.table(TABLE_NAME).insert(batch).execute()
        except Exception as exc:
            message = str(getattr(exc, "message", "No database message returned."))
            for name in ("SUPABASE_KEY", "SUPABASE_SERVICE_ROLE_KEY", "HF_TOKEN",
                         "HUGGINGFACEHUB_API_TOKEN"):
                secret = os.getenv(name)
                if secret:
                    message = message.replace(secret, "[REDACTED]")
            raise RuntimeError(
                f"Supabase insert failed at chunk {batch[0]['chunk_index']} "
                f"(code {getattr(exc, 'code', type(exc).__name__)}). "
                f"Database message: {message}\n"
                "Check INSERT/SELECT permissions, vector(384), and an identity/default "
                "for id. For a dimension mismatch, run supabase_schema.sql in the "
                "Supabase SQL Editor; changing Python's dimension does not alter the table. "
                "Earlier batches may exist; old document rows have been retained."
            ) from None
        if len(response.data or []) != len(batch):
            raise RuntimeError("Supabase did not acknowledge every row in the batch.")

        uploaded = min(start + DB_BATCH_SIZE, total)
        print(f"Uploaded {uploaded}/{total} chunks")


# ============================================================
# OPTIONAL SIMILARITY SEARCH
# ============================================================

def similarity_search(
    supabase: Client,
    model: InferenceClient,
    query: str,
    match_count: int = 5,
    metadata_filter: dict[str, Any] | None = None,
):
    """
    Calls the SQL function:

        public.match_chunks_bge(
            query_embedding vector(384),
            match_count int,
            filter jsonb
        )

    This is optional and is not required for ingestion.
    """

    query = "Represent this sentence for searching relevant passages: " + query
    query_embedding = request_embeddings(model, [query])[0]

    if len(query_embedding) != EXPECTED_EMBEDDING_DIM:
        raise ValueError(
            f"Query embedding has {len(query_embedding)} dimensions; "
            f"expected {EXPECTED_EMBEDDING_DIM}."
        )

    response = supabase.rpc(
        "match_chunks_bge",
        {
            "query_embedding": query_embedding.tolist(),
            "match_count": match_count,
            "filter": metadata_filter or {},
        },
    ).execute()

    return response.data


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    print("Checking Hugging Face configuration and Supabase access...", flush=True)
    model = load_embedding_model()
    supabase = get_supabase_client()
    try:
        supabase.table(TABLE_NAME).select(
            "id,doc_id,chunk_index,content,metadata,embedding"
        ).limit(1).execute()
    except Exception as exc:
        raise RuntimeError(
            f"Supabase preflight failed (code {getattr(exc, 'code', type(exc).__name__)}). "
            "Check public.chunks columns, credentials and SELECT permissions."
        ) from None
    print("1. Extracting PDF...")
    pages = extract_pdf_pages(PDF_PATH)
    print(f"   Extracted {len(pages)} pages")

    print("2. Cleaning text...")
    cleaned_pages = clean_pdf_pages(pages)
    print(f"   Retained {len(cleaned_pages)} non-empty pages")

    print(f"3. Creating chunks of {SENTENCES_PER_CHUNK} whole sentences...")
    chunks = create_sentence_chunks(cleaned_pages)

    if not chunks:
        raise ValueError(
            "No chunks were created. "
            "Check whether the PDF contains machine-readable text."
        )

    print(f"   Created {len(chunks)} chunks")
    print(
        f"   First chunk contains "
        f"{chunks[0]['metadata']['sentence_count']} sentences"
    )

    doc_id = PDF_PATH.name
    run_id = str(uuid4())
    print(f"4. Requesting API embeddings and uploading batches. Run: {run_id}", flush=True)
    for start in range(0, len(chunks), EMBEDDING_BATCH_SIZE):
        batch = chunks[start : start + EMBEDDING_BATCH_SIZE]
        embeddings = create_embeddings(model, batch)
        rows = prepare_rows(doc_id, batch, embeddings, run_id)
        insert_rows(supabase, rows)
        stored = supabase.table(TABLE_NAME).select("id", count="exact").eq(
            "doc_id", doc_id
        ).eq("metadata->>ingestion_run", run_id).not_.is_("embedding", "null").execute()
        expected = start + len(batch)
        if stored.count != expected:
            raise RuntimeError(f"Read-back verification failed: expected {expected}, got {stored.count}.")
        print(f"Verified {expected}/{len(chunks)} chunks in Supabase", flush=True)

    if REPLACE_EXISTING_DOCUMENT:
        remove_existing_document(supabase, doc_id, run_id)
        stored = supabase.table(TABLE_NAME).select("id", count="exact").eq("doc_id", doc_id).execute()
        if stored.count != len(chunks):
            raise RuntimeError("New upload verified, but old-row cleanup failed. Check DELETE permissions.")

    print("\nDone.")
    print(f"Document: {doc_id}")
    print(f"Chunks stored and verified: {len(chunks)}")
    print(f"Embedding dimension: {EXPECTED_EMBEDDING_DIM}")
    print(f"Embedding model: {EMBEDDING_MODEL}")


if __name__ == "__main__":
    main()
