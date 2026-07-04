"""init — create all 11 tables with indexes and constraints.

Revision ID: 0001
Revises:
Create Date: 2026-07-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── tables without foreign keys (created first) ──────────────────────

    op.create_table(
        "admin_users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False, server_default="admin"),
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("created_at", sa.String(), nullable=True),
        sa.Column("last_login_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("actor", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=True),
        sa.Column("target", sa.String(), nullable=True),
        sa.Column("detail_json", sa.String(), nullable=True),
        sa.Column("ip", sa.String(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("audit_logs", schema=None) as batch_op:
        batch_op.create_index("ix_audit_logs_created_at", ["created_at"], unique=False)
        batch_op.create_index("ix_audit_logs_actor", ["actor"], unique=False)

    op.create_table(
        "name_index",
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("name"),
    )

    op.create_table(
        "plugin_configs",
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("config_json", sa.String(), nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("name"),
    )

    op.create_table(
        "sessions",
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.Column("summary", sa.String(), nullable=False, server_default=""),
        sa.Column("nickname", sa.String(), nullable=False, server_default=""),
        sa.Column("last_active_at", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("meta_json", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("session_id"),
    )

    op.create_table(
        "settings",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value_json", sa.String(), nullable=False),
        sa.Column("group_name", sa.String(), nullable=True),
        sa.Column("is_secret", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("updated_at", sa.String(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("key"),
    )

    op.create_table(
        "social_edges",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("from_user_id", sa.Integer(), nullable=False),
        sa.Column("to_user_id", sa.Integer(), nullable=False),
        sa.Column("relation", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False, server_default=""),
        sa.Column("evidence", sa.String(), nullable=False, server_default=""),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.Column("learned_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "from_user_id",
            "to_user_id",
            "relation",
            "label",
            name="uq_social_edges_from_to_relation_label",
        ),
    )
    with op.batch_alter_table("social_edges", schema=None) as batch_op:
        batch_op.create_index(
            "ix_social_edges_from_user_id", ["from_user_id"], unique=False
        )
        batch_op.create_index(
            "ix_social_edges_to_user_id", ["to_user_id"], unique=False
        )

    op.create_table(
        "user_profiles",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("preferred_name", sa.String(), nullable=False, server_default=""),
        sa.Column("aliases_json", sa.String(), nullable=False, server_default="[]"),
        sa.Column("group_cards_json", sa.String(), nullable=False, server_default="{}"),
        sa.Column("stage", sa.String(), nullable=False, server_default="stranger"),
        sa.Column("first_met_at", sa.String(), nullable=True),
        sa.Column("last_seen_at", sa.String(), nullable=True),
        sa.Column("interaction_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_group_id", sa.Integer(), nullable=True),
        sa.Column("seen_in_private", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("seen_in_group", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("impression", sa.String(), nullable=False, server_default=""),
        sa.Column("cognition_summary", sa.String(), nullable=False, server_default=""),
        sa.Column("cognition_updated_at", sa.String(), nullable=True),
        sa.Column(
            "cognition_interaction_at_update",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("2")),
        sa.PrimaryKeyConstraint("user_id"),
    )

    # ── tables with foreign keys (created after their parents) ───────────

    op.create_table(
        "session_entities",
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sessions.session_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("session_id", "name", name="pk_session_entities"),
    )

    op.create_table(
        "session_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sessions.session_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id", "seq", name="uq_session_messages_session_id_seq"
        ),
    )
    with op.batch_alter_table("session_messages", schema=None) as batch_op:
        batch_op.create_index(
            "ix_session_messages_session_id_id", ["session_id", "id"], unique=False
        )

    op.create_table(
        "user_facts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False, server_default="general"),
        sa.Column("source_user_id", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("learned_at", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("1.0")),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("supersedes", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user_profiles.user_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("user_facts", schema=None) as batch_op:
        batch_op.create_index(
            "ix_user_facts_user_id_active_learned_at",
            ["user_id", "active", "learned_at"],
            unique=False,
        )


def downgrade() -> None:
    # ── drop in reverse order: child tables first ────────────────────────

    with op.batch_alter_table("user_facts", schema=None) as batch_op:
        batch_op.drop_index("ix_user_facts_user_id_active_learned_at")
    op.drop_table("user_facts")

    with op.batch_alter_table("session_messages", schema=None) as batch_op:
        batch_op.drop_index("ix_session_messages_session_id_id")
    op.drop_table("session_messages")

    op.drop_table("session_entities")
    op.drop_table("user_profiles")

    with op.batch_alter_table("social_edges", schema=None) as batch_op:
        batch_op.drop_index("ix_social_edges_to_user_id")
        batch_op.drop_index("ix_social_edges_from_user_id")
    op.drop_table("social_edges")

    op.drop_table("settings")
    op.drop_table("sessions")
    op.drop_table("plugin_configs")
    op.drop_table("name_index")

    with op.batch_alter_table("audit_logs", schema=None) as batch_op:
        batch_op.drop_index("ix_audit_logs_created_at")
        batch_op.drop_index("ix_audit_logs_actor")
    op.drop_table("audit_logs")

    op.drop_table("admin_users")
