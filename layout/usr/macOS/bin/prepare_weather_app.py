"""Prepare Ventura Weather for the iPadOS-hosted Catalyst launch contract.

No shebang: the target jailbreak's AMFI rejects execve of shebang scripts.
Invoke this file explicitly with /var/jb/usr/bin/python3.
"""

import os
import plistlib
import stat
import sys
import tempfile


WEATHER_IDENTIFIER = "com.apple.weather"
CONTAINER_REQUIRED = "com.apple.private.security.container-required"
SCENE_CONFIGURATION_NAME = "Weather Configuration"
BOOTSTRAP_CONFIGURATION_NAME = "MacWS Catalyst Bootstrap"
SCENE_DELEGATE_CLASS = "_TtC7Weather13SceneDelegate"
APPLICATION_ROLE = "UIWindowSceneSessionRoleApplication"


def fail(message):
    raise SystemExit(f"prepare_weather_app: {message}")


def read_plist(path):
    try:
        with open(path, "rb") as stream:
            return plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException) as error:
        fail(f"cannot read {path}: {error}")


def atomic_write_plist(path, value, output_format):
    try:
        original = os.lstat(path)
    except OSError as error:
        fail(f"cannot stat {path}: {error}")
    if stat.S_ISLNK(original.st_mode) or not stat.S_ISREG(original.st_mode):
        fail(f"refusing to replace non-regular plist: {path}")

    directory = os.path.dirname(os.path.abspath(path))
    descriptor, temporary = tempfile.mkstemp(prefix=".macws-weather-", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            plistlib.dump(value, stream, fmt=output_format, sort_keys=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, stat.S_IMODE(original.st_mode))
        os.chown(temporary, original.st_uid, original.st_gid)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def prepare_manifest(path):
    with open(path, "rb") as stream:
        header = stream.read(8)
    document = read_plist(path)
    if document.get("CFBundleIdentifier") != WEATHER_IDENTIFIER:
        fail(f"unexpected bundle identifier in {path}")

    manifest = document.setdefault("UIApplicationSceneManifest", {})
    if not isinstance(manifest, dict):
        fail("UIApplicationSceneManifest is not a dictionary")
    configurations = manifest.setdefault("UISceneConfigurations", {})
    if not isinstance(configurations, dict):
        fail("UISceneConfigurations is not a dictionary")
    application_scenes = configurations.setdefault(APPLICATION_ROLE, [])
    if not isinstance(application_scenes, list):
        fail(f"{APPLICATION_ROLE} is not an array")

    changed = False
    for configuration_name, delegate_class in (
            (SCENE_CONFIGURATION_NAME, SCENE_DELEGATE_CLASS),
            (BOOTSTRAP_CONFIGURATION_NAME, None)):
        desired = {
            "UISceneClassName": "UIWindowScene",
            "UISceneConfigurationName": configuration_name,
        }
        if delegate_class:
            desired["UISceneDelegateClassName"] = delegate_class
        matches = [
            entry for entry in application_scenes
            if isinstance(entry, dict)
            and entry.get("UISceneConfigurationName") == configuration_name
        ]
        if not matches or matches[0] != desired or len(matches) != 1:
            changed = True
        application_scenes[:] = [
            entry for entry in application_scenes
            if not (isinstance(entry, dict) and
                    entry.get("UISceneConfigurationName") ==
                    configuration_name)
        ]
        application_scenes.append(desired)

    if manifest.get("UIApplicationSupportsMultipleScenes") is not True:
        manifest["UIApplicationSupportsMultipleScenes"] = True
        changed = True
    if not changed:
        print(f"prepare_weather_app: manifest already prepared: {path}")
        return
    output_format = plistlib.FMT_BINARY if header == b"bplist00" else plistlib.FMT_XML
    atomic_write_plist(path, document, output_format)
    print("prepare_weather_app: added scene configurations "
          f"{SCENE_CONFIGURATION_NAME!r} and "
          f"{BOOTSTRAP_CONFIGURATION_NAME!r}: {path}")


def sanitize_entitlements(source, destination):
    entitlements = read_plist(source)
    if not isinstance(entitlements, dict):
        fail("entitlements plist is not a dictionary")
    application_identifier = entitlements.get("application-identifier")
    if application_identifier and not application_identifier.endswith(
            WEATHER_IDENTIFIER):
        fail(f"unexpected application-identifier: {application_identifier}")
    entitlements.pop(CONTAINER_REQUIRED, None)
    with open(destination, "wb") as stream:
        plistlib.dump(entitlements, stream, fmt=plistlib.FMT_XML, sort_keys=False)
    print(f"prepare_weather_app: removed {CONTAINER_REQUIRED}: {destination}")


def main(arguments):
    if len(arguments) == 3 and arguments[1] == "manifest":
        prepare_manifest(arguments[2])
        return
    if len(arguments) == 4 and arguments[1] == "entitlements":
        sanitize_entitlements(arguments[2], arguments[3])
        return
    fail("usage: prepare_weather_app.py manifest INFO_PLIST | "
         "entitlements INPUT_PLIST OUTPUT_PLIST")


if __name__ == "__main__":
    main(sys.argv)
