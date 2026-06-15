import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from flowsint_core.core.enricher_base import Enricher
from flowsint_core.core.logger import Logger
from flowsint_enrichers.registry import flowsint_enricher
from flowsint_types import SocialAccount, Username


BLACKBIRD_ROOT = Path("/opt/flowsint/blackbird")
BLACKBIRD_SCRIPT = BLACKBIRD_ROOT / "blackbird.py"
RESULTS_ROOT = BLACKBIRD_ROOT / "results"


@flowsint_enricher
class BlackbirdEnricher(Enricher):
    """[BLACKBIRD] Scans usernames for associated social accounts using Blackbird."""

    InputType = Username
    OutputType = SocialAccount

    @classmethod
    def name(cls) -> str:
        return "username_to_socials_blackbird"

    @classmethod
    def category(cls) -> str:
        return "social"

    @classmethod
    def key(cls) -> str:
        return "username"

    @classmethod
    def get_params_schema(cls) -> List[Dict[str, Any]]:
        return [
            {
                "name": "timeout",
                "type": "number",
                "description": "Total Blackbird process timeout in seconds.",
                "required": False,
                "default": 300,
            },
            {
                "name": "request_timeout",
                "type": "number",
                "description": "Per-site HTTP timeout passed to Blackbird.",
                "required": False,
                "default": 20,
            },
            {
                "name": "max_concurrent_requests",
                "type": "number",
                "description": "Maximum concurrent requests passed to Blackbird.",
                "required": False,
                "default": 20,
            },
            {
                "name": "filter",
                "type": "string",
                "description": "Optional Blackbird site-list filter, for example cat=social.",
                "required": False,
                "default": "cat=social",
            },
        ]

    def _param_int(self, name: str, default: int, minimum: int, maximum: int) -> int:
        raw = self.params.get(name, default) if self.params else default
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, value))

    def _param_str(self, name: str, default: str) -> str:
        raw = self.params.get(name, default) if self.params else default
        return str(raw).strip() if raw is not None else default

    def _latest_json_for(self, username: str) -> Optional[Path]:
        if not RESULTS_ROOT.exists():
            return None
        candidates = [
            path
            for path in RESULTS_ROOT.glob(f"{username}_*_blackbird/{username}_*_blackbird.json")
            if path.is_file()
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime)

    def _run_blackbird(self, username: str) -> Optional[Path]:
        if not BLACKBIRD_SCRIPT.exists():
            Logger.error(
                self.sketch_id,
                {"message": f"Blackbird script not found at {BLACKBIRD_SCRIPT}"},
            )
            return None

        before = self._latest_json_for(username)
        process_timeout = self._param_int("timeout", 300, 30, 900)
        request_timeout = self._param_int("request_timeout", 20, 5, 120)
        concurrency = self._param_int("max_concurrent_requests", 20, 1, 60)
        site_filter = self._param_str("filter", "cat=social")

        command = [
            sys.executable,
            str(BLACKBIRD_SCRIPT),
            "--username",
            username,
            "--json",
            "--no-update",
            "--timeout",
            str(request_timeout),
            "--max-concurrent-requests",
            str(concurrency),
        ]
        if site_filter:
            command.extend(["--filter", site_filter])

        try:
            result = subprocess.run(
                command,
                cwd=str(BLACKBIRD_ROOT),
                capture_output=True,
                text=True,
                timeout=process_timeout,
            )
        except subprocess.TimeoutExpired:
            Logger.error(
                self.sketch_id,
                {"message": f"Blackbird scan for {username} timed out."},
            )
            return None

        if result.returncode != 0:
            Logger.error(
                self.sketch_id,
                {
                    "message": (
                        f"Blackbird failed for {username}: "
                        f"{(result.stderr or result.stdout).strip()}"
                    )
                },
            )
            return None

        after = self._latest_json_for(username)
        if after is None or (before is not None and after == before):
            return None
        return after

    @staticmethod
    def _platform_from(hit: Dict[str, Any]) -> Optional[str]:
        name = hit.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        category = hit.get("category")
        if isinstance(category, str) and category.strip():
            return category.strip()
        url = hit.get("url")
        if isinstance(url, str):
            host = urlparse(url).netloc
            if host:
                return host
        return None

    @staticmethod
    def _metadata_value(metadata: Any, *names: str) -> Optional[str]:
        if not isinstance(metadata, list):
            return None
        wanted = {name.lower() for name in names}
        for item in metadata:
            if not isinstance(item, dict):
                continue
            item_name = str(item.get("name", "")).lower()
            if item_name not in wanted:
                continue
            for key in ("value", "content", "text"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    def _parse_blackbird_output(
        self, username: Username, output_file: Path
    ) -> List[SocialAccount]:
        try:
            raw_data = json.loads(output_file.read_text(encoding="utf-8"))
        except Exception as exc:
            Logger.error(
                self.sketch_id,
                {"message": f"Failed to parse Blackbird output for {username.value}: {exc}"},
            )
            return []

        if not isinstance(raw_data, list):
            return []

        results: List[SocialAccount] = []
        seen: set[tuple[str, str]] = set()
        for hit in raw_data:
            if not isinstance(hit, dict) or hit.get("status") != "FOUND":
                continue
            profile_url = hit.get("url")
            platform = self._platform_from(hit)
            if not isinstance(profile_url, str) or not profile_url.strip() or not platform:
                continue

            key = (platform.lower(), profile_url.strip())
            if key in seen:
                continue
            seen.add(key)

            metadata = hit.get("metadata")
            display_name = self._metadata_value(
                metadata, "name", "fullname", "full_name", "display_name", "title"
            )
            avatar = self._metadata_value(
                metadata, "image", "avatar", "profile_picture", "profile_picture_url"
            )
            bio = self._metadata_value(metadata, "bio", "description", "about")
            location = self._metadata_value(metadata, "location", "address")

            try:
                results.append(
                    SocialAccount(
                        username=username,
                        platform=platform,
                        profile_url=profile_url.strip(),
                        display_name=display_name,
                        profile_picture_url=avatar,
                        bio=bio,
                        location=location,
                    )
                )
            except Exception as exc:
                Logger.error(
                    self.sketch_id,
                    {
                        "message": (
                            f"Failed to create SocialAccount for {username.value} "
                            f"on {platform}: {exc}"
                        )
                    },
                )
        return results

    async def scan(self, data: List[InputType]) -> List[OutputType]:
        results: List[OutputType] = []
        for username in data:
            if not username.value:
                continue
            output_file = self._run_blackbird(username.value)
            if output_file is None:
                continue
            results.extend(self._parse_blackbird_output(username, output_file))
        return results

    def postprocess(
        self, results: List[OutputType], original_input: List[InputType]
    ) -> List[OutputType]:
        if not self._graph_service:
            return results

        for social_account in results:
            try:
                self.create_node(social_account.username)
                self.create_node(social_account)
                self.create_relationship(
                    social_account.username, social_account, "HAS_SOCIAL_ACCOUNT"
                )
                self.log_graph_message(
                    f"{social_account.username.value} -> account found on {social_account.platform}"
                )
            except Exception as exc:
                Logger.error(
                    self.sketch_id,
                    {
                        "message": (
                            "Failed to create graph nodes for "
                            f"{social_account.username.value} on "
                            f"{social_account.platform}: {exc}"
                        )
                    },
                )
        return results


InputType = BlackbirdEnricher.InputType
OutputType = BlackbirdEnricher.OutputType
