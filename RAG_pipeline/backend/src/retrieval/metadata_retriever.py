"""Metadata retriever — SQL queries for structured project/document data.

Provides formatted text answers for metadata questions (project lists,
document stats, system overview) that don't require vector search.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.document import Document
from src.retrieval.metadata_models import MetadataIntent

logger = logging.getLogger(__name__)


@dataclass
class MetadataContext:
    """Result of a metadata query, pre-formatted for the LLM."""

    text: str
    source_label: str
    query_type: str


def _project_id_filter(project_ids: list[str] | None) -> str:
    """Build a WHERE clause fragment for project-level RBAC.

    Returns empty string for admins (project_ids=None), or a SQL IN clause.
    """
    if project_ids is None:
        return ""  # admin — no restriction
    if not project_ids:
        return " AND 1=0"  # no access
    quoted = ", ".join(f"'{pid}'" for pid in project_ids)
    return f" AND p.id IN ({quoted})"


async def get_project_list(
    db: AsyncSession,
    project_ids: list[str] | None = None,
) -> MetadataContext:
    """List all accessible projects with key metadata."""
    rbac = _project_id_filter(project_ids)
    sql = text(f"""
        SELECT p.id, p.display_name, p.platform, p.status,
               p.job_count, p.doc_count,
               p.last_scan_at, p.last_generate_at
        FROM projects p
        WHERE 1=1 {rbac}
        ORDER BY p.display_name
    """)
    result = await db.execute(sql)
    rows = result.fetchall()

    if not rows:
        return MetadataContext(
            text="No projects found in the system.",
            source_label="System metadata",
            query_type="project_list",
        )

    lines = [f"**Projects ({len(rows)} total):**\n"]
    lines.append("| Project | Platform | Status | Jobs | Docs | Last Scan | Last Doc Gen |")
    lines.append("|---------|----------|--------|------|------|-----------|-------------|")
    for r in rows:
        scan = r.last_scan_at.strftime("%Y-%m-%d") if r.last_scan_at else "Never"
        gen = r.last_generate_at.strftime("%Y-%m-%d") if r.last_generate_at else "Never"
        lines.append(
            f"| {r.display_name} | {r.platform} | {r.status} "
            f"| {r.job_count} | {r.doc_count} | {scan} | {gen} |"
        )

    return MetadataContext(
        text="\n".join(lines),
        source_label="System metadata",
        query_type="project_list",
    )


async def get_project_detail(
    db: AsyncSession,
    project_identifier: str,
    project_ids: list[str] | None = None,
) -> MetadataContext:
    """Detailed info about a single project, matched by ID or display name."""
    rbac = _project_id_filter(project_ids)
    sql = text(f"""
        SELECT p.id, p.display_name, p.platform, p.source_type, p.status,
               p.repo_url, p.local_path, p.job_count, p.doc_count,
               p.last_scan_at, p.last_generate_at, p.last_rag_push_at,
               p.created_at, p.error_message
        FROM projects p
        WHERE (LOWER(p.id) = LOWER(:identifier) OR LOWER(p.display_name) ILIKE :pattern)
        {rbac}
        LIMIT 1
    """)
    result = await db.execute(sql, {
        "identifier": project_identifier,
        "pattern": f"%{project_identifier}%",
    })
    row = result.fetchone()

    if not row:
        return MetadataContext(
            text=f"No project found matching '{project_identifier}'.",
            source_label="System metadata",
            query_type="project_detail",
        )

    # Get document count from documents table for this project
    doc_result = await db.execute(
        select(func.count(Document.id)).where(Document.project_id == row.id)
    )
    rag_doc_count = doc_result.scalar_one()

    lines = [
        f"**Project: {row.display_name}**\n",
        f"- **ID**: {row.id}",
        f"- **Platform**: {row.platform}",
        f"- **Source type**: {row.source_type}",
        f"- **Status**: {row.status}",
        f"- **Integration jobs**: {row.job_count}",
        f"- **Generated docs**: {row.doc_count}",
        f"- **Documents in RAG**: {rag_doc_count}",
        f"- **Last scan**: {row.last_scan_at.strftime('%Y-%m-%d %H:%M') if row.last_scan_at else 'Never'}",
        f"- **Last doc generation**: {row.last_generate_at.strftime('%Y-%m-%d %H:%M') if row.last_generate_at else 'Never'}",
        f"- **Last RAG push**: {row.last_rag_push_at.strftime('%Y-%m-%d %H:%M') if row.last_rag_push_at else 'Never'}",
        f"- **Created**: {row.created_at.strftime('%Y-%m-%d') if row.created_at else 'Unknown'}",
    ]
    if row.repo_url:
        lines.append(f"- **Repository**: {row.repo_url}")
    if row.error_message:
        lines.append(f"- **Last error**: {row.error_message}")

    return MetadataContext(
        text="\n".join(lines),
        source_label=f"Project: {row.display_name}",
        query_type="project_detail",
    )


async def get_document_stats(
    db: AsyncSession,
    project_ids: list[str] | None = None,
    project_filter: str | None = None,
) -> MetadataContext:
    """Document counts by type and project."""
    query = select(
        Document.project_id,
        Document.file_type,
        func.count(Document.id).label("count"),
        func.sum(Document.chunk_count).label("total_chunks"),
    ).group_by(Document.project_id, Document.file_type)

    # RBAC filter
    if project_ids is not None:
        if not project_ids:
            return MetadataContext(
                text="You don't have access to any projects.",
                source_label="System metadata",
                query_type="document_stats",
            )
        query = query.where(Document.project_id.in_(project_ids))

    # Optional project name filter
    if project_filter:
        # Resolve project name to ID
        pid_sql = text(
            "SELECT id FROM projects WHERE LOWER(id) = LOWER(:name) OR LOWER(display_name) ILIKE :pattern LIMIT 1"
        )
        pid_result = await db.execute(pid_sql, {"name": project_filter, "pattern": f"%{project_filter}%"})
        pid_row = pid_result.fetchone()
        if pid_row:
            query = query.where(Document.project_id == pid_row.id)
        else:
            return MetadataContext(
                text=f"No project found matching '{project_filter}'.",
                source_label="System metadata",
                query_type="document_stats",
            )

    result = await db.execute(query.order_by(Document.project_id, Document.file_type))
    rows = result.fetchall()

    if not rows:
        return MetadataContext(
            text="No documents found in the system.",
            source_label="System metadata",
            query_type="document_stats",
        )

    total_docs = sum(r.count for r in rows)
    total_chunks = sum(r.total_chunks or 0 for r in rows)

    lines = [f"**Document Statistics ({total_docs} documents, {total_chunks} chunks total):**\n"]
    lines.append("| Project | File Type | Documents | Chunks |")
    lines.append("|---------|-----------|-----------|--------|")
    for r in rows:
        proj = r.project_id or "(no project)"
        lines.append(f"| {proj} | {r.file_type or 'unknown'} | {r.count} | {r.total_chunks or 0} |")

    return MetadataContext(
        text="\n".join(lines),
        source_label="System metadata",
        query_type="document_stats",
    )


async def get_document_list(
    db: AsyncSession,
    project_ids: list[str] | None = None,
    project_filter: str | None = None,
    limit: int = 20,
) -> MetadataContext:
    """List documents with metadata."""
    query = select(Document).order_by(Document.id.desc()).limit(limit)

    # RBAC filter
    if project_ids is not None:
        if not project_ids:
            return MetadataContext(
                text="You don't have access to any projects.",
                source_label="System metadata",
                query_type="document_list",
            )
        query = query.where(Document.project_id.in_(project_ids))

    # Optional project name filter
    if project_filter:
        pid_sql = text(
            "SELECT id FROM projects WHERE LOWER(id) = LOWER(:name) OR LOWER(display_name) ILIKE :pattern LIMIT 1"
        )
        pid_result = await db.execute(pid_sql, {"name": project_filter, "pattern": f"%{project_filter}%"})
        pid_row = pid_result.fetchone()
        if pid_row:
            query = query.where(Document.project_id == pid_row.id)
        else:
            return MetadataContext(
                text=f"No project found matching '{project_filter}'.",
                source_label="System metadata",
                query_type="document_list",
            )

    result = await db.execute(query)
    docs = result.scalars().all()

    if not docs:
        return MetadataContext(
            text="No documents found.",
            source_label="System metadata",
            query_type="document_list",
        )

    lines = [f"**Documents (showing {len(docs)}, most recent first):**\n"]
    lines.append("| # | Filename | Type | Project | Chunks |")
    lines.append("|---|----------|------|---------|--------|")
    for i, doc in enumerate(docs, 1):
        proj = doc.project_id or "-"
        lines.append(
            f"| {i} | {doc.filename} | {doc.file_type or '-'} | {proj} | {doc.chunk_count} |"
        )

    return MetadataContext(
        text="\n".join(lines),
        source_label="System metadata",
        query_type="document_list",
    )


async def get_system_overview(
    db: AsyncSession,
    project_ids: list[str] | None = None,
) -> MetadataContext:
    """High-level system overview: project count, doc count, total chunks."""
    # Project count
    rbac = _project_id_filter(project_ids)
    proj_sql = text(f"SELECT COUNT(*) FROM projects p WHERE 1=1 {rbac}")
    proj_result = await db.execute(proj_sql)
    project_count = proj_result.scalar_one()

    # Document stats
    doc_query = select(
        func.count(Document.id).label("doc_count"),
        func.coalesce(func.sum(Document.chunk_count), 0).label("total_chunks"),
    )
    if project_ids is not None:
        if not project_ids:
            doc_query = doc_query.where(Document.project_id.in_([]))
        else:
            doc_query = doc_query.where(Document.project_id.in_(project_ids))
    doc_result = await db.execute(doc_query)
    doc_row = doc_result.fetchone()
    doc_count = doc_row.doc_count
    total_chunks = doc_row.total_chunks

    # Project names
    names_sql = text(f"""
        SELECT p.display_name, p.platform FROM projects p WHERE 1=1 {rbac} ORDER BY p.display_name
    """)
    names_result = await db.execute(names_sql)
    projects = names_result.fetchall()

    lines = [
        "**System Overview:**\n",
        f"- **Projects**: {project_count}",
        f"- **Documents in RAG**: {doc_count}",
        f"- **Total chunks**: {total_chunks}",
        "",
    ]
    if projects:
        lines.append("**Available projects:**")
        for p in projects:
            lines.append(f"- {p.display_name} ({p.platform})")

    return MetadataContext(
        text="\n".join(lines),
        source_label="System metadata",
        query_type="system_overview",
    )


async def get_system_knowledge_summary(
    db: AsyncSession,
    project_ids: list[str] | None = None,
) -> str | None:
    """Lightweight ~100-token summary for injection into ALL queries.

    Returns a brief string like:
        "Available projects: Vaxxinova (15 docs), Acme (8 docs). Total: 23 documents."

    Returns None if no projects exist.
    """
    rbac = _project_id_filter(project_ids)
    sql = text(f"""
        SELECT p.display_name,
               (SELECT COUNT(*) FROM documents d WHERE d.project_id = p.id) as doc_count
        FROM projects p
        WHERE 1=1 {rbac}
        ORDER BY p.display_name
    """)
    result = await db.execute(sql)
    rows = result.fetchall()

    if not rows:
        return None

    parts = [f"{r.display_name} ({r.doc_count} docs)" for r in rows]
    total = sum(r.doc_count for r in rows)
    return f"Available projects: {', '.join(parts)}. Total: {total} documents in the knowledge base."


async def get_user_profile(
    db: AsyncSession,
    user_id: int,
) -> MetadataContext:
    """User profile: username, roles, permissions, assigned projects."""
    # User + roles + permissions
    user_sql = text("""
        SELECT u.username, u.email, u.is_superadmin,
               u.last_login_at, u.created_at
        FROM users u
        WHERE u.id = :uid
    """)
    user_result = await db.execute(user_sql, {"uid": user_id})
    user_row = user_result.fetchone()

    if not user_row:
        return MetadataContext(
            text="Could not find your user profile.",
            source_label="User profile",
            query_type="my_profile",
        )

    # Roles
    roles_sql = text("""
        SELECT r.name FROM roles r
        JOIN user_roles ur ON ur.role_id = r.id
        WHERE ur.user_id = :uid
        ORDER BY r.name
    """)
    roles_result = await db.execute(roles_sql, {"uid": user_id})
    roles = [r.name for r in roles_result.fetchall()]

    # Permissions (via roles)
    if user_row.is_superadmin:
        perms_sql = text("SELECT codename FROM permissions ORDER BY codename")
        perms_result = await db.execute(perms_sql)
    else:
        perms_sql = text("""
            SELECT DISTINCT p.codename FROM permissions p
            JOIN role_permissions rp ON rp.permission_id = p.id
            JOIN user_roles ur ON ur.role_id = rp.role_id
            WHERE ur.user_id = :uid
            ORDER BY p.codename
        """)
        perms_result = await db.execute(perms_sql, {"uid": user_id})
    permissions = [r.codename for r in perms_result.fetchall()]

    # Assigned projects
    projects_sql = text("""
        SELECT pa.project_id, p.display_name, p.platform
        FROM project_assignments pa
        LEFT JOIN projects p ON p.id = pa.project_id
        WHERE pa.user_id = :uid
        ORDER BY pa.project_id
    """)
    projects_result = await db.execute(projects_sql, {"uid": user_id})
    projects = projects_result.fetchall()

    lines = [
        f"**Your Profile: {user_row.username}**\n",
        f"- **Email**: {user_row.email or 'Not set'}",
        f"- **Superadmin**: {'Yes' if user_row.is_superadmin else 'No'}",
        f"- **Account created**: {user_row.created_at.strftime('%Y-%m-%d') if user_row.created_at else 'Unknown'}",
        f"- **Last login**: {user_row.last_login_at.strftime('%Y-%m-%d %H:%M') if user_row.last_login_at else 'Never'}",
        "",
        f"**Roles** ({len(roles)}):",
    ]
    if roles:
        for r in roles:
            lines.append(f"- {r}")
    else:
        lines.append("- (no roles assigned)")

    lines.append("")
    lines.append(f"**Permissions** ({len(permissions)}):")
    if permissions:
        for p in permissions:
            lines.append(f"- `{p}`")
    else:
        lines.append("- (no permissions)")

    lines.append("")
    if user_row.is_superadmin:
        lines.append("**Project access**: All projects (superadmin)")
    elif projects:
        lines.append(f"**Assigned projects** ({len(projects)}):")
        for proj in projects:
            name = proj.display_name or proj.project_id
            platform = f" ({proj.platform})" if proj.platform else ""
            lines.append(f"- {name}{platform}")
    else:
        lines.append("**Assigned projects**: None")

    return MetadataContext(
        text="\n".join(lines),
        source_label="User profile",
        query_type="my_profile",
    )


async def fetch_metadata(
    db: AsyncSession,
    intent: MetadataIntent | None,
    project_ids: list[str] | None,
    project_filter: str | None,
    user_id: int | None = None,
) -> MetadataContext:
    """Dispatch to the appropriate metadata retriever based on intent."""
    if intent == MetadataIntent.MY_PROFILE and user_id is not None:
        return await get_user_profile(db, user_id)
    elif intent == MetadataIntent.PROJECT_LIST:
        return await get_project_list(db, project_ids)
    elif intent == MetadataIntent.PROJECT_DETAIL and project_filter:
        return await get_project_detail(db, project_filter, project_ids)
    elif intent == MetadataIntent.DOCUMENT_STATS:
        return await get_document_stats(db, project_ids, project_filter)
    elif intent == MetadataIntent.DOCUMENT_LIST:
        return await get_document_list(db, project_ids, project_filter)
    elif intent == MetadataIntent.SYSTEM_OVERVIEW:
        return await get_system_overview(db, project_ids)
    elif intent == MetadataIntent.HYBRID:
        if project_filter:
            return await get_project_detail(db, project_filter, project_ids)
        return await get_system_overview(db, project_ids)
    else:
        return await get_system_overview(db, project_ids)
