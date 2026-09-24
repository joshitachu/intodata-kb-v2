"""Registration guard: one runtime source per tenant, not per shared endpoint.

Talend Cloud puts every customer in a region behind one base URL. The duplicate
guard used to key on that URL, so onboarding a second customer was rejected as a
duplicate of the first. These tests cover both directions: a different account
must register, and the same account must still be refused however it is
presented.

There used to be a SECOND guard here -- one source of a type per project -- and
it is gone. It refused a second, different Talend tenant in a project and told
the operator to widen the first source's ingest scope, which reaches nothing at
all of the second tenant. Two tests below (`test_a_second_different_tenant...`
and `test_a_second_tenant_is_not_a_separate_kind_of_conflict`) were written to
assert that refusal; they now assert its absence, and say so, because a deleted
test would leave no record that the behaviour flipped on purpose.

They also pin the SHAPE of the remaining refusal. Refusing is correct -- a
second registration is a second poll of the vendor and a second mirror of one
estate, free to drift -- but a refusal that only says "no" is a dead end, and
the supported way forward (attaching to the existing mirror) cannot be offered
by any client that does not know which source collided. The 409 therefore
carries a `conflict` block, and the disclosure rule it obeys is pinned here too:
it may not name a source the caller could not already read.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.config import settings
from src.db.models import Role, User, runtime_data  # noqa: F401 - registers source tables
from src.runtime.providers.talend_tmc import TalendTmcRuntimeProvider
from src.runtime.store import RuntimeStoreRepository

_EU = "https://api.eu.cloud.talend.com"
_TENANT_A = ["env-a1", "env-a2"]
_TENANT_B = ["env-b1"]
_TOKENS = {"token-a": _TENANT_A, "token-a-rotated": _TENANT_A, "token-b": _TENANT_B}
_CSRF = {"cookie": "csrf_token=test_csrf", "X-CSRF-Token": "test_csrf"}


def _mock_user() -> User:
    role = MagicMock(spec=Role)
    role.name = "admin"
    user = MagicMock(spec=User)
    user.id = 1
    user.username = "admin"
    user.roles = [role]
    return user


def _handler(request: httpx.Request) -> httpx.Response:
    token = request.headers["Authorization"].removeprefix("Bearer ")
    environment_ids = _TOKENS.get(token)
    if environment_ids is None:
        return httpx.Response(401, json={"error": "unauthorized"})
    return httpx.Response(
        200,
        json={
            "items": [{"id": item, "name": item.upper()} for item in environment_ids],
            "total": len(environment_ids),
        },
    )


def _body(token: str, label: str, project_id: str | None = "p1") -> dict:
    # TMC requires an explicit environment selection, so every registration
    # carries the scope its token can actually see.
    environment_ids = _TOKENS.get(token) or ["env-unknown"]
    body = {
        "provider_key": "tmc",
        "label": label,
        "credentials": {
            "api_token": token,
            "base_url": _EU,
            "environment_ids": environment_ids,
        },
        "sync_interval_seconds": 60,
    }
    # `project_id=None` OMITS the field rather than sending a null: that is the
    # ownerless registration a client actually makes, and the two must not be
    # allowed to diverge in what they mean.
    if project_id is not None:
        body["project_id"] = project_id
    return body


@pytest.fixture
async def client(db_session, monkeypatch):
    from src.api.dependencies import get_current_user, get_db

    monkeypatch.setattr(settings, "source_credentials_encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous", "")
    monkeypatch.setattr(settings, "source_credentials_encryption_key_id", "primary")
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous_id", "previous")

    provider = TalendTmcRuntimeProvider(transport=httpx.MockTransport(_handler))

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: _mock_user()

    transport = ASGITransport(app=app)
    with (
        patch("src.api.dependencies.has_permission", return_value=True),
        patch("src.api.routes.runtime_sources.get_source_provider", return_value=provider),
        patch(
            "src.api.routes.runtime_sources.get_user_project_ids",
            AsyncMock(return_value=None),
        ),
    ):
        async with AsyncClient(
            transport=transport, base_url="http://test", headers=_CSRF
        ) as http_client:
            yield SimpleNamespace(
                http=http_client,
                repo=RuntimeStoreRepository(db_session),
                provider=provider,
            )

    app.dependency_overrides.clear()


def _restricted_scope(project_ids: list[str]):
    """Re-patch the caller's project scope for one assertion.

    The client fixture pins scope to None (unrestricted). Nesting a second
    patch over the same target is what lets a single test ask what a
    narrower-scoped operator would have been told.
    """
    return patch(
        "src.api.routes.runtime_sources.get_user_project_ids",
        AsyncMock(return_value=project_ids),
    )


async def _seed_source(client, *, token: str, identity: str, project_id: str, label: str) -> int:
    """Insert a source carrying an identity written under an older rule."""
    source = await client.repo.create_source_config(
        project_id=project_id, source_type="tmc", physical_identity=identity, label=label
    )
    await client.repo.write_credentials(
        source_id=source.id,
        plaintext_payload={
            "api_token": token,
            "base_url": _EU,
            "environment_ids": _TOKENS.get(token) or ["env-unknown"],
        },
        credential_type="tmc",
        rotated_by="1",
    )
    return source.id


class TestCreate:
    async def test_a_second_different_tenant_in_one_project_now_registers(self, client):
        """THE REPORTED BUG, inverted into its fix.

        This test previously asserted a 409 reading "This project already has a
        TMC source. Edit its ingest scope to add environments." That expectation
        WAS the defect: the product owner was connecting a second, different
        Talend tenant -- another customer, another credential, another estate --
        and the advice pointed at the ingest scope of a source that mirrors none
        of it. `uq_runtime_sources_project_tmc` went with migration 105 and the
        route's pre-check went with it; both tenants now register, and the
        project reads both.
        """
        first = await client.http.post("/api/v1/runtime/sources", json=_body("token-a", "Customer A"))
        second = await client.http.post(
            "/api/v1/runtime/sources", json=_body("token-b", "Customer B", project_id="p1")
        )

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["source"]["id"] != second.json()["source"]["id"]
        # Both are attached to the one project, and both stamp their rows with
        # it -- two mirrors of two estates, not two mirrors of one.
        for response in (first, second):
            source = response.json()["source"]
            assert source["estate_key"] == "p1"
            assert source["linked_project_ids"] == ["p1"]
            assert source["is_ownerless"] is False

    async def test_second_account_on_the_same_endpoint_registers(self, client):
        first = await client.http.post("/api/v1/runtime/sources", json=_body("token-a", "Customer A"))
        second = await client.http.post(
            "/api/v1/runtime/sources", json=_body("token-b", "Customer B", project_id="p2")
        )

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["source"]["id"] != second.json()["source"]["id"]

    async def test_same_account_registered_twice_is_refused(self, client):
        await client.http.post("/api/v1/runtime/sources", json=_body("token-a", "Customer A"))
        again = await client.http.post(
            "/api/v1/runtime/sources", json=_body("token-a", "Customer A again", project_id="p2")
        )

        assert again.status_code == 409
        assert again.json()["detail"].startswith(
            "That physical runtime source is already registered"
        )

    async def test_the_refusal_names_the_owner_and_the_way_forward(self, client):
        """A duplicate 409 has to be a signpost, not a dead end.

        One Talend tenant backing two scanned projects is the normal case, and
        the supported answer is a read grant. An operator who is only told the
        source exists has no way to discover that.
        """
        await client.http.post("/api/v1/runtime/sources", json=_body("token-a", "Customer A"))
        again = await client.http.post(
            "/api/v1/runtime/sources", json=_body("token-a", "Customer A again", project_id="p2")
        )

        detail = again.json()["detail"]
        assert "'Customer A'" in detail and "'p1'" in detail
        assert "read access" in detail

    async def test_the_refusal_hides_a_project_the_caller_cannot_see(self, client):
        """A 409 is reachable by anyone holding a working token for the tenant.

        Naming an out-of-scope project there would hand it to someone with no
        access to it, so the owner is named only when already visible.
        """
        await client.http.post("/api/v1/runtime/sources", json=_body("token-a", "Customer A"))

        with _restricted_scope(["p2"]):
            again = await client.http.post(
                "/api/v1/runtime/sources",
                json=_body("token-a", "Customer A again", project_id="p2"),
            )

        detail = again.json()["detail"]
        assert again.status_code == 409
        assert "p1" not in detail and "Customer A" not in detail
        assert "in another project" in detail

    async def test_the_refusal_carries_a_machine_readable_conflict(self, client):
        """Prose alone cannot be acted on.

        A client that wants to offer the read grant has to know WHICH source
        collided; parsing it back out of an English sentence is not a contract.
        """
        created = await client.http.post(
            "/api/v1/runtime/sources", json=_body("token-a", "Customer A")
        )
        again = await client.http.post(
            "/api/v1/runtime/sources", json=_body("token-a", "Customer A again", project_id="p2")
        )

        body = again.json()
        assert again.status_code == 409
        # `detail` stays a plain sentence: clients that render only that field
        # must keep working.
        assert isinstance(body["detail"], str)
        assert body["conflict"] == {
            "kind": "physical_identity",
            "can_request_share": True,
            "source_id": created.json()["source"]["id"],
            "source_label": "Customer A",
            # `owner_project_id` used to stand here. It claimed a relation that
            # does not exist: what the source holds is the stamp its rows carry,
            # and who may read them is a list that can be empty or plural.
            "estate_key": "p1",
            "linked_project_ids": ["p1"],
        }

    async def test_a_second_tenant_is_not_a_separate_kind_of_conflict(self, client):
        """The `project_source_type` conflict kind is gone with its rule.

        This test previously asserted that a second TMC credential in one
        project produced a 409 carrying `kind: "project_source_type"`, so that a
        client could render "widen the ingest scope" instead of offering a
        share. There is no second kind to distinguish now, because there is no
        second refusal: only a collision on the TENANT is refused, and that one
        kind is `physical_identity`. A client switching on the old value would
        be branching on a case the API can no longer produce.
        """
        await client.http.post("/api/v1/runtime/sources", json=_body("token-a", "Customer A"))
        second = await client.http.post(
            "/api/v1/runtime/sources", json=_body("token-b", "Customer B", project_id="p1")
        )
        # ...and the surviving kind still reaches the wire, on the collision
        # that is still real: the same tenant twice.
        same_tenant = await client.http.post(
            "/api/v1/runtime/sources", json=_body("token-a", "Customer A again", project_id="p1")
        )

        assert second.status_code == 200
        assert "project_source_type" not in second.text
        assert same_tenant.status_code == 409
        assert same_tenant.json()["conflict"]["kind"] == "physical_identity"

    async def test_the_conflict_names_nothing_the_caller_cannot_already_see(self, client):
        """The structured block obeys the same disclosure rule as the prose.

        Otherwise the fix would reintroduce, as machine-readable JSON, exactly
        the leak the sentence is careful to avoid: a 409 is reachable by anyone
        holding a working token for the tenant, so an unconditional owner field
        answers "does project X exist" for someone with no access to it. The id
        is withheld on the same test -- every route keyed on it answers 404 for
        this caller, so it is a handle to nothing.
        """
        await client.http.post("/api/v1/runtime/sources", json=_body("token-a", "Customer A"))

        with _restricted_scope(["p2"]):
            again = await client.http.post(
                "/api/v1/runtime/sources",
                json=_body("token-a", "Customer A again", project_id="p2"),
            )

        body = again.json()
        assert again.status_code == 409
        # Still says the tenant is taken, and still names sharing as the route
        # forward -- withholding the owner must not withhold the way out.
        assert body["conflict"] == {"kind": "physical_identity", "can_request_share": False}
        assert "p1" not in again.text and "Customer A" not in again.text
        assert "read access" in body["detail"]

    async def test_same_account_under_a_second_token_is_refused(self, client):
        """The mirror case the guard exists for: one TMC, two registrations."""
        await client.http.post("/api/v1/runtime/sources", json=_body("token-a", "Customer A"))
        mirror = await client.http.post(
            "/api/v1/runtime/sources", json=_body("token-a-rotated", "Mirror", project_id="p2")
        )

        assert mirror.status_code == 409

    async def test_credentials_that_cannot_identify_an_account_are_rejected(self, client):
        response = await client.http.post("/api/v1/runtime/sources", json=_body("token-unknown", "Bad"))

        assert response.status_code == 400
        assert "token-unknown" not in response.text


class TestOwnerlessRegistration:
    """A tenant registered before any project exists, end to end through the API."""

    async def test_a_registration_with_no_project_succeeds(self, client):
        """The field is omitted entirely -- the shape a client actually sends."""
        response = await client.http.post(
            "/api/v1/runtime/sources", json=_body("token-a", "Tenant A", project_id=None)
        )

        source = response.json()["source"]
        assert response.status_code == 200
        assert source["is_ownerless"] is True
        assert source["estate_key"] == f"estate:{source['source_key']}"
        # Attached to nobody. `[]` is the true state, not a missing lookup.
        assert source["linked_project_ids"] == []

    async def test_an_ownerless_source_is_listed_with_its_attachment_state(self, client):
        """`GET /runtime/sources` publishes both halves for every source.

        A client cannot be asked to re-derive either one: the estate prefix is a
        server rule, and the attachment list has no other source of truth.
        """
        await client.http.post(
            "/api/v1/runtime/sources", json=_body("token-a", "Tenant A", project_id=None)
        )
        await client.http.post("/api/v1/runtime/sources", json=_body("token-b", "Tenant B"))

        listed = (await client.http.get("/api/v1/runtime/sources")).json()["sources"]

        by_label = {item["label"]: item for item in listed}
        assert by_label["Tenant A"]["is_ownerless"] is True
        assert by_label["Tenant A"]["linked_project_ids"] == []
        assert by_label["Tenant B"]["is_ownerless"] is False
        assert by_label["Tenant B"]["linked_project_ids"] == ["p1"]
        assert by_label["Tenant B"]["estate_key"] == "p1"
        # The conflated field is gone from the wire, not merely deprecated: a
        # client still reading it would silently get `undefined` instead of an
        # error, which is the failure mode this assertion exists to catch.
        assert "project_id" not in by_label["Tenant A"]

    async def test_attaching_a_project_makes_the_estate_readable(self, client):
        """Registration attaches nothing; the readers route is what attaches."""
        created = await client.http.post(
            "/api/v1/runtime/sources", json=_body("token-a", "Tenant A", project_id=None)
        )
        source_id = created.json()["source"]["id"]

        attached = await client.http.post(
            f"/api/v1/runtime/sources/{source_id}/readers", json={"project_id": "p1"}
        )
        refetched = await client.http.get(f"/api/v1/runtime/sources/{source_id}")

        assert attached.status_code == 200
        assert attached.json()["grant"]["source_estate_key"] == (
            created.json()["source"]["estate_key"]
        )
        assert refetched.json()["source"]["linked_project_ids"] == ["p1"]
        # Still ownerless: attaching a reader does not move the estate into a
        # project, and a UI that showed it as owned would be describing a
        # relation that no longer exists.
        assert refetched.json()["source"]["is_ownerless"] is True
        assert await client.repo.resolve_runtime_read_projects("p1") == [
            "p1",
            created.json()["source"]["estate_key"],
        ]

    async def test_the_same_tenant_is_still_refused_without_a_project(self, client):
        """Omitting the project is not a way around the one guard that remains."""
        await client.http.post(
            "/api/v1/runtime/sources", json=_body("token-a", "Tenant A", project_id=None)
        )

        again = await client.http.post(
            "/api/v1/runtime/sources", json=_body("token-a", "Tenant A again", project_id=None)
        )

        body = again.json()
        assert again.status_code == 409
        assert body["conflict"]["kind"] == "physical_identity"
        # Nothing to attach to, so nothing is offered -- but the source is still
        # named, because that is what makes the refusal actionable.
        assert body["conflict"]["can_request_share"] is False
        assert body["conflict"]["source_label"] == "Tenant A"
        assert body["conflict"]["linked_project_ids"] == []

    async def test_a_collision_with_an_unattached_source_is_disclosed(self, client):
        """The case the old disclosure rule got wrong.

        Withholding here was a side effect of testing visibility against an
        OWNING project: an `estate:` key is in nobody's scope, so a tenant
        registered before any project was reported as "in another project" --
        naming a project that does not exist, and hiding the one object that
        explains the refusal from a caller who can already list it and is
        entitled to attach to it.

        Disclosure is now exactly what `GET /runtime/sources/{id}` would return
        to this caller, so nothing here is reachable that was not reachable
        before the 409.
        """
        created = await client.http.post(
            "/api/v1/runtime/sources", json=_body("token-a", "Tenant A", project_id=None)
        )

        with _restricted_scope(["p2"]):
            again = await client.http.post(
                "/api/v1/runtime/sources",
                json=_body("token-a", "Tenant A again", project_id="p2"),
            )
            visible = await client.http.get(
                f"/api/v1/runtime/sources/{created.json()['source']['id']}"
            )

        body = again.json()
        assert again.status_code == 409
        assert body["conflict"] == {
            "kind": "physical_identity",
            # The caller holds p2 and the estate is attached to nobody, so the
            # attachment they would need is one they can create themselves.
            "can_request_share": True,
            "source_id": created.json()["source"]["id"],
            "source_label": "Tenant A",
            "estate_key": created.json()["source"]["estate_key"],
            "linked_project_ids": [],
        }
        assert "linked to no project yet" in body["detail"]
        # The disclosure test, stated as the property rather than the wording:
        # everything the 409 named, this caller could already GET.
        assert visible.status_code == 200

    async def test_a_caller_with_no_project_at_all_may_not_register(self, client):
        """The one refusal a missing project does NOT remove.

        An empty (non-None) scope holds no project, and the store refuses that
        caller every source -- including unattached ownerless ones. Letting the
        write through would create a connector, and a vendor poll, that its
        creator cannot then see, manage or delete.
        """
        with _restricted_scope([]):
            response = await client.http.post(
                "/api/v1/runtime/sources", json=_body("token-a", "Tenant A", project_id=None)
            )

        assert response.status_code == 403
        assert await client.repo.list_source_configs(None) == []

    async def test_omitting_the_project_does_not_skip_the_project_check(self, client):
        """The scope check is not optional; the thing it guards is.

        Supplying a project the caller does not hold is still a 403, and
        omitting it cannot be used to reach that project by another route --
        what is registered is attached to nobody.
        """
        with _restricted_scope(["p2"]):
            refused = await client.http.post(
                "/api/v1/runtime/sources", json=_body("token-a", "Tenant A", project_id="p1")
            )
            allowed = await client.http.post(
                "/api/v1/runtime/sources", json=_body("token-a", "Tenant A", project_id=None)
            )

        assert refused.status_code == 403
        assert allowed.status_code == 200
        assert allowed.json()["source"]["linked_project_ids"] == []
        # p1 -- the project the caller was refused -- reads nothing of it.
        assert await client.repo.resolve_runtime_read_projects("p1") == ["p1"]


class TestPreNarrowingRows:
    async def test_row_stored_under_the_endpoint_identity_is_repaired(self, client):
        source_id = await _seed_source(
            client, token="token-a", identity=f"tmc:{_EU}", project_id="p1", label="Customer A"
        )

        response = await client.http.post(
            "/api/v1/runtime/sources", json=_body("token-b", "Customer B", project_id="p2")
        )

        assert response.status_code == 200
        repaired = await client.repo.get_source_physical_identity(source_id)
        assert repaired.startswith(f"tmc:{_EU}#")

    async def test_repaired_row_still_blocks_its_own_account(self, client):
        await _seed_source(
            client, token="token-a", identity=f"tmc:{_EU}", project_id="p1", label="Customer A"
        )

        response = await client.http.post(
            "/api/v1/runtime/sources", json=_body("token-a", "Customer A again", project_id="p2")
        )

        assert response.status_code == 409

    async def test_dead_credentials_do_not_block_an_unrelated_registration(self, client):
        source_id = await _seed_source(
            client, token="token-revoked", identity=f"tmc:{_EU}", project_id="p1", label="Stale"
        )

        response = await client.http.post(
            "/api/v1/runtime/sources", json=_body("token-b", "Customer B", project_id="p2")
        )

        assert response.status_code == 200
        assert await client.repo.get_source_physical_identity(source_id) == f"tmc:{_EU}"


class TestRotate:
    async def test_rotating_within_the_same_account_keeps_the_identity(self, client):
        created = await client.http.post("/api/v1/runtime/sources", json=_body("token-a", "Customer A"))
        source_id = created.json()["source"]["id"]
        before = await client.repo.get_source_physical_identity(source_id)

        response = await client.http.post(
            f"/api/v1/runtime/sources/{source_id}/credentials/rotate",
            json={"credentials": {"api_token": "token-a-rotated", "base_url": _EU}},
        )

        assert response.status_code == 200
        assert await client.repo.get_source_physical_identity(source_id) == before

    async def test_rotating_onto_another_registered_account_is_refused(self, client):
        created = await client.http.post("/api/v1/runtime/sources", json=_body("token-a", "Customer A"))
        source_id = created.json()["source"]["id"]
        await client.http.post("/api/v1/runtime/sources", json=_body("token-b", "Customer B", project_id="p2"))
        before = await client.repo.get_source_physical_identity(source_id)

        response = await client.http.post(
            f"/api/v1/runtime/sources/{source_id}/credentials/rotate",
            json={"credentials": {"api_token": "token-b", "base_url": _EU}},
        )

        assert response.status_code == 409
        assert await client.repo.get_source_physical_identity(source_id) == before
