import unittest
from unittest.mock import MagicMock, patch

import numpy as np

import ingest_new as ingest


class IngestionTests(unittest.TestCase):
    def test_database_dimension_error_is_reported_without_credentials(self):
        error = RuntimeError("database error")
        error.code = "22000"
        error.message = "expected 1536 dimensions, not 384; test-secret"
        client = MagicMock()
        client.table.return_value.insert.return_value.execute.side_effect = error
        with patch.dict(ingest.os.environ, {"SUPABASE_KEY": "test-secret"}):
            with self.assertRaises(RuntimeError) as caught:
                ingest.insert_rows(client, [{"chunk_index": 0}])
        self.assertIn("expected 1536 dimensions, not 384", str(caught.exception))
        self.assertNotIn("test-secret", str(caught.exception))
        self.assertIn("supabase_schema.sql", str(caught.exception))

    def test_long_sentence_stays_whole_and_last_chunk_keeps_remainder(self):
        long_sentence = " ".join(["nutrition"] * 200) + "."
        pages = [{"page_number": 1, "text": long_sentence},
                 {"page_number": 2, "text": "Food supports health. Water is essential."}]
        with patch.object(ingest, "SENTENCES_PER_CHUNK", 2):
            chunks = ingest.create_sentence_chunks(pages)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["content"], long_sentence + " Food supports health.")
        self.assertEqual(chunks[0]["metadata"]["pages"], [1, 2])
        self.assertEqual(chunks[1]["content"], "Water is essential.")
        self.assertEqual(chunks[1]["metadata"]["sentence_count"], 1)

    def test_chunks_preserve_sentences_words_and_pages(self):
        text = " ".join(["Nutrition supports health."] * 100)
        chunks = ingest.create_sentence_chunks([{"page_number": 7, "text": text}])
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(c["metadata"]["sentence_count"] == ingest.SENTENCES_PER_CHUNK for c in chunks[:-1]))
        self.assertEqual(" ".join(c["content"] for c in chunks).split(), text.split())
        self.assertTrue(all(c["metadata"]["pages"] == [7] for c in chunks))

    def test_stale_endpoint_cannot_override_shared_model(self):
        with patch.dict(ingest.os.environ, {"HF_TOKEN": "test-token",
                       "HF_EMBEDDING_ENDPOINT": "https://unused.example"}), \
             patch.object(ingest, "InferenceClient") as client:
            ingest.load_embedding_model()
        self.assertEqual(client.call_args.kwargs["model"], "BAAI/bge-small-en-v1.5")
        self.assertEqual(client.call_args.kwargs["provider"], "hf-inference")

    def test_supabase_rest_url_is_normalized_to_project_url(self):
        for url in ["https://example.supabase.co", "https://example.supabase.co/",
                    "https://example.supabase.co/rest/v1/"]:
            self.assertEqual(ingest.normalize_supabase_url(url), "https://example.supabase.co")

    def test_api_vectors_are_normalized(self):
        client = MagicMock()
        client.feature_extraction.return_value = np.ones((2, 384))
        result = ingest.request_embeddings(client, ["first", "second"])
        np.testing.assert_allclose(np.linalg.norm(result, axis=1), 1, rtol=1e-6)
        client.feature_extraction.assert_called_once_with(["first", "second"], truncate=False)

    def test_invalid_api_vectors_are_rejected(self):
        for vectors in [np.ones((1, 768)), np.ones((2, 384)),
                        np.ones((1, 2, 384)), np.zeros((1, 384)),
                        np.full((1, 384), np.nan)]:
            with self.subTest(shape=vectors.shape):
                client = MagicMock()
                client.feature_extraction.return_value = vectors
                with self.assertRaises(ValueError):
                    ingest.request_embeddings(client, ["one chunk"])

    def test_rate_limit_is_retried(self):
        error = RuntimeError("rate limited")
        error.response = MagicMock(status_code=429)
        client = MagicMock()
        client.feature_extraction.side_effect = [error, np.ones((1, 384))]
        with patch.object(ingest.time, "sleep") as sleep:
            ingest.request_embeddings(client, ["chunk"])
        self.assertEqual(client.feature_extraction.call_count, 2)
        sleep.assert_called_once_with(2)

    def test_missing_embeddings_cannot_silently_drop_chunks(self):
        with self.assertRaises(ValueError):
            ingest.prepare_rows("doc", [{"content": "text"}], [], "run")

    def test_failed_upload_does_not_delete_existing_document(self):
        chunks = [{"chunk_index": 0, "content": "text", "metadata": {"sentence_count": 1}}]
        with patch.object(ingest, "load_embedding_model"), \
             patch.object(ingest, "get_supabase_client"), \
             patch.object(ingest, "extract_pdf_pages", return_value=[]), \
             patch.object(ingest, "clean_pdf_pages", return_value=[]), \
             patch.object(ingest, "create_sentence_chunks", return_value=chunks), \
             patch.object(ingest, "create_embeddings", return_value=np.ones((1, 384))), \
             patch.object(ingest, "insert_rows", side_effect=RuntimeError("insert failed")), \
             patch.object(ingest, "remove_existing_document") as delete:
            with self.assertRaisesRegex(RuntimeError, "insert failed"):
                ingest.main()
            delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
