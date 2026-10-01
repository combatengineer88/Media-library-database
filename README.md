# Media Library Database

Windows desktop media catalog with local SQLite storage, TMDB metadata matching, TV Series → Season → Episode organization, manual Fix Match, and built-in GitHub release updates.

## Current version
2.4.0

## Update channel
The desktop app checks this repository's **latest GitHub Release**. Release tags use `vMAJOR.MINOR.PATCH` and the updater expects an asset named:

`Media-Library-Database-update.zip`

The application database and user configuration live under `%LOCALAPPDATA%\\MediaLibraryDB`, outside the replaceable application files.

## Release automation
`.github/workflows/release.yml` builds a Windows PyInstaller package when a `v*` tag is pushed and publishes the updater ZIP as a GitHub Release asset.

## Privacy / access requirement
Installed copies intentionally do not contain a GitHub personal access token. Therefore this repository (or a future separate update repository) must be public for automatic release checks to work.
