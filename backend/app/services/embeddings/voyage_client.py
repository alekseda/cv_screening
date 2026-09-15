"""
Voyage AI (voyage-multilingual-2) embeddings for candidates and vacancies.

Both candidate profiles and vacancy descriptions are treated as "documents"
(not "queries") since we're comparing two descriptive texts against each
other symmetrically, rather than a short search query against a corpus -
voyage-multilingual-2's input_type parameter is optimized for retrieval
(short query vs. long document), which doesn't quite fit this use case.

Note: like the Anthropic client in ai_structurer.py, this is a synchronous/
blocking call made from inside async route handlers. Fine at this project's
scale (hundreds of resumes per vacancy, processed a handful at a time); if
throughput ever becomes a bottleneck, wrap with anyio.to_thread.run_sync or
move to a background job queue.
"""
import voyageai

from app.core.config import settings

_client: voyageai.Client | None = None


def _get_client() -> voyageai.Client:
    global _client
    if _client is None:
        if not settings.VOYAGE_API_KEY:
            raise EmbeddingError("VOYAGE_API_KEY is not configured")
        _client = voyageai.Client(api_key=settings.VOYAGE_API_KEY)
    return _client


class EmbeddingError(Exception):
    pass


def embed_text(text: str) -> list[float]:
    """Embeds a single piece of text. Raises EmbeddingError on any failure
    (missing key, API error, empty text) rather than returning a partial result."""
    client = _get_client()

    cleaned = text.strip()
    if not cleaned:
        raise EmbeddingError("Cannot embed empty text")

    try:
        result = client.embed([cleaned], model=settings.EMBEDDING_MODEL, input_type="document")
    except Exception as e:
        raise EmbeddingError(f"Voyage API call failed: {e}") from e

    if not result.embeddings:
        raise EmbeddingError("Voyage API returned no embeddings")

    return result.embeddings[0]
