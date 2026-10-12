"""Kiểm tra lớp AI độc lập, không kết nối database hoặc Gemini thật."""

import json
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

from app.ai.vendor.agent import (
    AgentStatus, ChatMessage, ModelReply, ToolCall, ToolExecutionResult, run_agent,
)
from app.ai.vendor.gemini_adapter import GeminiAdapter, GeminiApiError, GeminiTransportError
from app.ai.vendor.key_routing import ApiKeyEntry, ApiKeyRouter
from app.ai.vendor.recipe import GenerateRecipeError, generate_recipe
from app.ai.vendor.scan import scan_ingredients


class Model:
    def __init__(self, *replies):
        self.replies = iter(replies)
        self.conversations = []

    def complete(self, **kwargs):
        self.conversations.append(kwargs["conversation"])
        return next(self.replies)


class Dispatcher:
    def __init__(self, status="ok"):
        self.calls = []
        self.status = status

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        return ToolExecutionResult(status=self.status, data={"items": []})


def reply(name="view", **arguments):
    return ModelReply(tool_calls=(ToolCall(name, {"resource": "inventory", **arguments}),))


def test_agent_completes_at_budget_and_forwards_auth():
    model = Model(reply(), ModelReply(text="Kho đang trống."))
    dispatcher = Dispatcher()
    auth = {"user_id": "trusted-user"}
    result = run_agent(user_message="Xem kho", history=[], model_client=model,
                       tool_dispatcher=dispatcher, auth_context=auth, max_tool_calls=1)
    assert result.status == AgentStatus.OK
    assert result.steps_used == 1
    assert dispatcher.calls[0]["auth_context"] == auth
    assert model.conversations[-1][-1].parts[0]["functionResponse"]["name"] == "view"


def test_agent_rejects_batch_over_budget_before_dispatch():
    model = Model(ModelReply(tool_calls=(ToolCall("view", {"resource": "inventory"}),) * 2))
    dispatcher = Dispatcher()
    result = run_agent(user_message="Xem kho", history=[], model_client=model,
                       tool_dispatcher=dispatcher, max_tool_calls=1)
    assert result.error.code == "TOOL_CALL_LIMIT_REACHED"
    assert not dispatcher.calls


@pytest.mark.parametrize("invalid", [
    reply("add", items="egg"), reply("add", items=[]),
    reply("update", target={"name": "egg"}),
    reply("delete", target={"name": " "}),
    ModelReply(tool_calls=(ToolCall("view", {"resource": ["inventory"]}),)),
    ModelReply(tool_calls=(ToolCall("view", None),)),
    ModelReply(tool_calls=(ToolCall("drop", {"resource": "inventory"}),)),
])
def test_invalid_contract_never_reaches_dispatcher(invalid):
    dispatcher = Dispatcher()
    result = run_agent(user_message="Cập nhật kho", history=[],
                       model_client=Model(invalid, invalid), tool_dispatcher=dispatcher)
    assert result.error.code == "INVALID_TOOL_CALL"
    assert not dispatcher.calls


def test_agent_repairs_once():
    model = Model(reply("update"), reply("update", target={"name": "egg"},
                  patch={"quantity": 2}), ModelReply(text="Đã cập nhật."))
    dispatcher = Dispatcher()
    result = run_agent(user_message="Sửa trứng", history=[], model_client=model,
                       tool_dispatcher=dispatcher)
    assert result.status == AgentStatus.OK
    assert len(dispatcher.calls) == 1


def test_agent_stops_after_failed_tool():
    model = Model(reply(), ModelReply(text="Đã cập nhật."))
    result = run_agent(user_message="Xem kho", history=[], model_client=model,
                       tool_dispatcher=Dispatcher("error"))
    assert result.status == AgentStatus.ERROR
    assert result.error.code == "TOOL_EXECUTION_FAILED"
    assert len(model.conversations) == 1


def test_agent_rejects_more_tools_after_budget():
    dispatcher = Dispatcher()
    result = run_agent(user_message="Xem kho", history=[], model_client=Model(reply(), reply()),
                       tool_dispatcher=dispatcher, max_tool_calls=1)
    assert result.error.code == "TOOL_CALL_LIMIT_REACHED"
    assert result.steps_used == 1
    assert len(dispatcher.calls) == 1


@pytest.mark.parametrize("message,budget,code", [
    (" ", 5, "INVALID_USER_MESSAGE"), ("hello", 0, "INVALID_MAX_TOOL_CALLS"),
])
def test_agent_input_validation(message, budget, code):
    model = Model()
    result = run_agent(user_message=message, history=[], model_client=model,
                       tool_dispatcher=Dispatcher(), max_tool_calls=budget)
    assert result.error.code == code
    assert not model.conversations


def test_agent_empty_model_reply():
    result = run_agent(user_message="Chào", history=[], model_client=Model(ModelReply(text=" ")),
                       tool_dispatcher=Dispatcher())
    assert result.error.code == "EMPTY_MODEL_REPLY"


def adapter():
    return GeminiAdapter(key_router=ApiKeyRouter([
        ApiKeyEntry(name="free", api_key="test-free", tier="free"),
        ApiKeyEntry(name="standard", api_key="test-standard"),
    ]), model="gemini-2.5-flash")


def mock_transport(monkeypatch, responses):
    requests = []
    responses = iter(responses)

    def urlopen(request, timeout):
        requests.append(json.loads(request.data))
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return BytesIO(json.dumps(response).encode())

    monkeypatch.setattr("app.ai.vendor.gemini_adapter.urlopen", urlopen)
    return requests


def body(*parts):
    return {"candidates": [{"content": {"role": "model", "parts": list(parts)}}]}


def test_real_adapter_agent_roundtrip_keeps_function_parts(monkeypatch):
    call = {"functionCall": {"name": "view", "args": {"resource": "inventory"},
                             "id": "call-1"}, "thoughtSignature": "signature"}
    requests = mock_transport(monkeypatch, [body(call), body({"text": "Kho trống."})])
    result = run_agent(user_message="Xem kho", history=[], model_client=adapter(),
                       tool_dispatcher=Dispatcher())
    assert result.status == AgentStatus.OK
    assert requests[0]["systemInstruction"]
    assert requests[1]["contents"][1]["parts"] == [call]
    response = requests[1]["contents"][2]["parts"][0]["functionResponse"]
    assert response["id"] == "call-1"
    assert response["response"]["status"] == "ok"
    assert {item["name"] for item in requests[0]["tools"][0]["functionDeclarations"]} == {
        "view", "add", "update", "delete",
    }


def test_scan_and_json_generation_keep_existing_contract(monkeypatch):
    requests = mock_transport(monkeypatch, [
        body({"text": '{"ingredients":[{"name":"Trứng","confidence":null}],"image_quality":"ok"}'}),
        body({"text": '{"slots":{}}'}),
    ])
    client = adapter()
    result = scan_ingredients(image_bytes=b"image", mime_type="image/jpeg", model_client=client)
    assert result["ingredients"][0]["name"] == "Trứng"
    assert client.generate_text("plan") == '{"slots":{}}'
    assert all(request["generationConfig"]["responseMimeType"] == "application/json"
               for request in requests)
    assert requests[0]["contents"][0]["parts"][1]["inlineData"]["mimeType"] == "image/jpeg"


def test_recipe_paraphrase_uses_plain_text(monkeypatch):
    requests = mock_transport(monkeypatch, [body({"text": "  Luộc trứng 5 phút.  "})])
    result = generate_recipe(recipe="Boil egg for 5 minutes.",
                             user_context={"preferences": ["Vietnamese"]}, model_client=adapter())
    assert result == "Luộc trứng 5 phút."
    assert "generationConfig" not in requests[0]
    assert "Keep ingredients" in requests[0]["systemInstruction"]["parts"][0]["text"]


@pytest.mark.parametrize("recipe,context,code", [
    (" ", {}, "NO_REFERENCE_RECIPE"), (None, {}, "INVALID_INPUT"),
    ("Recipe", [], "INVALID_INPUT"),
])
def test_recipe_validation(recipe, context, code):
    with pytest.raises(GenerateRecipeError) as error:
        generate_recipe(recipe=recipe, user_context=context, model_client=adapter())
    assert error.value.code == code


def test_recipe_empty_model_text():
    class EmptyModel:
        def generate_recipe_text(self, **kwargs):
            return " "

    with pytest.raises(GenerateRecipeError) as error:
        generate_recipe(recipe="Recipe", user_context={}, model_client=EmptyModel())
    assert error.value.code == "EMPTY_MODEL_REPLY"


@pytest.mark.parametrize("failure", [
    HTTPError("https://example.invalid", 429, "rate limited", {}, None), URLError("offline"),
])
def test_transport_fallback(monkeypatch, failure):
    requests = mock_transport(monkeypatch, [failure, body({"text": "ok"})])
    assert adapter().generate_text("test") == "ok"
    assert len(requests) == 2


def test_invalid_provider_reply_is_rejected(monkeypatch):
    mock_transport(monkeypatch, [{}])
    with pytest.raises(GeminiTransportError):
        adapter().complete(system_instruction="rules", conversation=[ChatMessage("user", "hi")],
                           tools=["view"])


def test_fastapi_facade_uses_shared_client_and_safe_errors(monkeypatch):
    # Chỉ cần cấu hình hợp lệ để import; test không mở kết nối database.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://test@localhost/ai_unit_test")
    from app.ai import gemini_provider
    from app.core.errors import APIError

    monkeypatch.setattr(gemini_provider, "create_gemini_client",
                        lambda: Model(ModelReply(text="Xin chào.")))
    result = gemini_provider.run_agent(user_message="Chào", history=[], tool_dispatcher=Dispatcher())
    assert result.message == "Xin chào."

    class FailingModel:
        def complete(self, **kwargs):
            raise GeminiApiError(429)

    monkeypatch.setattr(gemini_provider, "create_gemini_client", FailingModel)
    with pytest.raises(APIError) as error:
        gemini_provider.run_agent(user_message="Chào", history=[], tool_dispatcher=Dispatcher())
    assert error.value.code == "AI_RATE_LIMITED"
