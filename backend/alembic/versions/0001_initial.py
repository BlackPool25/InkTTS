"""Initial schema: novels, chapters, voices, voice_map, pron_dict, jobs, progress, opds_tokens."""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "novels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("author", sa.String(), nullable=False, server_default=""),
        sa.Column("fingerprint", sa.String(), nullable=False, server_default=""),
        sa.Column("source_url", sa.String(), nullable=False, server_default=""),
        sa.Column("source_aliases", sa.String(), nullable=False, server_default=""),
        sa.Column("cover_path", sa.String(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_novels_title", "novels", ["title"])
    op.create_index("ix_novels_author", "novels", ["author"])
    op.create_index("ix_novels_fingerprint", "novels", ["fingerprint"])

    op.create_table(
        "chapters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("novel_id", sa.Integer(), sa.ForeignKey("novels.id"), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False, server_default=""),
        sa.Column("text_hash", sa.String(), nullable=False, server_default=""),
        sa.Column("render_key", sa.String(), nullable=False, server_default=""),
        sa.Column("voice", sa.String(), nullable=False, server_default=""),
        sa.Column("speed", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("audio_path", sa.String(), nullable=False, server_default=""),
        sa.Column("sidecar_path", sa.String(), nullable=False, server_default=""),
        sa.Column("status", sa.String(), nullable=False, server_default="ready"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_chapters_novel", "chapters", ["novel_id"])
    op.create_index("ix_chapters_idx", "chapters", ["idx"])
    op.create_index("ix_chapters_texthash", "chapters", ["text_hash"])
    op.create_index("ix_chapters_renderkey", "chapters", ["render_key"])
    op.create_index("ix_chapters_status", "chapters", ["status"])

    op.create_table(
        "voices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("lang", sa.String(), nullable=False, server_default="en"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_voices_name", "voices", ["name"], unique=True)

    op.create_table(
        "voice_map",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("novel_id", sa.Integer(), sa.ForeignKey("novels.id"), nullable=False),
        sa.Column("char_key", sa.String(), nullable=False),
        sa.Column("voice", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_voicemap_novel", "voice_map", ["novel_id"])
    op.create_index("ix_voicemap_charkey", "voice_map", ["char_key"])

    op.create_table(
        "pron_dict",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pattern", sa.String(), nullable=False),
        sa.Column("replacement", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_pron_pattern", "pron_dict", ["pattern"], unique=True)

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("ref", sa.String(), nullable=False, server_default=""),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("error_class", sa.String(), nullable=False, server_default=""),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("payload", sa.String(), nullable=False, server_default=""),
        sa.Column("error", sa.String(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_jobs_kind", "jobs", ["kind"])
    op.create_index("ix_jobs_ref", "jobs", ["ref"])
    op.create_index("ix_jobs_status", "jobs", ["status"])

    op.create_table(
        "progress",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("novel_id", sa.Integer(), sa.ForeignKey("novels.id"), nullable=False),
        sa.Column("chapter_idx", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("position_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("text_hash", sa.String(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_progress_novel", "progress", ["novel_id"])

    op.create_table(
        "opds_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("device", sa.String(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_opds_token", "opds_tokens", ["token"], unique=True)


def downgrade() -> None:
    for t, ixs in [
        ("opds_tokens", ["ix_opds_token"]),
        ("progress", ["ix_progress_novel"]),
        ("jobs", ["ix_jobs_kind", "ix_jobs_ref", "ix_jobs_status"]),
        ("pron_dict", ["ix_pron_pattern"]),
        ("voice_map", ["ix_voicemap_novel", "ix_voicemap_charkey"]),
        ("voices", ["ix_voices_name"]),
        ("chapters", ["ix_chapters_novel", "ix_chapters_idx", "ix_chapters_texthash",
                      "ix_chapters_renderkey", "ix_chapters_status"]),
        ("novels", ["ix_novels_title", "ix_novels_author", "ix_novels_fingerprint"]),
    ]:
        for ix in ixs:
            op.drop_index(ix, table_name=t)
        op.drop_table(t)
