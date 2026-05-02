-- Run this once against your Azure SQL database to create all required tables.
-- Compatible with Azure SQL (T-SQL).

-- ── Company Profile ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS company_profile (
    id               INT            PRIMARY KEY IDENTITY(1,1),
    company_name     NVARCHAR(200)  NOT NULL UNIQUE,
    industry         NVARCHAR(100),
    employee_count   INT,
    headquarters     NVARCHAR(200),
    strategic_focus  NVARCHAR(MAX),
    ai_vision        NVARCHAR(MAX),
    last_updated     DATE           DEFAULT CAST(GETDATE() AS DATE)
);

-- ── AI Initiatives ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ai_initiatives (
    initiative_id       INT            PRIMARY KEY IDENTITY(1,1),
    initiative_name     NVARCHAR(200)  NOT NULL UNIQUE,
    status              NVARCHAR(50)   CHECK (status IN ('Active','Completed','Planned','On Hold')),
    owner               NVARCHAR(100),
    department          NVARCHAR(100),
    budget_allocated    DECIMAL(18,2),
    budget_spent        DECIMAL(18,2),
    start_date          DATE,
    target_end_date     DATE,
    actual_end_date     DATE,
    priority            NVARCHAR(20)   CHECK (priority IN ('High','Medium','Low')),
    description         NVARCHAR(MAX),
    objectives          NVARCHAR(MAX),
    kpis                NVARCHAR(MAX),
    progress_percentage INT            CHECK (progress_percentage BETWEEN 0 AND 100),
    risks               NVARCHAR(MAX),
    last_updated        DATE           DEFAULT CAST(GETDATE() AS DATE),
    created_at          DATETIME       DEFAULT GETDATE()
);

CREATE INDEX idx_initiatives_status     ON ai_initiatives(status);
CREATE INDEX idx_initiatives_department ON ai_initiatives(department);
CREATE INDEX idx_initiatives_owner      ON ai_initiatives(owner);
CREATE INDEX idx_initiatives_priority   ON ai_initiatives(priority);

-- ── AI Adoption Index ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ai_adoption_index (
    id               INT            PRIMARY KEY IDENTITY(1,1),
    dimension        NVARCHAR(100)  NOT NULL,
    sub_dimension    NVARCHAR(100),
    current_score    DECIMAL(4,2)   CHECK (current_score BETWEEN 0 AND 5),
    target_score     DECIMAL(4,2)   CHECK (target_score  BETWEEN 0 AND 5),
    maturity_level   NVARCHAR(50)   CHECK (maturity_level IN
                         ('Initial','Developing','Defined','Managed','Optimizing')),
    benchmark_score  DECIMAL(4,2),
    gap_analysis     NVARCHAR(MAX),
    recommendations  NVARCHAR(MAX),
    assessment_date  DATE,
    assessor         NVARCHAR(100),
    notes            NVARCHAR(MAX),
    CONSTRAINT uq_adoption UNIQUE (dimension, sub_dimension)
);

CREATE INDEX idx_adoption_dimension ON ai_adoption_index(dimension);
CREATE INDEX idx_adoption_maturity  ON ai_adoption_index(maturity_level);
