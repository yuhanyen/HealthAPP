"""工具層註冊表（SPEC §7）。每個工具一檔，以 @registry.tool(schema=…) 註冊。

registry.call() 是 Agent 呼叫工具的唯一入口，在這裡統一執行：
- C-3：民眾側工具必附 consent_scope；所需項目 ⊆ 傳入範圍 ⊆ 本人實際授權；只能存取本人。
- C-5：高風險動作不執行，改建立待確認動作；Agent 傳入的 confirmed／action_id 一律剝除。
- C-6：每次呼叫（成功、拒絕、待確認、錯誤）恰好寫一筆稽核。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic.json_schema import SkipJsonSchema
from sqlalchemy.orm import Session

from app.actions.state_machine import create_pending
from app.audit.writer import ActorType, write_audit
from app.consent.service import effective_scope
from app.db.models_idn import PendingAction
from app.i18n import t

Side = Literal["citizen", "agency", "site"]
STRIPPED_ARGS = ("confirmed", "action_id")


class CitizenToolArgs(BaseModel):
    """民眾側工具參數基底：一定帶 user_id 與 consent_scope。"""

    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    consent_scope: dict[str, list[str]]
    # 只由確認狀態機注入，不出現在給 Agent 的 schema
    confirmed: SkipJsonSchema[bool] = False


A = TypeVar("A", bound=BaseModel)


@dataclass
class ToolContext:
    agent: str
    db: Session
    principal_user_id: uuid.UUID | None = None
    allowed_tools: frozenset[str] | None = None
    actor_type: ActorType = "agent"
    actor_id: str | None = None
    site_id: str | None = None  # 據點側工具：呼叫者所屬據點

    @property
    def audit_actor_id(self) -> str:
        if self.actor_id:
            return self.actor_id
        return f"{self.agent}:{self.principal_user_id}" if self.principal_user_id else self.agent


@dataclass
class ToolResult:
    status: Literal["ok", "confirmation_required"]
    data: dict[str, Any] | None = None
    action_id: uuid.UUID | None = None
    message: str | None = None


class ToolRejected(Exception):
    def __init__(self, reason_key: str, **params: object) -> None:
        self.reason_key = reason_key
        super().__init__(t(f"tool.error.{reason_key}", **params))


class ConsentScopeError(ToolRejected):
    pass


@dataclass
class ToolSpec(Generic[A]):
    name: str
    schema: type[A]
    fn: Callable[[ToolContext, A], dict[str, Any]]
    side: Side
    description: str
    required_scope: Callable[[A], Mapping[str, Sequence[str]]] | None = None
    high_risk: Callable[[A], bool] | None = None
    action_type: str | None = None
    summary: Callable[[A], str] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _flatten(scope: Mapping[str, Sequence[str]]) -> set[str]:
    return {f"{source}.{item}" for source, items in scope.items() for item in items}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec[Any]] = {}

    def tool(
        self,
        *,
        schema: type[A],
        side: Side,
        name: str | None = None,
        required_scope: Callable[[A], Mapping[str, Sequence[str]]] | None = None,
        high_risk: Callable[[A], bool] | None = None,
        action_type: str | None = None,
        summary: Callable[[A], str] | None = None,
    ) -> Callable[
        [Callable[[ToolContext, A], dict[str, Any]]], Callable[[ToolContext, A], dict[str, Any]]
    ]:
        if high_risk is not None and action_type is None:
            raise ValueError("高風險工具必須指定 action_type")
        if side == "citizen" and not issubclass(schema, CitizenToolArgs):
            raise TypeError("民眾側工具的 schema 必須繼承 CitizenToolArgs")

        def decorator(
            fn: Callable[[ToolContext, A], dict[str, Any]],
        ) -> Callable[[ToolContext, A], dict[str, Any]]:
            tool_name = name or fn.__name__
            if tool_name in self._tools:
                raise ValueError(f"工具重複註冊：{tool_name}")
            self._tools[tool_name] = ToolSpec(
                name=tool_name,
                schema=schema,
                fn=fn,
                side=side,
                description=(fn.__doc__ or "").strip(),
                required_scope=required_scope,
                high_risk=high_risk,
                action_type=action_type,
                summary=summary,
            )
            return fn

        return decorator

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self, names: Sequence[str] | None = None) -> list[dict[str, Any]]:
        """給 Claude Messages API 的 tools 定義。"""
        selected = names if names is not None else self.names()
        return [
            {
                "name": n,
                "description": self._tools[n].description,
                "input_schema": self._tools[n].schema.model_json_schema(),
            }
            for n in selected
        ]

    # --- 呼叫 ---

    def _audit(
        self,
        ctx: ToolContext,
        tool: str,
        decision: str,
        *,
        scope: Mapping[str, Any] | None,
        args: object,
        output: object = None,
        reason: str | None = None,
    ) -> None:
        write_audit(
            None,
            actor_type=ctx.actor_type,
            actor_id=ctx.audit_actor_id,
            action="tool.call",
            tool=tool,
            consent_scope=scope,
            input=args,
            output=output,
            decision=decision,
            reason=reason,
        )

    def _reject(
        self, ctx: ToolContext, tool: str, error: ToolRejected, *, scope: Any, args: object
    ) -> ToolRejected:
        self._audit(
            ctx,
            tool,
            "rejected",
            scope=scope if isinstance(scope, Mapping) else None,
            args=args,
            reason=str(error),
        )
        return error

    def _check_scope(self, spec: ToolSpec[Any], args: CitizenToolArgs, ctx: ToolContext) -> None:
        if ctx.principal_user_id is None or args.user_id != ctx.principal_user_id:
            raise ConsentScopeError("wrong_user")
        passed = _flatten(args.consent_scope)
        actual = _flatten(effective_scope(ctx.db, args.user_id))
        forged = passed - actual
        if forged:
            raise ConsentScopeError("scope_forged", items="、".join(sorted(forged)))
        required = _flatten(spec.required_scope(args)) if spec.required_scope else set()
        missing = required - passed
        if missing:
            raise ConsentScopeError("scope_rejected", items="、".join(sorted(missing)))

    def call(self, name: str, raw_args: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        args_dict = {k: v for k, v in raw_args.items() if k not in STRIPPED_ARGS}
        raw_scope = args_dict.get("consent_scope")

        spec = self._tools.get(name)
        if spec is None:
            raise self._reject(
                ctx, name, ToolRejected("unknown", tool=name), scope=raw_scope, args=args_dict
            )
        if ctx.allowed_tools is not None and name not in ctx.allowed_tools:
            raise self._reject(
                ctx, name, ToolRejected("not_allowed", tool=name), scope=raw_scope, args=args_dict
            )
        if spec.side == "citizen" and not raw_scope:
            raise self._reject(
                ctx, name, ConsentScopeError("scope_missing"), scope=None, args=args_dict
            )

        try:
            args = spec.schema.model_validate(args_dict)
        except ValidationError as exc:
            self._audit(
                ctx,
                name,
                "rejected",
                scope=None,
                args=args_dict,
                reason=f"參數錯誤：{exc.error_count()} 項",
            )
            raise

        if spec.side == "citizen":
            try:
                self._check_scope(spec, args, ctx)
            except ConsentScopeError as exc:
                raise self._reject(ctx, name, exc, scope=raw_scope, args=args_dict) from None

        if spec.high_risk is not None and spec.high_risk(args):
            assert spec.action_type is not None
            action = create_pending(
                ctx.db,
                user_id=args.user_id,
                action_type=spec.action_type,
                tool=name,
                payload=args.model_dump(mode="json", exclude={"confirmed"}),
                summary=spec.summary(args)
                if spec.summary
                else t(f"action.summary.{spec.action_type}"),
                created_by="a1" if ctx.agent == "A1" else "user",
            )
            self._audit(
                ctx,
                name,
                "pending_confirmation",
                scope=raw_scope,
                args=args_dict,
                output={"action_id": action.id},
            )
            return ToolResult(
                status="confirmation_required",
                action_id=action.id,
                message=t("tool.confirmation_required"),
                data={"summary": action.summary, "expires_at": action.expires_at.isoformat()},
            )

        try:
            data = spec.fn(ctx, args)
        except Exception as exc:
            self._audit(
                ctx, name, "error", scope=raw_scope, args=args_dict, reason=type(exc).__name__
            )
            raise
        self._audit(ctx, name, "executed", scope=raw_scope, args=args_dict, output=data)
        return ToolResult(status="ok", data=data)

    def execute_confirmed(self, session: Session, action: PendingAction) -> dict[str, Any]:
        """由確認狀態機呼叫：本人已確認後，以 confirmed=True 執行原本的工具呼叫。

        使用呼叫端（API，idn_app）的連線；授權範圍在執行當下重新檢查（確認前可能已撤回）。
        """
        assert action.tool is not None
        spec = self._tools[action.tool]
        args = spec.schema.model_validate({**action.payload, "confirmed": True})
        agent = "A1" if action.created_by == "a1" else "api"
        ctx = ToolContext(agent=agent, db=session, principal_user_id=action.user_id)
        scope = action.payload.get("consent_scope")
        if spec.side == "citizen":
            try:
                self._check_scope(spec, args, ctx)
            except ConsentScopeError as exc:
                raise self._reject(ctx, spec.name, exc, scope=scope, args=action.payload) from None
        data = spec.fn(ctx, args)
        self._audit(
            ctx,
            spec.name,
            "executed",
            scope=scope,
            args=action.payload,
            output=data,
            reason=f"本人已確認（action {action.id}）",
        )
        return data


registry = ToolRegistry()
