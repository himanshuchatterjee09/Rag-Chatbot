from typing import List
from openai import AsyncAzureOpenAI
from ..config import Settings
from ._aad_credential import HybridSyncTokenCredential


_OPENAI_SCOPE = "https://cognitiveservices.azure.com/.default"


def _token_provider():
    return HybridSyncTokenCredential().get_token(_OPENAI_SCOPE).token


class EmbeddingService:
    def __init__(self, settings: Settings):
        self._client = AsyncAzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            azure_ad_token_provider=_token_provider,
            api_version=settings.azure_openai_api_version,
        )
        self._deployment = settings.azure_openai_embedding_deployment
        self._dims = settings.embedding_dimensions

    async def embed(self, text: str) -> List[float]:
        clean = text.replace("\n", " ").strip()[:8000]
        resp = await self._client.embeddings.create(
            input=clean,
            model=self._deployment,
            dimensions=self._dims,
        )
        return resp.data[0].embedding

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        cleaned = [t.replace("\n", " ").strip()[:8000] for t in texts]
        resp = await self._client.embeddings.create(
            input=cleaned,
            model=self._deployment,
            dimensions=self._dims,
        )
        return [item.embedding for item in sorted(resp.data, key=lambda x: x.index)]
