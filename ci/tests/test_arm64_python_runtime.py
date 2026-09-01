from __future__ import annotations

import json
import pathlib
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
<a href="3.15.0rc1/">3.15.0rc1/</a>
""".strip()


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


def test_parse_versions_prefers_stable_for_non_prerelease_rows():
    spec = apr.RuntimeSpec("cp314", "3.14", False, False)

    releases = apr.parse_versions(ROOT_INDEX, spec)

    assert releases == ["3.14.7"]


def test_parse_versions_allows_prereleases_only_when_configured():
    spec = apr.RuntimeSpec("cp315", "3.15", False, True)

    releases = apr.parse_versions(ROOT_INDEX, spec)

    assert releases == ["3.15.0rc1"]


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
