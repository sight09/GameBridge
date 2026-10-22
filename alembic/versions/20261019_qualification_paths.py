"""add tournament qualification pathways

Revision ID: 20261019_qualification_paths
Revises: cc5ab77e57d9
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261019_qualification_paths"
down_revision: Union[str, Sequence[str], None] = "cc5ab77e57d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("tournament_qualification_paths"):
        return

    op.create_table(
        "tournament_qualification_paths",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_tournament_id", sa.String(length=36), nullable=False),
        sa.Column("destination_tournament_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tournament_qualification_paths_source_tournament_id",
        "tournament_qualification_paths",
        ["source_tournament_id"],
        unique=True,
    )
    op.create_index(
        "ix_tournament_qualification_paths_destination_tournament_id",
        "tournament_qualification_paths",
        ["destination_tournament_id"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("tournament_qualification_paths"):
        return
    op.drop_index(
        "ix_tournament_qualification_paths_destination_tournament_id",
        table_name="tournament_qualification_paths",
    )
    op.drop_index(
        "ix_tournament_qualification_paths_source_tournament_id",
        table_name="tournament_qualification_paths",
    )
    op.drop_table("tournament_qualification_paths")
