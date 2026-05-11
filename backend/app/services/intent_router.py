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
            sql_query = await self._llm.generate_sql(question, schema, history)
            try:
                sql_rows = await self._sql.fetch_all(sql_query)
            except Exception as exc:
                sql_rows = [{"error": str(exc), "generated_sql": sql_query}]

        if intent in (IntentType.SEMANTIC, IntentType.SUMMARY):
            sources = await self._search.hybrid_search(question, top=5)

        if intent == IntentType.SUMMARY and not sql_rows:
            try:
                sql_rows = await self._sql.fetch_all("""
                    SELECT initiative_name, portfolio_team, owner, stage, last_updated
                    FROM   ai_initiatives
                    ORDER  BY last_updated DESC
                """)
            except Exception:
                pass

        answer = await self._llm.synthesize(question, sources, sql_rows, history, intent, sql_query)

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
        print(f"\n[INTENT] {intent}")

        sources = []
        sql_rows = None
        sql_query = None

        # Run SQL for lookup/analytics/summary
        if intent in (IntentType.LOOKUP, IntentType.ANALYTICS):
            schema = await self._sql.get_schema_context()
            sql_query = await self._llm.generate_sql(question, schema, history)
            print(f"[SQL] {sql_query}")
            try:
                sql_rows = await self._sql.fetch_all(sql_query)
                print(f"[ROWS] {len(sql_rows)} rows returned")
            except Exception as exc:
                print(f"[SQL ERROR] {exc}")
                sql_rows = [{"error": str(exc), "generated_sql": sql_query}]

        if intent == IntentType.SUMMARY:
            try:
                sql_rows = await self._sql.fetch_all("""
                    SELECT initiative_name, portfolio_team, owner, stage, last_updated
                    FROM   ai_initiatives
                    ORDER  BY stage, portfolio_team
                """)
                print(f"[SUMMARY ROWS] {len(sql_rows)} rows returned")
            except Exception as exc:
                print(f"[SUMMARY ERROR] {exc}")

        # Semantic search for semantic/summary intents
        if intent in (IntentType.SEMANTIC, IntentType.SUMMARY):
            try:
                sources = await self._search.hybrid_search(question, top=5)
                print(f"[SEARCH] {len(sources)} results")
            except Exception as exc:
                print(f"[SEARCH ERROR] {exc}")

        async for chunk in self._llm.synthesize_stream(question, sources, sql_rows, history, sql_query):
            yield chunk
