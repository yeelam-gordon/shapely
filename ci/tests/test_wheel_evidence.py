from __future__ import annotations

import io
import json
import pathlib
import struct
import subprocess
import sys
import zipfile
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ci"))

import wheel_evidence as we


REAL_GEOS_SHA256 = "df2c50503295f325e7c8d7b783aca8ba4773919cde984193850cf9e361dfd28c"


def arm64_pe_bytes(machine: int = 0xAA64) -> bytes:
    data = bytearray(0x90)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", data, 0x84, machine)
    return bytes(data)


def tag_for_label(label: str) -> str:
    runtime = we.runtime_for_label(label)
    return f"{runtime['interpreter']}-{runtime['abi']}-win_arm64"


def create_wheel(
    directory: pathlib.Path,
    tag: str,
    *,
    wheel_tag: str | None = None,
    repaired: bool = True,
) -> pathlib.Path:
    interpreter, abi, platform = tag.split("-")
    path = directory / f"shapely-2.1.2-{interpreter}-{abi}-{platform}.whl"
    dist_info = "shapely-2.1.2.dist-info"
    license_dir = ROOT / "ci" / "wheelbuilder"
    with zipfile.ZipFile(path, "w") as wheel:
        wheel.writestr(
            f"{dist_info}/WHEEL",
            "\n".join(
                [
                    "Wheel-Version: 1.0",
                    "Generator: pytest",
                    "Root-Is-Purelib: false",
                    f"Tag: {wheel_tag or tag}",
                    "",
                ]
            ),
        )
        wheel.writestr(
            f"shapely/lib.{interpreter}-{platform}.pyd",
            arm64_pe_bytes(),
        )
        wheel.writestr(
            "shapely.libs/libgeos-test.dll",
            arm64_pe_bytes(),
        )
        wheel.writestr(
            f"{dist_info}/LICENSE_GEOS",
            (license_dir / "LICENSE_GEOS").read_bytes(),
        )
        wheel.writestr(
            f"{dist_info}/LICENSE_win32",
            (license_dir / "LICENSE_win32").read_bytes(),
        )
        if repaired:
            wheel.writestr(
                f"{dist_info}/DELVEWHEEL",
                "Version: 1.13.0\nArguments: ['delvewheel', 'repair']\n",
            )
    return path


def create_all_wheels(directory: pathlib.Path) -> dict[str, pathlib.Path]:
    wheels = {}
    for label in sorted(we.EXPECTED_LABELS):
        wheels[label] = create_wheel(directory, tag_for_label(label))
    return wheels


def set_github_env(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, *, job_key: str) -> None:
    env = {
        "GITHUB_REPOSITORY": "shapely/shapely",
        "GITHUB_RUN_ID": "123456789",
        "GITHUB_JOB": job_key,
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_SHA": "0123456789abcdef0123456789abcdef01234567",
        "GITHUB_REF": "refs/heads/main",
        "ImageOS": "windows11",
        "ImageVersion": "20260830.1",
        "GEOS_VERSION": "3.13.1",
        "GEOS_SHA256": REAL_GEOS_SHA256,
        "GEOS_CHECKSUM_PROVENANCE_URL": "https://github.com/libgeos/geos/releases/tag/3.13.1",
        "RUNNER_TEMP": str(tmp_path),
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def read_json(path: pathlib.Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def install_fake_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command, **kwargs):
        argv = [str(item) for item in command]
        if len(argv) >= 4 and argv[1:4] == ["-m", "delvewheel", "show"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    f"Analyzing {argv[4]}\n\n"
                    "delvewheel 1.13.0 has already repaired this wheel\n"
                ),
            )
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(we.subprocess, "run", fake_run)


def build_round_trip(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[pathlib.Path, pathlib.Path]:
    wheelhouse = tmp_path / "wheelhouse"
    verifier_dir = tmp_path / "verifier-records"
    wheelhouse.mkdir()
    verifier_dir.mkdir()
    create_all_wheels(wheelhouse)

    set_github_env(monkeypatch, tmp_path, job_key="build-windows-arm64")
    monkeypatch.setattr(we.platform, "machine", lambda: "ARM64")
    monkeypatch.setattr(we, "command_version", lambda *command: f"{command[0]} version")

    producer_path = tmp_path / "arm64-wheel-producer.json"
    we.producer_command(SimpleNamespace(wheelhouse=str(wheelhouse), output=str(producer_path)))
    we.validate_producer(read_json(producer_path))

    python_exe = tmp_path / "python.exe"
    python_exe.write_bytes(b"")

    monkeypatch.setattr(we, "require_arm64", lambda exe, expected: None)
    monkeypatch.setattr(we, "package_version", lambda name: "1.17.0")
    install_fake_subprocess(monkeypatch)

    for label in sorted(we.EXPECTED_LABELS):
        set_github_env(monkeypatch, tmp_path, job_key=f"verify-{label}")
        we.verify_command(
            SimpleNamespace(
                producer=str(producer_path),
                wheelhouse=str(wheelhouse),
                source_root=str(ROOT),
                label=label,
                python_exe=str(python_exe),
                output=str(verifier_dir / f"arm64-wheel-verifier-{label}.json"),
                report_dir="repair-reports",
            )
        )
        we.validate_verifier(read_json(verifier_dir / f"arm64-wheel-verifier-{label}.json"))

    set_github_env(monkeypatch, tmp_path, job_key="finalize-windows-arm64")
    final_path = tmp_path / "arm64-wheel-provenance.json"
    we.finalize_command(
        SimpleNamespace(
            producer=str(producer_path),
            verifier_dir=str(verifier_dir),
            output=str(final_path),
        )
    )
    we.validate_final(read_json(final_path))
    return producer_path, final_path


def test_producer_verify_finalize_round_trip(tmp_path, monkeypatch):
    producer_path, final_path = build_round_trip(tmp_path, monkeypatch)
    assert producer_path.exists()
    assert final_path.exists()
    final = read_json(final_path)
    assert {artifact["runtime"]["label"] for artifact in final["artifacts"]} == we.EXPECTED_LABELS
    assert len(final["artifacts"]) == 7


def test_verify_command_rejects_missing_matching_wheel(tmp_path, monkeypatch):
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    create_wheel(wheelhouse, tag_for_label("cp313"))

    set_github_env(monkeypatch, tmp_path, job_key="build-windows-arm64")
    monkeypatch.setattr(we.platform, "machine", lambda: "ARM64")
    monkeypatch.setattr(we, "command_version", lambda *command: f"{command[0]} version")
    producer_path = tmp_path / "arm64-wheel-producer.json"
    we.producer_command(SimpleNamespace(wheelhouse=str(wheelhouse), output=str(producer_path)))

    set_github_env(monkeypatch, tmp_path, job_key="verify-cp314")
    monkeypatch.setattr(we, "require_arm64", lambda exe, expected: None)
    install_fake_subprocess(monkeypatch)
    python_exe = tmp_path / "python.exe"
    python_exe.write_bytes(b"")

    with pytest.raises(ValueError, match="no retained wheel matches verifier label"):
        we.verify_command(
            SimpleNamespace(
                producer=str(producer_path),
                wheelhouse=str(wheelhouse),
                source_root=str(ROOT),
                label="cp314",
                python_exe=str(python_exe),
                output=str(tmp_path / "arm64-wheel-verifier-cp314.json"),
                report_dir="repair-reports",
            )
        )


def test_validate_producer_rejects_unknown_key(tmp_path):
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    wheel = create_wheel(wheelhouse, tag_for_label("cp313"))
    doc = {
        "schema_version": 1,
        "record_type": "producer",
        "created_at_utc": we.now_utc(),
        "job": {
            "repository": "shapely/shapely",
            "run_id": "123456789",
            "job_key": "build-windows-arm64",
            "run_url": "https://github.com/shapely/shapely/actions/runs/123456789",
            "commit_sha": "0123456789abcdef0123456789abcdef01234567",
            "ref": "refs/heads/main",
        },
        "source_artifact": dict(we.SOURCE_ARTIFACT),
        "producer": {
            "runner": {
                "os": "Windows",
                "architecture": "ARM64",
                "image_os": "windows11",
                "image_version": "20260830.1",
            },
            "toolchain": {
                "cibuildwheel_action": "pypa/cibuildwheel@v4.2.0",
                "msvc_toolset": "14.44",
                "cmake": "cmake version",
                "ninja": "ninja version",
            },
        },
        "geos": {
            "version": "3.13.1",
            "archive_url": "https://download.osgeo.org/geos/geos-3.13.1.tar.bz2",
            "sha256": REAL_GEOS_SHA256,
            "checksum_provenance_url": "https://github.com/libgeos/geos/releases/tag/3.13.1",
        },
        "artifacts": [we.wheel_core(wheel)],
        "extra": "boom",
    }
    with pytest.raises(ValueError, match="exact keys required"):
        we.validate_producer(doc)


def test_validate_final_rejects_missing_verifier_label(tmp_path, monkeypatch):
    _, final_path = build_round_trip(tmp_path, monkeypatch)
    final = read_json(final_path)
    final["verifiers"] = final["verifiers"][:-1]
    with pytest.raises(ValueError, match="seven verifier rows required"):
        we.validate_final(final)


def test_validate_final_rejects_seven_labels_with_one_wheel(tmp_path, monkeypatch):
    _, final_path = build_round_trip(tmp_path, monkeypatch)
    final = read_json(final_path)
    final["artifacts"] = final["artifacts"][:1]
    with pytest.raises(ValueError, match="exact ABI label coverage required"):
        we.validate_final(final)


def test_validate_final_rejects_absolute_repair_report(tmp_path, monkeypatch):
    _, final_path = build_round_trip(tmp_path, monkeypatch)
    final = read_json(final_path)
    final["artifacts"][0]["repair_report"] = "C:/escape.txt"
    with pytest.raises(ValueError, match="relative path required"):
        we.validate_final(final)


def test_finalize_command_rejects_parent_traversal_repair_report(tmp_path, monkeypatch):
    producer_path, _ = build_round_trip(tmp_path, monkeypatch)
    verifier_dir = tmp_path / "verifier-records"
    cp311_path = verifier_dir / "arm64-wheel-verifier-cp311.json"
    cp311 = read_json(cp311_path)
    cp311["artifacts"][0]["repair_report"] = "../escape.txt"
    we.canonical_write(cp311_path, cp311)

    set_github_env(monkeypatch, tmp_path, job_key="finalize-windows-arm64")
    with pytest.raises(ValueError, match="parent traversal forbidden"):
        we.finalize_command(
            SimpleNamespace(
                producer=str(producer_path),
                verifier_dir=str(verifier_dir),
                output=str(tmp_path / "broken.json"),
            )
        )


def test_pypi_check_requires_matching_digest(tmp_path, monkeypatch):
    _, final_path = build_round_trip(tmp_path, monkeypatch)

    class FakeResponse(io.StringIO):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self.close()
            return False

    def fake_urlopen(url, timeout):
        payload = {
            "info": {"name": "shapely", "version": "2.1.2"},
            "urls": [
                {
                    "filename": "shapely-2.1.2-cp311-cp311-win_arm64.whl",
                    "digests": {"sha256": "0" * 64},
                }
            ],
        }
        return FakeResponse(json.dumps(payload))

    monkeypatch.setattr(we.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(ValueError, match="missing/mismatched PyPI ARM64 wheel"):
        we.pypi_check(str(final_path), "shapely", "2.1.2")


def test_verify_one_accepts_realistic_already_repaired_delvewheel_output(tmp_path, monkeypatch):
    wheel = create_wheel(tmp_path, tag_for_label("cp313"))
    core = we.wheel_core(wheel)
    python_exe = tmp_path / "python.exe"
    python_exe.write_bytes(b"")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))

    def fake_run(command, **kwargs):
        argv = [str(item) for item in command]
        if len(argv) >= 4 and argv[1:4] == ["-m", "delvewheel", "show"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    f"Analyzing {argv[4]}\n\n"
                    "delvewheel 1.13.0 has already repaired this wheel\n"
                ),
            )
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(we.subprocess, "run", fake_run)

    verified = we.verify_one(
        core,
        wheel,
        ROOT,
        str(python_exe),
        tmp_path / "artifacts",
        "repair-reports",
    )

    assert verified["repair_report"].startswith("repair-reports/")


def test_verify_one_rejects_already_repaired_output_without_delvewheel_marker(tmp_path, monkeypatch):
    wheel = create_wheel(tmp_path, tag_for_label("cp313"), repaired=False)
    core = we.wheel_core(wheel)
    python_exe = tmp_path / "python.exe"
    python_exe.write_bytes(b"")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))

    def fake_run(command, **kwargs):
        argv = [str(item) for item in command]
        if len(argv) >= 4 and argv[1:4] == ["-m", "delvewheel", "show"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    f"Analyzing {argv[4]}\n\n"
                    "delvewheel 1.13.0 has already repaired this wheel\n"
                ),
            )
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(we.subprocess, "run", fake_run)

    with pytest.raises(ValueError, match="per-wheel delvewheel evidence failed"):
        we.verify_one(
            core,
            wheel,
            ROOT,
            str(python_exe),
            tmp_path / "artifacts",
            "repair-reports",
        )
