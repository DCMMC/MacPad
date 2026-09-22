"""Inventory runtime switches and reject diagnostics in shipped launch jobs.

Invoke from the repository root:
    python3 misc/audit_runtime_switches.py
"""

from __future__ import annotations

import os
import pathlib
import plistlib
import re
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "runtime-switches.tsv"
SOURCE_SUFFIXES = {".c", ".m", ".mm", ".x", ".xm", ".h", ".swift", ".sh"}
CLEANUP_HELPER = ROOT / 'layout/usr/macOS/bin/macws_diagnostic_flags.sh'
EXCLUDED_PARTS = {
    ".git",
    ".theos",
    ".build",
    "packages",
    "evidence",
    "tmp",
    "__pycache__",
}
# These are functional Steam compatibility settings explicitly set to zero in
# the shipped profile, not presence-gated diagnostics. Keep this allowlist
# exact: other production=off switches must be absent, including NAME=0.
EXPLICIT_DISABLED_ENVIRONMENT = {
    "SDL_JOYSTICK_HIDAPI": "0",
    "SDL_JOYSTICK_IOKIT": "0",
    "SDL_JOYSTICK_MFI": "0",
}
# Chromium command-line diagnostics are not environment/file gates, but must
# obey the same production-off policy. Match exact options before '=' so both
# '--option=value' and '--option value' forms are checked without guessing at
# ordinary URLs or filenames. Standalone diagnostic jobs are not shipped here.
FORBIDDEN_PRODUCTION_ARGUMENTS = {
    "--remote-debugging-port",
    "--remote-debugging-pipe",
    "--remote-allow-origins",
}


def load_manifest() -> dict[tuple[str, str], tuple[str, str, str]]:
    entries: dict[tuple[str, str], tuple[str, str, str]] = {}
    for line_number, raw in enumerate(MANIFEST.read_text().splitlines(), 1):
        if not raw or raw.startswith("#"):
            continue
        fields = raw.split("\t")
        if len(fields) != 5:
            raise ValueError(f"{MANIFEST}:{line_number}: expected 5 TSV fields")
        kind, name, production, scope, purpose = fields
        key = (kind, name)
        if key in entries:
            raise ValueError(f"{MANIFEST}:{line_number}: duplicate {kind} {name}")
        if production not in {"on", "off", "auto", "transient"}:
            raise ValueError(
                f"{MANIFEST}:{line_number}: invalid production state {production}"
            )
        entries[key] = (production, scope, purpose)
    return entries


def source_files() -> dict[pathlib.Path, str]:
    texts: dict[pathlib.Path, str] = {}
    for directory, children, files in os.walk(ROOT):
        children[:] = [name for name in children if name not in EXCLUDED_PARTS]
        for name in files:
            path = pathlib.Path(directory) / name
            if path.suffix in SOURCE_SUFFIXES:
                texts[path] = path.read_text(errors="ignore")
    return texts


def source_texts() -> list[str]:
    return list(source_files().values())


def diagnostic_cleanup_script(manifest) -> str:
    paths = sorted(name for (kind, name), (state, _, _) in manifest.items()
                   if kind == 'flag' and state in {'off', 'transient'})
    for path in paths:
        if not re.fullmatch(r'/(?:private/)?tmp/[A-Za-z0-9_.-]+', path):
            raise ValueError(f'flag is not an exact boot-local path: {path}')
    quoted = ' \\\n'.join('        ' + name for name in paths)
    environment = sorted(name for (kind, name), (state, _, _) in manifest.items()
                         if kind == 'env' and state == 'off'
                         and name not in EXPLICIT_DISABLED_ENVIRONMENT)
    pattern = '|'.join(environment)
    return ('# Generated from docs/runtime-switches.tsv; do not edit by hand.\n'
            '# All entries are diagnostics or retired switches, never payloads.\n'
            'macws_diagnostic_flag_paths() {\n'
            "    printf '%s\\n' \\\n" + quoted + '\n}\n\n'
            'macws_diagnostic_environment_pattern() {\n'
            "    printf '%s\\n' '" + pattern + "'\n}\n")


def normalized_c_source(text: str) -> str:
    # Preserve string literals while dropping comments, then join C's adjacent
    # literal spelling. This covers split getenv arguments and macro paths.
    token = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/')
    text = token.sub(lambda match: match[0] if match[0].startswith('"')
                     else ' ', text)
    return re.sub(r'"\s*"', '', text)


def discovered_switches(files):
    env_names, flag_names = set(), set()
    for path, text in files.items():
        source = normalized_c_source(text) if path.suffix != '.sh' else text
        env_names.update(re.findall(r'getenv\s*\(\s*"([A-Z][A-Z0-9_]+)"', source))
        # Launcher environment dictionaries and VAR=value spawn strings also
        # carry process contracts even when no local getenv consumer exists.
        env_names.update(re.findall(r'"(MACWS_[A-Z][A-Z0-9_]+)(?:=|"|:)', source))
        if path.suffix == '.sh':
            env_names.update(re.findall(r'\$\{(MACWS_[A-Z][A-Z0-9_]+):[-+?=]', source))
        # Objective-C environment dictionary reads are equivalent opt-ins.
        env_names.update(re.findall(r'environment\]\s*\[\s*@"([A-Z][A-Z0-9_]+)"', source))
        # Include literal consumers, macro-generated readers and named flag
        # constants: the previous access-only regexp missed all three classes.
        patterns = (
            r'access\s*\(\s*"([^"]+)"',
            r'(?:fileExistsAtPath|isReadableFileAtPath):\s*@?"([^"]+)"',
            r'MACWS_DEFINE_STARTUP_FLAG\s*\(\s*\w+\s*,\s*"([^"]+)"',
        )
        for pattern in patterns:
            flag_names.update(value for value in re.findall(pattern, source)
                              if re.match(r'/(?:private/)?tmp/(?:macws_|iosclear_|com\.macwsguide\.)', value))
        constants = {}
        for name, value in re.findall(r'\b(\w+)\s*(?:\[\])?\s*=\s*@?"([^"]+)"', source):
            constants.setdefault(name, set()).add(value)
        consumers = re.findall(r'access\s*\(\s*(\w+)\s*,', source)
        consumers += re.findall(r'(?:fileExistsAtPath|isReadableFileAtPath):\s*(\w+)\b', source)
        if path.suffix == '.sh':
            consumers += re.findall(r'\[\s*!?\s*-(?:e|f|r)\s+"\$\{?(\w+)', source)
        for name in consumers:
            for value in constants.get(name, ()):
                value = re.sub(r'^(?:\$ROOTFS|/var/mnt/rootfs|/private/var/mnt/rootfs)', '', value)
                if re.match(r'/(?:private/)?tmp/(?:macws[_.-]|iosclear_|com\.macwsguide\.)', value):
                    flag_names.add(value)
    return env_names, flag_names


def production_plists() -> list[pathlib.Path]:
    # Also cover optional jobs explicitly copied by after-stage; misc contains
    # diagnostic probes too, so treating every misc plist as shipped is wrong.
    paths = set((ROOT / "layout").rglob("*.plist"))
    # Chrome is an ordinary optional browser template, even when the package
    # does not install it automatically. Keep diagnostics out of that profile
    # as well; standalone benchmark/probe jobs remain outside this set.
    paths.add(ROOT / "misc/com.macwsguide.chrome150.plist")
    for relative in re.findall(r'misc/[^\s\\]+\.plist',
                               (ROOT / "Makefile").read_text()):
        paths.add(ROOT / relative)
    return sorted(paths)


def production_environment_errors(manifest, paths) -> list[str]:
    errors = []
    for path in paths:
        try:
            with path.open("rb") as stream:
                value = plistlib.load(stream)
            environment = value.get("EnvironmentVariables", {})
            if not isinstance(environment, dict):
                raise ValueError("EnvironmentVariables must be a dictionary")
        except (OSError, ValueError, AttributeError) as error:
            errors.append(f"invalid shipped plist {path}: {error}")
            continue
        for name in environment:
            if manifest.get(("env", name), (None,))[0] == "off":
                if (name in EXPLICIT_DISABLED_ENVIRONMENT and
                        environment[name] == EXPLICIT_DISABLED_ENVIRONMENT[name]):
                    continue
                # Reject presence even with value '0': some legacy consumers
                # still test getenv(name) rather than parsing its value.
                errors.append(f"production=off environment {name} in {path}")
    return errors


def production_argument_errors(paths) -> list[str]:
    errors = []
    for path in paths:
        try:
            with path.open("rb") as stream:
                value = plistlib.load(stream)
            arguments = value.get("ProgramArguments", [])
            if (not isinstance(arguments, list) or
                    any(not isinstance(argument, str) for argument in arguments)):
                raise ValueError("ProgramArguments must be an array of strings")
        except (OSError, ValueError, AttributeError) as error:
            errors.append(f"invalid shipped plist {path}: {error}")
            continue
        for argument in arguments:
            if argument.partition("=")[0] in FORBIDDEN_PRODUCTION_ARGUMENTS:
                errors.append(f"production=off argument {argument} in {path}")
    return errors


def plist_environment_names() -> set[str]:
    names: set[str] = set()
    candidates = list((ROOT / "layout").rglob("*.plist"))
    candidates += list((ROOT / "misc").glob("com.macwsguide.*.plist"))
    for path in candidates:
        try:
            with path.open("rb") as stream:
                value = plistlib.load(stream)
        except Exception:
            continue
        environment = value.get("EnvironmentVariables", {})
        if isinstance(environment, dict):
            names.update(str(name) for name in environment)
    return names


def main() -> int:
    manifest = load_manifest()
    files = source_files()
    env_names, flag_names = discovered_switches(files)
    env_names.update(plist_environment_names())

    missing_env = sorted(name for name in env_names if ("env", name) not in manifest)
    missing_flags = sorted(
        name for name in flag_names
        if not any((kind, alias) in manifest
                   for kind in ('flag', 'state', 'artifact')
                   for alias in (name, name.replace('/private/tmp/', '/tmp/', 1)))
    )
    shipped = production_plists()
    errors = production_environment_errors(manifest, shipped)
    errors += production_argument_errors(shipped)
    for (kind, name), (state, _, _) in manifest.items():
        if kind == 'flag' and state not in {'off', 'transient'}:
            errors.append(f'production function depends on a flag: {name} ({state})')
    expected_cleanup = diagnostic_cleanup_script(manifest)
    if '--write-cleanup' in sys.argv:
        CLEANUP_HELPER.write_text(expected_cleanup)
    if not CLEANUP_HELPER.exists() or CLEANUP_HELPER.read_text() != expected_cleanup:
        errors.append('diagnostic cleanup inventory is stale; run '
                      'python3 misc/audit_runtime_switches.py --write-cleanup')
    if missing_env:
        errors.append("unrecorded environment switches:\n  " + "\n  ".join(missing_env))
    if missing_flags:
        errors.append("unrecorded flag files:\n  " + "\n  ".join(missing_flags))
    malloc_state = manifest.get(("env", "MallocScribble"), (None,))[0]
    if malloc_state != "off":
        errors.append("MallocScribble must be recorded as production=off")

    if errors:
        print("runtime-switch audit FAILED", file=sys.stderr)
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(
        "runtime-switch audit OK: "
        f"{len(env_names)} source/plist env names, "
        f"{len(flag_names)} source flag files, "
        f"{len(manifest)} total recorded entries"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
