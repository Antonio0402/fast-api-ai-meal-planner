"""Logic agent từ GeminiAgentMealPLanner, có kiểm tra hợp đồng backend."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence

ALLOWED_TOOLS = frozenset({"view", "add", "update", "delete"})
ALLOWED_RESOURCES = frozenset({"user_context", "inventory", "shopping_list"})


class AgentStatus(str, Enum):
    OK = "ok"
    NEEDS_CLARIFICATION = "needs_clarification"
    ERROR = "error"


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str
    parts: Sequence[Mapping[str, Any]] = ()


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: Mapping[str, Any]
    call_id: str = ""


@dataclass(frozen=True)
class ModelReply:
    text: str | None = None
    tool_calls: Sequence[ToolCall] = ()
    raw: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ToolExecutionResult:
    status: str
    data: Mapping[str, Any] | None = None
    error: Mapping[str, Any] | None = None

    @property
    def is_ok(self) -> bool:
        return self.status == "ok"


@dataclass(frozen=True)
class AgentError:
    code: str
    message: str
    retryable: bool


@dataclass(frozen=True)
class AgentFinalResponse:
    status: AgentStatus
    message: str
    steps_used: int
    tool_results: Sequence[ToolExecutionResult] = ()
    error: AgentError | None = None
    meta: Mapping[str, Any] = field(default_factory=dict)


class ModelClient(Protocol):
    def complete(
        self,
        *,
        system_instruction: str,
        conversation: Sequence[ChatMessage],
        tools: Sequence[str],
    ) -> ModelReply:
        ...


class ToolDispatcher(Protocol):
    def execute(
        self,
        *,
        tool_name: str,
        payload: Mapping[str, Any],
        auth_context: Mapping[str, Any] | None,
    ) -> ToolExecutionResult:
        ...


SYSTEM_INSTRUCTION = (
    "Bạn là trợ lý meal planner. Chỉ dùng đúng bốn tool: view, add, update, delete. "
    "Resource chỉ gồm user_context, inventory, shopping_list. "
    "Không xác nhận đã cập nhật dữ liệu nếu tool chưa trả status=ok. "
    "Khi thiếu ID hoặc thao tác mơ hồ, phải hỏi làm rõ. "
    "Chuẩn hóa user_context để nhất quán: ưu tiên các key preferences (list), allergies (list), "
    "dietary_restrictions (list). Không tạo key mới như likes, intolerance, restrictions, dietary_preferences. "
    "Protocol bắt buộc khi gọi tool: "
    "1) view: {resource, filters?}. "
    "2) add: {resource, items:[{name, quantity?, unit?, purchased?}] } hoặc {resource, data:{...}} cho user_context. "
    "3) update: luôn có patch object. Với user_context: {resource:'user_context', patch:{...}} (không cần target). "
    "Với inventory/shopping_list: {resource, target:{name hoặc item_id}, patch:{...}}. "
    "4) delete: {resource, target:{name hoặc item_id}}. "
    "Trước khi gọi các tool add, update, delete, gọi tool view để kiểm tra trạng thái hiện tại của resource. "
    "Ưu tiên target theo name cho câu người dùng thông thường; chỉ dùng item_id khi hệ thống đã cung cấp sẵn. "
    "Không dùng key 'item' thay cho 'name'. Và 'name' luôn là từ tiếng Anh số ít như 'tomato', 'milk', 'egg'. "
    "Không dùng dạng số nhiều trừ khi thực sự cần thiết. "
    "Không gửi update nếu thiếu patch."
)


def run_agent(
    *,
    user_message: str,
    history: Sequence[ChatMessage],
    model_client: ModelClient,
    tool_dispatcher: ToolDispatcher,
    auth_context: Mapping[str, Any] | None = None,
    max_tool_calls: int = 5,
) -> AgentFinalResponse:
    """
    Run a function-calling loop with 4 tools (view/add/update/delete).

    This function enforces tool/resource contracts before dispatching to backend callbacks.
    """
    if max_tool_calls <= 0:
        return _error_response(
            code="INVALID_MAX_TOOL_CALLS",
            message="max_tool_calls must be greater than zero.",
            retryable=False,
            steps_used=0,
        )
    if not isinstance(user_message, str) or not user_message.strip():
        return _error_response(
            code="INVALID_USER_MESSAGE",
            message="Tin nhắn phải là chuỗi không rỗng.",
            retryable=False,
            steps_used=0,
        )

    conversation: list[ChatMessage] = list(history)
    conversation.append(ChatMessage(role="user", content=user_message))

    steps_used = 0
    executed_tool_results: list[ToolExecutionResult] = []
    used_invalid_tool_repair = False

    while True:
        model_reply = model_client.complete(
            system_instruction=SYSTEM_INSTRUCTION,
            conversation=tuple(conversation),
            tools=tuple(sorted(ALLOWED_TOOLS)),
        )

        if not model_reply.tool_calls:
            if isinstance(model_reply.text, str) and model_reply.text.strip():
                return AgentFinalResponse(
                    status=AgentStatus.OK,
                    message=model_reply.text.strip(),
                    steps_used=steps_used,
                    tool_results=tuple(executed_tool_results),
                )
            return _error_response(
                code="EMPTY_MODEL_REPLY",
                message="Model returned neither text nor tool calls.",
                retryable=True,
                steps_used=steps_used,
                tool_results=executed_tool_results,
            )

        if steps_used + len(model_reply.tool_calls) > max_tool_calls:
            return AgentFinalResponse(
                status=AgentStatus.NEEDS_CLARIFICATION,
                message=(
                    "Mình đã đạt giới hạn số lần gọi tool trong một lượt. "
                    "Bạn có thể xác nhận lại mục tiêu để mình tiếp tục an toàn."
                ),
                steps_used=steps_used,
                tool_results=tuple(executed_tool_results),
                error=AgentError(
                    code="TOOL_CALL_LIMIT_REACHED",
                    message="Maximum number of tool calls reached.",
                    retryable=True,
                ),
            )

        should_requery_model = False
        for tool_call in model_reply.tool_calls:
            validation_error = _validate_tool_call(tool_call)
            if validation_error is not None:
                if not used_invalid_tool_repair:
                    used_invalid_tool_repair = True
                    conversation.append(
                        ChatMessage(
                            role="assistant",
                            content=(
                                f"INVALID_TOOL_CALL {tool_call.name} "
                                f"{tool_call.arguments!r}"
                            ),
                        )
                    )
                    conversation.append(
                        ChatMessage(
                            role="user",
                            content=(
                                "Tool call trước không hợp lệ: "
                                f"{validation_error} "
                                "Hãy sửa lại đúng protocol và gọi lại tool."
                            ),
                        )
                    )
                    should_requery_model = True
                    break
                return AgentFinalResponse(
                    status=AgentStatus.NEEDS_CLARIFICATION,
                    message=validation_error,
                    steps_used=steps_used,
                    tool_results=tuple(executed_tool_results),
                    error=AgentError(
                        code="INVALID_TOOL_CALL",
                        message=validation_error,
                        retryable=False,
                    ),
                )

        if should_requery_model:
            continue

        # Giữ nguyên các part của Gemini, bao gồm thoughtSignature nếu có.
        raw = model_reply.raw or {}
        candidates = raw.get("candidates", [])
        content = candidates[0].get("content", {}) if candidates else {}
        parts = content.get("parts") or [
            {"functionCall": {"name": call.name, "args": dict(call.arguments),
                              **({"id": call.call_id} if call.call_id else {})}}
            for call in model_reply.tool_calls
        ]
        conversation.append(ChatMessage(role="assistant", content="", parts=parts))
        response_parts = []
        for tool_call in model_reply.tool_calls:
            dispatch_result = tool_dispatcher.execute(
                tool_name=tool_call.name,
                payload=tool_call.arguments,
                auth_context=auth_context,
            )
            steps_used += 1
            executed_tool_results.append(dispatch_result)

            if not dispatch_result.is_ok:
                return _error_response(
                    code="TOOL_EXECUTION_FAILED",
                    message="Backend chưa thực hiện được thao tác.",
                    retryable=False,
                    steps_used=steps_used,
                    tool_results=executed_tool_results,
                )
            response_parts.append({"functionResponse": {
                "name": tool_call.name,
                **({"id": tool_call.call_id} if tool_call.call_id else {}),
                "response": {"status": dispatch_result.status,
                             "data": dispatch_result.data, "error": dispatch_result.error},
            }})
        conversation.append(ChatMessage(
            role="tool", content=json.dumps(response_parts, ensure_ascii=False),
            parts=response_parts,
        ))


def _validate_tool_call(tool_call: ToolCall) -> str | None:
    if tool_call.name not in ALLOWED_TOOLS:
        return f"Tool '{tool_call.name}' is not allowed."

    if not isinstance(tool_call.arguments, Mapping):
        return "Tool arguments must be an object."
    resource = tool_call.arguments.get("resource")
    if not isinstance(resource, str) or resource not in ALLOWED_RESOURCES:
        return (
            "Invalid resource. Allowed resources: "
            f"{', '.join(sorted(ALLOWED_RESOURCES))}."
        )

    if tool_call.name == "delete":
        target = tool_call.arguments.get("target")
        if not _valid_target(target):
            return "Tool requires target with a non-empty name or item_id."

    if tool_call.name == "update":
        patch = tool_call.arguments.get("patch")
        if not isinstance(patch, Mapping) or not patch:
            return "Update requires non-empty 'patch'."
        resource = tool_call.arguments.get("resource")
        if resource != "user_context":
            target = tool_call.arguments.get("target")
            if not _valid_target(target):
                return "Update requires non-empty 'target' for this resource."

    if tool_call.name == "add":
        if resource == "user_context":
            data = tool_call.arguments.get("data")
            if not isinstance(data, Mapping) or not data:
                return "Add user_context requires a non-empty 'data' object."
        else:
            items = tool_call.arguments.get("items")
            if not isinstance(items, list) or not items or any(
                not isinstance(item, Mapping)
                or not isinstance(item.get("name"), str) or not item["name"].strip()
                for item in items
            ):
                return "Add requires a non-empty 'items' list with ingredient names."

    return None


def _valid_target(target: Any) -> bool:
    return isinstance(target, Mapping) and any(
        isinstance(target.get(key), str) and target[key].strip()
        for key in ("name", "item_id")
    )


def _error_response(
    *,
    code: str,
    message: str,
    retryable: bool,
    steps_used: int,
    tool_results: Sequence[ToolExecutionResult] = (),
) -> AgentFinalResponse:
    return AgentFinalResponse(
        status=AgentStatus.ERROR,
        message=message,
        steps_used=steps_used,
        tool_results=tuple(tool_results),
        error=AgentError(code=code, message=message, retryable=retryable),
    )
