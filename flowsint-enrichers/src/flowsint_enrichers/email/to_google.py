"""
GHunt-powered Google Account enricher.

Uses GHunt (https://github.com/mxrch/GHunt) to look up Google account
information from an email address:
- Gaia ID (Google account identifier)
- Profile picture URL (custom / default)
- Cover photo URL
- Display name
- Google Maps reviews & photo statistics
- Play Games profile (if available)
- Activated Google services

Input:  Email (email_address)
Output: SocialAccount (Google account profile)

Requires: pip install ghunt && ghunt login
"""

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from flowsint_core.core.enricher_base import Enricher
from flowsint_core.core.logger import Logger
from flowsint_types.email import Email
from flowsint_types.individual import Individual
from flowsint_types.social_account import SocialAccount
from flowsint_types.username import Username

from flowsint_enrichers.registry import flowsint_enricher

# ---------------------------------------------------------------------------
# Optional: GHunt
# ---------------------------------------------------------------------------
try:
    import httpx

    from ghunt import globals as gb
    from ghunt.modules.email import hunt as ghunt_email_hunt
    from ghunt.helpers.auth import load_and_auth
    from ghunt.helpers.utils import get_httpx_client
    from ghunt.objects.base import GHuntCreds
    from ghunt.errors import GHuntInvalidSession

    HAS_GHUNT = True
except ImportError:
    HAS_GHUNT = False

log = logging.getLogger(__name__)


@flowsint_enricher
class EmailToGoogleEnricher(Enricher):
    """Looks up Google account information from an email address.

    Uses GHunt to search Google's People API for a Google account associated
    with the given email address. Returns profile picture, Gaia ID, display
    name, cover photo, Google Maps review statistics, and Play Games data.

    Requires ``ghunt`` to be installed and authenticated:
        pip install ghunt
        ghunt login

    Authentication is done once via the GHunt Companion browser extension
    and the session is stored locally.
    """

    InputType = Email
    OutputType = SocialAccount

    @classmethod
    def name(cls) -> str:
        return "email_to_google_account"

    @classmethod
    def category(cls) -> str:
        return "Email"

    @classmethod
    def key(cls) -> str:
        return "email"

    @classmethod
    def icon(cls) -> Optional[str]:
        return "🔍"

    @classmethod
    def documentation(cls) -> str:
        return (
            "## GHunt Google Account Enricher\n\n"
            "Uses [GHunt](https://github.com/mxrch/GHunt) to look up Google "
            "account information from an email address:\n\n"
            "- **Gaia ID** – unique Google account identifier\n"
            "- **Profile picture** – URL to the custom or default avatar\n"
            "- **Cover photo** – URL to the cover photo\n"
            "- **Display name** – the account's public display name\n"
            "- **Google Maps stats** – number of reviews and photos\n"
            "- **Play Games** – gaming profile if available\n"
            "- **Activated services** – list of Google services the account uses\n\n"
            "### Authentication\n"
            "GHunt requires a one-time login via browser extension:\n"
            "1. ``pip install ghunt``\n"
            "2. ``ghunt login`` (follow the prompts)\n\n"
            "### Output\n"
            "Returns a SocialAccount entity with:\n"
            "- `profile_url` – set to the Gaia profile URL\n"
            "- `profile_picture_url` – the account's avatar\n"
            "- `display_name` – the Google account display name\n"
            "- `platform` – set to \"Google\"\n"
            "- `associated_emails` – the looked-up email\n"
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
    # scan — GHunt email lookup
    # ------------------------------------------------------------------
    async def scan(self, data: List[InputType]) -> List[OutputType]:
        """Look up Google account info for each email address."""
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
            return self._make_fallback(data, "GHunt not installed")

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
            return self._make_fallback(data, "GHunt not authenticated")

        results: List[OutputType] = []
        for email_item in data:
            email_addr = str(email_item.email)
            result = await self._lookup_one(email_addr, creds_path)
            results.append(result if result else self._empty_result(email_addr))

        return results

    async def _lookup_one(
        self, email_addr: str, creds_path: Path
    ) -> Optional[OutputType]:
        """Run GHunt email lookup for a single address."""
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
                                f"GHunt session invalid for {email_addr}: {e}. "
                                "Re-run: ghunt login"
                            ),
                        },
                    )
                    return None

                # Generate temporary JSON output path
                tmp = tempfile.NamedTemporaryFile(
                    suffix=".json", delete=False, mode="w"
                )
                tmp_path = tmp.name
                tmp.close()

                try:
                    # We can't easily call ghunt.modules.email.hunt() directly
                    # because it handles its own client and exits. Instead,
                    # use the internal components.
                    from ghunt.apis.peoplepa import PeoplePaHttp
                    from ghunt.helpers import gmaps, playgames

                    # Use Gaia module to check registration
                    from ghunt.helpers.gmail import is_email_registered

                    is_reg = await is_email_registered(client, email_addr)
                    if not is_reg:
                        Logger.info(
                            self.sketch_id,
                            {
                                "message": (
                                    f"No Google account found for {email_addr}"
                                )
                            },
                        )
                        return None

                    # Look up People API
                    people_pa = PeoplePaHttp(ghunt_creds)
                    found, target = await people_pa.people_lookup(
                        client, email_addr, params_template="max_details"
                    )
                    if not found or "PROFILE" not in target.sourceIds:
                        return None

                    container = "PROFILE"

                    # Build SocialAccount
                    gaia_id = target.personId
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
                    _, maps_stats = await gmaps.get_reviews(
                        client, target.personId
                    )

                    # Play Games
                    player_results = await playgames.search_player(
                        ghunt_creds, client, email_addr
                    )

                    # Create Username from Gaia ID
                    username = Username(
                        value=gaia_id,
                        platform="Google",
                    )

                    social_account = SocialAccount(
                        username=username,
                        display_name=display_name,
                        profile_url=f"https://www.google.com/maps/contrib/{gaia_id}",
                        profile_picture_url=profile_pic,
                        platform="Google",
                        bio=(
                            f"Google Account. "
                            f"Cover: {cover_photo or 'default'}. "
                            f"Maps reviews: {maps_stats.reviews if maps_stats else 'N/A'}, "
                            f"photos: {maps_stats.photos if maps_stats else 'N/A'}. "
                            f"Play Games: {'Yes' if player_results else 'No'}"
                        ),
                        associated_emails=[email_addr],
                    )

                    Logger.info(
                        self.sketch_id,
                        {
                            "message": (
                                f"GHunt found Google account for {email_addr}: "
                                f"Gaia ID {gaia_id}"
                            )
                        },
                    )

                    return social_account

                finally:
                    # Cleanup temp file
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)

        except Exception as exc:
            Logger.error(
                self.sketch_id,
                {
                    "message": (
                        f"GHunt lookup failed for {email_addr}: {exc}"
                    )
                },
            )
            return None

    def _make_fallback(
        self, data: List[InputType], reason: str
    ) -> List[OutputType]:
        """Return empty SocialAccount results for each email."""
        results = []
        for item in data:
            result = self._empty_result(str(item.email))
            results.append(result)
        return results

    def _empty_result(self, email_addr: str) -> OutputType:
        """Build an empty SocialAccount for an email."""
        username = Username(value=email_addr, platform="Google")
        return SocialAccount(
            username=username,
            platform="Google",
            associated_emails=[email_addr],
        )

    # ------------------------------------------------------------------
    # postprocess — write to graph
    # ------------------------------------------------------------------
    def postprocess(
        self, results: List[OutputType], original_input: List[InputType]
    ) -> List[OutputType]:
        """Write Google account nodes and create relationships to emails."""
        for idx, account in enumerate(results):
            if not self._graph_service:
                continue

            self.create_node(account)

            # Link the original email to this Google account
            if idx < len(original_input):
                email_item = original_input[idx]
                self.create_relationship(
                    email_item, account, "HAS_GOOGLE_ACCOUNT"
            )

            has_pic = bool(account.profile_picture_url)
            self.log_graph_message(
            f"GHunt: Google account {account.username.value} "
                f"(pic={'yes' if has_pic else 'no'})"
                )

        return results


# Make types available at module level
InputType = EmailToGoogleEnricher.InputType
OutputType = EmailToGoogleEnricher.OutputType
