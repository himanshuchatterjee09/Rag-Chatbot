from datetime import date
from typing import AsyncIterator, List, Optional
from openai import AsyncAzureOpenAI
from ..config import Settings
from ..models import IntentType, SearchResult


def _build_system_prompt() -> str:
    today = date.today().strftime("%Y-%m-%d")
    return f"""You are an expert AI strategy analyst assistant. Today's date is {today}.
You have access to two data sources:
1. **AI Initiatives** – AI projects with fields: item_id, initiative_name, portfolio_team, owner, last_updated (Mon-YY format e.g. Apr-26), stage (Proposed/Live/PoC/Pilot/In Progress/Completed/On Hold/Blocked/Reframed/Stopped), confirmed_scout
2. **Portfolios** – Portfolio areas with fields: portfolio, portfolio_lead, uk_lead, ai_scout (email)

Guidelines:
- CRITICAL: When "Structured Query Results" are present in the context, use ONLY those numbers. Never use counts or figures from conversation history — the SQL data is always the ground truth.
- Answer accurately and concisely based only on provided context
- Be direct — only state what IS true, never list what something is NOT (e.g. don't say "X is not the UK lead")
- Use today's date ({today}) for any date calculations — never assume or guess the date
- The last_updated field is stored as YYYY-MM text (e.g. '2026-04' = April 2026). Use plain string comparison for date filters.
- For stage/count queries: report every row from the SQL results — do not omit any stages or groups
- For analytics: present numbers clearly in a table where possible
- Use markdown tables or bullet lists when they improve readability
- If information is missing or uncertain, say so explicitly rather than guessing"""


class LLMService:
    def __init__(self, settings: Settings):
        self._client = AsyncAzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
        )
        self._deployment = settings.azure_openai_chat_deployment

    def _build_context(
        self,
        sources: List[SearchResult],
        sql_rows: Optional[list],
        sql_query: Optional[str] = None,
    ) -> str:
        parts: List[str] = []
        if sql_rows:
            header = f"**Structured Query Results** (from: `{sql_query}`):\n" if sql_query else "**Structured Query Results:**\n"
            parts.append(header + _format_rows(sql_rows))
        if sources:
            parts.append(
                "**Semantic Search Results:**\n"
                + "\n---\n".join(
                    f"[{s.source_table}] {s.content}" for s in sources
                )
            )
        return "\n\n".join(parts) if parts else "No relevant data found."

    async def classify_intent(self, question: str) -> IntentType:
        resp = await self._client.chat.completions.create(
            model=self._deployment,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Classify the user question into exactly one of these categories:\n"
                        "- lookup: asking about a specific named initiative, person, or portfolio\n"
                        "- analytics: ANY question involving counts, totals, grouping, filtering, ranking, "
                        "date comparisons, or stage/owner/portfolio breakdowns. Examples: "
                        "'how many are live', 'summary by stage', 'count by portfolio', 'not updated in 30 days', "
                        "'who owns the most', 'which are blocked', 'list all completed'\n"
                        "- semantic: searching by concept or theme (e.g. 'what initiatives involve machine learning')\n"
                        "- summary: very broad open-ended narrative question with no specific filter or count\n"
                        "When in doubt, choose analytics over summary or semantic.\n"
                        "Respond with ONLY the single category word."
                    ),
                },
                {"role": "user", "content": question},
            ],
            temperature=0,
            max_tokens=10,
        )
        raw = resp.choices[0].message.content.strip().lower()
        try:
            return IntentType(raw)
        except ValueError:
            return IntentType.SEMANTIC

    async def generate_sql(self, question: str, schema: str, history: List[dict] = None) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    f"You are a T-SQL expert for Azure SQL Server. Today's date is {date.today().strftime('%Y-%m-%d')}.\n"
                    f"Database schema:\n{schema}\n\n"
                    "Rules:\n"
                    "- Generate a single valid SELECT statement only\n"
                    "- Use TOP 200 by default; only use a lower TOP if the user explicitly asks for a small number\n"
                    "- Use LIKE with % wildcards for partial name matches\n"
                    "- Use ISNULL() to handle NULLs in aggregates\n"
                    "- The last_updated column is in YYYY-MM text format (e.g. '2026-04' = April 2026). "
                    "Use plain string comparison for date filters (e.g. last_updated < '2026-04')\n"
                    "- When searching for a person by name, ALWAYS query both tables using a JOIN:\n"
                    "  SELECT p.portfolio, p.portfolio_lead, p.uk_lead, p.ai_scout,\n"
                    "         ai.item_id, ai.initiative_name, ai.stage, ai.owner, ai.last_updated\n"
                    "  FROM portfolios p\n"
                    "  LEFT JOIN ai_initiatives ai ON ai.portfolio_team = p.portfolio\n"
                    "  WHERE p.portfolio_lead LIKE '%Name%' OR p.uk_lead LIKE '%Name%'\n"
                    "     OR p.ai_scout LIKE '%Name%' OR ai.owner LIKE '%Name%'\n"
                    "- If the question uses pronouns (him, her, his, their) or refers to something from prior context, "
                    "resolve the reference from the conversation history before writing SQL\n"
                    "- Return ONLY the raw SQL — no markdown, no explanation"
                ),
            }
        ]
        if history:
            messages.extend(history[-4:])
        messages.append({"role": "user", "content": question})
        resp = await self._client.chat.completions.create(
            model=self._deployment,
            messages=messages,
            temperature=0,
            max_tokens=400,
        )
        sql = resp.choices[0].message.content.strip()
        return sql.replace("```sql", "").replace("```", "").strip()

    async def synthesize(
        self,
        question: str,
        sources: List[SearchResult],
        sql_rows: Optional[list],
        history: List[dict],
        intent: IntentType,
        sql_query: Optional[str] = None,
    ) -> str:
        context = self._build_context(sources, sql_rows, sql_query)
        messages = [{"role": "system", "content": _build_system_prompt()}]
        messages.extend(history[-6:])
        messages.append({
            "role": "user",
            "content": f"Context data:\n{context}\n\nQuestion: {question}",
        })
        resp = await self._client.chat.completions.create(
            model=self._deployment,
            messages=messages,
            temperature=0.2,
            max_tokens=1200,
        )
        return resp.choices[0].message.content

    async def synthesize_stream(
        self,
        question: str,
        sources: List[SearchResult],
        sql_rows: Optional[list],
        history: List[dict],
        sql_query: Optional[str] = None,
    ) -> AsyncIterator[str]:
        context = self._build_context(sources, sql_rows, sql_query)
        messages = [{"role": "system", "content": _build_system_prompt()}]
        # Skip history when SQL data is present — history may contain stale numbers
        if not sql_rows:
            messages.extend(history[-6:])
        messages.append({
            "role": "user",
            "content": f"Context data:\n{context}\n\nQuestion: {question}",
        })
        stream = await self._client.chat.completions.create(
            model=self._deployment,
            messages=messages,
            temperature=0.2,
            max_tokens=1200,
            stream=True,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta


def _format_rows(rows: list) -> str:
    if not rows:
        return "No rows returned."
    if "error" in rows[0]:
        return f"Query error: {rows[0]['error']}"
    headers = list(rows[0].keys())
    lines = [" | ".join(headers), " | ".join("---" for _ in headers)]
    for row in rows[:200]:
        lines.append(" | ".join(str(row.get(h, "")) for h in headers))
    return "\n".join(lines)
