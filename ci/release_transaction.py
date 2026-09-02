from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass


HEX_OBJECT_ID = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
SAFE_ASSET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+@-]*$")


class ReleaseCommandError(RuntimeError):
    pass


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class LocalAsset:
    path: pathlib.Path
    digest: str
    size: int


def local_assets(paths: list[str]) -> dict[str, LocalAsset]:
    assets: dict[str, LocalAsset] = {}
    for value in paths:
        path = pathlib.Path(value)
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"{path}: regular release asset required")
        resolved = path.resolve(strict=True)
        name = resolved.name
        if not SAFE_ASSET_NAME.fullmatch(name):
            raise ValueError(f"{name}: GitHub-safe release asset name required")
        if name in assets:
            raise ValueError(f"duplicate local release asset: {name}")
        assets[name] = LocalAsset(
            path=resolved,
            digest=sha256(resolved),
            size=resolved.stat().st_size,
        )
    if not assets:
        raise ValueError("at least one release asset is required")
    return assets


class GhReleaseClient:
    def __init__(self, repository: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("repository must be OWNER/REPO")
        self.repository = repository

    def _run(
        self,
        arguments: list[str],
        *,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            arguments,
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise ReleaseCommandError(
                f"{' '.join(arguments[:3])} failed: {detail}"
            )
        return completed

    def _api_json(
        self,
        endpoint: str,
        *,
        method: str = "GET",
        payload: dict[str, object] | None = None,
        paginate: bool = False,
    ) -> object:
        command = [
            "gh",
            "api",
            "--method",
            method,
            "-H",
            "Accept: application/vnd.github+json",
            endpoint,
        ]
        input_text = None
        if payload is not None:
            command.extend(["--input", "-"])
            input_text = json.dumps(payload, separators=(",", ":"))
        if paginate:
            command.extend(["--paginate", "--slurp"])
        output = self._run(command, input_text=input_text).stdout
        try:
            return json.loads(output)
        except json.JSONDecodeError as error:
            raise ReleaseCommandError(
                f"GitHub API returned invalid JSON for {endpoint}: {error}"
            ) from error

    def _paged_list(self, endpoint: str) -> list[object]:
        pages = self._api_json(endpoint, paginate=True)
        if not isinstance(pages, list):
            raise ReleaseCommandError("GitHub API pagination returned non-list")
        flattened: list[object] = []
        for page in pages:
            if not isinstance(page, list):
                raise ReleaseCommandError(
                    "GitHub API pagination returned a non-list page"
                )
            flattened.extend(page)
        return flattened

    def get_release(self, tag: str) -> dict[str, object] | None:
        releases = self._paged_list(
            f"repos/{self.repository}/releases?per_page=100"
        )
        matches = [
            release
            for release in releases
            if isinstance(release, dict) and release.get("tag_name") == tag
        ]
        if len(matches) > 1:
            raise ReleaseCommandError(f"multiple releases found for tag {tag}")
        if not matches:
            return None
        release_id = matches[0].get("id")
        if not isinstance(release_id, int):
            raise ReleaseCommandError("GitHub release has invalid id")
        release = self._api_json(
            f"repos/{self.repository}/releases/{release_id}"
        )
        if not isinstance(release, dict):
            raise ReleaseCommandError("GitHub release response is not an object")
        release["assets"] = self._paged_list(
            f"repos/{self.repository}/releases/{release_id}/assets?per_page=100"
        )
        return release

    def get_tag_target(self, tag: str) -> str:
        encoded = urllib.parse.quote(tag, safe="")
        value = self._api_json(
            f"repos/{self.repository}/git/ref/tags/{encoded}"
        )
        if not isinstance(value, dict) or not isinstance(value.get("object"), dict):
            raise ReleaseCommandError("GitHub tag reference response is invalid")
        target = value["object"]
        seen: set[str] = set()
        for _ in range(8):
            object_type = target.get("type")
            object_id = target.get("sha")
            if (
                object_type not in {"commit", "tag"}
                or not isinstance(object_id, str)
                or not HEX_OBJECT_ID.fullmatch(object_id.lower())
            ):
                raise ReleaseCommandError("GitHub tag target is invalid")
            object_id = object_id.lower()
            if object_type == "commit":
                return object_id
            if object_id in seen:
                raise ReleaseCommandError("GitHub tag target cycle detected")
            seen.add(object_id)
            annotated = self._api_json(
                f"repos/{self.repository}/git/tags/{object_id}"
            )
            if (
                not isinstance(annotated, dict)
                or not isinstance(annotated.get("object"), dict)
            ):
                raise ReleaseCommandError(
                    "GitHub annotated tag response is invalid"
                )
            target = annotated["object"]
        raise ReleaseCommandError("GitHub tag target nesting is too deep")

    def create_draft(self, tag: str, target: str) -> dict[str, object]:
        created = self._api_json(
            f"repos/{self.repository}/releases",
            method="POST",
            payload={
                "tag_name": tag,
                "target_commitish": target,
                "name": tag,
                "body": "",
                "draft": True,
                "prerelease": False,
                "generate_release_notes": False,
            },
        )
        if not isinstance(created, dict):
            raise ReleaseCommandError("created GitHub release is invalid")
        release = self.get_release(tag)
        if release is None:
            raise ReleaseCommandError("created GitHub release cannot be found")
        return release

    def delete_asset(self, asset_id: int) -> None:
        self._run(
            [
                "gh",
                "api",
                "--method",
                "DELETE",
                f"repos/{self.repository}/releases/assets/{asset_id}",
                "--silent",
            ]
        )

    def upload_asset(self, tag: str, path: pathlib.Path) -> None:
        self._run(
            [
                "gh",
                "release",
                "upload",
                tag,
                str(path),
                "--repo",
                self.repository,
            ]
        )

    def publish(self, release_id: int, tag: str) -> None:
        value = self._api_json(
            f"repos/{self.repository}/releases/{release_id}",
            method="PATCH",
            payload={
                "tag_name": tag,
                "name": tag,
                "draft": False,
                "prerelease": False,
                "make_latest": "true",
            },
        )
        if not isinstance(value, dict):
            raise ReleaseCommandError("published GitHub release is invalid")


def release_id(release: dict[str, object]) -> int:
    value = release.get("id")
    if not isinstance(value, int):
        raise ValueError("GitHub release has invalid id")
    return value


def verify_release_identity(
    release: dict[str, object],
    tag: str,
) -> None:
    if release.get("tag_name") != tag:
        raise ValueError("GitHub release tag identity mismatch")
    if not isinstance(release.get("draft"), bool):
        raise ValueError("GitHub release has invalid draft state")
    if release.get("prerelease") is not False:
        raise ValueError("GitHub release must not be a prerelease")
    release_id(release)


def asset_map(release: dict[str, object]) -> dict[str, dict[str, object]]:
    values = release.get("assets")
    if not isinstance(values, list):
        raise ValueError("GitHub release has invalid assets")
    assets: dict[str, dict[str, object]] = {}
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("GitHub release has invalid asset")
        name = value.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("GitHub release asset has invalid name")
        if name in assets:
            raise ValueError(f"duplicate GitHub release asset: {name}")
        assets[name] = value
    return assets


def asset_matches(asset: dict[str, object], local: LocalAsset) -> bool:
    return (
        asset.get("state") == "uploaded"
        and asset.get("size") == local.size
        and asset.get("digest") == f"sha256:{local.digest}"
    )


def verify_exact_assets(
    release: dict[str, object],
    expected: dict[str, LocalAsset],
) -> None:
    actual = asset_map(release)
    if actual.keys() != expected.keys():
        missing = sorted(expected.keys() - actual.keys())
        extra = sorted(actual.keys() - expected.keys())
        raise ValueError(
            "GitHub release asset set mismatch "
            f"(missing={missing}, extra={extra})"
        )
    mismatched = sorted(
        name
        for name, local in expected.items()
        if not asset_matches(actual[name], local)
    )
    if mismatched:
        raise ValueError(
            f"GitHub release asset digest/state mismatch: {mismatched}"
        )


def run_transaction(
    client: GhReleaseClient,
    tag: str,
    target: str,
    paths: list[str],
) -> str:
    target = target.lower()
    if not HEX_OBJECT_ID.fullmatch(target):
        raise ValueError("target must be a full Git object id")
    if client.get_tag_target(tag) != target:
        raise ValueError("GitHub tag does not resolve to the expected target")
    expected = local_assets(paths)

    release = client.get_release(tag)
    if release is None:
        try:
            release = client.create_draft(tag, target)
        except ReleaseCommandError:
            release = client.get_release(tag)
            if release is None:
                raise
    verify_release_identity(release, tag)

    if release["draft"] is False:
        verify_exact_assets(release, expected)
        return "already-published"

    existing = asset_map(release)
    extras = sorted(existing.keys() - expected.keys())
    if extras:
        raise ValueError(f"unexpected GitHub release assets: {extras}")

    for name, local in expected.items():
        current = existing.get(name)
        if current is not None and asset_matches(current, local):
            continue
        if current is not None:
            asset_id = current.get("id")
            if not isinstance(asset_id, int):
                raise ValueError(f"{name}: GitHub release asset has invalid id")
            client.delete_asset(asset_id)
        if sha256(local.path) != local.digest:
            raise ValueError(f"{name}: local asset changed during transaction")
        client.upload_asset(tag, local.path)
        release = client.get_release(tag)
        if release is None:
            raise ValueError("GitHub release disappeared during asset upload")
        verify_release_identity(release, tag)
        if release["draft"] is False:
            verify_exact_assets(release, expected)
            return "already-published"
        existing = asset_map(release)
        uploaded = existing.get(name)
        if uploaded is None or not asset_matches(uploaded, local):
            raise ValueError(f"{name}: uploaded GitHub release asset not verified")

    release = client.get_release(tag)
    if release is None:
        raise ValueError("GitHub release disappeared before publication")
    verify_release_identity(release, tag)
    verify_exact_assets(release, expected)
    if release["draft"] is False:
        return "already-published"

    client.publish(release_id(release), tag)
    completed = client.get_release(tag)
    if completed is None:
        raise ValueError("GitHub release disappeared after publication")
    verify_release_identity(completed, tag)
    if completed["draft"] is not False:
        raise ValueError("GitHub release remained a draft after publication")
    verify_exact_assets(completed, expected)
    return "published"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--asset", nargs="+", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_transaction(
        GhReleaseClient(args.repository),
        args.tag,
        args.target,
        args.asset,
    )
    print(f"release-transaction: {result} {args.tag}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, ReleaseCommandError) as error:
        print(f"release-transaction: {error}", file=sys.stderr)
        raise SystemExit(1)
