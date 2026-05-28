from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Azure OpenAI (AAD auth — api_key kept optional for backward compat)
    azure_openai_endpoint: str
    azure_openai_api_key: str = ""
    azure_openai_api_version: str = "2024-05-01-preview"
    azure_openai_chat_deployment: str = "gpt-4o"
    azure_openai_embedding_deployment: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    # Azure SQL (Managed Identity / AAD auth — username/password kept optional for legacy/local fallback)
    azure_sql_server: str
    azure_sql_database: str
    azure_sql_username: str = ""
    azure_sql_password: str = ""
    azure_sql_driver: str = "ODBC Driver 18 for SQL Server"

    # External Source DB (the SQL view, read-only, Azure AD auth)
    external_sql_server: str = ""
    external_sql_database: str = ""
    external_sql_view: str = "[dbo].[Initiative_Details_View]"

    # Azure AI Search (AAD auth — api_key kept optional for backward compat)
    azure_search_endpoint: str
    azure_search_api_key: str = ""
    azure_search_index_name: str = "ai-initiatives"

    # App
    app_name: str = "AI Initiatives Chatbot"
    debug: bool = False
    cors_origins: str = "*"

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
