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
            SimpleField(name="department", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SimpleField(name="status", type=SearchFieldDataType.String, filterable=True, facetable=True),
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
                select=["id", "content", "source_table", "record_id", "department", "status", "title"],
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
                            "department": r.get("department"),
                            "status": r.get("status"),
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
                    f"Status: {r['status']}. Department: {r['department']}. "
                    f"Owner: {r['owner']}. Priority: {r['priority']}. "
                    f"Progress: {r.get('progress_percentage', 'N/A')}%. "
                    f"Description: {r.get('description') or ''}. "
                    f"Objectives: {r.get('objectives') or ''}. "
                    f"KPIs: {r.get('kpis') or ''}. "
                    f"Risks: {r.get('risks') or ''}."
                )
                docs.append({
                    "id": f"initiative-{r['initiative_id']}",
                    "source_table": "ai_initiatives",
                    "record_id": str(r["initiative_id"]),
                    "title": r["initiative_name"],
                    "content": content,
                    "department": r.get("department") or "",
                    "status": r.get("status") or "",
                    "owner": r.get("owner") or "",
                })

        if table in ("all", "ai_adoption_index"):
            rows = await sql_service.fetch_all("SELECT * FROM ai_adoption_index")
            for r in rows:
                content = (
                    f"AI Adoption Dimension: {r['dimension']}. "
                    f"Sub-dimension: {r.get('sub_dimension') or 'N/A'}. "
                    f"Current Score: {r['current_score']}/5. "
                    f"Maturity Level: {r['maturity_level']}. "
                    f"Gap Analysis: {r.get('gap_analysis') or ''}. "
                    f"Recommendations: {r.get('recommendations') or ''}."
                )
                docs.append({
                    "id": f"adoption-{r['id']}",
                    "source_table": "ai_adoption_index",
                    "record_id": str(r["id"]),
                    "title": f"{r['dimension']} – {r.get('sub_dimension') or ''}",
                    "content": content,
                    "department": "",
                    "status": r.get("maturity_level") or "",
                    "owner": r.get("assessor") or "",
                })

        if table in ("all", "company_profile"):
            rows = await sql_service.fetch_all("SELECT * FROM company_profile")
            for r in rows:
                content = (
                    f"Company: {r['company_name']}. Industry: {r['industry']}. "
                    f"Employees: {r.get('employee_count') or 'N/A'}. "
                    f"Headquarters: {r.get('headquarters') or 'N/A'}. "
                    f"Strategic Focus: {r.get('strategic_focus') or ''}. "
                    f"AI Vision: {r.get('ai_vision') or ''}."
                )
                docs.append({
                    "id": f"company-{r['id']}",
                    "source_table": "company_profile",
                    "record_id": str(r["id"]),
                    "title": r["company_name"],
                    "content": content,
                    "department": "",
                    "status": "",
                    "owner": "",
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
