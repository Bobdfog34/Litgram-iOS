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
import subprocess
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
    """Install the rewritten profiles where both consumers look for them.

    Delegates to install_fake_profiles.py, which re-signs each fixture with the
    fake certificate and rewrites its bundle id, team id and application group to
    match the app being built. Copying the original fixtures here (the previous
    behaviour) left "ph.telegra.Telegraph.Share" inside the profile, and analysis
    then failed with:
        the bundle_id ("org.litgram.ios.Share") did not match the id in the
        entitlements ("ph.telegra.Telegraph.Share")
    """
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "install_fake_profiles.py")
    result = subprocess.run([sys.executable, script], capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit("install_fake_profiles.py failed:\n" + result.stdout + result.stderr)
    print(result.stdout.strip())


def patch_bazel_args(command):
    """Pass bazel arguments through unchanged.

    `--//Telegram:disableExtensions` is deliberately NOT set. With it the IPA
    built fine but the installed app showed a black screen: the main executable
    was 0.1 MB while TelegramUIFramework was 116.8 MB, i.e. the entry point never
    ran because everything had been compiled into frameworks only.

    Extensions now build for real. Their provisioning labels resolve because
    install_fake_profiles.py rewrites each fixture's bundle id, team id and
    application group to match the app being built — that was the last failing
    step of analysis.
    """
    return list(command)


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
