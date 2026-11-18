#!/usr/bin/env python3
"""Install a bazel wrapper that disables app extensions for `build` invocations.

Why this exists
---------------
`Make.py build --xcodeManagedCodesigning` uses XcodeManagedCodesigningSource,
whose `copy_profiles_to_destination()` is a no-op. The `@build_configuration`
repository is therefore generated with an empty `provisioning/BUILD`, while
`//Telegram:ShareExtension` (and the other extensions) reference
`@build_configuration//provisioning:<Name>.mobileprovision` unconditionally.
Bazel analysis then fails with "no such target ... Share.mobileprovision".

`generateProject` dodges this by setting `disable_extensions = True` whenever
`--xcodeManagedCodesigning` is passed, but `build` does not. Make.py also parses
`--bazelArguments` yet never applies it. The only lever left is the bazel binary
itself, so this script renames it and drops a wrapper in its place that appends
`--//Telegram:disableExtensions` to `build` subcommands only. Query, generate and
any other invocations pass through untouched.

The resulting app has no Share/Notification/Widget/Intents/BroadcastUpload
extensions, which is exactly what an unsigned, sideload-installed IPA needs.
"""

import glob
import os
import shutil
import sys

MARKER = "; litgram-disable-extensions-wrapper"


def find_bazel():
    for pattern in ("build-input/bazel-*/bazel", "build-input/bazel-*"):
        for path in glob.glob(pattern):
            if os.path.isfile(path) and not path.endswith((".real", ".orig")):
                return path
    return None


def already_wrapped(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return MARKER in handle.read(4096)
    except OSError:
        return False


def main():
    target = find_bazel()
    if target is None:
        sys.exit("bazel binary not found under build-input/; run generateProject first")

    if already_wrapped(target):
        print("bazel wrapper already installed at", target)
        return

    real = target + ".real"
    if not os.path.exists(real):
        shutil.move(target, real)

    wrapper = "\n".join([
        "#!/bin/bash",
        "# " + MARKER.strip("; "),
        "ARGS=(\"$@\")",
        "if [ \"${ARGS[0]:-}\" = \"build\" ]; then",
        "  ARGS+=(--//Telegram:disableExtensions)",
        "fi",
        "exec \"{}\" \"${{ARGS[@]}}\"".format(real),
        "",
    ])
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(wrapper)
    os.chmod(target, 0o755)
    print("bazel wrapper installed at", target, "->", real)


if __name__ == "__main__":
    main()
