# Litgram iOS

iOS client for the Litgram server. The application code is Telegram's open
source [Telegram-iOS](https://github.com/TelegramMessenger/Telegram-iOS); this
repository only carries the Litgram configuration and the build workflow.

## Build

GitHub Actions builds an **unsigned** `.ipa` on a macOS runner
(`.github/workflows/build-ios.yml`, manual `workflow_dispatch`). An unsigned
IPA installs through AltStore/Sideloadly with a personal Apple ID, or on a
registered device with a paid team profile.

## Configuration

- Bundle identifier: `org.litgram.ios`
- Product name: `Litgram`
- API credentials: the ones already used by the Litgram Android build.

Telegram's guidelines apply: do not call the app Telegram and do not use their
logo.