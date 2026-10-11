# LaTA Grading System - Automated grading workflow using open-source LLMs.
# Copyright (C) 2026 Jesse A. Rodriguez
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""
Drop Gradescope exports in an inbox; LaTA unpacks them.

Gradescope hands you a .zip per assignment. Preparing it by hand meant unzipping both the
.tex and .pdf exports, renaming folders, and renaming the PDF export's metadata to
pdf_submission_metadata.yml so the ingestor could find it. This does that:

    data/input/inbox/
        ME373_HW3_submissions.zip        <- the .tex assignment export
        ME373_HW3_pdf_submissions.zip    <- the .pdf assignment export (timestamps only)

Each archive is identified by what is inside it rather than by its name, so which file you
downloaded first does not matter. The .tex export is unpacked in full; from the .pdf export
only submission_metadata.yml is taken, since the pipeline uses the PDFs for nothing but
their submission times -- that alone avoids copying hundreds of megabytes of PDFs.

Re-exporting after late submissions works: drop the new archive in and it merges into the
same directory, and the grader skips students it has already finished. Archives whose
contents are already unpacked are left alone, so running twice costs nothing.
"""

import hashlib
import json
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

MANIFEST = ".inbox_manifest.json"
METADATA_NAME = "submission_metadata.yml"
PDF_METADATA_NAME = "pdf_submission_metadata.yml"


@dataclass
class Archive:
    path: Path
    kind: str                     # "tex" | "pdf" | "unknown"
    metadata_member: Optional[str] = None     # path of submission_metadata.yml inside the zip
    root: str = ""                            # prefix inside the zip holding the export
    tex_count: int = 0
    pdf_count: int = 0


@dataclass
class InboxResult:
    export_dir: Path
    submissions: int
    actions: List[str] = field(default_factory=list)


def _sha256(path: Path, limit: int = 64 * 1024 * 1024) -> str:
    """Hash of the archive; large files are fingerprinted by head, tail and size."""
    h = hashlib.sha256()
    size = path.stat().st_size
    with path.open("rb") as f:
        if size <= limit:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        else:
            h.update(f.read(1 << 20))
            f.seek(-(1 << 20), 2)
            h.update(f.read())
    h.update(str(size).encode())
    return h.hexdigest()


def inspect_archive(path: Path) -> Archive:
    """Work out what a zip holds by looking inside it."""
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
    except zipfile.BadZipFile:
        return Archive(path=path, kind="unknown")

    meta = next((n for n in names if Path(n).name == METADATA_NAME), None)
    root = str(Path(meta).parent) if meta and str(Path(meta).parent) != "." else ""
    tex = sum(1 for n in names if n.lower().endswith(".tex"))
    pdf = sum(1 for n in names if n.lower().endswith(".pdf"))
    kind = "unknown"
    if meta:
        kind = "tex" if tex else ("pdf" if pdf else "unknown")
    return Archive(path=path, kind=kind, metadata_member=meta, root=root,
                   tex_count=tex, pdf_count=pdf)


def _safe_members(z: zipfile.ZipFile, root: str, want_tex_export: bool):
    """Members to extract, with their destination path relative to the export directory.

    Refuses absolute paths, parent traversal and symlinks, so a malformed or hostile
    archive cannot write outside the export directory.
    """
    for info in z.infolist():
        if info.is_dir():
            continue
        name = info.filename
        if root:
            prefix = root.rstrip("/") + "/"
            if not name.startswith(prefix):
                continue
            rel = name[len(prefix):]
        else:
            rel = name
        parts = Path(rel).parts
        if not parts or any(p in ("..", "") for p in parts) or Path(rel).is_absolute():
            continue
        if Path(rel).name.startswith("._") or "__MACOSX" in parts:
            continue
        if (info.external_attr >> 16) & 0o170000 == 0o120000:      # symlink
            continue
        if not want_tex_export and Path(rel).name != METADATA_NAME:
            continue                                               # PDF export: metadata only
        yield info, rel


def _merge_metadata(existing: Path, incoming_bytes: bytes) -> tuple:
    """Merge a submission_metadata.yml into one already on disk.

    Union by submission key, with the incoming copy winning. Replacing the file instead
    would drop every student missing from the newer archive -- which is what a partial
    re-export, or an unrelated export dropped in the inbox, looks like.
    Returns (added, updated, total).
    """
    import yaml
    incoming = yaml.safe_load(incoming_bytes.decode("utf-8", "replace")) or {}
    current = {}
    if existing.exists():
        try:
            current = yaml.safe_load(existing.read_text()) or {}
        except Exception:
            current = {}
    # A student who resubmitted appears in the newer export under a new submission key;
    # their old entry has to go, or they would be in the metadata twice
    def who(entry):
        subs = (entry or {}).get(':submitters') or (entry or {}).get('submitters') or []
        if not subs:
            return None
        s = subs[0]
        sid = s.get(':sid', s.get('sid'))
        email = s.get(':email', s.get('email'))
        return str(sid).strip() if sid else (str(email).strip().lower() if email else None)

    incoming_people = {who(v): k for k, v in incoming.items() if who(v)}
    for k in [k for k, v in current.items()
              if who(v) in incoming_people and incoming_people[who(v)] != k]:
        del current[k]
    added = sum(1 for k in incoming if k not in current)
    updated = len(incoming) - added
    current.update(incoming)
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text(yaml.safe_dump(current, sort_keys=False))
    return added, updated, len(current)


def _extract(archive: Archive, dest: Path, want_tex_export: bool) -> tuple:
    """Extract an archive into `dest`. Returns (files_written, added, updated, total)."""
    dest.mkdir(parents=True, exist_ok=True)
    meta_name = METADATA_NAME if want_tex_export else PDF_METADATA_NAME
    count = added = updated = total = 0
    with zipfile.ZipFile(archive.path) as z:
        for info, rel in _safe_members(z, archive.root, want_tex_export):
            if Path(rel).name == METADATA_NAME:
                with z.open(info) as src:
                    added, updated, total = _merge_metadata(dest / meta_name, src.read())
                count += 1
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
            count += 1
    return count, added, updated, total


def _count_submissions(export_dir: Path) -> int:
    meta = export_dir / METADATA_NAME
    if not meta.exists():
        return 0
    try:
        import yaml
        with meta.open() as f:
            data = yaml.safe_load(f) or {}
        return len(data)
    except Exception:
        return 0


def prepare_from_inbox(inbox_dir, export_dir, quiet: bool = False) -> Optional[InboxResult]:
    """Unpack any Gradescope archives sitting in `inbox_dir` into `export_dir`.

    Returns None when the inbox is absent or holds nothing new, so the caller just uses
    whatever is already in export_dir.
    """
    inbox = Path(inbox_dir).expanduser()
    export_dir = Path(export_dir)
    if not inbox.is_dir():
        return None

    archives = sorted(p for p in inbox.iterdir()
                      if p.is_file() and p.suffix.lower() == ".zip")
    if not archives:
        return None

    manifest_path = export_dir / MANIFEST
    manifest = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
        except Exception:
            manifest = {}

    actions = []
    log = (lambda m: None) if quiet else (lambda m: print(m))
    log(f"📥 Inbox: {len(archives)} archive(s) in {inbox}")

    todo = []
    for path in archives:
        info = inspect_archive(path)
        digest = _sha256(path)
        if manifest.get(path.name) == digest:
            log(f"   ↩︎  {path.name}: already unpacked")
            continue
        if info.kind == "unknown":
            log(f"   ⚠️  {path.name}: not a Gradescope export "
                f"(no {METADATA_NAME} inside, or no .tex/.pdf submissions) -- skipping")
            continue
        todo.append((info, digest))

    if not todo:
        return None

    # The .tex export must be unpacked before the .pdf metadata is placed beside it
    todo.sort(key=lambda t: 0 if t[0].kind == "tex" else 1)
    for info, digest in todo:
        if info.kind == "tex":
            n, added, updated, total = _extract(info, export_dir, want_tex_export=True)
            msg = (f"   ✅ {info.path.name}: {added} new submission(s), {updated} already known "
                   f"-> {export_dir}")
        else:
            n, added, updated, total = _extract(info, export_dir, want_tex_export=False)
            msg = (f"   ✅ {info.path.name}: submission times for {added + updated} submission(s) "
                   f"-> {PDF_METADATA_NAME} (skipped {info.pdf_count} PDFs, only the times are used)")
            if n == 0:
                msg = f"   ⚠️  {info.path.name}: no {METADATA_NAME} found inside"
        log(msg)
        actions.append(msg.strip())
        manifest[info.path.name] = digest

    export_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2))

    total = _count_submissions(export_dir)
    log(f"   📦 {export_dir} now holds {total} submission(s)")
    if not (export_dir / PDF_METADATA_NAME).exists():
        log(f"   ⚠️  No {PDF_METADATA_NAME} yet. Late penalties use the later of the .tex and "
            f".pdf submission times, so drop the .pdf assignment's export in the inbox too.")
    return InboxResult(export_dir=export_dir, submissions=total, actions=actions)
