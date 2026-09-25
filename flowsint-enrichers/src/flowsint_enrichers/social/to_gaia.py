"""
GHunt-powered Gaia ID Person Lookup enricher.

Uses GHunt to look up Google account information from a Gaia ID
(Google account identifier), returning profile picture, cover photo,
display name, Maps review stats, and activated services.

Input:  Username (value = Gaia ID string, platform = "Google")
Output: SocialAccount (Google account profile)

Requires: pip install ghunt && ghunt login
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from flowsint_core.core.enricher_base import Enricher
from flowsint_core.core.logger import Logger
from flowsint_types.social_account import SocialAccount
from flowsint_types.username import Username

from flowsint_enrichers.registry import flowsint_enricher

# ---------------------------------------------------------------------------
# Optional: GHunt
# ---------------------------------------------------------------------------
try:
    from ghunt import globals as gb
    from ghunt.apis.peoplepa import PeoplePaHttp
    from ghunt.helpers import gmaps, playgames
    from ghunt.helpers.auth import load_and_auth
    from ghunt.objects.base import GHuntCreds
    from ghunt.errors import GHuntInvalidSession

    HAS_GHUNT = True
except ImportError:
    HAS_GHUNT = False

log = logging.getLogger(__name__)


@flowsint_enricher
class GaiaToPersonEnricher(Enricher):
    """Looks up Google account information from a Gaia ID.

    Uses GHunt to query Google's People API for a Gaia ID, returning
    profile photo, cover photo, display name, Google Maps statistics,
    and Play Games data.

    Requires ``ghunt`` to be installed and authenticated (``ghunt login``).
    """

    InputType = Username
    OutputType = SocialAccount

    @classmethod
    def name(cls) -> str:
        return "gaia_id_to_person"

    @classmethod
    def category(cls) -> str:
        return "social"

    @classmethod
    def key(cls) -> str:
        return "username"

    @classmethod
    def icon(cls) -> Optional[str]:
        return "🔍"

    @classmethod
    def documentation(cls) -> str:
        return (
            "## GHunt Gaia ID Person Lookup\n\n"
            "Looks up a Google account by its Gaia ID (unique Google account "
            "identifier). Returns profile photo, display name, cover photo, "
            "Google Maps review statistics, and Play Games data.\n\n"
            "### Input\n"
            "A Gaia ID — a numeric Google account identifier (e.g., "
            "``101299910324306641283``).\n\n"
            "### Authentication\n"
            "Requires ``ghunt`` and a one-time login:\n"
            "1. ``pip install ghunt``\n"
            "2. ``ghunt login`` (browser extension)\n\n"
            "### Output\n"
            "Returns a SocialAccount entity with:\n"
            "- `profile_url` – Google Maps contributor page\n"
            "- `profile_picture_url` – account avatar\n"
            "- `display_name` – Google profile name\n"
            "- `platform` – Google\n"
            "- `bio` – Maps review/photo stats\n"
        )

    @classmethod
    def get_params_schema(cls) -> List[Dict[str, Any]]:
        return [
            {
                "name": "ghunt_creds_path",
                "type": "string",
                "default": str(Path.home() / ".ghunt" / "creds.json"),
                "label": "GHunt credentials path",
                "description": "Path to GHunt credentials JSON file",
            },
        ]

    # ------------------------------------------------------------------
    # scan — GHunt Gaia ID lookup
    # ------------------------------------------------------------------
    async def scan(self, data: List[InputType]) -> List[OutputType]:
        """Look up person info for each Gaia ID."""
        if not HAS_GHUNT:
            Logger.error(
                self.sketch_id,
                {
                    "message": (
                        "GHunt is not installed. "
                        "Run: pip install ghunt && ghunt login"
                    ),
                },
            )
            return self._make_fallback(data)

        creds_path = Path(
            self.params.get(
                "ghunt_creds_path",
                str(Path.home() / ".ghunt" / "creds.json"),
            )
        )

        if not creds_path.exists():
            Logger.error(
                self.sketch_id,
                {
                    "message": (
                        f"GHunt credentials not found at {creds_path}. "
                        "Run: ghunt login"
                    ),
                },
            )
            return self._make_fallback(data)

        results: List[OutputType] = []
        for username_item in data:
            gaia_id = username_item.value
            result = await self._lookup_one(gaia_id, creds_path)
            results.append(
                result
                if result
                else self._empty_result(gaia_id)
            )

        return results

    async def _lookup_one(
        self, gaia_id: str, creds_path: Path
    ) -> Optional[OutputType]:
        """Run GHunt Gaia lookup for a single ID."""
        try:
            async with httpx.AsyncClient() as client:
                try:
                    ghunt_creds = GHuntCreds()
                    ghunt_creds.load_creds(str(creds_path))
                except GHuntInvalidSession as e:
                    Logger.error(
                        self.sketch_id,
                        {
                            "message": (
                                f"GHunt session invalid: {e}. "
                                "Re-run: ghunt login"
                            ),
                        },
                    )
                    return None

                people_pa = PeoplePaHttp(ghunt_creds)
                found, target = await people_pa.people(
                    client, gaia_id, params_template="max_details"
                )
                if not found or "PROFILE" not in target.sourceIds:
                    return None

                container = "PROFILE"

                profile_pic = None
                cover_photo = None
                display_name = None

                if container in target.profilePhotos:
                    if not target.profilePhotos[container].isDefault:
                        profile_pic = target.profilePhotos[container].url

                if container in target.coverPhotos:
                    if not target.coverPhotos[container].isDefault:
                        cover_photo = target.coverPhotos[container].url

                if container in target.names:
                    display_name = target.names[container].fullname

                # Maps data
                _, maps_stats = await gmaps.get_reviews(client, target.personId)

                # Create Username from Gaia ID
                username = Username(value=gaia_id, platform="Google")

                social_account = SocialAccount(
                    username=username,
                    display_name=display_name,
                    profile_url=(
                        f"https://www.google.com/maps/contrib/{gaia_id}"
                    ),
                    profile_picture_url=profile_pic,
                    platform="Google",
                    bio=(
                        f"Google Account. "
                        f"Cover: {cover_photo or 'default'}. "
                        f"Maps reviews: "
                        f"{maps_stats.reviews if maps_stats else 'N/A'}, "
                        f"photos: "
                        f"{maps_stats.photos if maps_stats else 'N/A'}."
                    ),
                )

                Logger.info(
                    self.sketch_id,
                    {
                        "message": (
                            f"GHunt found person for Gaia ID {gaia_id}: "
                            f"{display_name or 'unnamed'}"
                        )
                    },
                )

                return social_account

        except Exception as exc:
            Logger.error(
                self.sketch_id,
                {
                    "message": (
                        f"GHunt Gaia lookup failed for {gaia_id}: {exc}"
                    )
                },
            )
            return None

    def _make_fallback(self, data: List[InputType]) -> List[OutputType]:
        """Return empty SocialAccount results for each Gaia ID."""
        return [self._empty_result(item.value) for item in data]

    def _empty_result(self, gaia_id: str) -> OutputType:
        """Build an empty SocialAccount for a Gaia ID."""
        username = Username(value=gaia_id, platform="Google")
        return SocialAccount(username=username, platform="Google")

    # ------------------------------------------------------------------
    # postprocess — write to graph
    # ------------------------------------------------------------------
    def postprocess(
        self, results: List[OutputType], original_input: List[InputType]
    ) -> List[OutputType]:
        for account in results:
            if not self._graph_service:
                continue
            self.create_node(account)
            has_pic = bool(account.profile_picture_url)
            self.log_graph_message(
                f"GHunt Gaia: person {account.username.value} "
                f"(pic={'yes' if has_pic else 'no'})"
            )
        return results


InputType = GaiaToPersonEnricher.InputType
OutputType = GaiaToPersonEnricher.OutputType
