from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
from enum import Enum


class IntentType(str, Enum):
    LOOKUP = "lookup"
    ANALYTICS = "analytics"
    SEMANTIC = "semantic"
    SUMMARY = "summary"


class Message(BaseModel):
    role: str
    content: str


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000)
    session_id: Optional[str] = None
    conversation_history: Optional[List[Message]] = []
    stream: bool = False


class SearchResult(BaseModel):
    content: str
    source_table: str
    score: float
    record_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class QueryResponse(BaseModel):
    answer: str
    intent: IntentType
    sources: List[SearchResult] = []
    sql_query: Optional[str] = None
    confidence: float = 0.0
    processing_time_ms: Optional[float] = None


class IndexRequest(BaseModel):
    table: str = "all"
    recreate_index: bool = False


class HealthResponse(BaseModel):
    status: str
    services: Dict[str, Any]
