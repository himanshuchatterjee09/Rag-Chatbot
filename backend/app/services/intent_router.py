from typing import AsyncIterator
from ..config import Settings
from ..models import IntentType, QueryRequest, QueryResponse
from .embedding_service import EmbeddingService
from .external_view_service import ExternalViewService
from .llm_service import LLMService
from .search_service import SearchService
from .sql_service import SQLService


class IntentRouter:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._sql = SQLService(settings)
        self._view = ExternalViewService(settings)
        self._search = SearchService(settings)
        self._embeddings = EmbeddingService(settings)
        self._llm = LLMService(settings)

    async def _combined_schema(self) -> str:
        local_schema = await self._sql.get_schema_context()
        return self._view.schema_context() + "\n\n" + local_schema

    def _is_view_query(self, sql: str) -> bool:
        s = sql.lower()
        return ("initiative_details_view" in s
                or "[dbo].[initiative_details_view]" in s
                or "initiative_id" in s and "portfolio_label" in s)

    async def _run_sql(self, sql: str):
        if self._is_view_query(sql):
            return await self._view.fetch_all(sql)
        return await self._sql.fetch_all(sql)

    @staticmethod
    def _split_statements(sql: str) -> list[str]:
        """Split LLM-generated SQL on lines containing only '---'."""
        import re
        parts = re.split(r"\n\s*---\s*\n", sql.strip())
        return [p.strip() for p in parts if p.strip()]

    async def _run_sql_multi(self, sql: str) -> list[dict]:
        """Runs one or more SQL statements (separated by ---) and returns a list of
        result blocks: [{'sql': ..., 'rows': [...]}, ...]"""
        statements = self._split_statements(sql)
        blocks = []
        for stmt in statements:
            try:
                rows = await self._run_sql(stmt)
            except Exception as exc:
                rows = [{"error": str(exc), "generated_sql": stmt}]
            blocks.append({"sql": stmt, "rows": rows})
        return blocks

    async def route(self, request: QueryRequest) -> QueryResponse:
        question = request.question
        history = [m.model_dump() for m in (request.conversation_history or [])]

        intent = await self._llm.classify_intent(question, history)
        sources = []
        sql_rows = None
        sql_query = None

        if intent in (IntentType.LOOKUP, IntentType.ANALYTICS):
            schema = await self._combined_schema()
            sql_query = await self._llm.generate_sql(question, schema, history)
            try:
                sql_rows = await self._run_sql(sql_query)
            except Exception as exc:
                sql_rows = [{"error": str(exc), "generated_sql": sql_query}]

        if intent in (IntentType.SEMANTIC, IntentType.SUMMARY):
            sources = await self._search.hybrid_search(question, top=5)

        if intent == IntentType.SUMMARY and not sql_rows:
            try:
                sql_rows = await self._view.fetch_initiatives_aggregated()
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

    async def route_stream(self, request: QueryRequest):
        """Yields dicts: {'type': 'chunk', 'text': ...} for streamed text and
        a final {'type': 'meta', ...} with SQL/intent/sources/row_count."""
        question = request.question
        history = [m.model_dump() for m in (request.conversation_history or [])]

        intent = await self._llm.classify_intent(question, history)
        print(f"\n[INTENT] {intent}")

        sources = []
        sql_rows = None
        sql_query = None
        sql_error = None

        if intent in (IntentType.LOOKUP, IntentType.ANALYTICS):
            schema = await self._combined_schema()
            sql_query = await self._llm.generate_sql(question, schema, history)
            print(f"[SQL] {sql_query}")
            blocks = await self._run_sql_multi(sql_query)
            for i, b in enumerate(blocks):
                print(f"[ROWS {i+1}] {len(b['rows'])} rows")
            if len(blocks) == 1:
                sql_rows = blocks[0]["rows"]
                if sql_rows and "error" in sql_rows[0]:
                    sql_error = sql_rows[0]["error"]
            else:
                # Flatten multiple result blocks into a single labeled list
                sql_rows = []
                for i, b in enumerate(blocks):
                    sql_rows.append({"__query_label__": f"Query {i+1}", "sql": b["sql"]})
                    sql_rows.extend(b["rows"])

        # Fallback: if lookup SQL found nothing useful, try semantic search
        no_useful_rows = (
            not sql_rows
            or len(sql_rows) == 0
            or (len(sql_rows) == 1 and sql_rows[0].get("reason") == "no_match")
        )
        if intent == IntentType.LOOKUP and no_useful_rows:
            try:
                sources = await self._search.hybrid_search(question, top=5)
                print(f"[FALLBACK SEARCH] {len(sources)} results")
                # Clear no_match SQL so synthesis uses semantic results only
                if sql_rows and sql_rows[0].get("reason") == "no_match":
                    sql_rows = None
            except Exception as exc:
                print(f"[FALLBACK SEARCH ERROR] {exc}")

        if intent == IntentType.SUMMARY:
            try:
                sql_rows = await self._view.fetch_initiatives_aggregated()
                print(f"[SUMMARY ROWS] {len(sql_rows)} rows returned")
            except Exception as exc:
                print(f"[SUMMARY ERROR] {exc}")

        if intent in (IntentType.SEMANTIC, IntentType.SUMMARY):
            try:
                sources = await self._search.hybrid_search(question, top=5)
                print(f"[SEARCH] {len(sources)} results")
            except Exception as exc:
                print(f"[SEARCH ERROR] {exc}")

        async for chunk in self._llm.synthesize_stream(question, sources, sql_rows, history, sql_query):
            yield {"type": "chunk", "text": chunk}

        yield {
            "type": "meta",
            "intent": str(intent.value if hasattr(intent, "value") else intent),
            "sql_query": sql_query,
            "sql_error": sql_error,
            "row_count": len(sql_rows) if sql_rows else 0,
            "rows_preview": (sql_rows or [])[:20],
            "sources": [s.model_dump() for s in sources[:5]],
        }
