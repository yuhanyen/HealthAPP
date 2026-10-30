"""M4：A1 對話紀錄；雙生快照當日可重算

- idn.chat_messages：民眾與 A1 的對話（只存文字回合，內容以 AES-GCM 加密），供多輪對話與
  雙生的 last_dialog_summary。撤回任何授權時清除（避免舊回覆再被引用），見 DECISIONS。
- idn_user_scoped 對 twin_snapshots 增加 UPDATE（同一天重跑批次時覆寫當日快照；受 RLS 限本人）。

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ME = "NULLIF(current_setting('app.user_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("meta", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('user', 'assistant')", name=op.f("ck_chat_messages_role")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["idn.users.id"], name=op.f("fk_chat_messages_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_messages")),
        schema="idn",
    )
    op.create_index(
        op.f("ix_chat_messages_user_id_id"), "chat_messages", ["user_id", "id"], schema="idn"
    )

    op.execute("GRANT SELECT, INSERT, DELETE ON idn.chat_messages TO idn_app")
    op.execute("GRANT SELECT, INSERT ON idn.chat_messages TO idn_user_scoped")
    op.execute("GRANT UPDATE ON idn.twin_snapshots TO idn_user_scoped")
    op.execute("ALTER TABLE idn.chat_messages ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY p_chat_messages_idn_app ON idn.chat_messages TO idn_app "
        "USING (true) WITH CHECK (true)"
    )
    op.execute(
        "CREATE POLICY p_chat_messages_user_scoped ON idn.chat_messages TO idn_user_scoped "
        f"USING (user_id = {_ME}) WITH CHECK (user_id = {_ME})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS p_chat_messages_idn_app ON idn.chat_messages")
    op.execute("DROP POLICY IF EXISTS p_chat_messages_user_scoped ON idn.chat_messages")
    op.execute("REVOKE UPDATE ON idn.twin_snapshots FROM idn_user_scoped")
    op.drop_index(op.f("ix_chat_messages_user_id_id"), "chat_messages", schema="idn")
    op.drop_table("chat_messages", schema="idn")
