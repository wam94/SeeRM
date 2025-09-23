# SeeRM Mac Onboarding Playbook

This guide explains how to prepare a double-clickable macOS experience for
non-technical teammates. It builds on the helper scripts in `scripts/onboarding`
and assumes you will wrap them with Automator or Platypus when producing the
final `.app` bundles.

## 1. Prepare the 1Password vault

1. Create or choose a shared vault (e.g. **SeeRM Deployment**).
2. Add a new item with a secure note or document field that contains the
   production `.env` contents. The bootstrapper expects to read it via
   `op://<Vault>/<Item>/.env`.
3. Store Gmail OAuth client credentials (`GMAIL_CLIENT_ID` and
   `GMAIL_CLIENT_SECRET`) and Notion keys in the same `.env` payload so all
   secrets stay in sync.
4. Share the vault with everyone who will run the Mac installer.

> Tip: Encourage teammates to set up the 1Password CLI once via `1Password →
> Developer → Connect CLI`. The bootstrapper validates that the session is
> unlocked before doing anything destructive.

## 2. Package the bootstrap app

The bootstrapper lives at `scripts/onboarding/bootstrap_teammate.py`. Wrap it
in an Automator “Run Shell Script” action or a Platypus app with arguments similar
to:

```bash
"$REPO/scripts/onboarding/bootstrap_teammate.py" \
  --op-env-reference "op://SeeRM Deployment/.env" \
  --version "$(git describe --tags --always)"
```

What the script handles:

- Pulls the shared secrets from 1Password and writes `~/.seerm/.env`
- Creates/refreshed the repository virtualenv using Python 3.11
- Installs dependencies and performs `python -m app.main health`
- Records the release version in `~/.seerm/version` for update checks

If Python 3.11 or the 1Password CLI is missing the script surfaces a friendly
error so the wrapper can show guidance.

### Use the generated .app bundles (optional)

Instead of hand-building Automator flows you can run the bundler:

```bash
python scripts/onboarding/build_mac_apps.py \
  --version 2024.09.15 \
  --op-env-reference "op://SeeRM Deployment/.env" \
  --manifest-url "https://downloads.example.com/seerm/latest.json"
```

This writes three ready-to-ship bundles into `dist/mac/`:

- `Setup SeeRM.app` (bootstrapper)
- `SeeRM Gmail Auth.app` (OAuth helper)
- `SeeRM Control Center.app` (daily operations)

The launcher scripts inside each bundle resolve the repository root relative to
the app location, so keep the `.app` directories alongside the SeeRM repo when
you generate your DMG/zip.

## 3. Run Gmail OAuth once per account

`scripts/onboarding/gmail_oauth_setup.py` guides teammates through the Google
consent screen and captures the refresh token. Suggested wrapper command:

```bash
"$REPO/scripts/onboarding/gmail_oauth_setup.py" \
  --op-env-reference "op://SeeRM Deployment/.env"
```

The helper reads the existing `.env`, launches the OAuth flow, writes the new
`GMAIL_REFRESH_TOKEN` locally, and (unless `--skip-1password`) pushes the updated
payload back into the shared 1Password item so the bootstrapper stays current.

If each teammate needs a unique mailbox, create one item per user and pass the
appropriate reference. The script is idempotent, so they can rerun it whenever a
refresh token is revoked.

## 4. Ship the control center

`scripts/onboarding/control_center.py` provides a Tkinter GUI with buttons for
common workflows—health check, digest dry run, full digest, and update check.
Package it as the main app your teammates will open day-to-day. When combined
with the bootstrapper it creates a turnkey “install, authenticate Gmail, click
run” experience.

By default the update checker looks for `SEERM_UPDATE_MANIFEST` or falls back to
`https://example.com/seerm/latest.json`. Publish a JSON manifest (see
`files/update_manifest.template.json`) containing the latest version metadata.

## 5. Release cadence

1. Tag the repository (`git tag vYYYY.MM.DD`) at the commit you want to ship.
2. Run your packaging workflow (e.g. build DMG with the wrapped apps).
3. Upload the artefact to your distribution location (S3, corporate CDN, etc.).
4. Update the manifest JSON with the new version number and download URL.
5. Notify teammates that an update is available or let the control center’s
   “Check for updates” button handle it automatically.

## 6. Testing on a clean Mac

- Use a fresh macOS VM or a sacrificial device to run the bootstrapper end to end.
- Verify the Gmail helper writes a valid refresh token and that the digest dry run
  completes without manual intervention.
- Confirm the control center buttons surface subprocess errors (bad credentials,
  network outages) as native alert dialogs so non-technical users receive clear
  guidance.

## 7. Troubleshooting quick wins

- **Missing 1Password session**: Have the teammate open the 1Password desktop
  app and unlock the vault, then rerun the app.
- **Python missing**: Ship Apple’s universal installer in the DMG and call it
  when the bootstrapper flags the problem.
- **Gmail token revoked**: Re-run the Gmail OAuth helper; it overwrites the old
  token locally and in 1Password.
- **Slow installs**: Pre-build wheels or a self-contained Python environment and
  host it alongside the DMG so the bootstrapper only has to unpack.

These scaffolds are intentionally light so you can adapt them to your internal
IT policies while preserving the zero-terminal experience for the team.
