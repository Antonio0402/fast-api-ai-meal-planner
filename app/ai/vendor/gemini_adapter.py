"""Gemini transport adapted from GeminiAgentMealPLanner/ai_module/gemini_adapter.py on 2026-10-11."""

from __future__ import annotations

import base64
import json
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.ai.vendor.key_routing import ApiKeyRouter
from app.ai.vendor.agent import ChatMessage, ModelReply, ToolCall
from app.ai.vendor.scan import SCAN_RESPONSE_SCHEMA


class GeminiApiError(RuntimeError):
    def __init__(self, status_code: int) -> None:
        super().__init__("Gemini request failed.")
        self.status_code = status_code


class GeminiTransportError(RuntimeError):
    pass


class GeminiTimeoutError(GeminiTransportError):
    pass


class GeminiAdapter:
    def __init__(
        self,
        *,
        key_router: ApiKeyRouter,
        model: str,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.key_router = key_router
        self.model = model
        self.timeout_seconds = timeout_seconds

    def complete(
        self, *, system_instruction: str, conversation: Sequence[ChatMessage],
        tools: Sequence[str],
    ) -> ModelReply:
        payload = {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": [
                {"role": "model" if message.role == "assistant" else "user",
                 "parts": list(message.parts) or [{"text": message.content}]}
                for message in conversation
            ],
            "tools": [{"functionDeclarations": _function_declarations(tools)}],
        }
        body = self._request(payload)
        text_parts = []
        calls = []
        for part in _extract_parts(body):
            if part.get("thought"):
                continue
            if isinstance(part.get("text"), str):
                text_parts.append(part["text"])
            call = part.get("functionCall")
            if isinstance(call, Mapping):
                if not isinstance(call.get("name"), str) or not isinstance(call.get("args", {}), Mapping):
                    raise GeminiTransportError("Gemini function call was invalid.")
                calls.append(ToolCall(
                    name=call["name"], arguments=call.get("args", {}),
                    call_id=call.get("id", ""),
                ))
        return ModelReply(text="\n".join(text_parts).strip() or None,
                          tool_calls=tuple(calls), raw=body)

    def generate_recipe_text(
        self, *, recipe: str, user_context: Mapping[str, Any], prompt: str,
    ) -> str:
        return self._generate({
            "systemInstruction": {"parts": [{"text": prompt}]},
            "contents": [{"role": "user", "parts": [{"text": (
                f"User context (JSON):\n{json.dumps(dict(user_context), ensure_ascii=False)}"
                f"\n\nRecipe source text:\n{recipe}"
            )}]}],
        })

    def scan_ingredients(
        self, *, image_bytes: bytes, mime_type: str, prompt: str
    ) -> Mapping[str, Any] | str:
        data = base64.b64encode(image_bytes).decode("ascii")
        return self._generate(
            {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {"text": f"{prompt}\nReturn JSON only."},
                            {"inlineData": {"mimeType": mime_type, "data": data}},
                        ],
                    }
                ],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseJsonSchema": SCAN_RESPONSE_SCHEMA,
                },
            }
        )

    def generate_text(self, prompt: str, *, response_schema: Mapping[str, Any] | None = None) -> str:
        config: dict[str, Any] = {"responseMimeType": "application/json"}
        if response_schema is not None:
            config["responseJsonSchema"] = dict(response_schema)
        return self._generate(
            {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": config,
            }
        )

    def _generate(self, payload: Mapping[str, Any]) -> str:
        return _extract_text(self._request(payload))

    def _request(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        attempted: set[str] = set()
        last_error: GeminiApiError | GeminiTransportError | None = None
        while True:
            try:
                entry = self.key_router.next_key()
            except RuntimeError as exc:
                if last_error is not None:
                    raise last_error from exc
                raise
            if entry.name in attempted:
                self.key_router.block(entry.name)
                continue
            attempted.add(entry.name)
            request = Request(
                url=(
                    "https://generativelanguage.googleapis.com/v1beta/"
                    f"models/{self.model}:generateContent"
                ),
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": entry.api_key,
                },
                method="POST",
            )
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    body = json.loads(response.read().decode("utf-8"))
                if not isinstance(body, Mapping):
                    raise GeminiTransportError("Gemini response was invalid.")
                return body
            except HTTPError as exc:
                last_error = GeminiApiError(exc.code)
                if exc.code not in {401, 403, 429, 500, 502, 503, 504}:
                    raise last_error from exc
                self.key_router.block(entry.name)
            except (URLError, TimeoutError, json.JSONDecodeError) as exc:
                # urllib có thể bọc socket timeout trong URLError.reason.
                if isinstance(exc, TimeoutError) or (
                    isinstance(exc, URLError) and isinstance(exc.reason, TimeoutError)
                ):
                    last_error = GeminiTimeoutError("Gemini request timed out.")
                else:
                    last_error = GeminiTransportError("Gemini transport failed.")
                self.key_router.block(entry.name)
                if isinstance(exc, json.JSONDecodeError):
                    raise last_error from exc


def _extract_text(body: Any) -> str:
    texts = [part["text"] for part in _extract_parts(body)
             if not part.get("thought") and isinstance(part.get("text"), str)]
    text = "\n".join(texts).strip()
    if not text:
        raise GeminiTransportError("Gemini response contained no text.")
    return text


def _extract_parts(body: Any) -> list[Mapping[str, Any]]:
    if not isinstance(body, Mapping):
        raise GeminiTransportError("Gemini response was invalid.")
    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise GeminiTransportError("Gemini response contained no candidate.")
    content = candidates[0].get("content") if isinstance(candidates[0], Mapping) else None
    parts = content.get("parts") if isinstance(content, Mapping) else None
    if not isinstance(parts, list):
        raise GeminiTransportError("Gemini response content was invalid.")
    if any(not isinstance(part, Mapping) for part in parts):
        raise GeminiTransportError("Gemini response part was invalid.")
    return parts


def _function_declarations(tools: Sequence[str]) -> list[dict[str, Any]]:
    # Mô tả rõ thuộc tính; Gemini không chấp nhận object schema rỗng.
    target = {"type": "OBJECT", "properties": {
        "item_id": {"type": "STRING"}, "name": {"type": "STRING"},
    }}
    item_properties = {
        "name": {"type": "STRING"}, "quantity": {"type": "NUMBER"},
        "unit": {"type": "STRING"}, "purchased": {"type": "BOOLEAN"},
    }
    context_properties = {
        key: {"type": "ARRAY", "items": {"type": "STRING"}}
        for key in ("preferences", "allergies", "dietary_restrictions")
    }
    declarations = []
    for name in tools:
        if name not in {"view", "add", "update", "delete"}:
            raise ValueError("Tool không được hỗ trợ.")
        properties = {"resource": {"type": "STRING", "enum": [
            "user_context", "inventory", "shopping_list",
        ]}}
        required = ["resource"]
        if name == "view":
            properties["filters"] = target
        elif name == "add":
            properties["items"] = {"type": "ARRAY", "items": {
                "type": "OBJECT", "properties": item_properties, "required": ["name"],
            }}
            properties["data"] = {"type": "OBJECT", "properties": context_properties}
        elif name == "update":
            properties["target"] = target
            properties["patch"] = {"type": "OBJECT", "properties": {
                **item_properties, **context_properties,
            }}
            required.append("patch")
        else:
            properties["target"] = target
            required.append("target")
        declarations.append({
            "name": name, "description": {
                "view": "Read current resource state before any mutation.",
                "add": "Add items, or use data for user_context.",
                "update": "Update with patch; target required except for user_context.",
                "delete": "Delete by existing item_id or name; never guess IDs.",
            }[name],
            "parameters": {"type": "OBJECT", "properties": properties, "required": required},
        })
    return declarations
