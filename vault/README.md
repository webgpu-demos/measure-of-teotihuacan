# datacheck: what's in the data, and proof it arrived intact

Part of Step 7 (store the lidar like evidence). One small script, `datacheck.py`, does three jobs:

| Command | When | What it writes | Who may see it |
|---|---|---|---|
| `specs` | Before buying the laptop | `specs.txt`: totals only (file counts and sizes by kind, lidar points, area, density, a disk-size estimate) | The team, and Claude if you like: it has no file names and no coordinates |
| `manifest` | Right after loading the laptop | `manifest.csv`: a SHA-256 fingerprint of every file | Stays with the data |
| `verify` | When the laptop arrives, and before any analysis | A list of missing, changed and extra files | The team |

`specs` reads only each LAS/LAZ file's header, so it takes seconds even on hundreds of gigabytes. `manifest` and `verify` read every byte, so expect roughly 2–20 minutes per 100 GB, from a fast internal SSD to an external hard drive.

`specs.txt` holds totals only, so it may go to Claude like any other derived number. **Never paste `manifest.csv` into an AI chat** (Claude, ChatGPT, Copilot or any other): it names every file and stays with the data.

## Steps for preparing the laptop

**1. Install Python** (once). On Windows, get Python 3.9 or newer from [python.org](https://www.python.org/downloads/) and tick "Add python.exe to PATH" in the installer. Nothing else is needed: the script uses only Python's standard library.

**2. Measure the data, before choosing the laptop.** Copy `datacheck.py` onto the computer that holds the data (the Desktop is fine, outside the data folders), open a terminal where it is and run, with your own data folders:

```
py datacheck.py specs D:\teo-data -o specs.txt
```

On a Mac or Linux, use `python3` instead of `py`. You can list several folders, for example `specs D:\lidar E:\cad -o specs.txt`. Send `specs.txt` to Jason. It gives the lidar's area, point density and the finest DEM it supports, plus how big the laptop's SSD should be.

Headers can't say whether the ground points are classified. Ask the vendor, and add the answer when you send `specs.txt`.

**3. Prepare the laptop before any data goes on it:**
- Turn on full-disk encryption (BitLocker on Windows, FileVault on a Mac). Keep the recovery key somewhere other than the laptop and its box.
- Turn off cloud sync: OneDrive backs up Documents and the Desktop by default, and so do Dropbox and iCloud.
- Give each person who will use the data their own account.
- Check that the permit allows the data to travel on this laptop, along the whole route.

**4. Load the data, then fingerprint it on the laptop:**

```
py datacheck.py manifest D:\teo-data -o D:\teo-data-manifest.csv
```

It prints its progress every few seconds and, at the end, a fingerprint of the manifest itself. Write that fingerprint down and send it separately from the laptop, for example in a message to Jason. It shows later that nobody edited the manifest. Keep `manifest.csv` with the data, on the laptop and on the backup drive.

**5. Make the backup drive** (encrypted too), copy the data and the manifest onto it, and run `verify` against the backup as well. Ship it separately from the laptop.

**6. When the laptop arrives:**

```
py datacheck.py verify D:\teo-data D:\teo-data-manifest.csv
```

First check that the manifest's own fingerprint matches the one written down. Then `verify` lists any file that is missing, changed or extra, and ends with "All good." if there are none. Add `--quick` to compare sizes only, which takes seconds but can miss a changed file of the same size.

## Tests

The tests use made-up LAS/LAZ headers, and cross-check the header reader against `laspy` when it is installed:

```bash
.venv/bin/python -m pytest vault/tests -q
```
