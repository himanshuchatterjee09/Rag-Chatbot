from typing import List, Optional
from azure.search.documents.aio import SearchClient
from azure.search.documents.indexes.aio import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SearchableField,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)
from azure.search.documents.models import VectorizedQuery
from azure.core.credentials import AzureKeyCredential

from ..config import Settings
from ..models import SearchResult
from .embedding_service import EmbeddingService
from .sql_service import SQLService


class SearchService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._cred = AzureKeyCredential(settings.azure_search_api_key)
        self._embeddings = EmbeddingService(settings)

    def _index_client(self) -> SearchIndexClient:
        return SearchIndexClient(
            endpoint=self._settings.azure_search_endpoint,
            credential=self._cred,
        )

    def _search_client(self) -> SearchClient:
        return SearchClient(
            endpoint=self._settings.azure_search_endpoint,
            index_name=self._settings.azure_search_index_name,
            credential=self._cred,
        )

    async def ensure_index_exists(self):
        async with self._index_client() as client:
            names = [i.name async for i in client.list_indexes()]
            if self._settings.azure_search_index_name not in names:
                await self._create_index(client)

    async def _create_index(self, client: SearchIndexClient):
        fields = [
            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
            SimpleField(name="source_table", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="record_id", type=SearchFieldDataType.String, filterable=True),
            SearchableField(name="title", type=SearchFieldDataType.String),
            SearchableField(name="content", type=SearchFieldDataType.String),
            SimpleField(name="portfolio_team", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SimpleField(name="stage", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SimpleField(name="owner", type=SearchFieldDataType.String, filterable=True),
            SearchField(
                name="content_vector",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=self._settings.embedding_dimensions,
                vector_search_profile_name="hnsw-profile",
            ),
        ]
        vector_search = VectorSearch(
            algorithms=[HnswAlgorithmConfiguration(name="hnsw-algo")],
            profiles=[VectorSearchProfile(name="hnsw-profile", algorithm_configuration_name="hnsw-algo")],
        )
        semantic = SemanticSearch(
            configurations=[
                SemanticConfiguration(
                    name="default",
                    prioritized_fields=SemanticPrioritizedFields(
                        content_fields=[SemanticField(field_name="content")],
                        title_field=SemanticField(field_name="title"),
                    ),
                )
            ]
        )
        await client.create_index(
            SearchIndex(
                name=self._settings.azure_search_index_name,
                fields=fields,
                vector_search=vector_search,
                semantic_search=semantic,
            )
        )

    async def hybrid_search(
        self,
        query: str,
        top: int = 5,
        filter_expr: Optional[str] = None,
    ) -> List[SearchResult]:
        embedding = await self._embeddings.embed(query)
        async with self._search_client() as client:
            results = await client.search(
                search_text=query,
                vector_queries=[
                    VectorizedQuery(
                        vector=embedding,
                        k_nearest_neighbors=top,
                        fields="content_vector",
                    )
                ],
                filter=filter_expr,
                select=["id", "content", "source_table", "record_id", "portfolio_team", "stage", "title"],
                top=top,
                query_type="semantic",
                semantic_configuration_name="default",
            )
            out: List[SearchResult] = []
            async for r in results:
                out.append(
                    SearchResult(
                        content=r["content"],
                        source_table=r.get("source_table", ""),
                        score=r.get("@search.score", 0.0),
                        record_id=r.get("record_id"),
                        metadata={
                            "title": r.get("title"),
                            "portfolio_team": r.get("portfolio_team"),
                            "stage": r.get("stage"),
                        },
                    )
                )
            return out

    async def index_from_sql(
        self,
        sql_service: SQLService,
        table: str = "all",
        recreate: bool = False,
    ) -> int:
        if recreate:
            async with self._index_client() as client:
                try:
                    await client.delete_index(self._settings.azure_search_index_name)
                except Exception:
                    pass
                await self._create_index(client)

        docs: list = []

        if table in ("all", "ai_initiatives"):
            rows = await sql_service.fetch_all("SELECT * FROM ai_initiatives")
            for r in rows:
                content = (
                    f"Initiative: {r['initiative_name']}. "
                    f"Stage: {r.get('stage') or ''}. "
                    f"Portfolio/Team: {r.get('portfolio_team') or ''}. "
                    f"Owner: {r.get('owner') or ''}. "
                    f"Last Updated: {r.get('last_updated') or ''}."
                )
                docs.append({
                    "id": f"initiative-{r['item_id']}",
                    "source_table": "ai_initiatives",
                    "record_id": str(r["item_id"]),
                    "title": r["initiative_name"],
                    "content": content,
                    "portfolio_team": r.get("portfolio_team") or "",
                    "stage": r.get("stage") or "",
                    "owner": r.get("owner") or "",
                })

        if table in ("all", "portfolios"):
            rows = await sql_service.fetch_all("SELECT * FROM portfolios")
            for r in rows:
                content = (
                    f"Portfolio: {r['portfolio']}. "
                    f"Portfolio Lead: {r.get('portfolio_lead') or ''}. "
                    f"UK Lead: {r.get('uk_lead') or ''}. "
                    f"AI Scout: {r.get('ai_scout') or ''}."
                )
                docs.append({
                    "id": f"portfolio-{r['id']}",
                    "source_table": "portfolios",
                    "record_id": str(r["id"]),
                    "title": r["portfolio"],
                    "content": content,
                    "portfolio_team": r.get("portfolio") or "",
                    "stage": "",
                    "owner": r.get("portfolio_lead") or "",
                })

        if not docs:
            return 0

        embeddings = await self._embeddings.embed_batch([d["content"] for d in docs])
        for doc, emb in zip(docs, embeddings):
            doc["content_vector"] = emb

        async with self._search_client() as client:
            for i in range(0, len(docs), 100):
                await client.upload_documents(docs[i : i + 100])

        return len(docs)
