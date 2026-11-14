"""Add chapters.text (source text lives with the row; audio is derived)."""
from alembic import op
import sqlalchemy as sa

revision = "0002_chapter_text"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("chapters", recreate="never") as t:
        t.add_column(sa.Column("text", sa.Text(), server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("chapters", recreate="never") as t:
        t.drop_column("text")
