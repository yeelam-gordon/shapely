from __future__ import annotations

import hashlib
import os
import pathlib
import subprocess
import sys
import urllib.request

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ci" / "install_geos.cmd"
REAL_GEOS_SHA256 = "df2c50503295f325e7c8d7b783aca8ba4773919cde984193850cf9e361dfd28c"
DOWNLOAD_OSGEO_TEST_ENV = "SHAPELY_TEST_DOWNLOAD_OSGEO"
pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="install_geos.cmd tests require Windows",
)


def _run_download_osgeo_test() -> bool:
    return os.environ.get(DOWNLOAD_OSGEO_TEST_ENV, "").lower() not in {
        "",
        "0",
        "false",
        "no",
        "off",
    }


def _hash_command() -> str:
    prefix = 'powershell.exe -NoProfile -NonInteractive -Command "'
    for line in SCRIPT.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix) and "SHA256]::Create()" in line:
            return line.removeprefix(prefix).removesuffix('"')
    raise AssertionError("SHA-256 PowerShell command not found")


def run_install_geos(
    cwd: pathlib.Path, geos_install: pathlib.Path, geos_sha256: str
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GEOS_INSTALL"] = str(geos_install)
    env["GEOS_VERSION"] = "3.13.1"
    env["GEOS_SHA256"] = geos_sha256
    env["PATH"] = str(cwd) + os.pathsep + env["PATH"]
    return subprocess.run(
        ["cmd.exe", "/d", "/c", "call", str(SCRIPT)],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_install_geos_uses_verified_cache(tmp_path):
    geos_install = tmp_path / "install"
    (geos_install / "include").mkdir(parents=True)
    (geos_install / "bin").mkdir()
    (geos_install / "include" / "geos_c.h").write_text("", encoding="utf-8")
    (geos_install / "bin" / "geos_c.dll").write_text("", encoding="utf-8")
    (geos_install / ".geos-source-sha256").write_text(REAL_GEOS_SHA256, encoding="utf-8")

    result = run_install_geos(tmp_path, geos_install, REAL_GEOS_SHA256)

    assert result.returncode == 0
    assert "Verified GEOS version=3.13.1" in result.stdout
    assert "https://download.osgeo.org/geos/geos-3.13.1.tar.bz2" in result.stdout


@pytest.mark.parametrize("marker", [None, "not-a-sha256", "f" * 64])
def test_install_geos_rejects_invalid_cache_marker(tmp_path, marker):
    geos_install = tmp_path / "install"
    (geos_install / "include").mkdir(parents=True)
    (geos_install / "bin").mkdir()
    (geos_install / "include" / "geos_c.h").write_text("", encoding="utf-8")
    (geos_install / "bin" / "geos_c.dll").write_text("", encoding="utf-8")
    if marker is not None:
        (geos_install / ".geos-source-sha256").write_text(marker, encoding="utf-8")

    result = run_install_geos(tmp_path, geos_install, REAL_GEOS_SHA256)

    assert result.returncode == 13


def test_install_geos_hash_file_is_cmd_compatible(tmp_path):
    archive = tmp_path / "archive.bin"
    hash_file = tmp_path / "archive.sha256"
    archive.write_bytes(b"local GEOS archive fixture")
    expected = hashlib.sha256(archive.read_bytes()).hexdigest()
    command = (
        _hash_command()
        .replace("%GEOS_ARCHIVE%", archive.name)
        .replace("%GEOS_HASH_FILE%", str(hash_file))
    )

    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    raw_hash = hash_file.read_bytes()
    assert raw_hash == expected.encode("ascii")
    assert len(raw_hash) == 64
    assert b"\0" not in raw_hash
    assert not raw_hash.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff"))

    readback_script = tmp_path / "readback.cmd"
    readback_script.write_text(
        "@echo off\n"
        "setlocal EnableDelayedExpansion\n"
        f'set /P ACTUAL_GEOS_SHA256=<"{hash_file}"\n'
        "echo(!ACTUAL_GEOS_SHA256!\n",
        encoding="ascii",
    )
    readback = subprocess.run(
        ["cmd.exe", "/d", "/c", str(readback_script)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert readback.returncode == 0
    assert readback.stdout.strip() == expected


@pytest.mark.skipif(
    not _run_download_osgeo_test(),
    reason=f"set {DOWNLOAD_OSGEO_TEST_ENV} to run the download.osgeo.org network test",
)
def test_install_geos_rejects_bad_hash_before_extraction(tmp_path):
    geos_install = tmp_path / "install"
    result = run_install_geos(tmp_path, geos_install, "0" * 64)

    assert result.returncode == 12
    assert not (tmp_path / "geos-3.13.1.tar.bz2").exists()
    assert not (tmp_path / "geos-3.13.1.tar").exists()
    assert not (tmp_path / "geos-3.13.1").exists()
    assert not geos_install.exists()
    assert not (geos_install / ".geos-source-sha256").exists()


@pytest.mark.skipif(
    not _run_download_osgeo_test(),
    reason=f"set {DOWNLOAD_OSGEO_TEST_ENV} to run the download.osgeo.org network test",
)
def test_geos_sha256_literal_matches_official_archive(tmp_path):
    archive = tmp_path / "geos-3.13.1.tar.bz2"
    with urllib.request.urlopen("https://download.osgeo.org/geos/geos-3.13.1.tar.bz2", timeout=30) as response:
        archive.write_bytes(response.read())

    actual = hashlib.sha256(archive.read_bytes()).hexdigest()

    assert actual == REAL_GEOS_SHA256
