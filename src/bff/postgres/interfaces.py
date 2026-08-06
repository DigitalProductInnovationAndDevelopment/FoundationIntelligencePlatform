"""Domain-sized repository interfaces for the PostgreSQL application runtime.

Route handlers depend on these protocols rather than on concrete repository
classes, so a repository can be replaced or faked in tests without touching the
API layer. Every method is asynchronous and every list-returning method is
expected to bound its own result set.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Protocol, Sequence


class OrganizationReader(Protocol):
    """Read contract for enriched organization profiles and their grants."""

    async def list(self, **filters: Any) -> list[dict[str, Any]]:
        """Search, filter, sort and paginate organization profiles."""
        ...

    async def detail(self, organization_id: int) -> Optional[dict[str, Any]]:
        """Return one organization with provenance and evidence, or None."""
        ...

    async def stats(self) -> dict[str, Any]:
        """Return dataset KPIs, source counts and organization-type counts."""
        ...

    async def grants(self, organization_id: int, role: str) -> dict[str, Any]:
        """Return observed transactions for one organization in the given role."""
        ...

    async def sankey(
        self, organization_id: int, *, currency: Optional[str], limit: int
    ) -> dict[str, Any]:
        """Return bounded donor-to-recipient flows for one organization."""
        ...

    async def score(
        self, organization_id: int, target_profile: Optional[Mapping[str, Any]]
    ) -> dict[str, Any]:
        """Calculate the experimental relevance score against a target profile."""
        ...


class RegistryReader(Protocol):
    """Read contract for the official Charity Commission registry layer."""

    async def page(self, **filters: Any) -> dict[str, Any]:
        """Return one cursor-paginated page of registry rows."""
        ...

    async def detail(self, registry_id: str) -> Optional[dict[str, Any]]:
        """Return one registry organization by textual registry ID, or None."""
        ...


class GrantAnalyticsReader(Protocol):
    """Read contract for versioned grant aggregates and map facts."""

    async def beneficiary_geographies(self) -> list[str]:
        """List the distinct normalized beneficiary geographies for filtering."""
        ...

    async def map(self, **filters: Any) -> dict[str, Any]:
        """Return beneficiary-country associations, totals and coverage metadata."""
        ...

    async def map_connections(
        self, *, currency: Optional[str], limit: int
    ) -> dict[str, Any]:
        """Return bounded illustrative funder-location to beneficiary associations."""
        ...

    async def overview(self, **filters: Any) -> dict[str, Any]:
        """Return the combined Overview aggregation for one applied grant scope."""
        ...

    async def suggestions(
        self, *, sources: Optional[Sequence[str]], limit: int
    ) -> dict[str, Any]:
        """Return bounded donor and recipient typeahead suggestions."""
        ...

    async def trends(self, **filters: Any) -> dict[str, Any]:
        """Return award-date period totals, including unknown-coverage periods."""
        ...

    async def drilldown(self, **filters: Any) -> dict[str, Any]:
        """Return the detailed rows behind one selected Overview segment."""
        ...

    async def summary(self) -> dict[str, Any]:
        """Return currency-separated network totals and rankings."""
        ...

    async def themes(self, *, currency: Optional[str]) -> dict[str, Any]:
        """Return programme allocations and classification coverage."""
        ...


class FunderRepository(Protocol):
    """Read and curation contract for observed source funders."""

    async def list(self, **filters: Any) -> dict[str, Any]:
        """Return the filtered, paginated observed-donor ranking."""
        ...

    async def detail(self, source_funder_key: str, **filters: Any) -> Optional[dict[str, Any]]:
        """Return one source funder's detail, or None when the key is unknown."""
        ...

    async def reset(self, source_funder_key: str, *, actor_id: str) -> Optional[dict[str, Any]]:
        """Discard a curated link override, returning the funder to source state."""
        ...

    async def relink(
        self, source_funder_key: str, profile_id: int, *, actor_id: str
    ) -> Optional[dict[str, Any]]:
        """Point a source funder at an explicit enriched profile."""
        ...

    async def queue_profile_cache(
        self, source_funder_key: str, *, actor_id: str, idempotency_key: str
    ) -> Optional[dict[str, Any]]:
        """Enqueue a durable job that rebuilds one funder's cached profile."""
        ...

    async def profile_cache(self, source_funder_key: str) -> Optional[dict[str, Any]]:
        """Return a funder's cached profile payload, or None when not yet built."""
        ...


class JobRepository(Protocol):
    """Durable job contract backing the pipeline administration surface."""

    async def enqueue(
        self,
        job_type: str,
        payload: Mapping[str, Any],
        *,
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Durably enqueue a job, deduplicating on the caller's idempotency key."""
        ...

    async def latest_status(self) -> dict[str, Any]:
        """Return the most recent job's state."""
        ...

    async def history(self, *, limit: int) -> list[dict[str, Any]]:
        """Return a bounded window of recent job runs."""
        ...

    async def events(self, *, limit: int) -> list[dict[str, Any]]:
        """Return a bounded window of recent structured job events."""
        ...
