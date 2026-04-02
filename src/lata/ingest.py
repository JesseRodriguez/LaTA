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

import os
import yaml
import glob
import hashlib
import pandas as pd
from pathlib import Path
from typing import List, Optional, Dict
from pydantic import BaseModel, Field
from pylatexenc.latexwalker import LatexWalker, LatexEnvironmentNode, LatexMacroNode

# -----------------------------------------------------------------------------
# Data Structures
# -----------------------------------------------------------------------------

class StudentSubmission(BaseModel):
    """
    Represents a single student's work, sanitized for the pipeline.
    """
    internal_id: str          # The Gradescope submission ID (e.g., "376213776")
    anonymized_id: str        # The hash we show the LLM (e.g., "a1b2c3d4")

    # Metadata (kept for reporting, HIDDEN from LLM)
    student_name: str
    student_email: str
    student_sid: str
    created_at: str           # Timestamp of submission (for detecting resubmissions)
    extension_hours: float = 0  # Deadline extension in hours (default: no extension)

    # The Content (What the LLM sees)
    raw_tex_path: str
    cleaned_content: str      # The preamble-stripped LaTeX

class IngestionResult(BaseModel):
    """
    Result object containing processed submissions and the mapping table.
    """
    submissions: List[StudentSubmission]
    mapping_dataframe: pd.DataFrame

    # TELLS PYDANTIC: "It's okay to accept complex types like DataFrames"
    model_config = {
        "arbitrary_types_allowed": True
    }

# -----------------------------------------------------------------------------
# Helper: Latex Cleaning
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# Helper: Latex Cleaning & Macro Extraction
# -----------------------------------------------------------------------------

def process_latex_content(tex_content: str) -> str:
    """
    Parses LaTeX to:
    1. Extract custom macro definitions from the preamble.
    2. Extract the main content inside \begin{document}...\end{document}.
    3. Return 'Macros + \n\n + Content'.
    """
    try:
        w = LatexWalker(tex_content)
        (nodelist, pos, len_) = w.get_latex_nodes(pos=0)
        
        preamble_macros = []
        body_content = ""
        
        # We need to distinguish between 'Preamble' and 'Body'
        # The body starts at the 'document' environment.
        
        for node in nodelist:
            # CHECK FOR BODY
            if isinstance(node, LatexEnvironmentNode) and node.environmentname == 'document':
                # Grab content using slicing (Defensive coding from previous error)
                inner_nodes = node.nodelist
                if inner_nodes:
                    # Find start/end of the inner content
                    # If inner_nodes is a list, accessing .pos works. 
                    # If it's a LatexNodeList, it acts like a list.
                    start_pos = inner_nodes[0].pos
                    end_pos = inner_nodes[-1].pos + inner_nodes[-1].len
                    body_content = tex_content[start_pos:end_pos].strip()
                continue # We found the body, stop processing top-level nodes? 
                         # Actually no, there might be stuff after end document (rare), but we ignore it.
            
            # CHECK FOR MACROS (Only if we haven't hit body yet)
            if not body_content: # We are still in preamble
                if isinstance(node, LatexMacroNode):
                    # We look for common definition commands
                    if node.macroname in ['newcommand', 'renewcommand', 'def', 'DeclareMathOperator', 'providecommand']:
                        # We reconstruct ONLY this definition line
                        # Note: This requires the node to support latex_listing(), which failed before.
                        # So we use slicing again to be safe.
                        macro_slice = tex_content[node.pos : node.pos + node.len]
                        preamble_macros.append(macro_slice)

        # Fallback if AST missed the document env
        if not body_content:
             if "\\begin{document}" in tex_content:
                body_content = tex_content.split("\\begin{document}")[1].split("\\end{document}")[0]
             else:
                body_content = tex_content # Return everything if structure is weird

        # Combine
        if preamble_macros:
            return "\n".join(preamble_macros) + "\n\n" + body_content
        else:
            return body_content

    except Exception as e:
        print(f"Warning: LaTeX parsing failed ({e}). Returning raw content.")
        return tex_content

# -----------------------------------------------------------------------------
# Main Loader Class
# -----------------------------------------------------------------------------

class GradescopeIngestor:
    def __init__(self, export_dir: str, anonymize: bool = False):
        self.export_dir = Path(export_dir)
        self.metadata_path = self.export_dir / "submission_metadata.yml"
        self.pdf_metadata_path = self.export_dir / "pdf_submission_metadata.yml"
        self.anonymize = anonymize # Store the preference
        
    def _clean_ruby_yaml_keys(self, data):
        if isinstance(data, dict):
            return {k.lstrip(':'): self._clean_ruby_yaml_keys(v) for k, v in data.items() if isinstance(k, str)}
        elif isinstance(data, list):
            return [self._clean_ruby_yaml_keys(i) for i in data]
        else:
            return data

    def _load_extensions(self, export_dir: Path) -> dict:
        """
        Load per-student deadline extensions from extensions.yaml.

        Returns dict mapping student_name → extension_hours:
            {"Benjamin Stewart": 24, "Rachel Wu": 48}

        If file doesn't exist, returns empty dict.
        """
        extensions_path = export_dir / "extensions.yaml"

        if not extensions_path.exists():
            return {}

        try:
            with open(extensions_path, 'r') as f:
                extensions = yaml.safe_load(f) or {}

            # Validate format
            if not isinstance(extensions, dict):
                print(f"⚠️  Warning: extensions.yaml is not a dict, ignoring")
                return {}

            # Convert all values to float (support int or float hours)
            extensions = {k: float(v) for k, v in extensions.items()}

            print(f"📅 Loaded deadline extensions for {len(extensions)} students")
            return extensions

        except Exception as e:
            print(f"⚠️  Warning: Could not load extensions.yaml: {e}")
            return {}

    def load(self) -> IngestionResult:
        if not self.metadata_path.exists():
            raise FileNotFoundError(f"Could not find metadata at {self.metadata_path}")

        # Load deadline extensions first
        extensions = self._load_extensions(self.export_dir)

        with open(self.metadata_path, 'r') as f:
            raw_meta = yaml.safe_load(f)

        meta_dict = self._clean_ruby_yaml_keys(raw_meta)

        # Load PDF submission metadata if available
        pdf_meta_dict = {}
        if self.pdf_metadata_path.exists():
            with open(self.pdf_metadata_path, 'r') as f:
                raw_pdf_meta = yaml.safe_load(f)
            pdf_meta_dict = self._clean_ruby_yaml_keys(raw_pdf_meta)

        submissions = []
        mapping_rows = []

        for sub_key, sub_data in meta_dict.items():
            internal_id = sub_key.split('_')[-1]

            if not sub_data.get('submitters'):
                continue

            submitter = sub_data['submitters'][0]
            name = submitter.get('name', 'Unknown')
            email = submitter.get('email', 'Unknown')
            sid = submitter.get('sid', 'Unknown')
            tex_created_at = sub_data.get('created_at', 'Unknown')

            # Check if there's a PDF submission and use the later timestamp
            # PDF submissions have different IDs, so match by student name/SID
            pdf_created_at = None
            for pdf_key, pdf_sub_data in pdf_meta_dict.items():
                if not pdf_sub_data.get('submitters'):
                    continue
                pdf_submitter = pdf_sub_data['submitters'][0]
                pdf_name = pdf_submitter.get('name', '')
                pdf_sid = pdf_submitter.get('sid', '')
                pdf_email = pdf_submitter.get('email', '')

                # Match by SID (most reliable) or email
                if pdf_sid == sid or (pdf_email == email and email != 'Unknown'):
                    pdf_created_at = pdf_sub_data.get('created_at')
                    break  # Found the matching PDF submission

            # Use the later of the two timestamps
            from datetime import datetime
            created_at = tex_created_at
            if pdf_created_at:
                try:
                    tex_dt = datetime.fromisoformat(str(tex_created_at).replace(' Z', '+00:00'))
                    pdf_dt = datetime.fromisoformat(str(pdf_created_at).replace(' Z', '+00:00'))
                    if pdf_dt > tex_dt:
                        created_at = pdf_created_at
                        #print(f"   📄 Using PDF timestamp for {name} (PDF submitted ~{int((pdf_dt - tex_dt).total_seconds() / 3600)}h later)")
                except (ValueError, AttributeError) as e:
                    # If parsing fails, just use tex timestamp
                    print(f"   ⚠️  Could not parse timestamps for {name}: {e}")
                    pass
            
            # --- ID SELECTION LOGIC ---
            if self.anonymize:
                # Use Hash
                display_id = hashlib.sha256(f"{sid}{internal_id}".encode()).hexdigest()[:8]
            else:
                # Use Human Readable Name + SID (Best for TAs)
                display_id = f"{name} ({sid})"
            
            # (File finding logic remains the same...)
            sub_dir = self.export_dir / sub_key
            tex_files = list(sub_dir.glob("*.tex"))
            
            if not tex_files:
                print(f"Warning: No .tex file found for {name}. Skipping.")
                continue
            
            target_file = tex_files[0]
            
            try:
                with open(target_file, 'r', encoding='utf-8', errors='replace') as f:
                    raw_content = f.read()
                cleaned_body = process_latex_content(raw_content)
            except Exception as e:
                print(f"Error reading file for {name}: {e}")
                cleaned_body = "Error reading submission."

            # Check if student has extension
            extension_hours = extensions.get(name, 0)

            submission = StudentSubmission(
                internal_id=internal_id,
                anonymized_id=display_id, # This field is now just 'display_id'
                student_name=name,
                student_email=email,
                student_sid=str(sid),
                created_at=str(created_at),
                extension_hours=extension_hours,
                raw_tex_path=str(target_file),
                cleaned_content=cleaned_body
            )
            
            submissions.append(submission)
            mapping_rows.append({
                "display_id": display_id,
                "real_name": name,
                "email": email
            })

        return IngestionResult(
            submissions=submissions,
            mapping_dataframe=pd.DataFrame(mapping_rows)
        )