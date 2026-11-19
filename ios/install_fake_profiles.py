#!/usr/bin/env python3
"""Install fake provisioning profiles where Xcode's finder looks for them.

Background
----------
`Make.py generateProject --xcodeManagedCodesigning` writes an empty provisioning
package and sets `telegram_use_xcode_managed_codesigning = True`, which makes
`//Telegram:Telegram` reference `:Telegram_xcode_profile` and, separately, a
`local_provisioning_profile` rule named `Telegram_local_profile`.

Building that rule runs
`rules_apple/tools/local_provisioning_profile_finder`, which scans
`~/Library/MobileDevice/Provisioning Profiles` for a profile whose **Name**
matches the requested string — in our case:

    iOS Team Provisioning Profile: org.litgram.ios

A GitHub-hosted runner has that directory empty, so analysis fails with
"no provisioning profile was found named '...'". The rule cannot be disabled:
rules_apple rejects a device build without a provisioning_profile attribute.

`Make.py generate-verification-profiles` would produce correctly signed profiles,
but under --xcodeManagedCodesigning `resolve_configuration` copies no profiles into
`build-input/remote-input/profiles`, so it reports "Generated 0 profiles".

What this does
--------------
Re-signs each fixture from `build-system/fake-codesigning/profiles` with the
fixture certificate `build-system/fake-codesigning/certs/SelfSigned.p12`, rewrites
its `Name` to the exact string the finder asks for, and drops the result into
`~/Library/MobileDevice/Provisioning Profiles`. The certificate is self-signed,
so the IPA produced from these profiles carries no real Apple signature and still
installs through Sideloadly/AltStore.

Run from the upstream checkout, after `generateProject`.
"""

import base64
import datetime
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile

PROFILES_SOURCE = "build-system/fake-codesigning/profiles"
PROVISIONING_DIR = "build-input/configuration-repository/provisioning"
CERT_PATH = "build-system/fake-codesigning/certs/SelfSigned.p12"
INSTALL_DIR = os.path.expanduser("~/Library/MobileDevice/Provisioning Profiles")

# bundle id -> (fixture profile, Name the finder asks for)
# local_provisioning_profile_finder requests "iOS Team Provisioning Profile: <id>"
# for the app and for every extension; the .appex ones are not reached because
# extensions are disabled, but installing them costs nothing and keeps a future
# non-disabled build working.
PROFILES = {
    "org.litgram.ios": "Telegram.mobileprovision",
    "org.litgram.ios.Share": "Share.mobileprovision",
    "org.litgram.ios.NotificationContent": "NotificationContent.mobileprovision",
    "org.litgram.ios.NotificationService": "NotificationService.mobileprovision",
    "org.litgram.ios.SiriIntents": "Intents.mobileprovision",
    "org.litgram.ios.Widget": "Widget.mobileprovision",
    "org.litgram.ios.BroadcastUpload": "BroadcastUpload.mobileprovision",
}


def run(command, **kwargs):
    result = subprocess.run(command, capture_output=True, **kwargs)
    if result.returncode != 0:
        raise SystemExit("{} failed: {}".format(
            " ".join(command), result.stderr.decode("utf-8", "replace")))
    return result.stdout


def setup_keychain(p12_path):
    """Import the fixture certificate into a keychain `security cms -S` can use."""
    name = "litgram-fake.keychain"
    password = "litgram"

    subprocess.run(["security", "delete-keychain", name], capture_output=True)
    run(["security", "create-keychain", "-p", password, name])
    run(["security", "set-keychain-settings", name])
    run(["security", "unlock-keychain", "-p", password, name])
    run(["security", "import", p12_path, "-k", name, "-P", "",
         "-T", "/usr/bin/codesign", "-T", "/usr/bin/security"])
    run(["security", "set-key-partition-list", "-S", "apple-tool:,apple:",
         "-k", password, name])

    existing = run(["security", "list-keychains", "-d", "user"]).decode("utf-8")
    existing = [line.strip().strip('"') for line in existing.splitlines() if line.strip()]
    run(["security", "list-keychains", "-d", "user", "-s", name] + existing)
    return name


def signing_identity(p12_path):
    """Common Name of the fixture certificate, used for `security cms -N`."""
    pem = run(["openssl", "pkcs12", "-in", p12_path, "-passin", "pass:",
               "-nokeys", "-legacy"])
    subject = run(["openssl", "x509", "-noout", "-subject", "-nameopt", "oneline"],
                  input=pem).decode("utf-8")
    if "CN = " not in subject:
        raise SystemExit("could not read CN from " + p12_path)
    return subject.split("CN = ")[-1].split(",")[0].strip()


def certificate_der(p12_path):
    pem = run(["openssl", "pkcs12", "-in", p12_path, "-passin", "pass:",
               "-nokeys", "-legacy"])
    der = run(["openssl", "x509", "-outform", "DER"], input=pem)
    return base64.b64encode(der).decode("utf-8")


def rewrite_profile(source, destination, bundle_id, cert_b64, identity, keychain):
    """Rewrite Name/identifier, then re-sign the profile with the fixture cert.

    Both steps happen on one decode: `security cms -D` only reads a profile that
    is still signed by whatever it was signed with, and re-signing with the
    fixture certificate produces a blob `security cms -D` refuses to decode again
    ("failed to add data to decoder"). So mutate the plist once, then sign.
    """
    raw = run(["security", "cms", "-D", "-i", source])
    with tempfile.NamedTemporaryFile("wb", suffix=".plist", delete=False) as handle:
        handle.write(raw)
        plist_path = handle.name
    try:
        with open(plist_path, "rb") as handle:
            profile = plistlib.load(handle)

        entitlements = profile.setdefault("Entitlements", {})
        old_id = entitlements.get("application-identifier", "")
        team_id = old_id.split(".")[0] if "." in old_id else "ABCDE12345"

        # local_provisioning_profile_finder matches on Name for this bundle id.
        profile["Name"] = "iOS Team Provisioning Profile: " + bundle_id
        profile["UUID"] = "litgram-" + bundle_id.replace(".", "-")
        entitlements["application-identifier"] = "{}.{}".format(team_id, bundle_id)
        entitlements["com.apple.developer.team-identifier"] = team_id
        # rules_apple validates every entitlement of the target against the
        # profile's Entitlements and fails with "uses entitlements
        # com.apple.security.application-groups value of X, but the profile does
        # not support it". The fixture profiles carry Telegram's group
        # (group.ph.telegra.Telegraph) while the app declares ours, so the group
        # list has to be rewritten to match the bundle being built.
        entitlements["com.apple.security.application-groups"] = ["group." + bundle_id]
        # The finder matches TeamIdentifier[:] against the requested team id, so the
        # top-level list has to agree with the entitlement.
        profile["TeamIdentifier"] = [team_id]
        # Expiry is irrelevant to the finder (it skips the check) but keep it
        # clearly valid so nothing downstream rejects the profile on sight.
        profile["ExpirationDate"] = datetime.datetime(2035, 1, 1, tzinfo=datetime.timezone.utc)
        if "CreationDate" not in profile:
            profile["CreationDate"] = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        # A push entitlement would make codesign demand a real identity; the whole
        # point is an unsigned build.
        entitlements.pop("aps-environment", None)
        profile["DeveloperCertificates"] = [base64.b64decode(cert_b64)]
        profile.pop("DER-Encoded-Profile", None)

        with open(plist_path, "wb") as handle:
            plistlib.dump(profile, handle)

        run(["security", "cms", "-S", "-k", keychain, "-N", identity,
             "-i", plist_path, "-o", destination])
    finally:
        os.unlink(plist_path)


def main():
    if not os.path.isdir(PROFILES_SOURCE):
        sys.exit("run from the Telegram-iOS upstream checkout")
    if not os.path.isfile(CERT_PATH):
        sys.exit("missing fixture certificate " + CERT_PATH)

    os.makedirs(INSTALL_DIR, exist_ok=True)
    keychain = setup_keychain(CERT_PATH)
    identity = signing_identity(CERT_PATH)
    cert_b64 = certificate_der(CERT_PATH)
    print("using signing identity:", identity)

    installed = []
    for bundle_id, fixture in PROFILES.items():
        source = os.path.join(PROFILES_SOURCE, fixture)
        if not os.path.isfile(source):
            print("skipping missing fixture:", source)
            continue
        destination = os.path.join(INSTALL_DIR, "litgram-{}.mobileprovision".format(
            bundle_id.replace(".", "-")))
        rewrite_profile(source, destination, bundle_id, cert_b64, identity, keychain)
        installed.append(bundle_id)

    print("installed {} profiles into {}".format(len(installed), INSTALL_DIR))
    for bundle_id in installed:
        print("  - iOS Team Provisioning Profile:", bundle_id)

    # `ios_extension` targets in Telegram/BUILD reference
    # @build_configuration//provisioning:<Name>.mobileprovision directly (see
    # ShareExtension's provisioning_profile select), and plisttool reads the
    # extension's application-identifier out of THAT file. Copying the raw fixture
    # therefore failed with:
    #   the bundle_id ("org.litgram.ios.Share") did not match the id in the
    #   entitlements ("ph.telegra.Telegraph.Share")
    # The rewritten profile (correct bundle id, team id and app group) has to go
    # in its place, under the fixture name the BUILD file asks for.
    os.makedirs(PROVISIONING_DIR, exist_ok=True)
    declared = []
    for bundle_id, fixture in sorted(PROFILES.items()):
        source = os.path.join(PROFILES_SOURCE, fixture)
        if not os.path.isfile(source):
            continue
        rewritten = os.path.join(INSTALL_DIR, "litgram-{}.mobileprovision".format(
            bundle_id.replace(".", "-")))
        if not os.path.isfile(rewritten):
            continue
        shutil.copyfile(rewritten, os.path.join(PROVISIONING_DIR, fixture))
        declared.append(fixture)
    lines = ["exports_files(["]
    lines.extend('    "{}",'.format(name) for name in sorted(declared))
    lines.append("])")
    with open(os.path.join(PROVISIONING_DIR, "BUILD"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print("provisioning package declares {} rewritten profiles".format(len(declared)))


if __name__ == "__main__":
    main()
