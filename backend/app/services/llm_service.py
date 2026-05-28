from datetime import date
from typing import AsyncIterator, List, Optional
from openai import AsyncAzureOpenAI
from ..config import Settings
from ..models import IntentType, SearchResult
from ._aad_credential import HybridSyncTokenCredential


_OPENAI_SCOPE = "https://cognitiveservices.azure.com/.default"


def _openai_token_provider():
    return HybridSyncTokenCredential().get_token(_OPENAI_SCOPE).token


def _build_system_prompt() -> str:
    today = date.today().strftime("%Y-%m-%d")
    return f"""You are an expert AI strategy analyst assistant. Today's date is {today}.
You have access to two data sources:
1. **AI Initiatives** – AI projects with fields: item_id, initiative_name, portfolio_team, owner, last_updated (Mon-YY format e.g. Apr-26), stage (Proposed/Live/PoC/Pilot/In Progress/Completed/On Hold/Blocked/Reframed/Stopped), confirmed_scout
2. **Portfolios** – Portfolio areas with fields: portfolio, portfolio_lead, uk_lead, ai_scout (email)

Business glossary (use these mappings when the user uses these terms):
- "inflight" / "in flight" / "work in progress" / "being delivered" → Stage IN ('In Progress', 'PoC/Pilot')
- "live" / "in production" / "deployed" → Stage = 'Live'
- "active" / "ongoing" → Stage IN ('In Progress', 'PoC/Pilot', 'Live')
- "done" / "delivered" / "shipped" → Stage = 'Completed'
- "stalled" / "stuck" → Stage IN ('On Hold', 'Blocked')
- "needs attention" / "urgent attention" / "urgent" / "needs review" / "at risk" → an initiative qualifies if ALL of the following:
    (a) Stage <> 'Reframed' (always exclude Reframed)
    AND (b) ANY of these conditions:
         - Stage IN ('Blocked', 'On Hold'), OR
         - Updated_At < DATEADD(day, -30, GETDATE())   (stale — no activity in 30+ days), OR
         - Stage IN ('Live', 'Completed') AND (Impact IS NULL OR LTRIM(RTRIM(Impact)) = '')   (missing impact documentation)
- "pipeline" / "upcoming" / "planned" → Stage = 'Proposed'

Guidelines:
- Structured Query Results are the AUTHORITATIVE source. When they contain rows, they ARE the answer.
  Use them in full — never substitute the smaller set of Semantic Search Results when SQL has rows.
- Semantic Search Results are SUPPLEMENTARY context for descriptive details only. Use them to enrich
  individual rows (e.g. add a description), NOT to replace or shrink the SQL row list.
- For COUNTS and analytics: use ONLY the numbers in "Structured Query Results". A COUNT of 0 means "0 initiatives"
  or "none found" — never say "no information available" when a count is present.
- ONLY use Semantic Search Results as the primary answer when SQL has 0 rows.
- Never use counts or figures from conversation history — the SQL data is always the ground truth when present.
- Answer accurately and concisely based only on provided context
- Be direct — only state what IS true. NEVER write sentences about what someone/something is NOT.
  Forbidden patterns: "He is not...", "They are not listed as...", "X does not appear in...", "Not the AI scout".
  If a person has no role in a category, simply omit that category from the answer — don't mention absence.
- Use today's date ({today}) for any date calculations — never assume or guess the date
- The last_updated field is stored as YYYY-MM text (e.g. '2026-04' = April 2026). Use plain string comparison for date filters.
- For stage/count queries: report every row from the SQL results — do not omit any stages or groups
- For analytics: present numbers clearly in a table where possible
- When SQL results have multiple rows, render them as a STRICT markdown table. EVERY line of the table — header, separator, and every data row — MUST start with `|` and end with `|`. Never use tabs, spaces-only, or unaligned formats. Example (correct):
  | Name | Stage |
  |------|-------|
  | A    | Live  |
  | B    | PoC   |
  Do NOT do this (wrong, mixes tab and pipe):
  Name\tStage
  A\tLive
  | B | PoC |
- NUMBER ACCURACY RULE: Every number you state in prose (e.g. "There are X initiatives") MUST exactly match
  the "_Total rows in this result: N_" line in the context, OR a COUNT value from a single-cell aggregate result.
  Do NOT estimate or round. If your prose says "There are 28 initiatives" and the data has 31 rows, you are wrong.
  Re-check: count the rows in the table you're about to write — that count IS the number to state.
- COMPLETENESS RULE: When the context contains N rows, you MUST include all N rows in your table.
  Never silently truncate. If you cannot include all N (e.g. token budget), START your response with this exact
  one-line warning: "**Note: showing X of N results.**" and then include the X you have room for.
  Never show 5 rows of a 78-row result as if they are "the" answer — that is misleading.
- Before listing, state the total count plainly: "There are N initiatives. Here they are:" then the table.
- If information is missing or uncertain, say so explicitly rather than guessing"""


class LLMService:
    def __init__(self, settings: Settings):
        self._client = AsyncAzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            azure_ad_token_provider=_openai_token_provider,
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

    async def classify_intent(self, question: str, history: List[dict] = None) -> IntentType:
        messages = [
            {
                "role": "system",
                "content": (
                    "Classify the user question into exactly one of these categories:\n"
                    "- lookup: asking about a SPECIFIC named entity that clearly exists in this dataset "
                    "(a known person, initiative, or portfolio). Do NOT use lookup for vague phrases, "
                    "capitalized topics, or things the user is asking ABOUT generically.\n"
                    "- analytics: ANY question involving counts, totals, grouping, filtering, ranking, sorting, "
                    "top-N, most/least recent, date comparisons, or stage/owner/portfolio breakdowns. "
                    "If the question asks for a LIST of initiatives (e.g. 'top 5', 'recent 10', 'all Live', "
                    "'show me initiatives that...', 'give me a list with their descriptions'), it is analytics — "
                    "the SQL must use ORDER BY / WHERE / TOP N. Examples: "
                    "'how many are live', 'summary by stage', 'count by portfolio', 'not updated in 30 days', "
                    "'who owns the most', 'which are blocked', 'list all completed', 'top 5 most recent', "
                    "'show initiatives along with descriptions'\n"
                    "- semantic: 'tell me about X', 'what initiatives involve X', or any conceptual/exploratory "
                    "query where X is a topic, theme, or domain (not a specific named entity)\n"
                    "- summary: very broad open-ended narrative question with no specific filter or count\n"
                    "CRITICAL — Follow-up questions: if the question is a short follow-up that depends on a prior "
                    "question (e.g. 'and what are they', 'show them', 'list them', 'which ones', 'tell me more'), "
                    "use the conversation history to resolve what 'they/them/those' refer to, and inherit the SAME "
                    "category as the prior turn. If the prior turn was analytics (a count or filter), the follow-up "
                    "is ALSO analytics — the SQL should list the same filtered set.\n"
                    "When in doubt between lookup and semantic, choose semantic. When in doubt between "
                    "summary and analytics, choose analytics.\n"
                    "Respond with ONLY the single category word."
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
                    "Business glossary — map these terms to the right Stage filter:\n"
                    "- 'inflight' / 'in flight' / 'work in progress' → Stage IN ('In Progress', 'PoC/Pilot')\n"
                    "- 'live' / 'deployed' / 'in production' → Stage = 'Live'\n"
                    "- 'active' / 'ongoing' → Stage IN ('In Progress', 'PoC/Pilot', 'Live')\n"
                    "- 'done' / 'delivered' / 'shipped' → Stage = 'Completed'\n"
                    "- 'stalled' / 'stuck' / 'paused' → Stage IN ('On Hold', 'Blocked')\n"
                    "- 'needs attention' / 'urgent attention' / 'urgent' / 'needs review' / 'at risk' → use this exact filter:\n"
                    "    WHERE Stage <> 'Reframed'\n"
                    "      AND (Stage IN ('Blocked', 'On Hold')\n"
                    "           OR Updated_At < DATEADD(day, -30, GETDATE())\n"
                    "           OR (Stage IN ('Live', 'Completed')\n"
                    "               AND (Impact IS NULL OR LTRIM(RTRIM(CAST(Impact AS NVARCHAR(MAX)))) = '')))\n"
                    "- 'pipeline' / 'upcoming' / 'planned' → Stage = 'Proposed'\n"
                    "Rules:\n"
                    "- Your response MUST be valid T-SQL ONLY. No English, no explanation, no markdown.\n"
                    "  If the user's question cannot be answered from the schema, return: SELECT 'no_match' AS reason\n"
                    "- For SIMPLE questions: generate a single SELECT statement.\n"
                    "- For COMPOUND questions (e.g. 'how many X AND list top 5 Y'), generate up to 3 SELECT statements "
                    "separated by a line containing only `---`. Each statement runs independently and results are "
                    "combined. Example:\n"
                    "  SELECT COUNT(DISTINCT Initiative_ID) AS total FROM [dbo].[Initiative_Details_View]\n"
                    "  ---\n"
                    "  SELECT TOP 5 Initiative_ID, MAX(Initiative_Name) AS name, MAX(Stage) AS stage\n"
                    "  FROM [dbo].[Initiative_Details_View] GROUP BY Initiative_ID ORDER BY MAX(Updated_At) DESC\n"
                    "- The local table `ai_initiatives` DOES NOT EXIST — never reference it. ALL initiative data is in the external view.\n"
                    "- TWO data sources exist on DIFFERENT databases — pick the right one, NEVER JOIN across them:\n"
                    "  1) External view [dbo].[Initiative_Details_View] — for ALL initiative data (counts, lists, filters, descriptions, owners, stages)\n"
                    "  2) Local table portfolios — ONLY for 'who is X?' / 'who leads X?' questions about portfolio leadership\n"
                    "- T-SQL pitfalls to AVOID:\n"
                    "  * STRING_AGG does NOT support DISTINCT. Wrong: STRING_AGG(DISTINCT x, ', '). Right: STRING_AGG(x, ', ').\n"
                    "    If you need distinct values, use a derived table: SELECT STRING_AGG(p, ', ') FROM (SELECT DISTINCT Participant_Name AS p FROM ...) t.\n"
                    "  * Don't add OPTION (MAXDOP n) hints — not needed.\n"
                    "- 'initiatives under [person]', 'initiatives owned by [person]', 'their initiatives', 'his/her initiatives' →\n"
                    "  MUST use this exact pattern (no JOINs, no subqueries, no portfolios table):\n"
                    "    SELECT COUNT(DISTINCT Initiative_ID) FROM [dbo].[Initiative_Details_View] WHERE Participant_Name LIKE '%[person]%'\n"
                    "  NEVER use a subquery like `WHERE Portfolio_Label IN (SELECT portfolio FROM portfolios WHERE ...)`.\n"
                    "  The portfolios table is on a DIFFERENT database — cross-DB subqueries fail.\n"
                    "  A person can be a Participant in initiatives even if they aren't a portfolio lead — search Participant_Name first.\n"
                    "  Only filter by Portfolio_Label if the question explicitly names a portfolio (e.g. 'initiatives in the Retail portfolio').\n"
                    "- The view returns one row per (initiative, participant) pair. Always handle duplicates:\n"
                    "  * COUNT initiatives: SELECT COUNT(DISTINCT Initiative_ID) FROM [dbo].[Initiative_Details_View] WHERE ...\n"
                    "  * GROUP BY stage: SELECT Stage, COUNT(DISTINCT Initiative_ID) FROM [dbo].[Initiative_Details_View] GROUP BY Stage\n"
                    "  * LIST initiatives (correct pattern): SELECT Initiative_ID, MAX(Initiative_Name) AS Initiative_Name,\n"
                    "       MAX(Stage) AS Stage, MAX(Portfolio_Label) AS Portfolio_Label,\n"
                    "       STRING_AGG(Participant_Name, ', ') AS Participants\n"
                    "    FROM [dbo].[Initiative_Details_View] WHERE ... GROUP BY Initiative_ID\n"
                    "  * DO NOT mix SELECT DISTINCT with MAX() — it's a syntax error. Use GROUP BY + MAX().\n"
                    "  * find by participant: WHERE Participant_Name LIKE '%Name%'\n"
                    "  * find by portfolio: WHERE Portfolio_Label LIKE '%Name%'\n"
                    "  * find by stage: WHERE Stage = 'Live'\n"
                    "  * Updated_At is DATETIME — compare directly: WHERE Updated_At >= '2026-01-01'\n"
                    "- 'AI initiatives' / 'initiatives' refer to ALL rows in the view — do NOT add a filter for the word 'AI'. The view itself is the AI initiatives dataset.\n"
                    "- Use LIKE with % wildcards for partial name matches\n"
                    "- Use TOP 200 only on the outer SELECT when listing rows (after GROUP BY)\n"
                    "- When asked who someone is (e.g. 'Who is Manit Mehta'), query the portfolios table:\n"
                    "  SELECT * FROM portfolios WHERE portfolio_lead LIKE '%Name%' OR uk_lead LIKE '%Name%' OR ai_scout LIKE '%Name%'\n"
                    "- If a question crosses both sources (e.g. 'initiatives under Manit Mehta's portfolio'), query the view\n"
                    "  using the portfolio name from prior conversation context, NOT a JOIN.\n"
                    "- If the question uses pronouns (him, her, his, their) or refers to something from prior context, "
                    "resolve the reference from the conversation history before writing SQL\n"
                    "- FOLLOW-UP RULE: When the question is a short follow-up like 'what are those?', 'list them', "
                    "'show them', 'which ones', INHERIT THE EXACT WHERE-CLAUSE from the MOST RECENT count/analytics SQL "
                    "in the conversation history. Do NOT invent a new filter or revert to filters from earlier turns. "
                    "Example: prior turn was COUNT WHERE Participant_Name LIKE '%X%' returning 8 → follow-up MUST also "
                    "use WHERE Participant_Name LIKE '%X%' (just SELECT the rows instead of COUNT).\n"
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
            max_tokens=4000,
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
    # Split into labeled groups if multi-query result
    if any("__query_label__" in r for r in rows):
        groups = []
        current_label = None
        current_rows: list = []
        for r in rows:
            if "__query_label__" in r:
                if current_rows:
                    groups.append((current_label, current_rows))
                current_label = f"{r['__query_label__']} (SQL: `{r.get('sql', '')}`)"
                current_rows = []
            else:
                current_rows.append(r)
        if current_rows:
            groups.append((current_label, current_rows))
        return "\n\n".join(f"### {label}\n{_format_single(rs)}" for label, rs in groups)
    return _format_single(rows)


def _format_single(rows: list) -> str:
    if not rows:
        return "No rows returned."
    if "error" in rows[0]:
        return f"Query error: {rows[0]['error']}"
    headers = list(rows[0].keys())
    total = len(rows)
    lines = [f"_Total rows in this result: {total}_",
             "| " + " | ".join(headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows[:200]:
        cells = [str(row.get(h, "")).replace("|", "\\|").replace("\n", " ") for h in headers]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)
