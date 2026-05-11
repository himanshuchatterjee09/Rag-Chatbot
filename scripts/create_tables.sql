-- Run this once against your Azure SQL database.
-- Compatible with Azure SQL (T-SQL).

-- ── Portfolios ────────────────────────────────────────────────────────────────
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'portfolios')
CREATE TABLE portfolios (
    id              INT            PRIMARY KEY IDENTITY(1,1),
    portfolio       NVARCHAR(300)  NOT NULL UNIQUE,
    portfolio_lead  NVARCHAR(100),
    uk_lead         NVARCHAR(100),
    ai_scout        NVARCHAR(200)
);

-- ── AI Initiatives ────────────────────────────────────────────────────────────
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'ai_initiatives')
CREATE TABLE ai_initiatives (
    item_id          NVARCHAR(10)   PRIMARY KEY,
    initiative_name  NVARCHAR(400)  NOT NULL,
    portfolio_team   NVARCHAR(300),
    owner            NVARCHAR(500),
    last_updated     NVARCHAR(20),
    stage            NVARCHAR(50),
    confirmed_scout  NVARCHAR(200)
);

CREATE INDEX idx_initiatives_stage         ON ai_initiatives(stage);
CREATE INDEX idx_initiatives_portfolio     ON ai_initiatives(portfolio_team);
