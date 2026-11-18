#!/usr/bin/env python3
"""Run Make.py build with provisioning profiles present for unsigned builds.

`Make.py build --xcodeManagedCodesigning` regenerates
`build-input/configuration-repository/provisioning/` with an empty
`exports_files([])` right before invoking bazel (see resolve_configuration in
build-system/Make/Make.py). The extensions reference
`@build_configuration//provisioning:<Name>.mobileprovision` unconditionally, so
analysis fails on the first missing one.

BazelLocation.locate_bazel also re-hashes the bazel binary and deletes it when the
digest differs, so patching the binary is not an option either. This launcher
instead patches the generated package *between* Make.py writing it and bazel
reading it: it swaps `call_executable` so that the very first bazel invocation is
preceded by copying the repository's fake provisioning profiles into place. The
profiles carry no aps-environment entitlement, so the app stays unsigned.

Usage (from the upstream checkout):

    python3 ../ios/make_unsigned_build.py build --configuration=release_arm64 ...
"""

import os
import runpy
import shutil
import sys

PROFILES_SOURCE = "build-system/fake-codesigning/profiles"
PROVISIONING_DIR = "build-input/configuration-repository/provisioning"
REQUIRED_PROFILES = [
    "Telegram.mobileprovision",
    "Share.mobileprovision",
    "NotificationContent.mobileprovision",
    "NotificationService.mobileprovision",
    "Intents.mobileprovision",
    "Widget.mobileprovision",
    "BroadcastUpload.mobileprovision",
]


def install_profiles():
    """Declare the fake profiles in the provisioning package."""
    os.makedirs(PROVISIONING_DIR, exist_ok=True)
    copied = []
    for name in REQUIRED_PROFILES:
        source = os.path.join(PROFILES_SOURCE, name)
        if not os.path.isfile(source):
            raise SystemExit("missing provisioning fixture: " + source)
        shutil.copyfile(source, os.path.join(PROVISIONING_DIR, name))
        copied.append(name)
    lines = ["exports_files(["]
    lines.extend('    "{}",'.format(name) for name in sorted(copied))
    lines.append("])")
    with open(os.path.join(PROVISIONING_DIR, "BUILD"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print("[litgram] provisioning package declares {} profiles".format(len(copied)))


def patch_bazel_args(command):
    """Force an unsigned, extension-less device build.

    Only `--//Telegram:disableExtensions` is set:

    * Extensions reference `@build_configuration//provisioning:<Name>.mobileprovision`
      and, on CI, also trigger `//Telegram:Telegram_local_profile` — a
      `local_provisioning_profile` rule that shells out to
      `local_provisioning_profile_finder`, which looks the profile up in the
      runner's keychain and fails with "no provisioning profile was found named
      'iOS Team Provisioning Profile: ...'". Disabling extensions removes both the
      labels and that lookup.

    * `--//Telegram:disableProvisioningProfiles` must NOT be used here: rules_apple
      rejects a device build without a provisioning_profile attribute
      ("Building for device, but no provisioning_profile attribute was set").
      The app target keeps `:Telegram_xcode_profile`, which changes nothing about
      the output because `telegram_aps_environment` is "" for Xcode-managed
      codesigning — the IPA has no signing identity and installs via Sideloadly.
    """
    command = list(command)
    command.append("--//Telegram:disableExtensions")
    return command


def main():
    sys.path.insert(0, os.path.join(os.getcwd(), "build-system", "Make"))
    import BuildEnvironment  # noqa: E402  (path set above)

    original = BuildEnvironment.call_executable
    patched_state = {"done": False}

    def call_executable(command, **kwargs):
        # Patch before the first bazel invocation of this process. Every other
        # call (killall, curl, zip, ...) runs unchanged.
        if not patched_state["done"] and command and os.path.basename(str(command[0])).startswith("bazel-"):
            install_profiles()
            command = patch_bazel_args(command)
            print("[litgram] bazel build args:", " ".join(command))
            patched_state["done"] = True
        return original(command, **kwargs)

    BuildEnvironment.call_executable = call_executable
    sys.argv = [os.path.join(os.getcwd(), "build-system", "Make", "Make.py")] + sys.argv[1:]
    runpy.run_path("build-system/Make/Make.py", run_name="__main__")


if __name__ == "__main__":
    main()
