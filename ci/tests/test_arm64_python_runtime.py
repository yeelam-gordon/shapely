from __future__ import annotations

import json
import os
import pathlib
import stat
import subprocess
import sys
import zipfile
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ci"))

import arm64_python_runtime as apr


ROOT_INDEX = """
<a href="3.13.4/">3.13.4/</a>
<a href="3.13.5/">3.13.5/</a>
<a href="3.14.7/">3.14.7/</a>
<a href="3.14.8rc1/">3.14.8rc1/</a>
<a href="3.15.0/">3.15.0/</a>
""".strip()

PRERELEASE_ROOT_INDEX = '<a href="3.15.0rc1/">3.15.0rc1/</a>'
PYTHON_315_DIRECTORY = '<a href="windows-3.15.0rc1.json">windows-3.15.0rc1.json</a>'


def write_zip_member(
    payload: zipfile.ZipFile, member_name: str, data: bytes
) -> None:
    member = zipfile.ZipInfo("placeholder")
    member.filename = member_name
    payload.writestr(member, data)


def zip_member(member_name: str) -> zipfile.ZipInfo:
    member = zipfile.ZipInfo("placeholder")
    member.filename = member_name
    return member


def make_manifest(*, entry_id: str, download_url: str, executable: str, sort_version: str = "3.14.7") -> dict[str, object]:
    return {
        "versions": [
            {
                "schema": 1,
                "id": entry_id,
                "sort-version": sort_version,
                "company": "PythonCore",
                "tag": "tag",
                "install-for": ["x"],
                "alias": [],
                "display-name": "display",
                "executable": executable,
                "url": download_url,
                "hash": {"sha256": "a" * 64},
            }
        ]
    }


def mock_python_org(monkeypatch, mapping):
    def fake_fetch_text(url):
        if url not in mapping:
            raise apr.urllib.error.HTTPError(url, 404, "missing", {}, None)
        return mapping[url]

    monkeypatch.setattr(apr, "fetch_text", fake_fetch_text)
    monkeypatch.setattr(apr, "fetch_json", lambda url: json.loads(fake_fetch_text(url)))


def test_parse_versions_prefers_stable_for_non_prerelease_rows():
    spec = apr.RuntimeSpec("cp314", "3.14", False, False)

    releases = apr.parse_versions(ROOT_INDEX, spec)

    assert releases == ["3.14.7"]


def test_parse_versions_allows_prereleases_only_when_configured():
    spec = apr.RuntimeSpec("cp315", "3.15", False, True)

    releases = apr.parse_versions(PRERELEASE_ROOT_INDEX, spec)

    assert releases == ["3.15.0rc1"]


def test_parse_prerelease_manifest_versions_filters_and_orders_candidates():
    directory_index = """
    <a href="windows-3.15.0rc1.json">rc1</a>
    <a href="windows-3.15.0rc2.json">rc2</a>
    <a href="windows-3.15.0.json">stable</a>
    <a href="windows-3.15.1a1.json">other patch</a>
    <a href="windows-3.14.9rc1.json">other series</a>
    <a href="windows-3.15.0rc2-extra.json">malformed</a>
    <a href="linux-3.15.0rc3.json">other platform</a>
    """.strip()

    releases = apr.parse_prerelease_manifest_versions(
        directory_index,
        apr.RuntimeSpec("cp315", "3.15", False, True),
        "3.15.0",
    )

    assert releases == ["3.15.0rc2", "3.15.0rc1"]


@pytest.mark.parametrize(
    ("label", "free_threaded", "entry_id", "executable"),
    [
        ("cp315", False, "pythoncore-3.15-arm64", "./python3.15.exe"),
        ("cp315t", True, "pythoncore-3.15t-arm64", "./python3.15t.exe"),
    ],
)
def test_discover_runtime_finds_315_prerelease_in_base_version_directory(
    monkeypatch, label, free_threaded, entry_id, executable
):
    manifest_location = apr.prerelease_manifest_url("3.15.0", "3.15.0rc1")
    mapping = {
        apr.ROOT_INDEX_URL: ROOT_INDEX,
        f"{apr.PYTHON_FTP_ROOT}/3.15.0/": PYTHON_315_DIRECTORY,
        manifest_location: json.dumps(
            make_manifest(
                entry_id=entry_id,
                download_url=f"https://www.python.org/ftp/python/3.15.0/python-{label}-arm64.zip",
                executable=executable,
                sort_version="3.15.0rc1",
            )
        ),
    }
    mock_python_org(monkeypatch, mapping)

    record = apr.discover_runtime(
        apr.RuntimeSpec(label, "3.15", free_threaded, True)
    )

    assert record["available"] is True
    assert record["release"] == "3.15.0rc1"
    assert record["manifest_url"] == manifest_location
    assert record["executable"] == executable


def test_discover_runtime_uses_newest_compatible_prerelease_manifest(monkeypatch):
    directory_index = """
    <a href="windows-3.15.0rc1.json">rc1</a>
    <a href="windows-3.15.0rc2.json">rc2</a>
    <a href="windows-3.15.1a1.json">other patch</a>
    """.strip()
    rc2_location = apr.prerelease_manifest_url("3.15.0", "3.15.0rc2")
    mapping = {
        apr.ROOT_INDEX_URL: ROOT_INDEX,
        f"{apr.PYTHON_FTP_ROOT}/3.15.0/": directory_index,
        rc2_location: json.dumps(
            make_manifest(
                entry_id="pythoncore-3.15-arm64",
                download_url="https://www.python.org/ftp/python/3.15.0/python-3.15.0rc2-arm64.zip",
                executable="./python3.15.exe",
                sort_version="3.15.0rc2",
            )
        ),
    }
    mock_python_org(monkeypatch, mapping)

    record = apr.discover_runtime(apr.RuntimeSpec("cp315", "3.15", False, True))

    assert record["release"] == "3.15.0rc2"
    assert record["manifest_url"] == rc2_location


def test_discover_runtime_prefers_exact_stable_manifest(monkeypatch):
    stable_location = apr.manifest_url("3.15.0")
    mapping = {
        apr.ROOT_INDEX_URL: ROOT_INDEX,
        stable_location: json.dumps(
            make_manifest(
                entry_id="pythoncore-3.15-arm64",
                download_url="https://www.python.org/ftp/python/3.15.0/python-3.15.0-arm64.zip",
                executable="./python3.15.exe",
                sort_version="3.15.0",
            )
        ),
    }
    mock_python_org(monkeypatch, mapping)

    record = apr.discover_runtime(apr.RuntimeSpec("cp315", "3.15", False, True))

    assert record["release"] == "3.15.0"
    assert record["manifest_url"] == stable_location


def test_discover_runtime_rejects_malformed_prerelease_manifest(monkeypatch):
    manifest_location = apr.prerelease_manifest_url("3.15.0", "3.15.0rc1")
    manifest = make_manifest(
        entry_id="pythoncore-3.15-arm64",
        download_url="https://www.python.org/ftp/python/3.15.0/python-3.15.0rc1-arm64.zip",
        executable="./python3.15.exe",
        sort_version="3.15.0rc1",
    )
    manifest["versions"][0]["unexpected"] = True
    mapping = {
        apr.ROOT_INDEX_URL: ROOT_INDEX,
        f"{apr.PYTHON_FTP_ROOT}/3.15.0/": PYTHON_315_DIRECTORY,
        manifest_location: json.dumps(manifest),
    }
    mock_python_org(monkeypatch, mapping)

    with pytest.raises(ValueError, match="unexpected manifest key set"):
        apr.discover_runtime(apr.RuntimeSpec("cp315", "3.15", False, True))


def test_discover_runtime_finds_latest_free_threaded_manifest(monkeypatch):
    mapping = {
        apr.ROOT_INDEX_URL: ROOT_INDEX,
        apr.manifest_url("3.14.7"): json.dumps(
            make_manifest(
                entry_id="pythoncore-3.14t-arm64",
                download_url="https://www.python.org/ftp/python/3.14.7/python-3.14.7t-arm64.zip",
                executable="./python3.14t.exe",
            )
        ),
    }

    monkeypatch.setattr(apr, "fetch_text", lambda url: mapping[url])
    monkeypatch.setattr(apr, "fetch_json", lambda url: json.loads(mapping[url]))

    record = apr.discover_runtime(apr.RuntimeSpec("cp314t", "3.14", True, False))

    assert record["available"] is True
    assert record["release"] == "3.14.7"
    assert record["download_url"].endswith("python-3.14.7t-arm64.zip")
    assert record["executable"] == "./python3.14t.exe"
    assert record["allow_prereleases"] is False


def test_discover_runtime_marks_unavailable_when_manifest_missing(monkeypatch):
    monkeypatch.setattr(apr, "fetch_text", lambda url: ROOT_INDEX)

    def fake_fetch_json(url):
        raise apr.urllib.error.HTTPError(url, 404, "missing", {}, None)

    monkeypatch.setattr(apr, "fetch_json", fake_fetch_json)

    record = apr.discover_runtime(apr.RuntimeSpec("cp315t", "3.15", True, True))

    assert record["available"] is False
    assert record["allow_prereleases"] is True
    assert "no python.org Windows ARM64 manifest entry found" in record["reason"]


def test_install_runtime_extracts_zip_and_bootstraps_pip(tmp_path, monkeypatch):
    runtime_zip = tmp_path / "python-3.13.5-arm64.zip"
    with zipfile.ZipFile(runtime_zip, "w") as payload:
        payload.writestr("bin/python.exe", b"stub")

    plan = {
        "label": "cp313",
        "series": "3.13",
        "free_threaded": False,
        "allow_prereleases": False,
        "available": True,
        "release": "3.13.5",
        "manifest_url": apr.manifest_url("3.13.5"),
        "download_url": "https://www.python.org/ftp/python/3.13.5/python-3.13.5-arm64.zip",
        "sha256": apr.sha256_file(runtime_zip),
        "executable": ".\\bin/python.exe",
        "reason": None,
    }

    monkeypatch.setattr(apr, "discover_runtime", lambda spec: plan)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return runtime_zip.read_bytes()

    monkeypatch.setattr(apr.urllib.request, "urlopen", lambda url, timeout=60: FakeResponse())
    calls = []
    monkeypatch.setattr(apr.subprocess, "check_call", lambda command: calls.append(command))

    record = apr.install_runtime(apr.RuntimeSpec("cp313", "3.13", False, False), tmp_path / "runtime-root")

    assert record["available"] is True
    assert pathlib.Path(record["python_executable"]).exists()
    assert calls == [
        [record["python_executable"], "-m", "ensurepip", "--upgrade"],
        [record["python_executable"], "-m", "pip", "install", "--upgrade", "pip"],
    ]


def test_safe_extract_zip_extracts_valid_archive(tmp_path):
    runtime_zip = tmp_path / "runtime.zip"
    with zipfile.ZipFile(runtime_zip, "w") as payload:
        payload.writestr("bin/", b"")
        payload.writestr("bin/python.exe", b"stub")
        payload.writestr("Lib/site.py", b"site")

    target = tmp_path / "target"
    with zipfile.ZipFile(runtime_zip) as payload:
        apr.safe_extract_zip(payload, target)

    assert (target / "bin" / "python.exe").read_bytes() == b"stub"
    assert (target / "Lib" / "site.py").read_bytes() == b"site"


@pytest.mark.parametrize(
    "member_name",
    [
        "../escaped.txt",
        "nested/../../escaped.txt",
        "..\\escaped.txt",
        "nested\\..\\..\\escaped.txt",
        "/absolute.txt",
        "\\absolute.txt",
        "C:/drive.txt",
        "C:\\drive.txt",
        "\\\\server\\share\\unc.txt",
        "nested/.. /escaped.txt",
    ],
)
def test_safe_extract_zip_rejects_unsafe_paths_without_writes(
    tmp_path, member_name
):
    runtime_zip = tmp_path / "runtime.zip"
    with zipfile.ZipFile(runtime_zip, "w") as payload:
        payload.writestr("valid-before-malicious.txt", b"must not be written")
        payload.writestr(member_name, b"escape")

    target = tmp_path / "target"
    outside = tmp_path / "escaped.txt"
    with zipfile.ZipFile(runtime_zip) as payload:
        with pytest.raises(ValueError, match="unsafe"):
            apr.safe_extract_zip(payload, target)

    assert not target.exists()
    assert not outside.exists()


@pytest.mark.parametrize(
    "member_name",
    [
        "CONIN$",
        "conin$.txt",
        "nested/CoNiN$ .cfg",
        "CONOUT$",
        "conout$.log",
        "nested/CoNoUt$ .cfg",
        "COM¹",
        "com².txt",
        "nested/CoM³ .cfg",
        "LPT¹",
        "lpt².txt",
        "nested/LpT³ .cfg",
    ],
)
def test_safe_extract_zip_rejects_windows_device_variants_without_writes(
    tmp_path, member_name
):
    runtime_zip = tmp_path / "runtime.zip"
    with zipfile.ZipFile(runtime_zip, "w") as payload:
        payload.writestr("valid-before-device.txt", b"must not be written")
        payload.writestr(member_name, b"device")

    target = tmp_path / "target"
    outside = tmp_path / "outside.txt"
    with zipfile.ZipFile(runtime_zip) as payload:
        with pytest.raises(ValueError, match="unsafe Windows device"):
            apr.safe_extract_zip(payload, target)

    assert not target.exists()
    assert not outside.exists()


@pytest.mark.parametrize(
    ("member_name", "error_match"),
    [
        *[
            (f"nested/bad{chr(codepoint)}name", "control character")
            for codepoint in range(0x20)
        ],
        *[
            (f"nested/bad{character}name", "Windows character")
            for character in '<>"|?*'
        ],
        ("nested/file.txt:stream", "drive or stream"),
        (f"nested/{'a' * 256}", "overlong Windows"),
        (f"nested/{'😀' * 128}", "overlong Windows"),
    ],
    ids=[
        *[f"control-u+{codepoint:04x}" for codepoint in range(0x20)],
        "less-than",
        "greater-than",
        "double-quote",
        "pipe",
        "question-mark",
        "asterisk",
        "ads-colon",
        "256-ascii-units",
        "256-supplementary-units",
    ],
)
def test_safe_extract_zip_rejects_windows_invalid_components_without_writes(
    tmp_path, member_name, error_match
):
    valid_member = zipfile.ZipInfo("valid-before-invalid.txt")
    invalid_member = zip_member(member_name)

    class InvalidPayload:
        def infolist(self):
            return [valid_member, invalid_member]

        def open(self, member):
            raise AssertionError(f"unexpected extraction of {member.filename}")

    target = tmp_path / "target"
    with pytest.raises(ValueError, match=error_match):
        apr.safe_extract_zip(InvalidPayload(), target)

    assert not target.exists()
    assert not (target / valid_member.filename).exists()


@pytest.mark.parametrize(
    "component",
    [
        "a" * 255,
        ("😀" * 127) + "a",
    ],
    ids=["255-ascii-units", "255-mixed-supplementary-units"],
)
def test_validated_zip_member_accepts_255_utf16_unit_components(
    tmp_path, component
):
    target = tmp_path / "target"
    member = zip_member(f"nested/{component}/python.exe")

    parts, is_directory = apr.validated_zip_member(target, member)

    assert parts == ("nested", component, "python.exe")
    assert is_directory is False
    assert not target.exists()


@pytest.mark.parametrize(
    "conflicting_members",
    [
        ("pkg/module.py", "PKG/MODULE.PY"),
        ("pkg/module.py", "pkg\\module.py"),
        ("pkg/module.py", "pkg/./module.py"),
        ("pkg/", "PKG"),
        ("pkg", "PKG/"),
    ],
)
def test_safe_extract_zip_rejects_destination_collisions_without_writes(
    tmp_path, conflicting_members
):
    runtime_zip = tmp_path / "runtime.zip"
    with zipfile.ZipFile(runtime_zip, "w") as payload:
        payload.writestr("valid-before-collision.txt", b"must not be written")
        for member_name in conflicting_members:
            write_zip_member(payload, member_name, b"collision")

    target = tmp_path / "target"
    outside = tmp_path / "outside.txt"
    with zipfile.ZipFile(runtime_zip) as payload:
        with pytest.raises(
            ValueError, match="unsafe (duplicate|file/directory) ZIP member"
        ):
            apr.safe_extract_zip(payload, target)

    assert not target.exists()
    assert not outside.exists()


@pytest.mark.parametrize(
    "conflicting_members",
    [
        ("pkg", "pkg/child.py"),
        ("pkg/child.py", "pkg"),
        ("PKG", "pkg/child.py"),
        ("pkg/child.py", "PKG"),
    ],
)
def test_safe_extract_zip_rejects_file_ancestor_without_writes(
    tmp_path, conflicting_members
):
    runtime_zip = tmp_path / "runtime.zip"
    with zipfile.ZipFile(runtime_zip, "w") as payload:
        payload.writestr("valid-before-collision.txt", b"must not be written")
        for member_name in conflicting_members:
            write_zip_member(payload, member_name, b"collision")

    target = tmp_path / "target"
    outside = tmp_path / "outside.txt"
    with zipfile.ZipFile(runtime_zip) as payload:
        with pytest.raises(ValueError, match="unsafe ZIP member path beneath file"):
            apr.safe_extract_zip(payload, target)

    assert not target.exists()
    assert not outside.exists()


@pytest.mark.parametrize(
    ("create_system", "external_attr"),
    [
        (3, (stat.S_IFLNK | 0o777) << 16),
        (0, apr.WINDOWS_REPARSE_POINT),
    ],
)
def test_safe_extract_zip_rejects_link_entries(
    tmp_path, create_system, external_attr
):
    runtime_zip = tmp_path / "runtime.zip"
    link = zipfile.ZipInfo("link")
    link.create_system = create_system
    link.external_attr = external_attr
    with zipfile.ZipFile(runtime_zip, "w") as payload:
        payload.writestr(link, "../escaped.txt")

    with zipfile.ZipFile(runtime_zip) as payload:
        with pytest.raises(ValueError, match="link or special|reparse-point"):
            apr.safe_extract_zip(payload, tmp_path / "target")

    assert not (tmp_path / "target").exists()
    assert not (tmp_path / "escaped.txt").exists()


def test_safe_extract_zip_rejects_existing_symlink_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    try:
        os.symlink(outside, target / "redirect", target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks unavailable: {error}")

    runtime_zip = tmp_path / "runtime.zip"
    with zipfile.ZipFile(runtime_zip, "w") as payload:
        payload.writestr("redirect/escaped.txt", b"escape")

    with zipfile.ZipFile(runtime_zip) as payload:
        with pytest.raises(
            ValueError, match="unsafe normalized|link or reparse point"
        ):
            apr.safe_extract_zip(payload, target)

    assert not (outside / "escaped.txt").exists()


def test_safe_extract_zip_rejects_target_ancestor_symlink_without_writes(
    tmp_path,
):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "target-link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks unavailable: {error}")

    runtime_zip = tmp_path / "runtime.zip"
    with zipfile.ZipFile(runtime_zip, "w") as payload:
        payload.writestr("python.exe", b"must not be written")

    with zipfile.ZipFile(runtime_zip) as payload:
        with pytest.raises(ValueError, match="target path contains a link"):
            apr.safe_extract_zip(payload, link / "runtime")

    assert list(outside.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="directory junctions are Windows-only")
def test_safe_extract_zip_rejects_target_ancestor_junction_without_writes(
    tmp_path,
):
    outside = tmp_path / "outside"
    outside.mkdir()
    junction = tmp_path / "target-junction"
    result = subprocess.run(
        [
            os.environ.get("COMSPEC", "cmd.exe"),
            "/c",
            "mklink",
            "/J",
            str(junction),
            str(outside),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        pytest.skip(f"directory junctions unavailable: {result.stderr.strip()}")

    runtime_zip = tmp_path / "runtime.zip"
    with zipfile.ZipFile(runtime_zip, "w") as payload:
        payload.writestr("python.exe", b"must not be written")

    with zipfile.ZipFile(runtime_zip) as payload:
        with pytest.raises(ValueError, match="target path contains a link"):
            apr.safe_extract_zip(payload, junction / "runtime")

    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("valid_member_first", [True, False])
@pytest.mark.parametrize(
    ("existing_kind", "conflicting_member", "error_match"),
    [
        (
            "file",
            "conflict/child.py",
            "destination ancestor is not a directory",
        ),
        (
            "directory",
            "conflict",
            "file conflicts with an existing directory",
        ),
    ],
)
def test_safe_extract_zip_preflights_existing_conflicts_without_partial_writes(
    tmp_path,
    valid_member_first,
    existing_kind,
    conflicting_member,
    error_match,
):
    target = tmp_path / "target"
    target.mkdir()
    conflict = target / "conflict"
    if existing_kind == "file":
        conflict.write_bytes(b"existing")
    else:
        conflict.mkdir()

    members = [
        ("valid.txt", b"must not be written"),
        (conflicting_member, b"conflict"),
    ]
    if not valid_member_first:
        members.reverse()

    runtime_zip = tmp_path / "runtime.zip"
    with zipfile.ZipFile(runtime_zip, "w") as payload:
        for member_name, data in members:
            payload.writestr(member_name, data)

    with zipfile.ZipFile(runtime_zip) as payload:
        with pytest.raises(ValueError, match=error_match):
            apr.safe_extract_zip(payload, target)

    assert not (target / "valid.txt").exists()
    if existing_kind == "file":
        assert conflict.read_bytes() == b"existing"
    else:
        assert list(conflict.iterdir()) == []


def test_safe_extract_zip_preflight_preserves_existing_file_on_later_conflict(
    tmp_path,
):
    target = tmp_path / "target"
    target.mkdir()
    existing = target / "existing.txt"
    existing.write_bytes(b"original")
    (target / "blocked").write_bytes(b"not a directory")

    runtime_zip = tmp_path / "runtime.zip"
    with zipfile.ZipFile(runtime_zip, "w") as payload:
        payload.writestr("existing.txt", b"replacement")
        payload.writestr("blocked/child.txt", b"must not be written")

    with zipfile.ZipFile(runtime_zip) as payload:
        with pytest.raises(
            ValueError, match="destination ancestor is not a directory"
        ):
            apr.safe_extract_zip(payload, target)

    assert existing.read_bytes() == b"original"
    assert (target / "blocked").read_bytes() == b"not a directory"


@pytest.mark.parametrize("executable", ["C:/python.exe", "C:\\python.exe", "\\\\server\\share\\python.exe", "..\\python.exe"])
def test_install_runtime_rejects_escape_paths(tmp_path, monkeypatch, executable):
    runtime_zip = tmp_path / "python-3.13.5-arm64.zip"
    with zipfile.ZipFile(runtime_zip, "w") as payload:
        payload.writestr("python.exe", b"stub")

    plan = {
        "label": "cp313",
        "series": "3.13",
        "free_threaded": False,
        "allow_prereleases": False,
        "available": True,
        "release": "3.13.5",
        "manifest_url": apr.manifest_url("3.13.5"),
        "download_url": "https://www.python.org/ftp/python/3.13.5/python-3.13.5-arm64.zip",
        "sha256": apr.sha256_file(runtime_zip),
        "executable": executable,
        "reason": None,
    }

    monkeypatch.setattr(apr, "discover_runtime", lambda spec: plan)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return runtime_zip.read_bytes()

    monkeypatch.setattr(apr.urllib.request, "urlopen", lambda url, timeout=60: FakeResponse())

    with pytest.raises(ValueError, match="runtime executable path"):
        apr.install_runtime(apr.RuntimeSpec("cp313", "3.13", False, False), tmp_path / "runtime-root")


def test_install_runtime_normalizes_mixed_separators(tmp_path, monkeypatch):
    runtime_zip = tmp_path / "python-3.13.5-arm64.zip"
    with zipfile.ZipFile(runtime_zip, "w") as payload:
        payload.writestr("bin/python/python.exe", b"stub")

    plan = {
        "label": "cp313",
        "series": "3.13",
        "free_threaded": False,
        "allow_prereleases": False,
        "available": True,
        "release": "3.13.5",
        "manifest_url": apr.manifest_url("3.13.5"),
        "download_url": "https://www.python.org/ftp/python/3.13.5/python-3.13.5-arm64.zip",
        "sha256": apr.sha256_file(runtime_zip),
        "executable": ".\\bin/python\\python.exe",
        "reason": None,
    }

    monkeypatch.setattr(apr, "discover_runtime", lambda spec: plan)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return runtime_zip.read_bytes()

    monkeypatch.setattr(apr.urllib.request, "urlopen", lambda url, timeout=60: FakeResponse())
    monkeypatch.setattr(apr.subprocess, "check_call", lambda command: None)

    record = apr.install_runtime(apr.RuntimeSpec("cp313", "3.13", False, False), tmp_path / "runtime-root")

    assert pathlib.Path(record["python_executable"]).name == "python.exe"
    assert pathlib.Path(record["python_executable"]).parts[-3:] == ("bin", "python", "python.exe")


def test_selected_specs_override_allow_prereleases():
    specs = apr.selected_specs(["cp315", "cp314"], allow_prereleases=True)

    assert [spec.allow_prereleases for spec in specs] == [True, True]


def test_install_command_returns_one_for_unavailable_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(
        apr,
        "install_runtime",
        lambda spec, root: {
            "label": spec.label,
            "series": spec.series,
            "free_threaded": spec.free_threaded,
            "allow_prereleases": spec.allow_prereleases,
            "available": False,
            "release": None,
            "manifest_url": None,
            "download_url": None,
            "sha256": None,
            "executable": None,
            "reason": "missing",
        },
    )

    code = apr.install_command(
        SimpleNamespace(
            label="cp315t",
            allow_prereleases="true",
            root=str(tmp_path / "runtimes"),
            output=str(tmp_path / "runtime.json"),
        )
    )

    assert code == 1
    runtime = json.loads((tmp_path / "runtime.json").read_text(encoding="utf-8"))["runtime"]
    assert runtime["available"] is False
    assert runtime["allow_prereleases"] is True
