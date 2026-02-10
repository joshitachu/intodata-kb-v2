-- Users
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) NOT NULL UNIQUE,
    email VARCHAR(255) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Roles
CREATE TABLE IF NOT EXISTS roles (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    description VARCHAR(500),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- User <-> Role M2M
CREATE TABLE IF NOT EXISTS user_roles (
    user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id INT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, role_id)
);

-- Documents
CREATE TABLE IF NOT EXISTS documents (
    id SERIAL PRIMARY KEY,
    filename VARCHAR(500) NOT NULL,
    file_type VARCHAR(20) NOT NULL,
    source VARCHAR(500),
    author VARCHAR(255),
    department VARCHAR(255),
    chunk_count INT NOT NULL DEFAULT 0,
    uploaded_by INT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Ingestion jobs
CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id SERIAL PRIMARY KEY,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    chunks_created INT NOT NULL DEFAULT 0,
    error_message TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    document_id INT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Query logs
CREATE TABLE IF NOT EXISTS query_logs (
    id SERIAL PRIMARY KEY,
    query_text TEXT NOT NULL,
    response_text TEXT,
    sources JSONB,
    latency_ms INT,
    user_id INT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Useful indexes (optional but align with model intent)
CREATE INDEX IF NOT EXISTS idx_documents_filename ON documents (filename);
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_status ON ingestion_jobs (status);
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_document_id ON ingestion_jobs (document_id);
CREATE INDEX IF NOT EXISTS idx_query_logs_user_id ON query_logs (user_id);