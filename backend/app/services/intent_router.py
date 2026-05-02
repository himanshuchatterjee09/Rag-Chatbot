from typing import AsyncIterator
from ..config import Settings
from ..models import IntentType, QueryRequest, QueryResponse
from .embedding_service import EmbeddingService
from .llm_service import LLMService
from .search_service import SearchService
from .sql_service import SQLService


class IntentRouter:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._sql = SQLService(settings)
        self._search = SearchService(settings)
        self._embeddings = EmbeddingService(settings)
        self._llm = LLMService(settings)

    async def route(self, request: QueryRequest) -> QueryResponse:
        question = request.question
        history = [m.model_dump() for m in (request.conversation_history or [])]

        intent = await self._llm.classify_intent(question)
        sources = []
        sql_rows = None
        sql_query = None

        if intent in (IntentType.LOOKUP, IntentType.ANALYTICS):
            schema = await self._sql.get_schema_context()
            sql_query = await self._llm.generate_sql(question, schema)
            try:
                sql_rows = await self._sql.fetch_all(sql_query)
            except Exception as exc:
                sql_rows = [{"error": str(exc), "generated_sql": sql_query}]

        if intent in (IntentType.SEMANTIC, IntentType.SUMMARY):
            sources = await self._search.hybrid_search(question, top=5)

        if intent == IntentType.SUMMARY and not sql_rows:
            try:
                sql_rows = await self._sql.fetch_all("""
                    SELECT 'initiatives' AS src, initiative_name AS name,
                           status, department,
                           CAST(progress_percentage AS NVARCHAR) AS progress
                    FROM   ai_initiatives
                    UNION ALL
                    SELECT 'adoption', dimension, maturity_level,
                           ISNULL(sub_dimension,''),
                           CAST(current_score AS NVARCHAR)
                    FROM   ai_adoption_index
                """)
            except Exception:
                pass

        answer = await self._llm.synthesize(question, sources, sql_rows, history, intent)

        confidence = (
            0.9 if sql_rows and "error" not in (sql_rows[0] if sql_rows else {})
            else (sources[0].score if sources else 0.5)
        )

        return QueryResponse(
            answer=answer,
            intent=intent,
            sources=sources[:3],
            sql_query=sql_query,
            confidence=round(confidence, 2),
        )

    async def route_stream(self, request: QueryRequest) -> AsyncIterator[str]:
        question = request.question
        history = [m.model_dump() for m in (request.conversation_history or [])]

        intent = await self._llm.classify_intent(question)
        sources = []
        sql_rows = None

        if intent in (IntentType.LOOKUP, IntentType.ANALYTICS):
            schema = await self._sql.get_schema_context()
            sql_query = await self._llm.generate_sql(question, schema)
            try:
                sql_rows = await self._sql.fetch_all(sql_query)
            except Exception as exc:
                sql_rows = [{"error": str(exc)}]

        if intent in (IntentType.SEMANTIC, IntentType.SUMMARY):
            sources = await self._search.hybrid_search(question, top=5)

        async for chunk in self._llm.synthesize_stream(question, sources, sql_rows, history):
            yield chunk
