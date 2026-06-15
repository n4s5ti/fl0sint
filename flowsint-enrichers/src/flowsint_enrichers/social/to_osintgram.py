import configparser
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from flowsint_core.core.enricher_base import Enricher
from flowsint_core.core.logger import Logger
from flowsint_enrichers.registry import flowsint_enricher
from flowsint_types import SocialAccount, Username


DEFAULT_OSINTGRAM_ROOT = "/opt/flowsint/Osintgram"
DEFAULT_OSINTGRAM_PYTHON = f"{DEFAULT_OSINTGRAM_ROOT}/.venv/bin/python"
DEFAULT_TIMEOUT_SECONDS = "180"


@flowsint_enricher
class OsintgramEnricher(Enricher):
    """[OSINTGRAM] Enrich Instagram usernames with profile metadata."""

    InputType = Username
    OutputType = SocialAccount

    def __init__(
        self,
        sketch_id: Optional[str] = None,
        scan_id: Optional[str] = None,
        vault=None,
        params: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            sketch_id=sketch_id,
            scan_id=scan_id,
            params_schema=self.get_params_schema(),
            vault=vault,
            params=params,
        )

    @classmethod
    def name(cls) -> str:
        return "username_to_instagram_osintgram"

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
                "name": "HIKERAPI_TOKEN",
                "type": "vaultSecret",
                "description": "Optional HikerAPI token used by Osintgram for Instagram lookups.",
                "required": False,
            },
            {
                "name": "OSINTGRAM_USERNAME",
                "type": "vaultSecret",
                "description": "Optional Instagram username for Osintgram private-api login.",
                "required": False,
            },
            {
                "name": "OSINTGRAM_PASSWORD",
                "type": "vaultSecret",
                "description": "Optional Instagram password for Osintgram private-api login.",
                "required": False,
            },
            {
                "name": "OSINTGRAM_ROOT",
                "type": "string",
                "description": "Path to the cloned Osintgram repository.",
                "required": False,
                "default": DEFAULT_OSINTGRAM_ROOT,
            },
            {
                "name": "OSINTGRAM_PYTHON",
                "type": "string",
                "description": "Python interpreter with Osintgram dependencies installed.",
                "required": False,
                "default": DEFAULT_OSINTGRAM_PYTHON,
            },
            {
                "name": "OSINTGRAM_TIMEOUT_SECONDS",
                "type": "string",
                "description": "Per-username Osintgram command timeout.",
                "required": False,
                "default": DEFAULT_TIMEOUT_SECONDS,
            },
        ]

    def _param(self, name: str, default: Optional[str] = None) -> Optional[str]:
        value = self.get_secret(name, os.getenv(name, default))
        return value if value not in (None, "") else default

    def _timeout_seconds(self) -> int:
        raw = self._param("OSINTGRAM_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
        try:
            timeout = int(raw or DEFAULT_TIMEOUT_SECONDS)
        except ValueError:
            return int(DEFAULT_TIMEOUT_SECONDS)
        return max(1, timeout)

    @staticmethod
    def _repo_has_credentials(root: Path) -> bool:
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(root / "config" / "credentials.ini")
        if not parser.has_section("Credentials"):
            return False
        credentials = parser["Credentials"]
        if credentials.get("hikerapi_token", "").strip():
            return True
        return bool(
            credentials.get("username", "").strip()
            and credentials.get("password", "").strip()
        )

    @staticmethod
    def _redact(value: str, secrets: List[Optional[str]]) -> str:
        redacted = value
        for secret in secrets:
            if secret:
                redacted = redacted.replace(secret, "<redacted>")
        return redacted

    def _workspace(
        self,
        root: Path,
        hiker_token: Optional[str],
        username: Optional[str],
        password: Optional[str],
    ) -> Tuple[tempfile.TemporaryDirectory[str] | None, Path]:
        if not (username and password):
            return None, root

        temp_dir = tempfile.TemporaryDirectory(prefix="flowsint-osintgram-")
        workdir = Path(temp_dir.name)
        (workdir / "config").mkdir(parents=True, exist_ok=True)
        (workdir / "main.py").symlink_to(root / "main.py")
        (workdir / "src").symlink_to(root / "src", target_is_directory=True)
        credentials = workdir / "config" / "credentials.ini"
        credentials.write_text(
            "[Credentials]\n"
            f"username = {username}\n"
            f"password = {password}\n"
            f"hikerapi_token = {hiker_token or ''}\n",
            encoding="utf-8",
        )
        return temp_dir, workdir

    @staticmethod
    def _optional_int(value: Any) -> Optional[int]:
        try:
            return int(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _compact_list(values: List[Optional[str]]) -> Optional[List[str]]:
        result: List[str] = []
        for value in values:
            if value and value not in result:
                result.append(value)
        return result or None

    def _to_social_account(self, username: Username, data: Dict[str, Any]) -> SocialAccount:
        emails = self._compact_list([data.get("email")])
        phones = self._compact_list(
            [data.get("whatsapp_number"), data.get("contact_phone_number")]
        )
        location = ", ".join(
            str(part)
            for part in [data.get("address_street"), data.get("city_name")]
            if part
        ) or None

        return SocialAccount(
            username=username,
            platform="instagram.com",
            profile_url=f"https://www.instagram.com/{username.value}/",
            display_name=data.get("full_name") or None,
            profile_picture_url=data.get("profile_pic_url_hd") or None,
            bio=data.get("biography") or None,
            location=location,
            followers_count=self._optional_int(data.get("edge_followed_by")),
            following_count=self._optional_int(data.get("edge_follow")),
            verified=data.get("is_verified"),
            associated_emails=emails,
            associated_phones=phones,
            instagram_id=str(data["id"]) if data.get("id") is not None else None,
            is_business_account=data.get("is_business_account"),
            connected_fb_page=data.get("connected_fb_page"),
        )

    def _run_osintgram(
        self,
        username: Username,
        root: Path,
        python_bin: Path,
        hiker_token: Optional[str],
        instagram_username: Optional[str],
        instagram_password: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        temp_workspace, workdir = self._workspace(
            root, hiker_token, instagram_username, instagram_password
        )
        try:
            with tempfile.TemporaryDirectory(prefix="flowsint-osintgram-output-") as output_dir:
                env = os.environ.copy()
                env["PYTHONUNBUFFERED"] = "1"
                if hiker_token:
                    env["HIKERAPI_TOKEN"] = hiker_token

                result = subprocess.run(
                    [
                        str(python_bin),
                        "main.py",
                        username.value,
                        "--json",
                        "--command",
                        "info",
                        "--output",
                        output_dir,
                    ],
                    cwd=str(workdir),
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_seconds(),
                    env=env,
                )
                secrets = [hiker_token, instagram_username, instagram_password]
                if result.returncode != 0:
                    Logger.error(
                        self.sketch_id,
                        {
                            "message": (
                                f"Osintgram failed for {username.value}: "
                                f"{self._redact(result.stderr or result.stdout, secrets).strip()}"
                            )
                        },
                    )
                    return None

                info_path = Path(output_dir) / username.value / f"{username.value}_info.json"
                if not info_path.exists():
                    Logger.error(
                        self.sketch_id,
                        {"message": f"Osintgram produced no info JSON for {username.value}."},
                    )
                    return None

                return json.loads(info_path.read_text(encoding="utf-8"))
        except subprocess.TimeoutExpired:
            Logger.error(
                self.sketch_id,
                {"message": f"Osintgram timed out for {username.value}."},
            )
            return None
        except Exception as exc:
            Logger.error(
                self.sketch_id,
                {"message": f"Osintgram errored for {username.value}: {exc}"},
            )
            return None
        finally:
            if temp_workspace is not None:
                temp_workspace.cleanup()

    async def scan(self, data: List[InputType]) -> List[OutputType]:
        results: List[OutputType] = []
        root = Path(self._param("OSINTGRAM_ROOT", DEFAULT_OSINTGRAM_ROOT) or DEFAULT_OSINTGRAM_ROOT)
        python_bin = Path(
            self._param("OSINTGRAM_PYTHON", DEFAULT_OSINTGRAM_PYTHON)
            or DEFAULT_OSINTGRAM_PYTHON
        )
        hiker_token = self._param("HIKERAPI_TOKEN")
        instagram_username = self._param("OSINTGRAM_USERNAME")
        instagram_password = self._param("OSINTGRAM_PASSWORD")

        if not root.exists():
            Logger.error(self.sketch_id, {"message": f"Osintgram root not found: {root}"})
            return results
        if not python_bin.exists():
            Logger.error(
                self.sketch_id,
                {"message": f"Osintgram Python not found: {python_bin}"},
            )
            return results
        if not (
            hiker_token
            or (instagram_username and instagram_password)
            or self._repo_has_credentials(root)
        ):
            Logger.error(
                self.sketch_id,
                {
                    "message": (
                        "Osintgram needs HIKERAPI_TOKEN, OSINTGRAM_USERNAME/OSINTGRAM_PASSWORD, "
                        "or a configured Osintgram config/credentials.ini."
                    )
                },
            )
            return results

        for username in data:
            if not username.value:
                continue
            profile_data = self._run_osintgram(
                username,
                root,
                python_bin,
                hiker_token,
                instagram_username,
                instagram_password,
            )
            if not profile_data:
                continue
            try:
                results.append(self._to_social_account(username, profile_data))
            except Exception as exc:
                Logger.error(
                    self.sketch_id,
                    {"message": f"Failed to normalize Osintgram output for {username.value}: {exc}"},
                )

        return results

    def postprocess(self, results: List[OutputType], original_input: List[InputType]) -> List[OutputType]:
        if not self._graph_service:
            return results

        for profile in results:
            try:
                self.create_node(profile.username)
                self.create_node(profile)
                self.create_relationship(profile.username, profile, "HAS_SOCIAL_ACCOUNT")
                self.log_graph_message(
                    f"{profile.username.value} -> Instagram account found via Osintgram"
                )
            except Exception as exc:
                Logger.error(
                    self.sketch_id,
                    {
                        "message": (
                            "Failed to create graph nodes for "
                            f"{profile.username.value} on Instagram: {exc}"
                        )
                    },
                )
                continue
        return results


InputType = OsintgramEnricher.InputType
OutputType = OsintgramEnricher.OutputType
