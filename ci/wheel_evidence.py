from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import platform
import re
import struct
import subprocess
import sys
import sysconfig
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from importlib.metadata import version as package_version

from packaging.tags import Tag, parse_tag
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

NAME = canonicalize_name("shapely")
HEX = re.compile(r"^[0-9a-f]{64}$")
UTC = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
JOB_KEYS = {"repository", "run_id", "job_key", "run_url", "commit_sha", "ref"}
SOURCE_ARTIFACT = {"name": "release-windows-11-arm-ARM64", "retention_days": 30}
CORE_KEYS = {
    "filename",
    "distribution",
    "version",
    "sha256",
    "size_bytes",
    "filename_tags",
    "wheel_tags",
    "runtime",
}
RUNTIME_KEYS = {
    "label",
    "setup_selector",
    "numeric_version",
    "free_threaded",
    "interpreter",
    "abi",
}
PRODUCER_METADATA_KEYS = {"created_at_utc", "job", "runner", "toolchain"}
VERIFIED_ARTIFACT_KEYS = CORE_KEYS | {
    "pe_machines",
    "license_members",
    "repair_report",
    "repair_report_sha256",
    "checks",
}
PRODUCER_KEYS = {
    "schema_version",
    "record_type",
    "created_at_utc",
    "job",
    "source_artifact",
    "producer",
    "geos",
    "artifacts",
}
VERIFIER_KEYS = {
    "schema_version",
    "record_type",
    "created_at_utc",
    "producer_sha256",
    "job",
    "runtime",
    "verifier",
    "artifacts",
}
FINAL_KEYS = {
    "schema_version",
    "record_type",
    "created_at_utc",
    "finalizer_job",
    "producer_sha256",
    "source_artifact",
    "producer",
    "geos",
    "verifiers",
    "artifacts",
}
CHECK_KEYS = {
    "wheel_tag",
    "pe_architecture",
    "license_contents",
    "delvewheel_repair",
    "clean_external_cwd_tests",
}
RUNTIMES = {
    ("cp311", "cp311"): {
        "label": "cp311",
        "setup_selector": "3.11",
        "numeric_version": "3.11",
        "free_threaded": False,
        "interpreter": "cp311",
        "abi": "cp311",
    },
    ("cp312", "cp312"): {
        "label": "cp312",
        "setup_selector": "3.12",
        "numeric_version": "3.12",
        "free_threaded": False,
        "interpreter": "cp312",
        "abi": "cp312",
    },
    ("cp313", "cp313"): {
        "label": "cp313",
        "setup_selector": "3.13",
        "numeric_version": "3.13",
        "free_threaded": False,
        "interpreter": "cp313",
        "abi": "cp313",
    },
    ("cp314", "cp314"): {
        "label": "cp314",
        "setup_selector": "3.14",
        "numeric_version": "3.14",
        "free_threaded": False,
        "interpreter": "cp314",
        "abi": "cp314",
    },
    ("cp314", "cp314t"): {
        "label": "cp314t",
        "setup_selector": "3.14t",
        "numeric_version": "3.14",
        "free_threaded": True,
        "interpreter": "cp314",
        "abi": "cp314t",
    },
    ("cp315", "cp315"): {
        "label": "cp315",
        "setup_selector": "3.15",
        "numeric_version": "3.15",
        "free_threaded": False,
        "interpreter": "cp315",
        "abi": "cp315",
    },
    ("cp315", "cp315t"): {
        "label": "cp315t",
        "setup_selector": "3.15t",
        "numeric_version": "3.15",
        "free_threaded": True,
        "interpreter": "cp315",
        "abi": "cp315t",
    },
}
EXPECTED_LABELS = {value["label"] for value in RUNTIMES.values()}


def fail(message: str) -> None:
    raise ValueError(message)


def ensure_exact_keys(value: object, expected: set[str], where: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        fail(f"{where}: exact keys required")


def require_text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        fail(f"{where}: non-empty string required")
    return value


def require_bool(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        fail(f"{where}: boolean required")
    return value


def require_int(value: object, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        fail(f"{where}: integer required")
    return value


def validate_utc(value: object, where: str) -> str:
    text = require_text(value, where)
    if not UTC.fullmatch(text):
        fail(f"{where}: RFC-3339 UTC required")
    if datetime.fromisoformat(text[:-1] + "+00:00").utcoffset() != timedelta(0):
        fail(f"{where}: UTC offset required")
    return text


def now_utc() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def sha256(path: os.PathLike[str] | str) -> str:
    with open(path, "rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def document(path: os.PathLike[str] | str) -> object:
    with open(path, encoding="utf-8") as source:
        return json.load(source)


def canonical_write(path: os.PathLike[str] | str, value: object) -> None:
    destination = pathlib.Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=destination.name + ".",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out:
            out.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp_name, destination)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def environment(name: str) -> str:
    return require_text(os.environ.get(name, ""), f"environment.{name}")


def command_version(*command: str) -> str:
    lines = subprocess.check_output(command, text=True).splitlines()
    if not lines:
        fail("tool version: command returned no output")
    return require_text(lines[0].strip(), "tool version")


def require_hex64(value: object, where: str) -> str:
    text = require_text(value, where)
    if not HEX.fullmatch(text):
        fail(f"{where}: lower-case SHA-256 required")
    return text


def validate_job(value: object, where: str) -> dict[str, str]:
    ensure_exact_keys(value, JOB_KEYS, where)
    job = value
    repository = require_text(job["repository"], f"{where}.repository")
    if not re.fullmatch(r"[^/\s]+/shapely", repository):
        fail(f"{where}.repository: owner/shapely required")
    run_id = require_text(job["run_id"], f"{where}.run_id")
    if not run_id.isdigit():
        fail(f"{where}.run_id: decimal string required")
    require_text(job["job_key"], f"{where}.job_key")
    run_url = require_text(job["run_url"], f"{where}.run_url")
    if not run_url.startswith("https://"):
        fail(f"{where}.run_url: HTTPS URL required")
    commit_sha = require_text(job["commit_sha"], f"{where}.commit_sha")
    if not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
        fail(f"{where}.commit_sha: 40 lowercase hex required")
    require_text(job["ref"], f"{where}.ref")
    return job


def validate_source_artifact(value: object, where: str = "source_artifact") -> dict[str, object]:
    ensure_exact_keys(value, {"name", "retention_days"}, where)
    if value != SOURCE_ARTIFACT:
        fail(f"{where}: unexpected source artifact contract")
    return value


def validate_runner(value: object, where: str) -> dict[str, str]:
    ensure_exact_keys(value, {"os", "architecture", "image_os", "image_version"}, where)
    runner = value
    if runner["os"] != "Windows" or runner["architecture"] != "ARM64":
        fail(f"{where}: Windows ARM64 runner required")
    require_text(runner["image_os"], f"{where}.image_os")
    require_text(runner["image_version"], f"{where}.image_version")
    return runner


def validate_toolchain(value: object, where: str) -> dict[str, str]:
    ensure_exact_keys(value, {"cibuildwheel_action", "msvc_toolset", "cmake", "ninja"}, where)
    toolchain = value
    if toolchain["cibuildwheel_action"] != "pypa/cibuildwheel@v4.2.0":
        fail(f"{where}.cibuildwheel_action: unexpected value")
    if toolchain["msvc_toolset"] != "14.44":
        fail(f"{where}.msvc_toolset: unexpected value")
    require_text(toolchain["cmake"], f"{where}.cmake")
    require_text(toolchain["ninja"], f"{where}.ninja")
    return toolchain


def validate_geos(value: object, where: str = "geos") -> dict[str, str]:
    ensure_exact_keys(value, {"version", "archive_url", "sha256", "checksum_provenance_url"}, where)
    geos = value
    version = require_text(geos["version"], f"{where}.version")
    archive_url = require_text(geos["archive_url"], f"{where}.archive_url")
    if archive_url != f"https://download.osgeo.org/geos/geos-{version}.tar.bz2":
        fail(f"{where}.archive_url: unexpected GEOS archive URL")
    require_hex64(geos["sha256"], f"{where}.sha256")
    provenance = require_text(geos["checksum_provenance_url"], f"{where}.checksum_provenance_url")
    if not provenance.startswith("https://"):
        fail(f"{where}.checksum_provenance_url: HTTPS URL required")
    return geos


def runtime_for_tag(tag: Tag, where: str) -> dict[str, object]:
    runtime = RUNTIMES.get((tag.interpreter, tag.abi))
    if tag.platform != "win_arm64" or runtime is None:
        fail(f"{where}: unsupported ARM64 CPython ABI")
    return dict(runtime)


def runtime_for_label(label: str) -> dict[str, object]:
    for runtime in RUNTIMES.values():
        if runtime["label"] == label:
            return dict(runtime)
    fail(f"unknown verifier label: {label}")


def parse_single_tag_string(value: object, where: str) -> Tag:
    if not isinstance(value, list) or len(value) != 1:
        fail(f"{where}: exactly one tag required")
    raw = require_text(value[0], f"{where}[0]")
    try:
        parsed = parse_tag(raw)
    except ValueError as error:
        fail(f"{where}: malformed tag: {error}")
    if len(parsed) != 1:
        fail(f"{where}: compressed/ambiguous tag forbidden")
    return next(iter(parsed))


def validate_runtime(value: object, where: str) -> dict[str, object]:
    ensure_exact_keys(value, RUNTIME_KEYS, where)
    tag = Tag(
        require_text(value["interpreter"], f"{where}.interpreter"),
        require_text(value["abi"], f"{where}.abi"),
        "win_arm64",
    )
    expected = runtime_for_tag(tag, where)
    if value != expected:
        fail(f"{where}: runtime mapping mismatch")
    require_bool(value["free_threaded"], f"{where}.free_threaded")
    return value


def validate_core(value: object, where: str) -> dict[str, object]:
    ensure_exact_keys(value, CORE_KEYS, where)
    core = value
    filename = require_text(core["filename"], f"{where}.filename")
    distribution = require_text(core["distribution"], f"{where}.distribution")
    version = require_text(core["version"], f"{where}.version")
    require_hex64(core["sha256"], f"{where}.sha256")
    size_bytes = require_int(core["size_bytes"], f"{where}.size_bytes")
    if size_bytes <= 0:
        fail(f"{where}.size_bytes: positive integer required")
    filename_tag = parse_single_tag_string(core["filename_tags"], f"{where}.filename_tags")
    wheel_tag = parse_single_tag_string(core["wheel_tags"], f"{where}.wheel_tags")
    if filename_tag != wheel_tag:
        fail(f"{where}: filename/WHEEL tag-set mismatch")
    try:
        got_name, got_version, _, filename_tags = parse_wheel_filename(filename)
    except (TypeError, ValueError) as error:
        fail(f"{where}.filename: invalid wheel filename: {error}")
    if distribution != NAME or got_name != NAME:
        fail(f"{where}.distribution: canonical shapely name required")
    if str(got_version) != version:
        fail(f"{where}.version: filename/version mismatch")
    if filename_tags != frozenset({filename_tag}):
        fail(f"{where}: filename tag mismatch")
    if core["runtime"] != runtime_for_tag(filename_tag, where):
        fail(f"{where}.runtime: runtime mapping mismatch")
    Version(version)
    validate_runtime(core["runtime"], f"{where}.runtime")
    return core


def validate_producer_environment(value: object) -> dict[str, object]:
    ensure_exact_keys(value, {"runner", "toolchain"}, "producer")
    validate_runner(value["runner"], "producer.runner")
    validate_toolchain(value["toolchain"], "producer.toolchain")
    return value


def validate_verifier_environment(runtime: dict[str, object], value: object) -> dict[str, object]:
    ensure_exact_keys(
        value,
        {"runner", "python_executable", "python_version", "free_threaded", "delvewheel_version"},
        "verifier",
    )
    validate_runner(value["runner"], "verifier.runner")
    python_executable = pathlib.Path(require_text(value["python_executable"], "verifier.python_executable"))
    if not python_executable.is_absolute() or python_executable.suffix.lower() != ".exe":
        fail("verifier.python_executable: absolute *.exe path required")
    python_version = require_text(value["python_version"], "verifier.python_version")
    if python_version != runtime["numeric_version"]:
        fail("verifier.python_version: runtime mismatch")
    free_threaded = require_bool(value["free_threaded"], "verifier.free_threaded")
    if free_threaded != runtime["free_threaded"]:
        fail("verifier.free_threaded: runtime mismatch")
    require_text(value["delvewheel_version"], "verifier.delvewheel_version")
    return value


def normalize_relative_report_path(value: object, where: str = "repair_report") -> str:
    text = require_text(value, where)
    if text.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", text):
        fail(f"{where}: relative path required")
    pure = pathlib.PurePosixPath(text.replace("\\", "/"))
    parts = []
    for part in pure.parts:
        if part in ("", "."):
            continue
        if part == "..":
            fail(f"{where}: parent traversal forbidden")
        if ":" in part:
            fail(f"{where}: relative path required")
        parts.append(part)
    if not parts:
        fail(f"{where}: relative path required")
    return pathlib.PurePosixPath(*parts).as_posix()


def require_artifact_label_coverage(
    artifacts: list[dict[str, object]],
    expected_labels: set[str],
    where: str,
) -> set[str]:
    labels = {artifact["runtime"]["label"] for artifact in artifacts}
    if labels != expected_labels:
        fail(f"{where}: exact ABI label coverage required")
    return labels


def validate_verified_artifact(value: object, where: str, runtime: dict[str, object] | None = None) -> dict[str, object]:
    ensure_exact_keys(value, VERIFIED_ARTIFACT_KEYS, where)
    artifact = value
    validate_core({key: artifact[key] for key in CORE_KEYS}, where)
    if runtime is not None and artifact["runtime"] != runtime:
        fail(f"{where}.runtime: verifier/runtime mismatch")
    if not isinstance(artifact["pe_machines"], dict) or not artifact["pe_machines"]:
        fail(f"{where}.pe_machines: non-empty mapping required")
    for member, machine in artifact["pe_machines"].items():
        require_text(member, f"{where}.pe_machines.key")
        if require_text(machine, f"{where}.pe_machines[{member}]") != "0xAA64":
            fail(f"{where}.pe_machines[{member}]: ARM64 0xAA64 required")
    if not isinstance(artifact["license_members"], dict):
        fail(f"{where}.license_members: mapping required")
    if set(artifact["license_members"]) != {"LICENSE_GEOS", "LICENSE_win32"}:
        fail(f"{where}.license_members: exact keys required")
    for key, path in artifact["license_members"].items():
        require_text(key, f"{where}.license_members.key")
        require_text(path, f"{where}.license_members.{key}")
    artifact["repair_report"] = normalize_relative_report_path(
        artifact["repair_report"],
        f"{where}.repair_report",
    )
    require_hex64(artifact["repair_report_sha256"], f"{where}.repair_report_sha256")
    ensure_exact_keys(artifact["checks"], CHECK_KEYS, f"{where}.checks")
    for key, code in artifact["checks"].items():
        if require_int(code, f"{where}.checks.{key}") != 0:
            fail(f"{where}.checks.{key}: expected zero exit")
    return artifact


def validate_producer(value: object) -> dict[str, object]:
    ensure_exact_keys(value, PRODUCER_KEYS, "producer")
    producer = value
    if require_int(producer["schema_version"], "producer.schema_version") != 1:
        fail("producer.schema_version: version 1 required")
    if producer["record_type"] != "producer":
        fail("producer.record_type: producer required")
    validate_utc(producer["created_at_utc"], "producer.created_at_utc")
    validate_job(producer["job"], "producer.job")
    validate_source_artifact(producer["source_artifact"])
    validate_producer_environment(producer["producer"])
    validate_geos(producer["geos"])
    artifacts = producer["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        fail("producer.artifacts: non-empty list required")
    seen = set()
    for index, artifact in enumerate(artifacts):
        validate_core(artifact, f"producer.artifacts[{index}]")
        key = (artifact["filename"], artifact["sha256"])
        if key in seen:
            fail("producer.artifacts: duplicate wheel forbidden")
        seen.add(key)
    return producer


def validate_verifier(value: object) -> dict[str, object]:
    ensure_exact_keys(value, VERIFIER_KEYS, "verifier")
    verifier = value
    if require_int(verifier["schema_version"], "verifier.schema_version") != 1:
        fail("verifier.schema_version: version 1 required")
    if verifier["record_type"] != "verifier":
        fail("verifier.record_type: verifier required")
    validate_utc(verifier["created_at_utc"], "verifier.created_at_utc")
    require_hex64(verifier["producer_sha256"], "verifier.producer_sha256")
    validate_job(verifier["job"], "verifier.job")
    runtime = validate_runtime(verifier["runtime"], "verifier.runtime")
    validate_verifier_environment(runtime, verifier["verifier"])
    artifacts = verifier["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        fail("verifier.artifacts: non-empty list required")
    seen = set()
    for index, artifact in enumerate(artifacts):
        validate_verified_artifact(artifact, f"verifier.artifacts[{index}]", runtime)
        key = (artifact["filename"], artifact["sha256"])
        if key in seen:
            fail("verifier.artifacts: duplicate wheel forbidden")
        seen.add(key)
    require_artifact_label_coverage(artifacts, {runtime["label"]}, "verifier.artifacts")
    return verifier


def producer_metadata(value: dict[str, object]) -> dict[str, object]:
    return {
        "created_at_utc": value["created_at_utc"],
        "job": value["job"],
        "runner": value["producer"]["runner"],
        "toolchain": value["producer"]["toolchain"],
    }


def validate_producer_metadata(value: object, where: str = "final.producer") -> dict[str, object]:
    ensure_exact_keys(value, PRODUCER_METADATA_KEYS, where)
    validate_utc(value["created_at_utc"], f"{where}.created_at_utc")
    validate_job(value["job"], f"{where}.job")
    validate_runner(value["runner"], f"{where}.runner")
    validate_toolchain(value["toolchain"], f"{where}.toolchain")
    return value


def same_job_identity(first: dict[str, str], second: dict[str, str], where: str) -> None:
    for key in ("repository", "run_id", "run_url", "commit_sha", "ref"):
        if first[key] != second[key]:
            fail(f"{where}: mismatched job identity for {key}")


def validate_final(value: object) -> dict[str, object]:
    ensure_exact_keys(value, FINAL_KEYS, "final")
    final = value
    if require_int(final["schema_version"], "final.schema_version") != 1:
        fail("final.schema_version: version 1 required")
    if final["record_type"] != "final":
        fail("final.record_type: final required")
    validate_utc(final["created_at_utc"], "final.created_at_utc")
    finalizer_job = validate_job(final["finalizer_job"], "final.finalizer_job")
    require_hex64(final["producer_sha256"], "final.producer_sha256")
    validate_source_artifact(final["source_artifact"])
    producer = validate_producer_metadata(final["producer"])
    validate_geos(final["geos"])
    same_job_identity(finalizer_job, producer["job"], "final.finalizer_job")

    artifacts = final["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        fail("final.artifacts: non-empty list required")
    seen = set()
    for index, artifact in enumerate(artifacts):
        validate_verified_artifact(artifact, f"final.artifacts[{index}]")
        key = (artifact["filename"], artifact["sha256"])
        if key in seen:
            fail("final.artifacts: duplicate wheel forbidden")
        seen.add(key)
    require_artifact_label_coverage(artifacts, EXPECTED_LABELS, "final.artifacts")

    synthetic_producer = {
        "schema_version": 1,
        "record_type": "producer",
        "created_at_utc": producer["created_at_utc"],
        "job": producer["job"],
        "source_artifact": final["source_artifact"],
        "producer": {
            "runner": producer["runner"],
            "toolchain": producer["toolchain"],
        },
        "geos": final["geos"],
        "artifacts": [{key: item[key] for key in CORE_KEYS} for item in artifacts],
    }
    validate_producer(synthetic_producer)

    verifiers = final["verifiers"]
    if not isinstance(verifiers, list) or len(verifiers) != len(EXPECTED_LABELS):
        fail("final.verifiers: seven verifier rows required")
    labels = set()
    for index, verifier in enumerate(verifiers):
        ensure_exact_keys(verifier, {"label", "sha256", "created_at_utc", "job"}, f"final.verifiers[{index}]")
        label = require_text(verifier["label"], f"final.verifiers[{index}].label")
        if label not in EXPECTED_LABELS or label in labels:
            fail("final.verifiers: unique known labels required")
        labels.add(label)
        require_hex64(verifier["sha256"], f"final.verifiers[{index}].sha256")
        validate_utc(verifier["created_at_utc"], f"final.verifiers[{index}].created_at_utc")
        same_job_identity(
            validate_job(verifier["job"], f"final.verifiers[{index}].job"),
            producer["job"],
            f"final.verifiers[{index}].job",
        )
    if labels != EXPECTED_LABELS:
        fail("final.verifiers: incomplete label set")
    return final


def wheel_core(path: os.PathLike[str] | str) -> dict[str, object]:
    wheel_path = pathlib.Path(path)
    distribution, parsed_version, _, filename_tags = parse_wheel_filename(wheel_path.name)
    if distribution != NAME or len(filename_tags) != 1:
        fail(f"{wheel_path}: ambiguous or non-Shapely filename tag")
    filename_tag = next(iter(filename_tags))
    runtime = runtime_for_tag(filename_tag, str(wheel_path))

    with zipfile.ZipFile(wheel_path) as wheel:
        members = wheel.namelist()
        wheel_members = [member for member in members if member.endswith(".dist-info/WHEEL")]
        if len(wheel_members) != 1:
            fail(f"{wheel_path}: exactly one WHEEL file required")
        raw_tags = [
            line[5:]
            for line in wheel.read(wheel_members[0]).decode("utf-8").splitlines()
            if line.startswith("Tag: ")
        ]
        if len(raw_tags) != 1:
            fail(f"{wheel_path}: exactly one WHEEL Tag entry required")
        try:
            parsed = parse_tag(raw_tags[0])
        except ValueError as error:
            fail(f"{wheel_path}: malformed WHEEL tag: {error}")
        if len(parsed) != 1:
            fail(f"{wheel_path}: compressed/ambiguous WHEEL tag forbidden")
        wheel_tag = next(iter(parsed))
        if filename_tag != wheel_tag:
            fail(f"{wheel_path}: filename/WHEEL tag-set mismatch")
        if wheel_tag.platform != "win_arm64":
            fail(f"{wheel_path}: non-ARM64 WHEEL tag")

    core = {
        "filename": wheel_path.name,
        "distribution": distribution,
        "version": str(parsed_version),
        "sha256": sha256(wheel_path),
        "size_bytes": wheel_path.stat().st_size,
        "filename_tags": [str(filename_tag)],
        "wheel_tags": [str(wheel_tag)],
        "runtime": runtime,
    }
    validate_core(core, str(wheel_path))
    return core


def pe_machines(path: os.PathLike[str] | str) -> dict[str, str]:
    wheel_path = pathlib.Path(path)
    found: dict[str, str] = {}
    with zipfile.ZipFile(wheel_path) as wheel:
        native_members = [
            member
            for member in wheel.namelist()
            if member.lower().endswith((".pyd", ".dll"))
        ]
        if not native_members:
            fail(f"{wheel_path}: no native members found")
        for member in native_members:
            data = wheel.read(member)
            if len(data) < 0x40 or data[:2] != b"MZ":
                fail(f"{member}: invalid DOS header")
            offset = struct.unpack_from("<I", data, 0x3C)[0]
            if offset + 6 > len(data) or data[offset : offset + 4] != b"PE\0\0":
                fail(f"{member}: invalid PE header")
            machine = struct.unpack_from("<H", data, offset + 4)[0]
            if machine != 0xAA64:
                fail(f"{member}: PE machine 0x{machine:04X}, expected 0xAA64")
            found[member] = "0xAA64"
    return dict(sorted(found.items()))


def wheel_licenses(path: os.PathLike[str] | str, source_root: os.PathLike[str] | str) -> dict[str, str]:
    root = pathlib.Path(source_root)
    expected = {
        name: (root / "ci" / "wheelbuilder" / name).read_bytes()
        for name in ("LICENSE_GEOS", "LICENSE_win32")
    }
    result: dict[str, str] = {}
    with zipfile.ZipFile(path) as wheel:
        names = wheel.namelist()
        for name, content in expected.items():
            matches = [
                member
                for member in names
                if pathlib.PurePosixPath(member).name == name
            ]
            if len(matches) != 1:
                fail(f"{path}: {name} missing or duplicated")
            if wheel.read(matches[0]) != content:
                fail(f"{path}: {name} contents differ from source")
            result[name] = matches[0]
    return result


def repaired_wheel_members(path: os.PathLike[str] | str) -> tuple[bool, bool, bool]:
    with zipfile.ZipFile(path) as wheel:
        names = wheel.namelist()
    has_delvewheel_marker = any(name.endswith(".dist-info/DELVEWHEEL") for name in names)
    has_shapely_libs = any(name.startswith("shapely.libs/") for name in names)
    has_geos_dll = any(
        name.startswith("shapely.libs/")
        and name.lower().endswith(".dll")
        and "geos" in pathlib.PurePosixPath(name).name.lower()
        for name in names
    )
    return has_delvewheel_marker, has_shapely_libs, has_geos_dll


def repair_report_is_acceptable(report_text: str, wheel_path: os.PathLike[str] | str) -> bool:
    lower = report_text.lower()
    if "shapely.libs" in lower and "libgeos" in lower:
        return True
    if "already repaired this wheel" not in lower:
        return False
    return all(repaired_wheel_members(wheel_path))


def require_arm64(python_exe: str, expected: dict[str, object]) -> None:
    probe = subprocess.check_output(
        [
            python_exe,
            "-c",
            (
                "import json, platform, sys, sysconfig; "
                "print(json.dumps({"
                "'machine': platform.machine().lower(), "
                "'version': f'{sys.version_info[0]}.{sys.version_info[1]}', "
                "'gil': bool(sysconfig.get_config_var(\"Py_GIL_DISABLED\"))"
                "}))"
            ),
        ],
        text=True,
    )
    state = json.loads(probe)
    if state["machine"] not in {"arm64", "aarch64"}:
        fail(f"wrong verifier runtime: {state}")
    if state["version"] != expected["numeric_version"]:
        fail(f"wrong verifier runtime: {state}")
    if state["gil"] is not expected["free_threaded"]:
        fail(f"wrong verifier runtime: {state}")


def verify_one(
    core: dict[str, object],
    wheel_path: os.PathLike[str] | str,
    source_root: os.PathLike[str] | str,
    python_exe: str,
    artifact_root: os.PathLike[str] | str,
    report_dir: os.PathLike[str] | str,
) -> dict[str, object]:
    wheel_file = pathlib.Path(wheel_path)
    if sha256(wheel_file) != core["sha256"]:
        fail(f"{wheel_file}: wheel changed after producer")
    if wheel_file.stat().st_size != core["size_bytes"]:
        fail(f"{wheel_file}: wheel size changed after producer")
    if wheel_core(wheel_file) != core:
        fail(f"{wheel_file}: wheel metadata changed after producer")

    root = pathlib.Path(artifact_root).resolve()
    report_root = pathlib.Path(report_dir)
    if not report_root.is_absolute():
        report_root = root / report_root
    report_root = report_root.resolve()
    try:
        relative_report_root = report_root.relative_to(root)
    except ValueError as error:
        fail(f"report_dir: escapes verifier artifact root ({error})")
    report_path = report_root / f"{core['filename']}.delvewheel.txt"
    report_member = normalize_relative_report_path(
        (
            pathlib.PurePosixPath(relative_report_root.as_posix())
            / f"{core['filename']}.delvewheel.txt"
        ).as_posix(),
        "repair_report",
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-m", "delvewheel", "show", str(wheel_file)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    report_text = result.stdout or ""
    report_path.write_text(report_text, encoding="utf-8", newline="\n")
    if result.returncode != 0 or not repair_report_is_acceptable(report_text, wheel_file):
        fail(f"{wheel_file}: per-wheel delvewheel evidence failed")

    runner_temp = pathlib.Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir()))
    with tempfile.TemporaryDirectory(dir=runner_temp) as temp_dir:
        temp_root = pathlib.Path(temp_dir)
        venv = temp_root / "venv"
        clean_cwd = temp_root / "external-cwd"
        subprocess.run([python_exe, "-m", "venv", str(venv)], check=True)
        clean_cwd.mkdir()
        venv_python = venv / "Scripts" / "python.exe"
        subprocess.run(
            [venv_python, "-m", "pip", "install", "--disable-pip-version-check", "pytest"],
            check=True,
        )
        subprocess.run(
            [
                venv_python,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--force-reinstall",
                str(wheel_file),
            ],
            check=True,
        )
        subprocess.run([venv_python, "-m", "pip", "check"], check=True)
        subprocess.run(
            [
                venv_python,
                "-I",
                "-c",
                "from shapely import Point; assert Point(0, 0).buffer(1).area > 3",
            ],
            check=True,
            cwd=clean_cwd,
        )
        subprocess.run(
            [venv_python, "-I", "-m", "pytest", "--pyargs", "shapely.tests"],
            check=True,
            cwd=clean_cwd,
        )

    verified = {
        **core,
        "pe_machines": pe_machines(wheel_file),
        "license_members": wheel_licenses(wheel_file, source_root),
        "repair_report": report_member,
        "repair_report_sha256": sha256(report_path),
        "checks": {
            "wheel_tag": 0,
            "pe_architecture": 0,
            "license_contents": 0,
            "delvewheel_repair": 0,
            "clean_external_cwd_tests": 0,
        },
    }
    validate_verified_artifact(verified, wheel_file.name, core["runtime"])
    return verified


def producer_command(args: argparse.Namespace) -> None:
    wheels = sorted(pathlib.Path(args.wheelhouse).glob("*.whl"))
    if not wheels:
        fail("producer wheelhouse is empty")
    if platform.machine().lower() not in {"arm64", "aarch64"}:
        fail("producer is not native ARM64")

    repository = environment("GITHUB_REPOSITORY")
    run_id = environment("GITHUB_RUN_ID")
    job = {
        "repository": repository,
        "run_id": run_id,
        "job_key": environment("GITHUB_JOB"),
        "run_url": f"{environment('GITHUB_SERVER_URL')}/{repository}/actions/runs/{run_id}",
        "commit_sha": environment("GITHUB_SHA").lower(),
        "ref": environment("GITHUB_REF"),
    }
    geos_version = environment("GEOS_VERSION")
    doc = {
        "schema_version": 1,
        "record_type": "producer",
        "created_at_utc": now_utc(),
        "job": job,
        "source_artifact": SOURCE_ARTIFACT,
        "producer": {
            "runner": {
                "os": "Windows",
                "architecture": "ARM64",
                "image_os": environment("ImageOS"),
                "image_version": environment("ImageVersion"),
            },
            "toolchain": {
                "cibuildwheel_action": "pypa/cibuildwheel@v4.2.0",
                "msvc_toolset": "14.44",
                "cmake": command_version("cmake", "--version"),
                "ninja": command_version("ninja", "--version"),
            },
        },
        "geos": {
            "version": geos_version,
            "archive_url": f"https://download.osgeo.org/geos/geos-{geos_version}.tar.bz2",
            "sha256": environment("GEOS_SHA256").lower(),
            "checksum_provenance_url": environment("GEOS_CHECKSUM_PROVENANCE_URL"),
        },
        "artifacts": [wheel_core(path) for path in wheels],
    }
    validate_producer(doc)
    canonical_write(args.output, doc)


def verify_command(args: argparse.Namespace) -> None:
    producer = validate_producer(document(args.producer))
    runtime = runtime_for_label(args.label)
    require_arm64(args.python_exe, runtime)
    wheelhouse = pathlib.Path(args.wheelhouse)
    by_name = {path.name: path for path in wheelhouse.glob("*.whl")}
    selected = [core for core in producer["artifacts"] if core["runtime"]["label"] == args.label]
    if not selected:
        fail("no retained wheel matches verifier label")
    if any(core["filename"] not in by_name for core in selected):
        fail("producer wheel missing from retained artifact")
    artifact_root = pathlib.Path(args.output).resolve().parent
    verified = [
        verify_one(
            core,
            by_name[core["filename"]],
            args.source_root,
            args.python_exe,
            artifact_root,
            args.report_dir,
        )
        for core in selected
    ]
    repository = environment("GITHUB_REPOSITORY")
    run_id = environment("GITHUB_RUN_ID")
    doc = {
        "schema_version": 1,
        "record_type": "verifier",
        "created_at_utc": now_utc(),
        "producer_sha256": sha256(args.producer),
        "job": {
            "repository": repository,
            "run_id": run_id,
            "job_key": environment("GITHUB_JOB"),
            "run_url": f"{environment('GITHUB_SERVER_URL')}/{repository}/actions/runs/{run_id}",
            "commit_sha": environment("GITHUB_SHA").lower(),
            "ref": environment("GITHUB_REF"),
        },
        "runtime": runtime,
        "verifier": {
            "runner": {
                "os": "Windows",
                "architecture": "ARM64",
                "image_os": environment("ImageOS"),
                "image_version": environment("ImageVersion"),
            },
            "python_executable": str(pathlib.Path(args.python_exe).resolve()),
            "python_version": runtime["numeric_version"],
            "free_threaded": runtime["free_threaded"],
            "delvewheel_version": package_version("delvewheel"),
        },
        "artifacts": verified,
    }
    validate_verifier(doc)
    canonical_write(args.output, doc)


def resolve_report_path(base_dir: pathlib.Path, value: str) -> pathlib.Path:
    normalized = normalize_relative_report_path(value, "repair_report")
    root = base_dir.resolve()
    candidate = (root / pathlib.Path(*normalized.split("/"))).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        fail(f"repair_report: escapes verifier artifact root ({error})")
    return candidate


def finalize_command(args: argparse.Namespace) -> None:
    producer_path = pathlib.Path(args.producer)
    producer = validate_producer(document(producer_path))
    producer_hash = sha256(producer_path)
    require_artifact_label_coverage(producer["artifacts"], EXPECTED_LABELS, "producer.artifacts")
    verifier_dir = pathlib.Path(args.verifier_dir)
    verifier_paths = sorted(verifier_dir.glob("arm64-wheel-verifier-*.json"))
    if len(verifier_paths) != len(EXPECTED_LABELS):
        fail("missing or duplicate verifier record")

    records: list[tuple[pathlib.Path, dict[str, object]]] = []
    for path in verifier_paths:
        verifier = validate_verifier(document(path))
        if verifier["producer_sha256"] != producer_hash:
            fail("verifier is for a different producer")
        same_job_identity(verifier["job"], producer["job"], path.name)
        records.append((path, verifier))

    labels = {verifier["runtime"]["label"] for _, verifier in records}
    if labels != EXPECTED_LABELS:
        fail("missing or duplicate verifier label")

    merged = [artifact for _, verifier in records for artifact in verifier["artifacts"]]
    producer_set = {(artifact["filename"], artifact["sha256"]) for artifact in producer["artifacts"]}
    merged_set = {(artifact["filename"], artifact["sha256"]) for artifact in merged}
    if not producer_set or merged_set != producer_set or len(merged) != len(producer["artifacts"]):
        fail("verifier/producer wheel set mismatch")
    require_artifact_label_coverage(merged, EXPECTED_LABELS, "final.artifacts")

    verifier_entries = []
    for path, verifier in records:
        verifier_entries.append(
            {
                "label": verifier["runtime"]["label"],
                "sha256": sha256(path),
                "created_at_utc": verifier["created_at_utc"],
                "job": verifier["job"],
            }
        )
        for artifact in verifier["artifacts"]:
            report_path = resolve_report_path(verifier_dir, artifact["repair_report"])
            if not report_path.exists():
                fail("missing per-wheel repair report")
            if sha256(report_path) != artifact["repair_report_sha256"]:
                fail("altered per-wheel repair report")

    repository = environment("GITHUB_REPOSITORY")
    run_id = environment("GITHUB_RUN_ID")
    finalizer_job = {
        "repository": repository,
        "run_id": run_id,
        "job_key": environment("GITHUB_JOB"),
        "run_url": f"{environment('GITHUB_SERVER_URL')}/{repository}/actions/runs/{run_id}",
        "commit_sha": environment("GITHUB_SHA").lower(),
        "ref": environment("GITHUB_REF"),
    }
    same_job_identity(finalizer_job, producer["job"], "finalizer")
    doc = {
        "schema_version": 1,
        "record_type": "final",
        "created_at_utc": now_utc(),
        "finalizer_job": finalizer_job,
        "producer_sha256": producer_hash,
        "source_artifact": producer["source_artifact"],
        "producer": producer_metadata(producer),
        "geos": producer["geos"],
        "verifiers": sorted(verifier_entries, key=lambda item: item["label"]),
        "artifacts": sorted(merged, key=lambda item: item["filename"]),
    }
    validate_final(doc)
    canonical_write(args.output, doc)


def pypi_check(manifest_path: str, expected_distribution: str, expected_version: str) -> None:
    if canonicalize_name(expected_distribution) != NAME:
        fail("PyPI distribution must be shapely")
    final = validate_final(document(manifest_path))
    wanted = Version(expected_version)
    if not final["artifacts"]:
        fail("empty final artifact set")
    with urllib.request.urlopen(
        f"https://pypi.org/pypi/{expected_distribution}/{wanted}/json",
        timeout=30,
    ) as response:
        release = json.load(response)
    if canonicalize_name(release["info"]["name"]) != NAME:
        fail("wrong PyPI distribution/version")
    if Version(release["info"]["version"]) != wanted:
        fail("wrong PyPI distribution/version")

    published: dict[str, object] = {}
    for item in release["urls"]:
        filename = item.get("filename", "")
        if filename in published:
            fail("duplicate PyPI filename")
        published[filename] = item

    for index, artifact in enumerate(final["artifacts"]):
        validate_verified_artifact(artifact, f"PyPI.artifacts[{index}]")
        core = {key: artifact[key] for key in CORE_KEYS}
        if Version(core["version"]) != wanted:
            fail("missing/mismatched PyPI ARM64 wheel")
        published_item = published.get(core["filename"])
        if published_item is None:
            fail("missing/mismatched PyPI ARM64 wheel")
        digest = published_item.get("digests", {}).get("sha256", "").lower()
        if digest != core["sha256"]:
            fail("missing/mismatched PyPI ARM64 wheel")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    producer = commands.add_parser("producer")
    producer.add_argument("--wheelhouse", required=True)
    producer.add_argument("--output", required=True)

    verify = commands.add_parser("verify")
    for option in (
        "--producer",
        "--wheelhouse",
        "--source-root",
        "--label",
        "--python-exe",
        "--output",
        "--report-dir",
    ):
        verify.add_argument(option, required=True)

    finalize = commands.add_parser("finalize")
    finalize.add_argument("--producer", required=True)
    finalize.add_argument("--verifier-dir", required=True)
    finalize.add_argument("--output", required=True)

    validate = commands.add_parser("validate")
    validate.add_argument("--kind", choices=("producer", "verifier", "final"), required=True)
    validate.add_argument("--input", required=True)

    pypi = commands.add_parser("pypi")
    pypi.add_argument("--manifest", required=True)
    pypi.add_argument("--distribution", required=True)
    pypi.add_argument("--version", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "producer":
        producer_command(args)
    elif args.command == "verify":
        verify_command(args)
    elif args.command == "finalize":
        finalize_command(args)
    elif args.command == "validate":
        validator = {
            "producer": validate_producer,
            "verifier": validate_verifier,
            "final": validate_final,
        }[args.kind]
        validator(document(args.input))
    else:
        pypi_check(args.manifest, args.distribution, args.version)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        print(f"wheel-evidence: {error}", file=sys.stderr)
        raise SystemExit(1) from error
