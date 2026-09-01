from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import stat
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
PRERELEASE_MANIFEST_PATTERN = re.compile(r'href="windows-([^"/]+)\.json"')
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
WINDOWS_REPARSE_POINT = 0x400
WINDOWS_INVALID_COMPONENT_CHARACTERS = frozenset('<>"|?*')
WINDOWS_MAX_COMPONENT_UTF16_UNITS = 255
WINDOWS_RESERVED_NAMES = {
    "AUX",
    "CON",
    "CONIN$",
    "CONOUT$",
    "NUL",
    "PRN",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"COM{number}" for number in "¹²³"),
    *(f"LPT{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in "¹²³"),
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


def prerelease_manifest_url(directory_release: str, release: str) -> str:
    return f"{PYTHON_FTP_ROOT}/{directory_release}/windows-{release}.json"


def parse_prerelease_manifest_versions(
    html: str, spec: RuntimeSpec, directory_release: str
) -> list[str]:
    directory_version = Version(directory_release)
    releases = []
    for match in PRERELEASE_MANIFEST_PATTERN.finditer(html):
        candidate = match.group(1)
        if not candidate.startswith(spec.series + "."):
            continue
        try:
            parsed = Version(candidate)
        except InvalidVersion:
            continue
        if not parsed.is_prerelease or not spec.allow_prereleases:
            continue
        if parsed.base_version != directory_version.base_version:
            continue
        releases.append(candidate)
    return sorted(set(releases), key=Version, reverse=True)


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


def manifest_record_for(
    spec: RuntimeSpec, release: str, directory_release: str | None = None
) -> dict[str, object] | None:
    url = (
        manifest_url(release)
        if directory_release is None
        else prerelease_manifest_url(directory_release, release)
    )
    try:
        payload = fetch_json(url)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise
    if not isinstance(payload, dict):
        return None
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
    for directory_release in releases:
        release = directory_release
        location = manifest_url(release)
        record = manifest_record_for(spec, release)
        if record is None and spec.allow_prereleases:
            directory_url = f"{PYTHON_FTP_ROOT}/{directory_release}/"
            try:
                directory_index = fetch_text(directory_url)
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    continue
                raise
            prereleases = parse_prerelease_manifest_versions(
                directory_index, spec, directory_release
            )
            for prerelease in prereleases:
                record = manifest_record_for(spec, prerelease, directory_release)
                if record is not None:
                    release = prerelease
                    location = prerelease_manifest_url(directory_release, prerelease)
                    break
        if record is None:
            continue
        if set(record) != MANIFEST_ENTRY_KEYS:
            fail(f"{release}: unexpected manifest key set")
        download_url = record.get("url")
        executable = record.get("executable")
        hashes = record.get("hash")
        digest = hashes.get("sha256") if isinstance(hashes, dict) else None
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
            "manifest_url": location,
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


def path_is_link_or_reparse_point(path: pathlib.Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & WINDOWS_REPARSE_POINT
    )


def existing_path_metadata(path: pathlib.Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def validated_extraction_target(target: pathlib.Path) -> pathlib.Path:
    target = pathlib.Path(os.path.abspath(target))
    anchor = pathlib.Path(target.anchor)
    if not target.anchor or existing_path_metadata(anchor) is None:
        fail(f"ZIP extraction target has no existing filesystem anchor: {target}")

    current = anchor
    relative_parts = target.parts[1:]
    for index, part in enumerate(relative_parts):
        current = current / part
        metadata = existing_path_metadata(current)
        if metadata is None:
            break
        if stat.S_ISLNK(metadata.st_mode) or bool(
            getattr(metadata, "st_file_attributes", 0) & WINDOWS_REPARSE_POINT
        ):
            fail(
                "ZIP extraction target path contains a link or reparse point: "
                f"{current}"
            )
        if index < len(relative_parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
            fail(f"ZIP extraction target ancestor is not a directory: {current}")

    target_metadata = existing_path_metadata(target)
    if target_metadata is not None and not stat.S_ISDIR(target_metadata.st_mode):
        fail(f"ZIP extraction target is not a directory: {target}")
    return target


def validated_zip_member(
    target_root: pathlib.Path, member: zipfile.ZipInfo
) -> tuple[tuple[str, ...], bool]:
    filename = member.filename
    normalized = filename.replace("\\", "/")
    windows_path = pathlib.PureWindowsPath(filename)
    if windows_path.drive or windows_path.root or normalized.startswith("/"):
        fail(f"unsafe absolute ZIP member path: {filename}")

    parts = []
    for part in pathlib.PurePosixPath(normalized).parts:
        if part in ("", "."):
            continue
        if part == "..":
            fail(f"unsafe ZIP member path traversal: {filename}")
        if ":" in part:
            fail(f"unsafe ZIP member drive or stream path: {filename}")
        if any(ord(character) <= 0x1F for character in part):
            fail(f"unsafe Windows control character in ZIP member path: {filename}")
        if any(
            character in WINDOWS_INVALID_COMPONENT_CHARACTERS
            for character in part
        ):
            fail(f"unsafe Windows character in ZIP member path: {filename}")
        utf16_units = sum(
            2 if ord(character) > 0xFFFF else 1 for character in part
        )
        if utf16_units > WINDOWS_MAX_COMPONENT_UTF16_UNITS:
            fail(f"unsafe overlong Windows ZIP member component: {filename}")
        if part.rstrip(" .") != part:
            fail(f"unsafe Windows-normalized ZIP member path: {filename}")
        reserved_name = part.partition(".")[0].rstrip(" ").upper()
        if reserved_name in WINDOWS_RESERVED_NAMES:
            fail(f"unsafe Windows device ZIP member path: {filename}")
        parts.append(part)
    if not parts:
        fail(f"unsafe empty ZIP member path: {filename}")

    mode = member.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
        fail(f"unsafe link or special ZIP member: {filename}")
    if member.external_attr & WINDOWS_REPARSE_POINT:
        fail(f"unsafe reparse-point ZIP member: {filename}")

    is_directory = member.is_dir() or normalized.endswith("/")
    if file_type == stat.S_IFDIR and not is_directory:
        fail(f"malformed ZIP directory member: {filename}")

    destination = target_root.joinpath(*parts)
    try:
        destination.relative_to(target_root)
    except ValueError:
        fail(f"unsafe normalized ZIP member path: {filename}")
    return tuple(parts), is_directory


def preflight_zip_destination(
    target_root: pathlib.Path,
    member: zipfile.ZipInfo,
    parts: tuple[str, ...],
    is_directory: bool,
) -> None:
    current = target_root
    for index, part in enumerate(parts):
        current = current / part
        metadata = existing_path_metadata(current)
        if metadata is None:
            return
        if stat.S_ISLNK(metadata.st_mode) or bool(
            getattr(metadata, "st_file_attributes", 0) & WINDOWS_REPARSE_POINT
        ):
            fail(
                "ZIP extraction destination contains a link or reparse point: "
                f"{member.filename}"
            )

        is_destination = index == len(parts) - 1
        if not is_destination:
            if not stat.S_ISDIR(metadata.st_mode):
                fail(
                    "ZIP extraction destination ancestor is not a directory: "
                    f"{member.filename}"
                )
            continue

        if is_directory and not stat.S_ISDIR(metadata.st_mode):
            fail(
                "ZIP extraction directory conflicts with an existing file: "
                f"{member.filename}"
            )
        if not is_directory and stat.S_ISDIR(metadata.st_mode):
            fail(
                "ZIP extraction file conflicts with an existing directory: "
                f"{member.filename}"
            )
        if not is_directory and not stat.S_ISREG(metadata.st_mode):
            fail(
                "ZIP extraction file conflicts with an existing special file: "
                f"{member.filename}"
            )


def ensure_safe_directory(target_root: pathlib.Path, directory: pathlib.Path) -> None:
    try:
        relative = directory.relative_to(target_root)
    except ValueError:
        fail(f"ZIP extraction directory escapes target: {directory}")

    current = target_root
    for part in relative.parts:
        current = current / part
        if path_is_link_or_reparse_point(current):
            fail(f"ZIP extraction encountered link or reparse point: {current}")
        try:
            current.mkdir()
        except FileExistsError:
            if not current.is_dir():
                raise
        resolved = current.resolve()
        try:
            resolved.relative_to(target_root)
        except ValueError:
            fail(f"ZIP extraction directory escapes target: {current}")


def safe_extract_zip(payload: zipfile.ZipFile, target: pathlib.Path) -> None:
    target_root = validated_extraction_target(target)
    members = [
        (member, *validated_zip_member(target_root, member))
        for member in payload.infolist()
    ]
    destinations: dict[tuple[str, ...], tuple[zipfile.ZipInfo, bool]] = {}
    for member, parts, is_directory in members:
        destination_key = tuple(part.casefold() for part in parts)
        previous = destinations.get(destination_key)
        if previous is not None:
            previous_member, previous_is_directory = previous
            collision = (
                "file/directory"
                if previous_is_directory != is_directory
                else "duplicate"
            )
            fail(
                f"unsafe {collision} ZIP member destination: {member.filename} "
                f"conflicts with {previous_member.filename}"
            )
        destinations[destination_key] = (member, is_directory)

    for member, parts, _ in members:
        destination_key = tuple(part.casefold() for part in parts)
        for depth in range(1, len(destination_key)):
            ancestor = destinations.get(destination_key[:depth])
            if ancestor is not None and not ancestor[1]:
                fail(
                    f"unsafe ZIP member path beneath file: {member.filename} "
                    f"conflicts with {ancestor[0].filename}"
                )

    for member, parts, is_directory in members:
        preflight_zip_destination(target_root, member, parts, is_directory)

    target_root.mkdir(parents=True, exist_ok=True)
    if path_is_link_or_reparse_point(target_root):
        fail(f"ZIP extraction target is a link or reparse point: {target_root}")
    target_root = target_root.resolve(strict=True)

    for member, parts, is_directory in members:
        destination = target_root.joinpath(*parts)
        if is_directory:
            ensure_safe_directory(target_root, destination)
            continue

        ensure_safe_directory(target_root, destination.parent)
        if path_is_link_or_reparse_point(destination):
            fail(f"ZIP extraction encountered link or reparse point: {destination}")
        resolved_parent = destination.parent.resolve(strict=True)
        try:
            resolved_parent.relative_to(target_root)
        except ValueError:
            fail(f"ZIP extraction destination escapes target: {destination}")

        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(destination, flags, 0o666)
        with os.fdopen(descriptor, "wb") as output, payload.open(member) as source:
            shutil.copyfileobj(source, output)


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

    with urllib.request.urlopen(download_url, timeout=60) as response:
        archive.write_bytes(response.read())
    actual_sha256 = sha256_file(archive)
    if actual_sha256 != expected_sha256:
        fail(
            f"{spec.label}: runtime digest mismatch: expected {expected_sha256}; "
            f"actual {actual_sha256}"
        )

    with zipfile.ZipFile(archive) as payload:
        safe_extract_zip(payload, target)

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
