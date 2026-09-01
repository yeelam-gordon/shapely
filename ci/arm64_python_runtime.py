from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone

from packaging.version import InvalidVersion, Version

from wheel_evidence import EXPECTED_LABELS, RUNTIMES

ROOT_INDEX_URL = "https://www.python.org/ftp/python/"
PYTHON_FTP_ROOT = "https://www.python.org/ftp/python"
ENTRY_PATTERN = re.compile(r'href="([^"/]+)/"')
MANIFEST_ENTRY_KEYS = {
    "schema",
    "id",
    "sort-version",
    "company",
    "tag",
    "install-for",
    "alias",
    "display-name",
    "executable",
    "url",
    "hash",
}


@dataclass(frozen=True)
class RuntimeSpec:
    label: str
    series: str
    free_threaded: bool
    allow_prereleases: bool


def fail(message: str) -> None:
    raise ValueError(message)


def runtime_specs() -> list[RuntimeSpec]:
    specs = []
    for label in sorted(EXPECTED_LABELS):
        runtime = next(value for value in RUNTIMES.values() if value["label"] == label)
        specs.append(
            RuntimeSpec(
                label=label,
                series=runtime["numeric_version"],
                free_threaded=runtime["free_threaded"],
                allow_prereleases=label in {"cp315", "cp315t"},
            )
        )
    return specs


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "shapely-arm64-runtime-plan"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def fetch_json(url: str) -> object:
    return json.loads(fetch_text(url))


def canonical_write(path: os.PathLike[str] | str, value: object) -> None:
    destination = pathlib.Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def parse_versions(html: str, spec: RuntimeSpec) -> list[str]:
    releases = []
    for match in ENTRY_PATTERN.finditer(html):
        candidate = match.group(1)
        if not candidate.startswith(spec.series + "."):
            continue
        try:
            parsed = Version(candidate)
        except InvalidVersion:
            continue
        if f"{parsed.major}.{parsed.minor}" != spec.series:
            continue
        if parsed.is_prerelease and not spec.allow_prereleases:
            continue
        releases.append(candidate)
    return sorted(set(releases), key=Version, reverse=True)


def manifest_url(release: str) -> str:
    return f"{PYTHON_FTP_ROOT}/{release}/windows-{release}.json"


def manifest_entry_id(spec: RuntimeSpec) -> str:
    suffix = "t" if spec.free_threaded else ""
    return f"pythoncore-{spec.series}{suffix}-arm64"


def parse_cli_bool(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    fail(f"invalid boolean value: {value}")


def normalize_runtime_executable_path(root: pathlib.Path, value: object) -> pathlib.Path:
    if not isinstance(value, str) or not value:
        fail("runtime executable path missing or malformed")
    text = value.replace("\\", "/")
    if text.startswith("//") or value.startswith("\\\\"):
        fail("runtime executable path must be relative")
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        fail("runtime executable path must be relative")
    pure = pathlib.PurePosixPath(text)
    parts = []
    for part in pure.parts:
        if part in ("", "."):
            continue
        if part == "..":
            fail("runtime executable path cannot escape runtime root")
        if ":" in part:
            fail("runtime executable path must be relative")
        parts.append(part)
    if not parts:
        fail("runtime executable path missing or malformed")
    candidate = (root.resolve() / pathlib.Path(*parts)).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as error:
        fail(f"runtime executable path escapes runtime root ({error})")
    return candidate


def validate_runtime_executable_text(value: object) -> str:
    if not isinstance(value, str) or not value:
        fail("runtime executable path missing or malformed")
    normalize_runtime_executable_path(pathlib.Path.cwd(), value)
    return value


def runtime_spec_for_label(label: str, allow_prereleases: bool | None = None) -> RuntimeSpec:
    specs = {spec.label: spec for spec in runtime_specs()}
    if label not in specs:
        fail(f"unknown runtime label(s): {label}")
    spec = specs[label]
    if allow_prereleases is None:
        return spec
    return RuntimeSpec(
        label=spec.label,
        series=spec.series,
        free_threaded=spec.free_threaded,
        allow_prereleases=allow_prereleases,
    )


def manifest_record_for(spec: RuntimeSpec, release: str) -> dict[str, object] | None:
    url = manifest_url(release)
    try:
        payload = fetch_json(url)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise
    versions = payload.get("versions")
    if not isinstance(versions, list):
        return None
    wanted = manifest_entry_id(spec)
    for item in versions:
        if not isinstance(item, dict):
            continue
        if item.get("id") == wanted:
            return item
    return None


def discover_runtime(spec: RuntimeSpec) -> dict[str, object]:
    root_index = fetch_text(ROOT_INDEX_URL)
    releases = parse_versions(root_index, spec)
    if not releases:
        return {
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
            "reason": f"no python.org FTP releases found for {spec.series}",
        }
    for release in releases:
        record = manifest_record_for(spec, release)
        if record is None:
            continue
        if set(record) != MANIFEST_ENTRY_KEYS:
            fail(f"{release}: unexpected manifest key set")
        download_url = record.get("url")
        executable = record.get("executable")
        digest = record.get("hash", {}).get("sha256")
        if not isinstance(download_url, str) or not download_url.startswith("https://"):
            fail(f"{release}: runtime URL missing or non-HTTPS")
        executable = validate_runtime_executable_text(executable)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            fail(f"{release}: runtime SHA-256 missing or malformed")
        return {
            "label": spec.label,
            "series": spec.series,
            "free_threaded": spec.free_threaded,
            "allow_prereleases": spec.allow_prereleases,
            "available": True,
            "release": release,
            "manifest_url": manifest_url(release),
            "download_url": download_url,
            "sha256": digest,
            "executable": executable,
            "reason": None,
        }
    return {
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
        "reason": (
            f"no python.org Windows ARM64 manifest entry found for {spec.label}; "
            "serialized runner must record this as unavailable rather than verified"
        ),
    }


def sha256_file(path: pathlib.Path) -> str:
    with open(path, "rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def output_path(root: pathlib.Path, spec: RuntimeSpec, release: str) -> pathlib.Path:
    return root / f"{spec.label}-{release}"


def install_runtime(spec: RuntimeSpec, root: pathlib.Path) -> dict[str, object]:
    plan = discover_runtime(spec)
    if not plan["available"]:
        return plan

    release = str(plan["release"])
    download_url = str(plan["download_url"])
    expected_sha256 = str(plan["sha256"])
    target = output_path(root, spec, release)
    archive = root / "downloads" / pathlib.Path(download_url).name
    archive.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)

    with urllib.request.urlopen(download_url, timeout=60) as response:
        archive.write_bytes(response.read())
    actual_sha256 = sha256_file(archive)
    if actual_sha256 != expected_sha256:
        fail(
            f"{spec.label}: runtime digest mismatch: expected {expected_sha256}; "
            f"actual {actual_sha256}"
        )

    with zipfile.ZipFile(archive) as payload:
        payload.extractall(target)

    python_executable = normalize_runtime_executable_path(target, plan["executable"])
    if not python_executable.exists():
        fail(f"{spec.label}: runtime executable missing after extraction")
    subprocess.check_call([str(python_executable), "-m", "ensurepip", "--upgrade"])
    subprocess.check_call([str(python_executable), "-m", "pip", "install", "--upgrade", "pip"])

    return {
        **plan,
        "target_dir": str(target.resolve()),
        "python_executable": str(python_executable),
        "downloaded_sha256": actual_sha256,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    plan = commands.add_parser("plan")
    plan.add_argument("--label", action="append", help="Specific ABI label(s) to plan")
    plan.add_argument("--allow-prereleases", choices=("true", "false"))
    plan.add_argument("--output", required=True)

    install = commands.add_parser("install")
    install.add_argument("--label", required=True)
    install.add_argument("--allow-prereleases", choices=("true", "false"))
    install.add_argument("--root", required=True)
    install.add_argument("--output", required=True)
    return parser


def selected_specs(labels: list[str] | None, allow_prereleases: bool | None = None) -> list[RuntimeSpec]:
    specs = {spec.label: spec for spec in runtime_specs()}
    if not labels:
        if allow_prereleases is None:
            return [specs[label] for label in sorted(specs)]
        return [runtime_spec_for_label(label, allow_prereleases) for label in sorted(specs)]
    missing = [label for label in labels if label not in specs]
    if missing:
        fail(f"unknown runtime label(s): {', '.join(sorted(missing))}")
    return [runtime_spec_for_label(label, allow_prereleases) for label in labels]


def plan_command(args: argparse.Namespace) -> None:
    allow_prereleases = None if args.allow_prereleases is None else parse_cli_bool(args.allow_prereleases)
    runtimes = [discover_runtime(spec) for spec in selected_specs(args.label, allow_prereleases)]
    doc = {
        "schema_version": 1,
        "record_type": "runtime_plan",
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "runtimes": runtimes,
    }
    canonical_write(args.output, doc)


def install_command(args: argparse.Namespace) -> int:
    allow_prereleases = None if args.allow_prereleases is None else parse_cli_bool(args.allow_prereleases)
    spec = selected_specs([args.label], allow_prereleases)[0]
    record = install_runtime(spec, pathlib.Path(args.root))
    doc = {
        "schema_version": 1,
        "record_type": "runtime_install",
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "runtime": record,
    }
    canonical_write(args.output, doc)
    return 0 if record["available"] else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "plan":
        plan_command(args)
        return 0
    return install_command(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError, urllib.error.URLError, zipfile.BadZipFile) as error:
        print(f"arm64-python-runtime: {error}", file=sys.stderr)
        raise SystemExit(1) from error
