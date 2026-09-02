from __future__ import annotations

import copy
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ci"))

import release_transaction as rt


TARGET = "a" * 40
TAG = "2.1.2"


class FakeClient:
    def __init__(
        self,
        release: dict[str, object] | None = None,
        *,
        target: str = TARGET,
    ) -> None:
        self.release = copy.deepcopy(release)
        self.target = target
        self.calls: list[tuple[object, ...]] = []
        self.next_asset_id = 10
        self.fail_upload = False
        self.fail_publish = False

    def get_tag_target(self, tag: str) -> str:
        self.calls.append(("get_tag_target", tag))
        return self.target

    def get_release(self, tag: str) -> dict[str, object] | None:
        self.calls.append(("get_release", tag))
        return copy.deepcopy(self.release)

    def create_draft(self, tag: str, target: str) -> dict[str, object]:
        self.calls.append(("create_draft", tag, target))
        self.release = make_release(draft=True)
        return copy.deepcopy(self.release)

    def delete_asset(self, asset_id: int) -> None:
        self.calls.append(("delete_asset", asset_id))
        self.release["assets"] = [
            asset
            for asset in self.release["assets"]
            if asset["id"] != asset_id
        ]

    def upload_asset(self, tag: str, path: pathlib.Path) -> None:
        self.calls.append(("upload_asset", tag, path.name))
        if self.fail_upload:
            raise rt.ReleaseCommandError("upload failed")
        self.release["assets"].append(
            make_asset(path, asset_id=self.next_asset_id)
        )
        self.next_asset_id += 1

    def publish(self, release_id: int, tag: str) -> None:
        self.calls.append(("publish", release_id, tag))
        if self.fail_publish:
            raise rt.ReleaseCommandError("publish failed")
        self.release["draft"] = False


def make_release(
    *,
    draft: bool,
    assets: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "id": 1,
        "tag_name": TAG,
        "draft": draft,
        "prerelease": False,
        "assets": [] if assets is None else assets,
    }


def make_asset(
    path: pathlib.Path,
    *,
    asset_id: int = 1,
    digest: str | None = None,
) -> dict[str, object]:
    return {
        "id": asset_id,
        "name": path.name,
        "state": "uploaded",
        "size": path.stat().st_size,
        "digest": digest or f"sha256:{rt.sha256(path)}",
    }


def create_asset(tmp_path: pathlib.Path, name: str, contents: bytes) -> pathlib.Path:
    path = tmp_path / name
    path.write_bytes(contents)
    return path


def mutation_calls(client: FakeClient) -> list[str]:
    return [
        call[0]
        for call in client.calls
        if call[0] in {"create_draft", "delete_asset", "upload_asset", "publish"}
    ]


def test_absent_release_is_created_as_draft_uploaded_verified_and_published(
    tmp_path,
):
    asset = create_asset(tmp_path, "shapely-2.1.2.tar.gz", b"sdist")
    client = FakeClient()

    assert rt.run_transaction(client, TAG, TARGET, [str(asset)]) == "published"

    assert client.release["draft"] is False
    assert mutation_calls(client) == ["create_draft", "upload_asset", "publish"]


def test_partial_draft_preserves_identical_asset_and_uploads_only_missing(
    tmp_path,
):
    first = create_asset(tmp_path, "shapely-2.1.2.tar.gz", b"sdist")
    second = create_asset(tmp_path, "checksums.txt", b"checksums")
    client = FakeClient(
        make_release(draft=True, assets=[make_asset(first)])
    )

    assert (
        rt.run_transaction(client, TAG, TARGET, [str(first), str(second)])
        == "published"
    )

    assert mutation_calls(client) == ["upload_asset", "publish"]
    assert client.calls.count(("upload_asset", TAG, first.name)) == 0


def test_identical_draft_asset_is_not_reuploaded(tmp_path):
    asset = create_asset(tmp_path, "shapely-2.1.2.tar.gz", b"sdist")
    client = FakeClient(
        make_release(draft=True, assets=[make_asset(asset)])
    )

    assert rt.run_transaction(client, TAG, TARGET, [str(asset)]) == "published"

    assert mutation_calls(client) == ["publish"]


def test_conflicting_draft_asset_is_replaced_and_digest_verified(tmp_path):
    asset = create_asset(tmp_path, "shapely-2.1.2.tar.gz", b"new-sdist")
    conflicting = make_asset(asset, digest=f"sha256:{'0' * 64}")
    client = FakeClient(make_release(draft=True, assets=[conflicting]))

    assert rt.run_transaction(client, TAG, TARGET, [str(asset)]) == "published"

    assert mutation_calls(client) == ["delete_asset", "upload_asset", "publish"]
    assert client.release["assets"][0]["digest"] == f"sha256:{rt.sha256(asset)}"


def test_conflicting_published_asset_fails_without_replacement(tmp_path):
    asset = create_asset(tmp_path, "shapely-2.1.2.tar.gz", b"new-sdist")
    conflicting = make_asset(asset, digest=f"sha256:{'0' * 64}")
    client = FakeClient(make_release(draft=False, assets=[conflicting]))

    with pytest.raises(ValueError, match="digest/state mismatch"):
        rt.run_transaction(client, TAG, TARGET, [str(asset)])

    assert mutation_calls(client) == []


def test_completed_release_rerun_with_identical_assets_succeeds(tmp_path):
    asset = create_asset(tmp_path, "shapely-2.1.2.tar.gz", b"sdist")
    client = FakeClient()

    assert rt.run_transaction(client, TAG, TARGET, [str(asset)]) == "published"
    first_calls = list(client.calls)
    assert (
        rt.run_transaction(client, TAG, TARGET, [str(asset)])
        == "already-published"
    )

    assert mutation_calls(client) == [
        call[0]
        for call in first_calls
        if call[0] in {"create_draft", "delete_asset", "upload_asset", "publish"}
    ]


def test_asset_failure_leaves_incomplete_release_draft(tmp_path):
    asset = create_asset(tmp_path, "shapely-2.1.2.tar.gz", b"sdist")
    client = FakeClient()
    client.fail_upload = True

    with pytest.raises(rt.ReleaseCommandError, match="upload failed"):
        rt.run_transaction(client, TAG, TARGET, [str(asset)])

    assert client.release["draft"] is True
    assert "publish" not in mutation_calls(client)


def test_publish_failure_leaves_verified_release_draft(tmp_path):
    asset = create_asset(tmp_path, "shapely-2.1.2.tar.gz", b"sdist")
    client = FakeClient()
    client.fail_publish = True

    with pytest.raises(rt.ReleaseCommandError, match="publish failed"):
        rt.run_transaction(client, TAG, TARGET, [str(asset)])

    assert client.release["draft"] is True
    rt.verify_exact_assets(client.release, rt.local_assets([str(asset)]))


def test_target_mismatch_fails_before_release_creation(tmp_path):
    asset = create_asset(tmp_path, "shapely-2.1.2.tar.gz", b"sdist")
    client = FakeClient(target="b" * 40)

    with pytest.raises(ValueError, match="expected target"):
        rt.run_transaction(client, TAG, TARGET, [str(asset)])

    assert mutation_calls(client) == []
