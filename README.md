# SIT Output Quality Checker - Web (upload-based)

This is the hosted counterpart to the local `QCChecker` tool. Same checks,
same report, byte-for-byte identical `qc/`/`reference/`/`tests/` packages -
the only difference is how a filesystem path gets in front of the checker:
instead of typing a local path, you upload an archive of your output folder.
It's extracted into a private temp directory on the server for your session
and checked from there, then discarded.

**This always runs the complete, non-sampled-by-default check pipeline
(the same one as the desktop tool) against your real uploaded data - never
a reduced or pre-sampled version.** For very large folders this means it
can take a while (see "How long will this take?" below) - that's a
deliberate tradeoff: this tool never trades completeness for speed.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy (Streamlit Community Cloud)

1. Push this folder to its own GitHub repo (or a subfolder - Streamlit Cloud
   lets you point at a specific file path within a repo).
2. On [share.streamlit.io](https://share.streamlit.io), create a new app
   pointing at this repo's `app.py`.
3. That's it - `requirements.txt` and `.streamlit/config.toml` are picked up
   automatically.

## Supported archive formats

`.zip`, `.7z`, `.tar`, `.tar.gz`, `.tgz`, `.tar.bz2`. `.rar` is deliberately
**not** supported - reading it needs the external `unrar` binary, which
generally isn't installable on managed/shared hosting; re-compress as `.zip`
or `.7z` instead.

Every extraction is checked against zip-slip path-traversal attacks (a
malicious archive entry trying to write outside the target directory) before
any file is written - required for any app that accepts uploads from
untrusted users, which a publicly reachable app must always assume.

## How long will this take? (real numbers, not estimates)

Tested end-to-end against a real ~14,700-document SIT output folder,
packaged as a 93MB `.7z` archive with 75,055 individual files:

| Step | Time |
|---|---|
| Extract archive | ~4.5 minutes |
| Discover Version_* runs | <1 second |
| Run all checks (sampled, default settings) | ~1 minute |
| **Total** | **~5.5 minutes** |

**Extraction time scales with file *count*, not archive byte-size** - this
93MB archive with 75,055 small files took far longer to extract than its
size alone would suggest, because of per-file filesystem overhead. This
matters a lot for planning: a 15GB folder could be small-file-heavy (in
which case, extrapolating the numbers above, extraction alone could take
**hours**) or could be a smaller number of large files (in which case it
would extract much faster than that extrapolation suggests). There's no way
to know which without testing the actual folder.

**Practical implication:** hosted platforms (including Streamlit Community
Cloud) generally kill requests/sessions that run for a very long time, and
browsers/networks aren't reliable over multi-hour uploads either. This tool
places no artificial cap on size or file count - but for your very largest
folders (rather than assuming it will "just work, however long it takes"),
test against a real one on your actual target host before relying on it, and
budget for the possibility that extraction time (not upload bandwidth, and
not the checks themselves) is the real bottleneck at large scale.

## What's identical to the desktop QCChecker

Everything except the input mechanism: the full checklist (folder structure,
context_output_normalized field/language audit, corpus/metadata field
completeness, chunk label/sit_found consistency, SITGrader detail,
scenario_id consistency, version-path consistency, and all the rest),
the same glanceable report UI, and JSON/HTML/PDF export.

The `qc/`, `reference/`, and `tests/` folders here are direct copies of
`QCChecker`'s - if you fix a bug or add a check in one, copy it to the other
to keep them in sync. (`qc/streamlit_report.py` holds the actual report
*rendering* code shared by both apps' `app.py`, so at least that part can't
drift between them.)

## Data handling / privacy note

Uploaded archives are extracted to a temporary directory on whatever server
runs this app, for the duration of your session. Use the **Clear uploaded
data** button when you're done, especially since SIT output data is designed
to look like real sensitive information (SSNs, passport numbers, national
IDs) even though it's synthetic. If your organization has policies against
uploading such data to third-party hosting, use the local `QCChecker` tool
instead - it never sends your data anywhere.
