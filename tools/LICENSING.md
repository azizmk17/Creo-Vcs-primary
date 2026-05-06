# CreoVCS Licensing — Vendor Operations Guide

This document is for **you (the vendor/admin)** — it covers everything needed
to issue, deploy, and revoke licenses.  Keep the private key safe and this
file private.

---

## How It Works (Overview)

```
Your machine (private key)          Customer machine
──────────────────────────          ───────────────────────────────────────
keygen.py  → ed25519_private.key    creovcs.exe
             ed25519_public.key  →  (baked in via CREOVCS_PUBLIC_KEY_HEX)

sign_license.py → customer.lic  →  customer places next to creovcs.exe

sign_revocation.py → revoked.json → shared folder (\\server\share\)
                                     app checks this at every startup
```

- Licenses are **signed JSON files** (`.lic`).  A customer cannot forge or
  modify one without the private key.
- The **public key** is embedded in the running application via an environment
  variable.  It verifies signatures but cannot create them.
- The **private key** never leaves your machine.  Guard it like a password.
- Revocation is a **signed `revoked.json`** in the shared folder.  Any edit
  without re-signing is silently ignored by the app.

---

## File Map

```
creo_vcs_v4/
├── keys/
│   ├── ed25519_private.key   ← KEEP SECRET — never share or commit
│   └── ed25519_public.key    ← embed in each app deployment
│
├── tools/licensing/
│   ├── keygen.py             ← one-time key pair generation
│   ├── sign_license.py       ← issue a new license
│   └── sign_revocation.py   ← revoke a license
│
├── license_template.json     ← edit this, then run sign_license.py
│
└── \\server\share\
    ├── creo_vcs.db
    └── revoked.json          ← place here after sign_revocation.py
```

---

## Step 0 — Key Generation (done once, already complete)

You already have your key pair in `keys/`.  **Skip this step.**

If you ever need to rotate keys (e.g. private key compromised):
```powershell
# WARNING: rotating keys invalidates ALL existing customer licenses.
# You must re-sign every customer license and ship a new application build.
python tools/licensing/keygen.py --out-dir ./keys_new
```

Your current public key:
```
ab9020d80ccc70146c1eb9100b562022c46d326e7e9a2c0ce566591415064907
```

---

## Step 1 — Issue a License to a New Customer

### 1a. Edit the template

Open `license_template.json` and fill in the customer details:

```json
{
  "product":    "CreoVCS",
  "licensee":   "Customer Company Name",
  "issued_at":  "2026-02-18T00:00:00+00:00",
  "expires_at": "2027-02-18T00:00:00+00:00",
  "features":   [
    "bom_management",
    "commit",
    "snapshot",
    "advanced_diff",
    "admin",
    "baseline",
    "package_export"
  ],
  "version_constraint": ">=4.0,<5.0",
  "machine_id": []
}
```

**Key fields:**

| Field | Notes |
|-------|-------|
| `licensee` | Customer's company name — appears in About dialog |
| `issued_at` | Today's date in ISO-8601 UTC format |
| `expires_at` | Expiry date, or `null` for a perpetual license |
| `features` | Remove features the customer did not purchase |
| `machine_id` | Leave `[]` for floating (any machine). See §1b for node-locked |

**Available features:**

| Feature string | What it unlocks |
|----------------|----------------|
| `bom_management` | BOM page — view and edit parts |
| `commit` | Commit / validate / merge workflow |
| `snapshot` | Snapshot capture and restore |
| `advanced_diff` | STEP file diff analysis |
| `admin` | Admin panel (user management) |
| `baseline` | Baseline management |
| `package_export` | PDF/STEP package export |

### 1b. Node-locked license (optional)

If the customer should be locked to specific machines, get each machine's ID:

```powershell
# Run this on the customer's machine:
python -c "from core.licensing.machine import get_machine_id; print(get_machine_id())"
```

Then put the IDs in the template:
```json
"machine_id": ["a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"]
```

### 1c. Sign and produce the `.lic` file

```powershell
python tools/licensing/sign_license.py `
    --template license_template.json `
    --private-key keys/ed25519_private.key `
    --out customer_companyname_2026.lic
```

The tool prints a summary — verify the details are correct before sending.

### 1d. Deliver to the customer

Send the customer **two files**:

1. `creovcs.exe` (the application)
2. `customer_companyname_2026.lic` (their license)
3. `setup_license.exe` (the license installer — compiled from `setup_license.py`)

**Customer steps (one time only):**

1. Run `setup_license.exe` (or double-click it).
2. Click **Browse…**, select their `.lic` file, click **Install License**.
3. Done — the license is saved to `%APPDATA%\CreoVCS\creovcs.lic` and
   CreoVCS will find it automatically from that point on, no matter where
   `creovcs.exe` is placed.

The app looks for the license in this order:
1. `CREOVCS_LICENSE_PATH` env var (for dev/testing only)
2. `%APPDATA%\CreoVCS\creovcs.lic` ← installed by `setup_license.exe`
3. `creovcs.lic` next to the exe (legacy fallback)

---

## Step 2 — Deploy the Application

The application needs two things at startup:

| Item | How to set it |
|------|--------------|
| Public key | `CREOVCS_PUBLIC_KEY_HEX` environment variable |
| License file path | `CREOVCS_LICENSE_PATH` environment variable (default: `creovcs.lic` next to the exe) |

**Running manually (development / testing):**
```powershell
$env:CREOVCS_PUBLIC_KEY_HEX = "ab9020d80ccc70146c1eb9100b562022c46d326e7e9a2c0ce566591415064907"
$env:CREOVCS_LICENSE_PATH   = "customer.lic"
python main3.py
```

**For a distributed `.exe`:**
The public key is already hardcoded in `main3.py` as `_PRODUCTION_PUBLIC_KEY`.
Customers need no environment setup — just the `.exe` and their `.lic` file.
The `CREOVCS_PUBLIC_KEY_HEX` env var can still override it for development.

---

## Step 3 — Revoke a License

### 3a. Get the signature to revoke

Open the customer's `.lic` file, copy the `"signature"` value:

```json
{
  ...
  "signature": "9n4Q1N7ZwXO30QLFMtcIKhTdgKANfMbZTocRijukmM7Hn4zHAAtu8NHZFc5KF46JI9PArkHecXubI-Px7m-mCw"
}
```

### 3b. Run the revocation tool

**First ever revocation** (creates `revoked.json` from scratch):
```powershell
python tools/licensing/sign_revocation.py `
    --add "9n4Q1N7ZwXO30QLFMtcIKhTdgKANfMbZTocRijukmM7Hn4zHAAtu8NHZFc5KF46JI9PArkHecXubI-Px7m-mCw" `
    --private-key keys/ed25519_private.key `
    --out "\\server\share\revoked.json"
```

**Adding another revocation** to an existing list:
```powershell
python tools/licensing/sign_revocation.py `
    --add "anotherCustomerSignatureHere" `
    --existing "\\server\share\revoked.json" `
    --private-key keys/ed25519_private.key `
    --out "\\server\share\revoked.json"
```

**Revoking multiple at once** (comma-separated):
```powershell
python tools/licensing/sign_revocation.py `
    --add "sig1,sig2,sig3" `
    --private-key keys/ed25519_private.key `
    --out "\\server\share\revoked.json"
```

### 3c. Result

The next time any user with that `.lic` file starts CreoVCS, they see:

> *"This license has been revoked by the administrator. Contact your
> administrator for a new license."*

No rebuild, no redistribution required.  All other users are unaffected.

### Why users cannot bypass the revocation

`revoked.json` is cryptographically signed with your private key.  If a user
edits the file (e.g. removes their signature), the `"rl_signature"` field
becomes invalid and the app **ignores the whole file** (fail-open — other
users are unaffected).  A user cannot produce a valid `revoked.json` without
your private key.

---

## Step 4 — Publish a New Version (Update Notification)

When you release a new version of CreoVCS, publish it to the shared database.
Users running an older version will see a non-blocking toast notification on
startup — they are **not** blocked from working.

### 4a. How it works

Two rows are written to the `app_metadata` table in the shared `creo_vcs.db`:

| key | value |
|-----|-------|
| `latest_version` | `"4.1.0"` (plain semver string) |
| `latest_version_sig` | Ed25519 signature of the version string |

The signature prevents users from editing the version number in a SQLite
browser to suppress the notification.  Any edit invalidates the signature and
the check is silently skipped (fail-open — user is never blocked).

### 4b. Run the update tool

```powershell
# Publish to the local DB:
python tools/licensing/update_version.py `
    --version 4.1.0 `
    --private-key keys/ed25519_private.key `
    --db creo_vcs.db

# Publish directly to the shared network DB:
python tools/licensing/update_version.py `
    --version 4.1.0 `
    --private-key keys/ed25519_private.key `
    --db "\\server\share\creo_vcs.db"
```

### 4c. Result

Users whose `APP_VERSION` is older than `4.1.0` will see, 0.5 s after the
main window opens:

> *"A newer version of CreoVCS is available (4.1.0). You are running version
> X.Y. Please contact your administrator to update."*

The message also appears in the status bar for 10 seconds.  The user can
continue working normally.

---

## Step 5 — Renewal

When a license expires, the customer simply cannot start the app.  To renew:

1. Edit `license_template.json` with a new `expires_at` date.
2. Run `sign_license.py` again (§1c).
3. Send the new `.lic` file to the customer.

The customer replaces their old `.lic` file with the new one.

---

## Error Messages (What Customers See)

| Error | Cause | Your action |
|-------|-------|-------------|
| *"No public key configured"* | `CREOVCS_PUBLIC_KEY_HEX` not set | Check deployment config |
| *"License file not found"* | `.lic` file missing | Customer to place it correctly |
| *"License signature is invalid"* | `.lic` file tampered or wrong key | Re-issue or investigate |
| *"License expired on YYYY-MM-DD"* | Past `expires_at` | Issue renewal |
| *"This machine is not authorised"* | Node-locked, wrong machine | Add machine ID to license |
| *"This license has been revoked"* | In `revoked.json` | Intentional — or contact you |

---

## Security Checklist

- [ ] `keys/ed25519_private.key` is **not** committed to git (add `keys/` to `.gitignore`)
- [ ] Private key is backed up in a secure location (password manager / encrypted drive)
- [ ] `revoked.json` in the shared folder is **readable** by all users but only **writable** by the admin account (set NTFS permissions)
- [ ] `CREOVCS_PUBLIC_KEY_HEX` is not stored in source code — use a build script or env var
- [ ] License files issued per-customer are tracked (keep a log of who has what signature)

---

## Quick Reference

```powershell
# Issue a license
python tools/licensing/sign_license.py --template license_template.json --private-key keys/ed25519_private.key --out customer.lic

# Revoke a license (first time)
python tools/licensing/sign_revocation.py --add "<signature>" --private-key keys/ed25519_private.key --out "\\server\share\revoked.json"

# Revoke (append to existing list)
python tools/licensing/sign_revocation.py --add "<signature>" --existing "\\server\share\revoked.json" --private-key keys/ed25519_private.key --out "\\server\share\revoked.json"

# Publish a new version to the shared DB (triggers update notification for older clients)
python tools/licensing/update_version.py --version 4.1.0 --private-key keys/ed25519_private.key --db "\\server\share\creo_vcs.db"

# Get a machine's ID (run on the target machine)
python -c "from core.licensing.machine import get_machine_id; print(get_machine_id())"

# Test the application locally
$env:CREOVCS_PUBLIC_KEY_HEX = "ab9020d80ccc70146c1eb9100b562022c46d326e7e9a2c0ce566591415064907"
$env:CREOVCS_LICENSE_PATH   = "customer.lic"
python main3.py
```
