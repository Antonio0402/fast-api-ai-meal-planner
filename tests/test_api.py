from uuid import uuid4


def test_every_product_endpoint_requires_a_valid_token(client):
    image = b"\xff\xd8\xff"
    requests = [
        ("GET", "/api/v1/users/me/profile", {}),
        ("PATCH", "/api/v1/users/me/profile", {"json": {"default_portions": 3}}),
        ("GET", "/api/v1/inventory-items", {}),
        ("POST", "/api/v1/inventory-items", {"json": {"items": [{"name": "Trứng", "unit_code": "quả"}]}}),
        ("PATCH", f"/api/v1/inventory-items/{uuid4()}", {"json": {"quantity": 1}}),
        ("DELETE", f"/api/v1/inventory-items/{uuid4()}", {}),
        ("DELETE", "/api/v1/inventory-items", {}),
        (
            "POST",
            "/api/v1/scan-jobs",
            {
                "files": {"image": ("test.jpg", image, "image/jpeg")},
                "headers": {"Idempotency-Key": "auth-test"},
            },
        ),
        ("GET", f"/api/v1/scan-jobs/{uuid4()}", {}),
        ("POST", f"/api/v1/scan-jobs/{uuid4()}/confirm", {"json": {"items": []}}),
        ("GET", "/api/v1/recipes", {}),
        ("GET", "/api/v1/weekly-plans?week_start=2026-10-12", {}),
        ("POST", "/api/v1/weekly-plans/generate", {"json": {"week_start": "2026-10-12", "portions": 2}}),
        ("PUT", "/api/v1/weekly-plans/2026-10-12/draft", {"json": {"portions": 2, "slots": {}}}),
        ("PUT", "/api/v1/weekly-plans/2026-10-12", {"json": {"expected_version": None, "portions": 2, "slots": {}}}),
        ("GET", "/api/v1/weekly-plans/2026-10-12/shopping-list", {}),
        (
            "PATCH",
            "/api/v1/weekly-plans/2026-10-12/shopping-checks",
            {"json": {"revision_id": str(uuid4()), "items": [{"ingredient_id": str(uuid4()), "dimension_key": "g", "is_checked": True}]}},
        ),
    ]
    for method, path, kwargs in requests:
        response = client.request(method, path, **kwargs)
        assert response.status_code == 401, (method, path, response.text)
        assert "error" in response.json()


def test_auth_rejects_invalid_and_expired_rsa_tokens(client, expired_token):
    invalid = client.get(
        "/api/v1/users/me/profile",
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    expired = client.get(
        "/api/v1/users/me/profile",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert invalid.status_code == 401
    assert expired.status_code == 401


def test_profile_inventory_and_recipes(client, auth_user, headers):
    profile = client.get("/api/v1/users/me/profile", headers=headers)
    assert profile.status_code == 200
    assert profile.json()["default_portions"] == 2
    assert profile.json()["user_id"] == auth_user["id"]

    updated_profile = client.patch(
        "/api/v1/users/me/profile",
        headers=headers,
        json={"default_portions": 4},
    )
    assert updated_profile.status_code == 200
    assert updated_profile.json()["default_portions"] == 4
    assert client.patch(
        "/api/v1/users/me/profile", headers=headers, json={"default_portions": 13}
    ).status_code == 422

    recipes = client.get("/api/v1/recipes", headers=headers)
    assert recipes.status_code == 200
    assert recipes.json()["total"] == 9
    first_recipe = recipes.json()["items"][0]
    assert first_recipe["servings"] > 0
    assert {"ingredient_id", "name", "quantity", "unit_code"} <= set(
        first_recipe["items"][0]
    )
    assert client.get(
        "/api/v1/recipes?limit=201", headers=headers
    ).status_code == 422

    payload = {
        "items": [
            {
                "name": "Cà chua",
                "quantity": 300,
                "unit_code": "g",
                "expiry_date": None,
            }
        ]
    }
    key_headers = {**headers, "Idempotency-Key": "inventory-replay-1"}
    first = client.post(
        "/api/v1/inventory-items", headers=key_headers, json=payload
    )
    replay = client.post(
        "/api/v1/inventory-items", headers=key_headers, json=payload
    )
    assert first.status_code == 201
    assert replay.status_code == 200
    item = first.json()["items"][0]
    assert replay.json() == first.json()

    changed = client.post(
        "/api/v1/inventory-items",
        headers=key_headers,
        json={"items": [{"name": "Sữa", "quantity": 1, "unit_code": "ml"}]},
    )
    assert changed.status_code == 409
    assert client.post(
        "/api/v1/inventory-items",
        headers=headers,
        json={"items": [{"name": "Cà chua", "quantity": 25, "unit_code": "g"}]},
    ).status_code == 201
    inventory = client.get("/api/v1/inventory-items", headers=headers)
    assert inventory.json()["total"] == 1
    assert inventory.json()["items"][0]["quantity"] == 325
    assert client.get(
        "/api/v1/inventory-items?limit=201", headers=headers
    ).status_code == 422

    patched = client.patch(
        f"/api/v1/inventory-items/{item['id']}",
        headers=headers,
        json={"quantity": 100, "expiry_date": "2026-12-25"},
    )
    assert patched.status_code == 200
    assert patched.json()["quantity"] == 100
    assert patched.json()["expiry_date"] == "2026-12-25"
    assert client.patch(
        f"/api/v1/inventory-items/{uuid4()}",
        headers=headers,
        json={"quantity": 5},
    ).status_code == 404
    assert client.delete(
        f"/api/v1/inventory-items/{item['id']}", headers=headers
    ).status_code == 204


def test_inventory_isolation_and_delete_all(client, auth_user, second_auth_user, headers, other_headers):
    created = client.post(
        "/api/v1/inventory-items",
        headers=headers,
        json={
            "items": [
                {"name": "Trứng", "quantity": 4, "unit_code": "quả"},
                {"name": "Sữa", "quantity": 250, "unit_code": "ml"},
            ]
        },
    )
    assert created.status_code == 201
    item_id = created.json()["items"][0]["id"]
    assert client.get("/api/v1/inventory-items", headers=other_headers).json()["total"] == 0
    assert client.delete(
        f"/api/v1/inventory-items/{item_id}", headers=other_headers
    ).status_code == 404
    assert client.delete("/api/v1/inventory-items", headers=headers).status_code == 204
    assert client.get("/api/v1/inventory-items", headers=headers).json()["total"] == 0


def test_weekly_plans_publish_generate_and_shopping(client, headers, monkeypatch):
    from app.services import meal_plan_service
    from tests.fakes import FakeGeminiClient

    no_plan = client.get(
        "/api/v1/weekly-plans?week_start=2026-10-12", headers=headers
    )
    assert no_plan.status_code == 200
    assert no_plan.json() == {"plan": None}

    draft_slots = {"0-0": "r1", "0-1": "r2"}
    draft = client.put(
        "/api/v1/weekly-plans/2026-10-12/draft",
        headers=headers,
        json={"portions": 2, "slots": draft_slots},
    )
    assert draft.status_code == 200
    assert client.get(
        "/api/v1/weekly-plans?week_start=2026-10-12", headers=headers
    ).json()["plan"]["draft_slots"] == draft_slots
    assert client.put(
        "/api/v1/weekly-plans/2026-10-13/draft",
        headers=headers,
        json={"portions": 2, "slots": {}},
    ).status_code == 422
    assert client.put(
        "/api/v1/weekly-plans/2026-10-12/draft",
        headers=headers,
        json={"portions": 2, "slots": {"0-0": "missing-recipe"}},
    ).status_code == 404

    publish_payload = {
        "expected_version": None,
        "portions": 2,
        "slots": {**{f"{day}-{meal}": "r1" for day in range(2) for meal in range(3)}, "2-0": "r3"},
    }
    published = client.put(
        "/api/v1/weekly-plans/2026-10-12",
        headers=headers,
        json=publish_payload,
    )
    assert published.status_code == 200
    assert published.json()["version"] == 1
    assert client.put(
        "/api/v1/weekly-plans/2026-10-12",
        headers=headers,
        json={**publish_payload, "expected_version": 0},
    ).status_code == 409

    inventory = client.post(
        "/api/v1/inventory-items",
        headers=headers,
        json={
            "items": [
                {"name": "Cà chua", "quantity": 100, "unit_code": "g"},
                {"name": "Trứng", "quantity": None, "unit_code": "quả"},
                {"name": "Sữa", "quantity": 50, "unit_code": "g"},
            ]
        },
    )
    assert inventory.status_code == 201
    shopping = client.get(
        "/api/v1/weekly-plans/2026-10-12/shopping-list", headers=headers
    )
    assert shopping.status_code == 200
    items = shopping.json()["items"]
    tomato = next(item for item in items if item["name"] == "Cà chua")
    egg = next(item for item in items if item["name"] == "Trứng")
    milk = next(item for item in items if item["name"] == "Sữa")
    assert tomato["unit"] == "kg"
    assert tomato["to_buy"] == 1.1
    assert tomato["key"].endswith("|g")
    assert egg["needs_check"] is True
    assert milk["needs_check"] is True
    checked = client.patch(
        "/api/v1/weekly-plans/2026-10-12/shopping-checks",
        headers=headers,
        json={
            "revision_id": published.json()["revision_id"],
            "items": [
                {
                    "ingredient_id": tomato["ingredient_id"],
                    "dimension_key": "g",
                    "is_checked": True,
                }
            ],
        },
    )
    assert checked.status_code == 200
    assert checked.json()["items"][0]["is_checked"] is True

    second_published = client.put(
        "/api/v1/weekly-plans/2026-10-12",
        headers=headers,
        json={**publish_payload, "expected_version": 1},
    )
    assert second_published.status_code == 200
    stale_check = client.patch(
        "/api/v1/weekly-plans/2026-10-12/shopping-checks",
        headers=headers,
        json={
            "revision_id": published.json()["revision_id"],
            "items": [
                {
                    "ingredient_id": tomato["ingredient_id"],
                    "dimension_key": "g",
                    "is_checked": True,
                }
            ],
        },
    )
    assert stale_check.status_code == 409
    assert client.get(
        "/api/v1/weekly-plans/2026-11-02/shopping-list", headers=headers
    ).status_code == 404

    fake_client = FakeGeminiClient(plan_result="invalid json")
    monkeypatch.setattr(
        meal_plan_service, "create_gemini_client", lambda: fake_client
    )
    generated = client.post(
        "/api/v1/weekly-plans/generate",
        headers=headers,
        json={"week_start": "2026-10-12", "portions": 3},
    )
    assert generated.status_code == 200
    assert len(generated.json()["slots"]) == 21
    assert generated.json()["slots"]["0-0"] == "r1"
    assert generated.json()["slots"]["0-1"] == "r2"
    assert client.post(
        "/api/v1/weekly-plans/generate",
        headers=headers,
        json={"week_start": "2026-10-13", "portions": 3},
    ).status_code == 422
