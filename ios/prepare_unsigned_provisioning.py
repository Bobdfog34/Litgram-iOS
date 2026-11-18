#!/usr/bin/env python3
"""Make an unsigned Telegram-iOS build analyse without provisioning profiles.

Why this exists
---------------
`Make.py build --xcodeManagedCodesigning` uses XcodeManagedCodesigningSource,
whose `copy_profiles_to_destination()` is a no-op. `resolve_configuration()`
therefore writes

    build-input/configuration-repository/provisioning/BUILD
        exports_files([
        ])

while every extension target in `Telegram/BUILD` references
`@build_configuration//provisioning:<Name>.mobileprovision` unconditionally. Bazel
analysis then fails with:

    no such target '@@build_configuration+//provisioning:Share.mobileprovision'
    ... referenced by '//Telegram:ShareExtension'

`generateProject` avoids this because Make.py force-disables extensions when
`--xcodeManagedCodesigning` is set, but `build` has no such switch, and Make.py's
`--bazelArguments` flag is parsed yet never applied.

What this does
--------------
Replaces the generated `provisioning/BUILD` with one that declares every profile
the repository ships for exactly this purpose under
`build-system/fake-codesigning/profiles` (Telegram.mobileprovision,
Share.mobileprovision, NotificationService.mobileprovision, ...). The files are
self-signed fixtures whose entitlements carry no `aps-environment`, so the
resulting app is still unsigned and `telegram_aps_environment` stays "".

Run it after `generateProject` and before `build`, from the upstream directory.
The wrapper that used to edit the bazel binary is deliberately gone: BazelLocation
verifies the binary's SHA256 and deletes anything that does not match.
"""

import os
import shutil
import sys

PROFILES_SOURCE = "build-system/fake-codesigning/profiles"
PROVISIONING_DIR = "build-input/configuration-repository/provisioning"

# Every profile the extensions and the app itself may reference. Keeping the list
# explicit means a missing file in the repository fails loudly here instead of
# turning into a confusing bazel analysis error later.
REQUIRED_PROFILES = [
    "Telegram.mobileprovision",
    "Share.mobileprovision",
    "NotificationContent.mobileprovision",
    "NotificationService.mobileprovision",
    "Intents.mobileprovision",
    "Widget.mobileprovision",
    "BroadcastUpload.mobileprovision",
]


def main():
    if not os.path.isdir(PROFILES_SOURCE):
        sys.exit("{} is missing; run from the Telegram-iOS upstream checkout".format(PROFILES_SOURCE))
    os.makedirs(PROVISIONING_DIR, exist_ok=True)

    copied = []
    for name in REQUIRED_PROFILES:
        source = os.path.join(PROFILES_SOURCE, name)
        if not os.path.isfile(source):
            sys.exit("required provisioning profile {} not found in {}".format(name, PROFILES_SOURCE))
        shutil.copyfile(source, os.path.join(PROVISIONING_DIR, name))
        copied.append(name)

    # The package must survive a bazel re-resolution, which rewrites this file from
    # whatever the codesigning source produced (nothing, for Xcode-managed).
    lines = ["exports_files(["]
    lines.extend('    "{}",'.format(name) for name in sorted(copied))
    lines.append("])")
    with open(os.path.join(PROVISIONING_DIR, "BUILD"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")

    print("provisioning package now declares {} profiles:".format(len(copied)))
    for name in sorted(copied):
        print("  -", name)


if __name__ == "__main__":
    main()
