"""decode_care_list（SPEC §7.3）：據點人員以任務編號解碼關懷代號。

逐次稽核，只回做事需要的欄位。
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Any

from pydantic import Field

from app.care.decode import decode_care_task
from app.db.engines import DbRole, session_scope
from app.masking import mask_name
from app.tools._common import SiteToolArgs
from app.tools.registry import ToolContext, registry


class DecodeArgs(SiteToolArgs):
    code: str = Field(pattern=r"^N-[0-9A-Za-z]{3,8}$")
    task_ref: str = Field(description="任務編號（必填）")


@registry.tool(name="decode_care_list", schema=DecodeArgs, side="site")
def decode_care_list(ctx: ToolContext, args: DecodeArgs) -> dict[str, Any]:
    """把關懷代號解碼成可執行任務所需的聯絡資訊；必須附任務編號，只限本據點的代號。"""
    if ctx.site_id is None or ctx.actor_id is None:
        raise PermissionError("只有據點人員可以解碼")
    with session_scope(DbRole.IDN_APP) as s:
        decoded = decode_care_task(
            s,
            code=args.code,
            task_ref=args.task_ref,
            staff_account_id=uuid.UUID(ctx.actor_id),
            site_id=ctx.site_id,
        )
    data = asdict(decoded)
    data["display_name"] = mask_name(decoded.name)
    return data
