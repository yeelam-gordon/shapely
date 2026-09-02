from __future__ import annotations

import io
import json
import pathlib
import shutil
import struct
import subprocess
import sys
import urllib.error
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

    payload = {
        "info": {"name": "shapely", "version": "2.1.2"},
        "urls": [
            {
                "filename": "shapely-2.1.2-cp311-cp311-win_arm64.whl",
                "digests": {"sha256": "0" * 64},
            }
        ],
    }
    monkeypatch.setattr(
        we.urllib.request,
        "urlopen",
        lambda url, timeout: fake_pypi_response(payload),
    )
    with pytest.raises(ValueError, match="PyPI ARM64 wheel map mismatch"):
        we.pypi_check(str(final_path), "shapely", "2.1.2")


def fake_pypi_response(payload: object) -> io.StringIO:
    return io.StringIO(json.dumps(payload))


def pypi_payload(final_path: pathlib.Path) -> dict[str, object]:
    final = read_json(final_path)
    return {
        "info": {"name": "Shapely", "version": "2.1.2"},
        "urls": [
            {
                "filename": artifact["filename"],
                "digests": {"sha256": artifact["sha256"]},
            }
            for artifact in final["artifacts"]
        ],
    }


def test_pypi_check_succeeds_for_exact_finalized_wheels(tmp_path, monkeypatch, capsys):
    _, final_path = build_round_trip(tmp_path, monkeypatch)
    monkeypatch.setattr(
        we.urllib.request,
        "urlopen",
        lambda url, timeout: fake_pypi_response(pypi_payload(final_path)),
    )

    we.pypi_check(str(final_path), "shapely", "2.1.2")

    assert "verified 7 PyPI ARM64 wheels for shapely 2.1.2" in capsys.readouterr().out


def test_verify_pypi_release_rejects_extra_arm64_wheel(tmp_path, monkeypatch):
    _, final_path = build_round_trip(tmp_path, monkeypatch)
    final = read_json(final_path)
    payload = pypi_payload(final_path)
    payload["urls"].append(
        {
            "filename": "shapely-2.1.2-cp310-cp310-win_arm64.whl",
            "digests": {"sha256": "a" * 64},
        }
    )

    with pytest.raises(ValueError, match=r"extra=.*cp310"):
        we.verify_pypi_release(final, payload, we.Version("2.1.2"))


def test_verify_pypi_release_ignores_non_arm64_files(tmp_path, monkeypatch):
    _, final_path = build_round_trip(tmp_path, monkeypatch)
    final = read_json(final_path)
    payload = pypi_payload(final_path)
    payload["urls"].extend(
        [
            {
                "filename": "shapely-2.1.2-cp311-cp311-manylinux_2_17_x86_64.whl",
                "digests": {"sha256": "a" * 64},
            },
            {
                "filename": "shapely-2.1.2.tar.gz",
                "digests": {"sha256": "b" * 64},
            },
        ]
    )

    we.verify_pypi_release(final, payload, we.Version("2.1.2"))


def test_verify_pypi_release_rejects_wrong_version_wheel(tmp_path, monkeypatch):
    _, final_path = build_round_trip(tmp_path, monkeypatch)
    final = read_json(final_path)
    payload = pypi_payload(final_path)
    payload["urls"].append(
        {
            "filename": "shapely-2.1.1-cp311-cp311-win_arm64.whl",
            "digests": {"sha256": "a" * 64},
        }
    )

    with pytest.raises(ValueError, match="wrong-version PyPI distribution"):
        we.verify_pypi_release(final, payload, we.Version("2.1.2"))


def test_verify_pypi_release_rejects_duplicate_filename(tmp_path, monkeypatch):
    _, final_path = build_round_trip(tmp_path, monkeypatch)
    final = read_json(final_path)
    payload = pypi_payload(final_path)
    payload["urls"].append(dict(payload["urls"][0]))

    with pytest.raises(ValueError, match="duplicate PyPI filename"):
        we.verify_pypi_release(final, payload, we.Version("2.1.2"))


def test_pypi_check_retries_until_index_contains_finalized_wheels(
    tmp_path, monkeypatch
):
    _, final_path = build_round_trip(tmp_path, monkeypatch)
    responses = [
        {"info": {"name": "shapely", "version": "2.1.2"}, "urls": []},
        pypi_payload(final_path),
    ]
    sleeps = []

    def fake_urlopen(url, timeout):
        return fake_pypi_response(responses.pop(0))

    monkeypatch.setattr(we.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(we.time, "sleep", sleeps.append)

    we.pypi_check(
        str(final_path),
        "shapely",
        "2.1.2",
        attempts=3,
        retry_delay_seconds=0.25,
    )

    assert responses == []
    assert sleeps == [0.25]


def test_pypi_check_fails_after_bounded_retries(tmp_path, monkeypatch):
    _, final_path = build_round_trip(tmp_path, monkeypatch)
    calls = []
    sleeps = []

    def fake_urlopen(url, timeout):
        calls.append((url, timeout))
        return fake_pypi_response(
            {"info": {"name": "shapely", "version": "2.1.2"}, "urls": []}
        )

    monkeypatch.setattr(we.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(we.time, "sleep", sleeps.append)

    with pytest.raises(ValueError, match="PyPI verification failed after 3 attempts"):
        we.pypi_check(
            str(final_path),
            "shapely",
            "2.1.2",
            attempts=3,
            retry_delay_seconds=0.25,
        )

    assert len(calls) == 3
    assert sleeps == [0.25, 0.25]


def create_release_distributions(
    directory: pathlib.Path,
    arm64_wheels: list[pathlib.Path] | None = None,
) -> dict[str, pathlib.Path]:
    distributions = {}
    for filename, contents in [
        ("shapely-2.1.2.tar.gz", b"sdist"),
        (
            "shapely-2.1.2-cp311-cp311-manylinux_2_17_x86_64.whl",
            b"linux-wheel",
        ),
        (
            "shapely-2.1.2-cp311-cp311-macosx_11_0_arm64.whl",
            b"macos-wheel",
        ),
        ("shapely-2.1.2-cp311-cp311-win_amd64.whl", b"win-amd64-wheel"),
    ]:
        path = directory / filename
        path.write_bytes(contents)
        distributions[filename] = path
    if arm64_wheels is None:
        arm64_wheels = []
        path = directory / "shapely-2.1.2-cp311-cp311-win_arm64.whl"
        path.write_bytes(b"arm64-wheel")
        distributions[path.name] = path
    for source in arm64_wheels:
        path = directory / source.name
        shutil.copy2(source, path)
        distributions[path.name] = path
    return distributions


def release_payload_for_files(paths: list[pathlib.Path]) -> dict[str, object]:
    return {
        "info": {"name": "Shapely", "version": "2.1.2"},
        "urls": [
            {
                "filename": path.name,
                "digests": {"sha256": we.sha256(path)},
            }
            for path in paths
        ],
    }


def stage_args(
    wheelhouse: pathlib.Path,
    staging: pathlib.Path,
    manifest: pathlib.Path,
    github_output: pathlib.Path | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        wheelhouse=str(wheelhouse),
        staging_dir=str(staging),
        manifest=str(manifest),
        distribution="shapely",
        version="2.1.2",
        attempts=3,
        retry_delay_seconds=0,
        github_output=None if github_output is None else str(github_output),
    )


def create_provenance_bound_release(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[pathlib.Path, pathlib.Path, dict[str, pathlib.Path]]:
    _, final_path = build_round_trip(tmp_path, monkeypatch)
    wheelhouse = tmp_path / "dist"
    wheelhouse.mkdir()
    distributions = create_release_distributions(
        wheelhouse,
        sorted((tmp_path / "wheelhouse").glob("*.whl")),
    )
    return wheelhouse, final_path, distributions


def test_stage_pypi_404_stages_sdist_and_all_platform_wheels(
    tmp_path, monkeypatch, capsys
):
    staging = tmp_path / "pypi-upload"
    output = tmp_path / "github-output"
    wheelhouse, final_path, distributions = create_provenance_bound_release(
        tmp_path, monkeypatch
    )
    monkeypatch.setattr(we, "load_pypi_release", lambda *args, **kwargs: None)

    assert (
        we.main(
            [
                "stage-pypi",
                "--wheelhouse",
                str(wheelhouse),
                "--staging-dir",
                str(staging),
                "--manifest",
                str(final_path),
                "--distribution",
                "shapely",
                "--version",
                "2.1.2",
                "--attempts",
                "1",
                "--github-output",
                str(output),
            ]
        )
        == 0
    )
    assert {path.name for path in staging.iterdir()} == set(distributions)
    assert output.read_text(encoding="utf-8").splitlines() == [
        "has_files=true",
        f"staged_count={len(distributions)}",
    ]
    assert (
        f"verified 0 existing and staged {len(distributions)} missing"
        in capsys.readouterr().out
    )


def test_load_pypi_release_treats_404_as_new_version(monkeypatch):
    def not_found(url, timeout):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(we.urllib.request, "urlopen", not_found)

    assert (
        we.load_pypi_release("shapely", we.Version("2.1.2"), allow_not_found=True)
        is None
    )


def test_stage_pypi_published_subset_recovery_stages_only_missing_and_cleans_stale(
    tmp_path, monkeypatch
):
    staging = tmp_path / "pypi-upload"
    wheelhouse, final_path, distributions = create_provenance_bound_release(
        tmp_path, monkeypatch
    )
    staging.mkdir()
    (staging / "stale.whl").write_bytes(b"stale")
    existing_arm64 = next(
        path for name, path in distributions.items() if "win_arm64" in name
    )
    existing = [
        distributions["shapely-2.1.2.tar.gz"],
        distributions[
            "shapely-2.1.2-cp311-cp311-manylinux_2_17_x86_64.whl"
        ],
        existing_arm64,
    ]
    monkeypatch.setattr(
        we,
        "load_pypi_release",
        lambda *args, **kwargs: release_payload_for_files(existing),
    )

    we.stage_pypi_command(stage_args(wheelhouse, staging, final_path))

    assert {path.name for path in staging.iterdir()} == set(distributions) - {
        path.name for path in existing
    }


def test_stage_pypi_rejects_extra_published_arm64_before_staging(
    tmp_path, monkeypatch
):
    staging = tmp_path / "pypi-upload"
    wheelhouse, final_path, _ = create_provenance_bound_release(
        tmp_path, monkeypatch
    )
    extra = tmp_path / "shapely-2.1.2-cp310-cp310-win_arm64.whl"
    extra.write_bytes(b"unexpected-arm64")
    monkeypatch.setattr(
        we,
        "load_pypi_release",
        lambda *args, **kwargs: release_payload_for_files([extra]),
    )

    def forbid_copy(*args, **kwargs):
        raise AssertionError("preflight must fail before staging")

    monkeypatch.setattr(we.shutil, "copy2", forbid_copy)

    with pytest.raises(
        ValueError,
        match="not a digest-matching finalized provenance subset",
    ):
        we.stage_pypi_command(stage_args(wheelhouse, staging, final_path))

    assert list(staging.iterdir()) == []


@pytest.mark.parametrize("difference", ["missing", "mismatched", "extra"])
def test_stage_pypi_requires_local_arm64_exactly_match_final_provenance(
    tmp_path, monkeypatch, difference
):
    staging = tmp_path / "pypi-upload"
    wheelhouse, final_path, distributions = create_provenance_bound_release(
        tmp_path, monkeypatch
    )
    arm64 = sorted(
        path for name, path in distributions.items() if "win_arm64" in name
    )
    if difference == "missing":
        arm64[0].unlink()
    elif difference == "mismatched":
        arm64[0].write_bytes(b"different-local-wheel")
    else:
        (wheelhouse / "shapely-2.1.2-cp310-cp310-win_arm64.whl").write_bytes(
            b"unexpected-local-wheel"
        )

    def forbid_metadata(*args, **kwargs):
        raise AssertionError("local/provenance preflight must precede PyPI lookup")

    monkeypatch.setattr(we, "load_pypi_release", forbid_metadata)

    with pytest.raises(
        ValueError,
        match="local/finalized provenance ARM64 wheel map mismatch",
    ):
        we.stage_pypi_command(stage_args(wheelhouse, staging, final_path))

    assert list(staging.iterdir()) == []


def test_stage_pypi_rejects_existing_digest_mismatch_and_leaves_clean_staging(
    tmp_path, monkeypatch
):
    staging = tmp_path / "pypi-upload"
    wheelhouse, final_path, distributions = create_provenance_bound_release(
        tmp_path, monkeypatch
    )
    staging.mkdir()
    (staging / "stale.whl").write_bytes(b"stale")
    payload = release_payload_for_files([distributions["shapely-2.1.2.tar.gz"]])
    payload["urls"][0]["digests"]["sha256"] = "0" * 64
    monkeypatch.setattr(
        we, "load_pypi_release", lambda *args, **kwargs: payload
    )

    with pytest.raises(ValueError, match="immutable PyPI file has different SHA-256"):
        we.stage_pypi_command(stage_args(wheelhouse, staging, final_path))

    assert list(staging.iterdir()) == []


def test_stage_pypi_all_existing_skips_upload(tmp_path, monkeypatch):
    staging = tmp_path / "pypi-upload"
    output = tmp_path / "github-output"
    wheelhouse, final_path, distributions = create_provenance_bound_release(
        tmp_path, monkeypatch
    )
    monkeypatch.setattr(
        we,
        "load_pypi_release",
        lambda *args, **kwargs: release_payload_for_files(
            list(distributions.values())
        ),
    )

    we.stage_pypi_command(stage_args(wheelhouse, staging, final_path, output))

    assert list(staging.iterdir()) == []
    assert output.read_text(encoding="utf-8").splitlines() == [
        "has_files=false",
        "staged_count=0",
    ]


def test_stage_pypi_network_failure_is_bounded_and_cleans_staging(
    tmp_path, monkeypatch
):
    staging = tmp_path / "pypi-upload"
    wheelhouse, final_path, _ = create_provenance_bound_release(
        tmp_path, monkeypatch
    )
    staging.mkdir()
    (staging / "stale.whl").write_bytes(b"stale")
    calls = []

    def unavailable(*args, **kwargs):
        calls.append(None)
        raise OSError("network unavailable")

    monkeypatch.setattr(we, "load_pypi_release", unavailable)
    monkeypatch.setattr(we.time, "sleep", lambda delay: None)

    with pytest.raises(ValueError, match="PyPI metadata failed after 3 attempts"):
        we.stage_pypi_command(stage_args(wheelhouse, staging, final_path))

    assert len(calls) == 3
    assert list(staging.iterdir()) == []


def test_stage_pypi_copy_failure_removes_partially_staged_files(
    tmp_path, monkeypatch
):
    staging = tmp_path / "pypi-upload"
    wheelhouse, final_path, _ = create_provenance_bound_release(
        tmp_path, monkeypatch
    )
    monkeypatch.setattr(we, "load_pypi_release", lambda *args, **kwargs: None)
    real_copy2 = we.shutil.copy2
    calls = []

    def fail_second_copy(source, destination):
        calls.append(source)
        if len(calls) == 2:
            raise OSError("copy failed")
        return real_copy2(source, destination)

    monkeypatch.setattr(we.shutil, "copy2", fail_second_copy)

    with pytest.raises(OSError, match="copy failed"):
        we.stage_pypi_command(stage_args(wheelhouse, staging, final_path))

    assert list(staging.iterdir()) == []


def test_stage_pypi_rejects_overlapping_staging_without_deleting_wheelhouse(
    tmp_path, monkeypatch
):
    wheelhouse, final_path, distributions = create_provenance_bound_release(
        tmp_path, monkeypatch
    )

    with pytest.raises(ValueError, match="must not overlap wheelhouse"):
        we.stage_pypi_command(
            stage_args(wheelhouse, wheelhouse / "staging", final_path)
        )

    assert {path.name for path in wheelhouse.iterdir()} == set(distributions)


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
