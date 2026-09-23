"""SIT Output Quality Checker - Web (upload-based) entrypoint.

This is the hosted counterpart to the local-path QCChecker app: identical
checks, identical report rendering (both import the same qc/ package,
copied byte-for-byte from QCChecker) - the ONLY difference is how a
filesystem path gets in front of qc.discovery. A hosted app has no access
to a user's local disk, so instead of typing a path, the user uploads an
archive of their output folder; it's extracted into a private temp
directory on the server for the duration of the session and checked from
there.

Run:
    pip install -r requirements.txt
    streamlit run app.py

Deploy: push this folder to its own GitHub repo, then point Streamlit
Community Cloud at app.py in that repo. See README.md for details and for
the size/privacy tradeoffs of this upload-based approach.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from qc.discovery import discover
from qc.models import strip_root_prefix
from qc.progress import make_reporter
from qc.registry import run_all
from qc.streamlit_report import render_report, render_run_all_summary_table
from reference.sit_packs_report import build_report, check_sit_language
from upload_utils import (
    UnsafeArchiveError,
    UnsupportedArchiveError,
    cleanup_dir,
    new_temp_dir,
    safe_extract,
)

USER_GUIDE_PATH = Path(__file__).parent / "User_Guide.docx"


def _user_guide_download_button(key: str) -> None:
    if not USER_GUIDE_PATH.exists():
        return
    st.download_button(
        "📖 Download User Guide (Word)",
        data=USER_GUIDE_PATH.read_bytes(),
        file_name="SIT_Quality_Checker_User_Guide.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        key=key,
        help="A plain-language, step-by-step guide to using this tool.",
    )

st.set_page_config(page_title="SIT Output Quality Checker", layout="wide")

# Streamlit's file_uploader auto-generates a "<size> per file - <types>" line
# under the drop zone with no parameter to customize it. Hide that built-in
# line and show our own accepted-formats caption instead (file types only,
# no size limit called out - the size cap is a technical config detail, not
# something a user needs to see every time).
st.markdown(
    '<style>[data-testid="stFileUploaderDropzoneInstructions"] { display: none; }</style>',
    unsafe_allow_html=True,
)

UPLOAD_TYPES = ["zip", "7z", "tar", "gz", "tgz", "bz2"]
ARCHIVE_ACCEPTED_FORMATS = "Accepted formats: .zip, .7z, .tar, .tar.gz, .tgz, .tar.bz2"
ARCHIVE_HELP = (
    "Supported: .zip, .7z, .tar, .tar.gz, .tgz, .tar.bz2. "
    ".rar is not supported here (it needs a system tool this host doesn't have) - "
    "re-compress as .zip or .7z instead."
)


def _get_or_extract(uploaded_file, cache_key: str) -> Path | None:
    """Extract an uploaded archive into a cached temp dir, re-using the
    extraction across Streamlit reruns (the whole script re-executes on
    every widget interaction) unless the uploaded file actually changed."""
    if uploaded_file is None:
        return None
    upload_id = f"{uploaded_file.name}:{uploaded_file.size}"
    cache = st.session_state.setdefault("_archive_cache", {})
    cached = cache.get(cache_key)
    if cached and cached["upload_id"] == upload_id:
        return Path(cached["extract_root"])

    if cached:
        cleanup_dir(cached.get("tmp_root"))

    tmp_root = new_temp_dir("qcweb_")
    archive_path = tmp_root / f"upload_{uploaded_file.name}"
    with open(archive_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    extract_root = tmp_root / "extracted"
    try:
        with st.spinner(f"Extracting {uploaded_file.name}..."):
            count = safe_extract(archive_path, extract_root, uploaded_file.name)
    except (UnsafeArchiveError, UnsupportedArchiveError) as exc:
        st.error(f"Could not use '{uploaded_file.name}': {exc}")
        cleanup_dir(tmp_root)
        return None

    cache[cache_key] = {
        "upload_id": upload_id,
        "tmp_root": str(tmp_root),
        "extract_root": str(extract_root),
    }
    st.toast(f"Extracted {count} file(s) from {uploaded_file.name}.")
    return extract_root


def _clear_all_uploaded_data() -> None:
    cache = st.session_state.pop("_archive_cache", {})
    for entry in cache.values():
        cleanup_dir(entry.get("tmp_root"))
    st.session_state.pop("contexts", None)


def page_run_checks() -> None:
    st.title("SIT Output Quality Checker")
    _user_guide_download_button(key="dl_guide_main")

    with st.sidebar:
        st.header("Settings")
        _user_guide_download_button(key="dl_guide_sidebar")
        st.divider()
        uploaded = st.file_uploader("Output folder archive", type=UPLOAD_TYPES, help=ARCHIVE_HELP)
        st.caption(ARCHIVE_ACCEPTED_FORMATS)
        threshold = st.number_input(
            "context_output_normalized ratio threshold (records / total docs)",
            min_value=0.0, value=1.0, step=0.1,
            help="Minimum ratio of context_output_normalized records to total (positive+negative) "
                 "documents. Default 1.0x reflects observed real output; raise it if your pipeline "
                 "is expected to emit several context records per document.")
        exhaustive = st.checkbox("Exhaustive per-document scan (slower, checks every file)", value=False)
        sample_size = st.number_input("Sample size per check (if not exhaustive)", min_value=10,
                                       value=200, step=10)
        run_clicked = st.button("Run Checks", type="primary")
        if st.button("Clear uploaded data"):
            _clear_all_uploaded_data()
            st.success("Cleared.")
            st.rerun()

    if uploaded is None:
        st.info("Upload an archive in the sidebar and click **Run Checks**.")
        return

    if run_clicked:
        extract_root = _get_or_extract(uploaded, "run_checks")
        if extract_root is None:
            return
        with st.status("Discovering Version_* run directories...", expanded=True) as status:
            reporter = make_reporter(status.write)
            contexts = discover(str(extract_root), reporter)
            status.update(
                label=f"Discovery complete - found {len(contexts)} run(s).",
                state="complete" if contexts else "error",
            )
        st.session_state["contexts"] = contexts
        st.session_state["extract_root"] = str(extract_root)

    contexts = st.session_state.get("contexts")
    if contexts is None:
        return
    extract_root = st.session_state.get("extract_root")

    if not contexts:
        st.error("No Version_YYYYMMDD_HHMM directories found in the uploaded archive.")
        return

    st.success(f"Found {len(contexts)} run(s).")
    options = {
        "context_ratio_threshold": threshold,
        "exhaustive_chunk_scan": exhaustive,
        "chunk_sample_size": int(sample_size),
    }

    labels = [c.label for c in contexts]
    choice = st.selectbox("Select a version to run (or All to run every version found)",
                           ["All"] + labels)

    selected = contexts if choice == "All" else [c for c in contexts if c.label == choice]

    if choice == "All" and len(contexts) > 1:
        reports = []
        with st.status(f"Running checks on {len(contexts)} run(s)...", expanded=True) as status:
            reporter = make_reporter(status.write)
            for ctx in selected:
                rep = run_all(ctx, options, reporter)
                strip_root_prefix(rep, extract_root)
                reports.append(rep)
            status.update(label=f"All {len(contexts)} run(s) checked.", state="complete")
        render_run_all_summary_table(reports)
        st.divider()
        for rep in reports:
            render_report(rep)
            st.divider()
    else:
        with st.status("Running checks...", expanded=True) as status:
            reporter = make_reporter(status.write)
            rep = run_all(selected[0], options, reporter)
            strip_root_prefix(rep, extract_root)
            status.update(label="Checks complete.", state="complete")
        render_report(rep)


def page_reference_completeness() -> None:
    st.title("Reference Completeness")
    st.write(
        "Checks the *reference material* itself (not any specific run's output): does "
        "every `sit_packs/<sit>/` folder carry the expected file set, and does every "
        "`SIT Specs/*.docx` have a matching pack? Upload these once - independent of any "
        "output run."
    )
    packs_archive = st.file_uploader("sit_packs archive", type=UPLOAD_TYPES, key="ref_packs",
                                      help=ARCHIVE_HELP)
    st.caption(ARCHIVE_ACCEPTED_FORMATS)
    specs_archive = st.file_uploader("SIT Specs archive (optional)", type=UPLOAD_TYPES,
                                      key="ref_specs", help=ARCHIVE_HELP)
    st.caption(ARCHIVE_ACCEPTED_FORMATS)
    mce_json = st.file_uploader("213 All MCE Keywords sit_keyword_list.json (optional)",
                                 type=["json"], key="ref_mce",
                                 help="The master keyword catalogue - if given, it's also used to "
                                      "confirm language coverage for a specific SIT below, since it "
                                      "records language as a readable name rather than a locale code.")
    st.caption("Accepted format: .json")

    sit_packs_dir = _get_or_extract(packs_archive, "ref_packs")
    sit_specs_dir = _get_or_extract(specs_archive, "ref_specs")

    mce_path = None
    if mce_json is not None:
        cache = st.session_state.setdefault("_archive_cache", {})
        upload_id = f"{mce_json.name}:{mce_json.size}"
        cached = cache.get("ref_mce")
        if cached and cached["upload_id"] == upload_id:
            mce_path = Path(cached["path"])
        else:
            if cached:
                cleanup_dir(Path(cached["path"]).parent)
            tmp_root = new_temp_dir("qcweb_mce_")
            mce_path = tmp_root / mce_json.name
            mce_path.write_bytes(mce_json.getbuffer())
            cache["ref_mce"] = {"upload_id": upload_id, "path": str(mce_path)}

    st.markdown("#### Look up a specific SIT + language")
    col1, col2 = st.columns(2)
    lookup_sit = col1.text_input("SIT name", key="lookup_sit_name", placeholder="e.g. Taiwan Passport Number")
    lookup_lang = col2.text_input("Language", key="lookup_language", placeholder="e.g. English")
    if st.button("Check this SIT + language"):
        if not lookup_sit or not lookup_lang:
            st.error("Enter both a SIT name and a language.")
        else:
            result = check_sit_language(sit_packs_dir, sit_specs_dir, mce_path, lookup_sit, lookup_lang)
            if result.status == "confirmed":
                st.success(result.message)
            else:
                st.error(result.message)
            st.caption(f"Matched pack: {result.matched_pack or '(none)'} | "
                       f"pack language covered: {result.pack_language_covered} | "
                       f"matched spec: {result.matched_spec or '(none)'} | "
                       f"matched 213-keywords entry: {result.matched_mce_entry or '(none)'} | "
                       f"213-keywords language covered: {result.mce_language_covered}")
            if result.link:
                st.markdown(f"[Open the SIT specs reference folder]({result.link})")
            st.caption("Best-effort name matching, not authoritative - a 'not present' result "
                       "is a prompt to double-check manually, not a certainty.")

    st.divider()
    if not st.button("Build Reference Report"):
        return
    if sit_packs_dir is None:
        st.error("Upload a sit_packs archive first.")
        return

    report = build_report(sit_packs_dir, sit_specs_dir)

    st.subheader("Pack file-set completeness")
    rows = [{
        "Pack": p.pack_name,
        "Present": ", ".join(p.present),
        "Missing": ", ".join(p.missing) or "-",
        "Extra files": ", ".join(p.extra) or "-",
    } for p in report.packs]
    st.dataframe(pd.DataFrame(rows), width='stretch')

    if report.specs:
        st.subheader("SIT Specs files")
        spec_rows = [{
            "File": s.filename,
            "Size (bytes)": s.size_bytes,
            "DRM-encrypted": s.is_encrypted,
            "Matched pack": s.matched_pack or "(none found)",
        } for s in report.specs]
        st.dataframe(pd.DataFrame(spec_rows), width='stretch')

        st.caption("Matching is best-effort (token-overlap on file/folder names) - some SITs "
                   "abbreviate in one source and spell out in the other (e.g. 'SIN' vs "
                   "'Social Insurance Number'), which name matching alone can't catch. Treat "
                   "the lists below as a starting point for manual review.")
        if report.packs_without_spec:
            st.warning(f"Packs with no matching spec doc: {', '.join(report.packs_without_spec)}")
        if report.specs_without_pack:
            st.warning(f"Spec docs with no matching pack: {', '.join(report.specs_without_pack)}")
        encrypted = [s.filename for s in report.specs if s.is_encrypted]
        if encrypted:
            st.info(f"{len(encrypted)} spec doc(s) are IRM/DRM-encrypted and could not be "
                    "inspected for content - only filename/size are reported for those.")


tab1, tab2 = st.tabs(["Run Checks", "Reference Completeness"])
with tab1:
    page_run_checks()
with tab2:
    page_reference_completeness()
