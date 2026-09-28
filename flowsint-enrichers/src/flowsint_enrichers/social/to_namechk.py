"""
Namechk Username Availability Checker Enricher.

Scans a username across hundreds of social networks and web services
using the ``naminter`` library (WhatsMyName dataset), reporting which
services have an account registered with that username.

Input:  Username
Output: SocialAccount

Requires: pip install naminter
"""

from typing import Any, Dict, List, Optional

from flowsint_core.core.enricher_base import Enricher
from flowsint_core.core.logger import Logger
from flowsint_types.social_account import SocialAccount
from flowsint_types.username import Username

from flowsint_enrichers.registry import flowsint_enricher

# ---------------------------------------------------------------------------
# Optional: naminter
# ---------------------------------------------------------------------------
try:
    from naminter import CurlCFFISession, Naminter, WMNResult, WMNStatus

    HAS_NAMECHK = True
except ImportError:
    HAS_NAMECHK = False

    # Stub type references so type hints remain valid
    Naminter = None  # type: ignore[assignment, misc]
    CurlCFFISession = None  # type: ignore[assignment, misc]
    WMNResult = None  # type: ignore[assignment, misc]
    WMNStatus = None  # type: ignore[assignment, misc]


class Hunter:
    """Simple async wrapper around naminter's ``Naminter`` class.

    Provides a clean ``hunt(username)`` interface for checking a single
    username across all sites in the WhatsMyName dataset.
    """

    def __init__(self) -> None:
        if not HAS_NAMECHK:
            raise RuntimeError("naminter is not installed")
        self._http = CurlCFFISession()
        self._naminter = Naminter(http_client=self._http)

    async def hunt(self, username: str) -> List[Dict[str, Any]]:
        """Check *username* across all sites.

        Returns a list of dicts with keys ``site``, ``url``, ``exists``.
        """
        results: List[Dict[str, Any]] = []
        try:
            async for result in self._naminter.enumerate_usernames([username]):
                results.append(
                    {
                        "site": result.name,
                        "url": result.uri_pretty or result.uri_check or "",
                        "exists": result.status == WMNStatus.exists,
                    }
                )
        except Exception:
            # Individual site errors are caught internally by naminter;
            # re-raise only unexpected failures
            raise
        return results

    async def close(self) -> None:
        """Release HTTP sessions."""
        try:
            await self._naminter.close()
        except Exception:
            pass
        try:
            await self._http.close()
        except Exception:
            pass


@flowsint_enricher
class NamechkEnricher(Enricher):
    """[NAMECHK] Checks username availability across social networks using naminter."""

    InputType = Username
    OutputType = SocialAccount

    @classmethod
    def name(cls) -> str:
        return "username_to_namechk"

    @classmethod
    def category(cls) -> str:
        return "social"

    @classmethod
    def key(cls) -> str:
        return "value"

    @classmethod
    def icon(cls) -> str:
        return "✓"

    @classmethod
    def documentation(cls) -> str:
        return (
            "## Namechk Username Checker\n\n"
            "Scans a username across hundreds of social networks using the "
            "naminter library (WhatsMyName dataset). Reports which services "
            "have an account registered with that username.\n\n"
            "### Input\n"
            "A ``Username`` entity — the handle to check.\n\n"
            "### Requirements\n"
            "``pip install naminter``\n\n"
            "### Output\n"
            "Returns a ``SocialAccount`` for each site where the username "
            "was found, including the profile URL and platform name.\n"
        )

    @classmethod
    def get_params_schema(cls) -> List[Dict[str, Any]]:
        return [
            {
                "name": "max_timeout",
                "type": "integer",
                "default": 120,
                "label": "Scan timeout (seconds)",
                "description": "Maximum time to wait for the naminter scan.",
            },
        ]

    async def scan(self, data: List[InputType]) -> List[OutputType]:
        """Check each username across social networks."""
        if not HAS_NAMECHK:
            Logger.error(
                self.sketch_id,
                {
                    "message": (
                        "naminter is not installed. "
                        "Run: pip install naminter"
                    ),
                },
            )
            return self._make_fallback(data)

        results: List[OutputType] = []
        hunter: Optional[Hunter] = None
        try:
            hunter = Hunter()
            for username_item in data:
                username_value = username_item.value
                raw_results = await hunter.hunt(username_value)
                for entry in raw_results:
                    if entry.get("exists"):
                        site_name = entry.get("site", "unknown")
                        url = entry.get("url", "")
                        social = SocialAccount(
                            username=Username(
                                value=username_value,
                                platform=site_name,
                            ),
                            profile_url=url,
                            platform=site_name,
                        )
                        results.append(social)
        except Exception as exc:
            Logger.error(
                self.sketch_id,
                {"message": f"Namechk scan failed: {exc}"},
            )
        finally:
            if hunter is not None:
                await hunter.close()

        return results

    def _make_fallback(self, data: List[InputType]) -> List[OutputType]:
        """Return empty results when naminter is unavailable."""
        return [self._empty_result(item) for item in data]

    def _empty_result(self, username_item: InputType) -> OutputType:
        """Build an empty SocialAccount for a username."""
        return SocialAccount(
            username=username_item,
            platform=username_item.platform or "unknown",
        )

    def postprocess(
        self, results: List[OutputType], original_input: List[InputType]
    ) -> List[OutputType]:
        """Create Neo4j relationships for found social accounts."""
        if not self._graph_service:
            return results

        for account in results:
            try:
                self.create_node(account.username)
                self.create_node(account)
                self.create_relationship(
                    account.username, account, "HAS_SOCIAL_ACCOUNT"
                )
                self.log_graph_message(
                    f"{account.username.value} -> account found on "
                    f"{account.platform}"
                )
            except Exception as e:
                Logger.error(
                    self.sketch_id,
                    {
                        "message": (
                            f"Failed to create graph nodes for "
                            f"{account.username.value} on "
                            f"{account.platform}: {e}"
                        )
                    },
                )
                continue

        return results


# Make types available at module level for easy access
InputType = NamechkEnricher.InputType
OutputType = NamechkEnricher.OutputType
