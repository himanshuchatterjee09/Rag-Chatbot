from typing import AsyncIterator, List, Optional
from openai import AsyncAzureOpenAI
from ..config import Settings
from ..models import IntentType, SearchResult


SYSTEM_PROMPT = """You are an expert AI strategy analyst for a company's AI initiatives chatbot.
You have access to three data sources:
1. **Company Profile** – company background, industry, AI vision, strategic goals
2. **AI Initiatives** – active/planned/completed AI projects with status, owners, departments, budgets, timelines, KPIs, and progress
3. **AI Adoption Index** – maturity scores (1–5 scale) across dimensions like Strategy, Data, Technology, Talent, and Governance

Guidelines:
- Answer accurately and concisely based only on provided context
- For status queries: include progress %, dates, owner, and risks if available
- For analytics: present numbers clearly with comparisons or rankings where helpful
- For maturity scores: interpret the 1–5 scale (1=Initial, 2=Developing, 3=Defined, 4=Managed, 5=Optimizing)
- Use markdown tables or bullet lists when they improve readability
- Always mention the data source (table name) when citing specific records
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
    ) -> str:
        parts: List[str] = []
        if sql_rows:
            parts.append("**Structured Query Results:**\n" + _format_rows(sql_rows))
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
                        "- lookup: asking about a specific named initiative or company detail\n"
                        "- analytics: counting, aggregating, comparing, ranking (how many, which most, etc.)\n"
                        "- semantic: searching by concept, theme, or keyword without a specific record name\n"
                        "- summary: overview, narrative, maturity assessment, or broad strategic question\n"
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

    async def generate_sql(self, question: str, schema: str) -> str:
        resp = await self._client.chat.completions.create(
            model=self._deployment,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a T-SQL expert for Azure SQL Server.\n"
                        f"Database schema:\n{schema}\n\n"
                        "Rules:\n"
                        "- Generate a single valid SELECT statement only\n"
                        "- Use TOP 50 unless the user asks for all records\n"
                        "- Use LIKE with % wildcards for partial name matches\n"
                        "- Use ISNULL() to handle NULLs in aggregates\n"
                        "- Return ONLY the raw SQL — no markdown, no explanation"
                    ),
                },
                {"role": "user", "content": question},
            ],
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
    ) -> str:
        context = self._build_context(sources, sql_rows)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
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
    ) -> AsyncIterator[str]:
        context = self._build_context(sources, sql_rows)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
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
    for row in rows[:50]:
        lines.append(" | ".join(str(row.get(h, "")) for h in headers))
    return "\n".join(lines)
