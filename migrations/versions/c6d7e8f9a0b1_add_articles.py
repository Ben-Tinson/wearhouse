"""add articles and article blocks

Revision ID: c6d7e8f9a0b1
Revises: b1c2d3e4f5a6
Create Date: 2026-01-29 10:45:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c6d7e8f9a0b1"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "article",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("brand", sa.String(length=150), nullable=True),
        sa.Column("tags", sa.Text(), nullable=True),
        sa.Column("hero_image_url", sa.String(length=1024), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_article_slug"),
    )
    op.create_index(op.f("ix_article_brand"), "article", ["brand"], unique=False)
    op.create_index(op.f("ix_article_published_at"), "article", ["published_at"], unique=False)
    op.create_index(op.f("ix_article_slug"), "article", ["slug"], unique=False)

    op.create_table(
        "article_block",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("block_type", sa.String(length=50), nullable=False),
        sa.Column("heading_text", sa.Text(), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("image_url", sa.String(length=1024), nullable=True),
        sa.Column("caption", sa.String(length=255), nullable=True),
        sa.Column("align", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["article_id"], ["article.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("article_id", "position", name="uq_article_block_position"),
    )
    op.create_index(op.f("ix_article_block_article_id"), "article_block", ["article_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_article_block_article_id"), table_name="article_block")
    op.drop_table("article_block")
    op.drop_index(op.f("ix_article_slug"), table_name="article")
    op.drop_index(op.f("ix_article_published_at"), table_name="article")
    op.drop_index(op.f("ix_article_brand"), table_name="article")
    op.drop_table("article")
