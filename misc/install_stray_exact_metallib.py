"""Convert one captured Stray MTLB and atomically install its exact mapping.

No shebang: the target jailbreak's AMFI rejects execve of shebang scripts.
Invoke this file explicitly with /var/jb/usr/bin/python3.

The proprietary input/output blobs stay on the device and are never copied
into the repository.  Selection remains byte-exact: the capture filename's
FNV-1a identity must match its contents, and libmachook independently checks
the installed output length, MTLB header/container length and FNV at runtime.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile


CAPTURE_RE = re.compile(r"^macws_mtl_data_[0-9]+_([0-9a-f]{16})\.bin$")
FNV_OFFSET = 1469598103934665603
FNV_PRIME = 1099511628211
FNV_MASK = (1 << 64) - 1
SUPPORTED_LLVM_MAJOR = 15


def fnv1a64(data: bytes) -> int:
    value = FNV_OFFSET
    for byte in data:
        value = ((value ^ byte) * FNV_PRIME) & FNV_MASK
    return value


def validate_mtlb(data: bytes, expected_hash: int | None = None) -> int:
    if len(data) < 24 or data[:4] != b"MTLB":
        raise ValueError("file is not an MTLB container")
    if struct.unpack_from("<Q", data, 16)[0] != len(data):
        raise ValueError("MTLB container length does not match file size")
    value = fnv1a64(data)
    if expected_hash is not None and value != expected_hash:
        raise ValueError(
            f"source FNV mismatch: expected {expected_hash:016x}, "
            f"observed {value:016x}"
        )
    return value


def llvm_major(tool: str) -> int:
    """Return an LLVM tool's major version or fail before rewriting AIR."""
    try:
        result = subprocess.run(
            [tool, "--version"], check=True, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"cannot execute LLVM tool {tool!r}: {error}") from error
    match = re.search(r"LLVM version ([0-9]+)(?:\.[0-9]+)*", result.stdout)
    if not match:
        raise SystemExit(
            f"cannot determine LLVM version from {tool!r}: {result.stdout!r}"
        )
    return int(match.group(1))


def atomic_install(source: pathlib.Path, destination: pathlib.Path,
                   mode: int, uid: int, gid: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{destination.name}.", dir=destination.parent, delete=False
    ) as temporary:
        temporary_path = pathlib.Path(temporary.name)
        with source.open("rb") as input_file:
            shutil.copyfileobj(input_file, temporary)
        temporary.flush()
        os.fsync(temporary.fileno())
    try:
        os.chmod(temporary_path, mode)
        os.chown(temporary_path, uid, gid)
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=pathlib.Path)
    parser.add_argument(
        "--converter",
        type=pathlib.Path,
        default=pathlib.Path(__file__).with_name("metal2metal.py"),
    )
    parser.add_argument(
        "--llvm-dis",
        help="LLVM 15 llvm-dis; required unless --prebuilt-replacement is used",
    )
    parser.add_argument(
        "--llvm-as",
        help="LLVM 15 llvm-as; required unless --prebuilt-replacement is used",
    )
    parser.add_argument(
        "--prebuilt-replacement",
        type=pathlib.Path,
        help=(
            "install an already converted MTLB and regenerate its exact "
            "length/FNV metadata instead of invoking llvm-dis/llvm-as"
        ),
    )
    parser.add_argument(
        "--target-triple", default="air64-apple-ios19.0.0-macabi"
    )
    parser.add_argument(
        "--container-target", choices=("macabi", "ios", "macos"),
        default="macabi",
    )
    parser.add_argument("--target-major", type=int, default=19)
    parser.add_argument("--target-minor", type=int, default=0)
    parser.add_argument("--function", action="append")
    parser.add_argument("--auto-lower-known-air", action="store_true")
    parser.add_argument("--abi-report", type=pathlib.Path)
    parser.add_argument("--lower-zero-memset-function", action="append")
    parser.add_argument(
        "--archive", type=pathlib.Path,
        default=pathlib.Path("/var/jb/var/mobile/macws-stray-source"),
    )
    parser.add_argument(
        "--exact-dir", type=pathlib.Path,
        default=pathlib.Path(
            "/var/mnt/rootfs/usr/local/share/macws/stray/exact"
        ),
    )
    args = parser.parse_args()

    if os.geteuid() != 0:
        raise SystemExit("run through sudo so installed mappings are root-owned")
    match = CAPTURE_RE.match(args.capture.name)
    if not match:
        raise SystemExit("capture filename lacks the expected exact FNV suffix")
    source = args.capture.read_bytes()
    expected_source_hash = int(match.group(1), 16)
    source_hash = validate_mtlb(source, expected_source_hash)
    source_length = len(source)

    if not args.prebuilt_replacement:
        if not args.llvm_dis or not args.llvm_as:
            raise SystemExit(
                "conversion requires explicit LLVM 15 --llvm-dis and --llvm-as; "
                "on the iPad install a host-converted file with "
                "--prebuilt-replacement"
            )
        versions = {
            "llvm-dis": llvm_major(args.llvm_dis),
            "llvm-as": llvm_major(args.llvm_as),
        }
        incompatible = {
            name: version for name, version in versions.items()
            if version != SUPPORTED_LLVM_MAJOR
        }
        if incompatible:
            details = ", ".join(
                f"{name}={version}" for name, version in incompatible.items()
            )
            raise SystemExit(
                f"Stray AIR conversion requires LLVM {SUPPORTED_LLVM_MAJOR}; "
                f"incompatible tools: {details}"
            )

    args.archive.mkdir(parents=True, exist_ok=True)
    archived_capture = args.archive / args.capture.name
    with tempfile.NamedTemporaryFile(
        prefix=f".{args.capture.name}.", dir=args.archive, delete=False
    ) as temporary:
        archive_temporary = pathlib.Path(temporary.name)
        temporary.write(source)
        temporary.flush()
        os.fsync(temporary.fileno())
    try:
        os.chmod(archive_temporary, 0o600)
        os.chown(archive_temporary, 501, 20)
        os.replace(archive_temporary, archived_capture)
    finally:
        archive_temporary.unlink(missing_ok=True)

    output_name = f"{source_length}-{source_hash:016x}.metallib"
    archived_output = args.archive / output_name
    if args.prebuilt_replacement:
        prebuilt = args.prebuilt_replacement.read_bytes()
        validate_mtlb(prebuilt)
        atomic_install(
            args.prebuilt_replacement, archived_output, 0o600, 501, 20,
        )
    else:
        converter_command = [
            sys.executable,
            str(args.converter),
            "translate",
            str(archived_capture),
            str(archived_output),
            "--llvm-dis", args.llvm_dis,
            "--llvm-as", args.llvm_as,
            "--target-triple", args.target_triple,
            "--container-target", args.container_target,
            "--target-major", str(args.target_major),
            "--target-minor", str(args.target_minor),
        ]
        for function in args.function or []:
            converter_command.extend(("--function", function))
        if args.auto_lower_known_air:
            converter_command.append("--auto-lower-known-air")
        if args.abi_report:
            converter_command.extend(("--abi-report", str(args.abi_report)))
        for function in args.lower_zero_memset_function or []:
            converter_command.extend(
                ("--lower-zero-memset-function", function)
            )
        subprocess.run(converter_command, check=True)
    replacement = archived_output.read_bytes()
    replacement_hash = validate_mtlb(replacement)
    replacement_length = len(replacement)
    os.chmod(archived_output, 0o600)
    os.chown(archived_output, 501, 20)

    exact_stem = f"{source_length}-{source_hash:016x}"
    exact_library = args.exact_dir / f"{exact_stem}.metallib"
    atomic_install(archived_output, exact_library, 0o644, 0, 0)

    metadata_text = f"{replacement_length} {replacement_hash:016x}\n"
    with tempfile.NamedTemporaryFile(mode="w", encoding="ascii", delete=False) as meta:
        metadata_source = pathlib.Path(meta.name)
        meta.write(metadata_text)
        meta.flush()
        os.fsync(meta.fileno())
    try:
        atomic_install(
            metadata_source, args.exact_dir / f"{exact_stem}.meta",
            0o644, 0, 0,
        )
    finally:
        metadata_source.unlink(missing_ok=True)

    print(
        f"installed source={source_length}/{source_hash:016x} "
        f"replacement={replacement_length}/{replacement_hash:016x} "
        f"path={exact_library}"
    )


if __name__ == "__main__":
    main()
