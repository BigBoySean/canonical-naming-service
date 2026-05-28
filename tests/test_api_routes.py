"""End-to-end HTTP tests for all five API endpoints.

Uses `app.dependency_overrides` to inject a `ResolverService` backed by
`MockLLMMatcher`, so the suite runs fully offline. The fixture keeps a
single service instance across all requests in a test (so e.g.
`POST /entities` -> `POST /resolve` sees the new entity within the same
test, exercising cache invalidation over HTTP).
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from canonical_naming.api.deps import get_resolver_service
from canonical_naming.main import app
from canonical_naming.matching.exact import ExactMatcher
from canonical_naming.matching.fuzzy import FuzzyMatcher
from canonical_naming.repos.entity_repo import load_repo_from_seed
from canonical_naming.services.resolver import ResolverService
from tests.fixtures import MockLLMMatcher


@pytest.fixture
def client() -> Iterator[TestClient]:
    """TestClient with a per-test ResolverService (mock LLM, no network).

    The same service is returned for every request in this test, so
    state-mutating endpoints (POST /entities) and subsequent reads
    (GET /entities/{id}, POST /resolve) share the same repo — exactly
    as they do in the running server.
    """
    repo = load_repo_from_seed()
    service = ResolverService(
        matchers=[
            ExactMatcher(),
            FuzzyMatcher(threshold=92),
            MockLLMMatcher({}),
        ],
        repo=repo,
    )
    app.dependency_overrides[get_resolver_service] = lambda: service
    yield TestClient(app)
    app.dependency_overrides.clear()


# ============================================================================
# POST /resolve
# ============================================================================


def test_resolve_bcp_viii_returns_blackstone(client: TestClient) -> None:
    response = client.post("/resolve", json={"raw_name": "BCP VIII"})
    assert response.status_code == 200
    data = response.json()
    assert data["raw_name"] == "BCP VIII"
    assert data["canonical_id"] == "ent_blackstone_cp_viii"
    assert data["canonical_name"] == "Blackstone Capital Partners VIII, L.P."
    assert data["method"] == "exact"
    assert data["needs_review"] is False
    assert data["confidence"] == 1.0


def test_resolve_hf_xi_returns_200_needs_review(client: TestClient) -> None:
    """The brief's planted distractor over HTTP — must be 200 with
    needs_review=true, not 404."""
    response = client.post(
        "/resolve",
        json={"raw_name": "Hellman & Friedman Capital Partners XI"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["needs_review"] is True
    assert data["canonical_id"] is None
    assert data["canonical_name"] is None
    assert data["method"] == "none"
    assert data["confidence"] == 0.0


def test_resolve_empty_raw_name_returns_422(client: TestClient) -> None:
    """Pydantic validation: `raw_name` has `min_length=1`. An empty string
    is a malformed request, not a needs_review case."""
    response = client.post("/resolve", json={"raw_name": ""})
    assert response.status_code == 422


def test_resolve_missing_field_returns_422(client: TestClient) -> None:
    response = client.post("/resolve", json={})
    assert response.status_code == 422


# ============================================================================
# POST /resolve/batch
# ============================================================================


def test_resolve_batch_ordered_results(client: TestClient) -> None:
    inputs = [
        "BCP VIII",
        "Apollo Inv. Fund IX",
        "Completely Made Up Fund ZZZ",
        "KKR Americas Fund 13 - Parallel Vehicle",
    ]
    response = client.post("/resolve/batch", json={"raw_names": inputs})
    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) == 4
    assert data["results"][0]["canonical_id"] == "ent_blackstone_cp_viii"
    assert data["results"][1]["canonical_id"] == "ent_apollo_investment_ix"
    assert data["results"][2]["needs_review"] is True
    assert data["results"][3]["canonical_id"] == "ent_kkr_americas_xiii"
    for i, raw in enumerate(inputs):
        assert data["results"][i]["raw_name"] == raw


def test_resolve_batch_empty_list_returns_422(client: TestClient) -> None:
    # `raw_names: list[str] = Field(min_length=1)` — Pydantic rejects empty.
    response = client.post("/resolve/batch", json={"raw_names": []})
    assert response.status_code == 422


# ============================================================================
# GET /entities/{id}
# ============================================================================


def test_get_entity_known_id_returns_200(client: TestClient) -> None:
    response = client.get("/entities/ent_blackstone_cp_viii")
    assert response.status_code == 200
    data = response.json()
    assert data["canonical_id"] == "ent_blackstone_cp_viii"
    assert data["canonical_name"] == "Blackstone Capital Partners VIII, L.P."
    assert "BCP VIII" in data["aliases"]


def test_get_entity_unknown_id_returns_404(client: TestClient) -> None:
    response = client.get("/entities/ent_does_not_exist")
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "not found" in detail.lower()


# ============================================================================
# POST /entities — including the end-to-end cache-invalidation test
# ============================================================================


def test_create_entity_returns_201_with_generated_id(client: TestClient) -> None:
    response = client.post(
        "/entities",
        json={
            "canonical_name": "Brand New Fund III, L.P.",
            "aliases": ["Brand New III"],
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["canonical_id"].startswith("ent_brand_new_fund")
    assert data["canonical_name"] == "Brand New Fund III, L.P."
    assert data["aliases"] == ["Brand New III"]


def test_create_entity_collision_returns_409(client: TestClient) -> None:
    body = {"canonical_name": "Duplicate Fund I, L.P."}
    first = client.post("/entities", json=body)
    assert first.status_code == 201
    second = client.post("/entities", json=body)
    assert second.status_code == 409
    assert "collision" in second.json()["detail"].lower()


def test_register_then_get_then_resolve_via_http_full_loop(
    client: TestClient,
) -> None:
    """The end-to-end cache-invalidation test over HTTP.

    Equivalent to the in-process `test_register_entity_then_resolve_finds_it`
    in `test_resolver_service.py`, but exercised through real FastAPI
    requests. Proves that the API's dependency-injected singleton service
    invalidates matcher caches correctly across request boundaries.
    """
    # Step 1: POST /entities — register a new entity.
    create = client.post(
        "/entities",
        json={
            "canonical_name": "Brand New Fund III, L.P.",
            "aliases": ["Brand New III", "BNF III"],
        },
    )
    assert create.status_code == 201
    new_id = create.json()["canonical_id"]

    # Step 2: GET /entities/{id} — proves it persisted.
    get_resp = client.get(f"/entities/{new_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["canonical_name"] == "Brand New Fund III, L.P."

    # Step 3: POST /resolve with the new name — proves cache invalidation.
    resolve = client.post(
        "/resolve",
        json={"raw_name": "Brand New Fund III, L.P."},
    )
    assert resolve.status_code == 200
    data = resolve.json()
    assert data["canonical_id"] == new_id
    assert data["method"] == "exact"
    assert data["needs_review"] is False


# ============================================================================
# GET /health (regression)
# ============================================================================


def test_health_still_works(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


# ============================================================================
# OpenAPI smoke
# ============================================================================


def test_openapi_lists_all_five_endpoints(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/health" in paths
    assert "/resolve" in paths
    assert "/resolve/batch" in paths
    assert "/entities/{entity_id}" in paths
    assert "/entities" in paths
