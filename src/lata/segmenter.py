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

import re
import yaml
import instructor
from openai import OpenAI
from pydantic import BaseModel, Field
from typing import List
from pathlib import Path

# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------

class ProblemChunk(BaseModel):
    label: str = Field(..., description="The label (e.g. 'Problem 1').")
    content: str = Field(..., description="The LaTeX content.")

class SegmentationResult(BaseModel):
    chunks: List[ProblemChunk]
    segmentation_warning: bool = False  # Flag to indicate segmentation issues

# -----------------------------------------------------------------------------
# The Hybrid Librarian (Regex First -> LLM Fallback)
# -----------------------------------------------------------------------------

class LatexSegmenter:
    def __init__(self, config_path="config.yaml"):
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)
            
        # Initialize LLM Client (Lazy load or init here)
        # We init here so it's ready for the fallback
        self.client = instructor.from_openai(
            OpenAI(
                base_url=self.config['system']['ollama_host'],
                api_key="ollama"
            ),
            mode=instructor.Mode.JSON
        )
        
        self.model_name = self.config['system']['segmenter']['model_name']
        self.options = self.config['system']['segmenter'].get('ollama_options', {})

    def segment_submission(self, raw_latex: str) -> SegmentationResult:
        """
        Attempt 1: Strict Regex (Fast, Accurate for Templates)
        Attempt 2: LLM "Fuzzy" Parse (Slow, Good for messy students)
        """
        
        # --- ATTEMPT 1: REGEX ---
        # Normalize newlines
        text = raw_latex.replace('\r\n', '\n')

        # Pattern: \Prob{...}
        # We use r'' to avoid SyntaxWarnings about escape sequences
        # Only match top-level problem numbers (digits only, possibly with leading/trailing whitespace)
        # This filters out sub-headings like "1a Undetermined Coefficients"
        pattern = r'\\Prob\s*\{([^{}]+)\}'

        # First, find ALL \Prob markers
        all_matches = list(re.finditer(pattern, text))

        chunks = []
        if all_matches:
            print("✂️  [Regex] Detected \\Prob{...} markers.")

            # Filter to only top-level problems (just digits, possibly with spaces)
            # Top-level: \Prob{1}, \Prob{2}, etc.
            # NOT top-level: \Prob{1a ...}, \Prob{Problem 1}, etc.
            top_level_matches = [m for m in all_matches if re.match(r'^\s*\d+\s*$', m.group(1))]

            if not top_level_matches:
                # No top-level markers found, fall through to try other patterns
                print("   ⚠️  No top-level \\Prob{N} markers found (only sub-headings detected)")
            else:
                # Detect duplicate problem numbers
                seen_problems = {}
                has_duplicates = False

                for idx, match in enumerate(top_level_matches):
                    prob_num = match.group(1).strip()

                    if prob_num in seen_problems:
                        has_duplicates = True
                        print(f"   ⚠️  Duplicate \\Prob{{{prob_num}}} detected at position {match.start()}")
                    else:
                        seen_problems[prob_num] = idx

                # Split text at top-level problem boundaries
                # For duplicates: use first occurrence, merge subsequent with last segment
                for idx, match in enumerate(top_level_matches):
                    prob_num = match.group(1).strip()
                    start_pos = match.start()

                    # Skip if this is a duplicate occurrence (not the first)
                    if seen_problems[prob_num] != idx:
                        print(f"   ⏭️  Skipping duplicate \\Prob{{{prob_num}}}, merging with previous segment")
                        continue

                    # Determine end position (start of next UNIQUE problem or end of text)
                    # Find next match that is NOT a duplicate of current problem
                    end_pos = len(text)
                    for next_idx in range(idx + 1, len(top_level_matches)):
                        next_prob_num = top_level_matches[next_idx].group(1).strip()
                        if next_prob_num != prob_num and seen_problems[next_prob_num] == next_idx:
                            end_pos = top_level_matches[next_idx].start()
                            break

                    # Extract content for this problem (includes all content until next unique problem)
                    prob_content = text[start_pos:end_pos]

                    label = f"Problem {prob_num}"
                    chunks.append(ProblemChunk(label=label, content=prob_content))

                if has_duplicates:
                    print("   ⚠️  Segmentation issues detected - warning will be added to feedback report")

                return SegmentationResult(chunks=chunks, segmentation_warning=has_duplicates)

        # Pattern: \section{...}
        pattern_sec = r'\\section\*?\{([^{}]+)\}'
        split_data_sec = re.split(pattern_sec, text)
        
        if len(split_data_sec) > 1:
            print("✂️  [Regex] Detected \\section{...} markers.")
            for i in range(1, len(split_data_sec), 2):
                header = split_data_sec[i].strip()
                content = split_data_sec[i+1]
                label = header if "Problem" in header else f"Section {header}"
                full_content = f"\\section{{{header}}}\n{content}"
                chunks.append(ProblemChunk(label=label, content=full_content))
            return SegmentationResult(chunks=chunks)

        # --- ATTEMPT 2: LLM FALLBACK ---
        print("⚠️  [Regex] No markers found. Falling back to LLM segmentation...")
        result = self._segment_with_llm(raw_latex)
        # Set warning flag since we had to use LLM fallback
        result.segmentation_warning = True
        print("   ⚠️  LLM fallback used - warning will be added to feedback report")
        return result

    def _segment_with_llm(self, raw_latex: str) -> SegmentationResult:
        """
        Asks the model to just extract the text blocks.
        We strictly forbid it from solving the problem.
        """
        print(f"🧠 [LLM] Segmenting with {self.model_name}...")
        
        system_prompt = (
            "You are a text extraction tool. Your job is to split a LaTeX document into Problem chunks.\n"
            "You must NOT solve the problems. You must NOT write python code.\n"
            "You must ONLY copy the text from the source into the JSON fields.\n\n"
            "Structure:\n"
            "{ 'chunks': [ {'label': 'Problem 1', 'content': '...raw latex...'} ] }"
        )

        user_prompt = f"--- SOURCE DOCUMENT ---\n{raw_latex}\n--- END SOURCE ---\n\nExtract problems now."

        try:
            result = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_model=SegmentationResult,
                temperature=0.0,
                extra_body={"options": self.options}
            )
            print(f"✅ [LLM] Found {len(result.chunks)} chunks.")
            # Warning flag will be set by caller
            return result
        except Exception as e:
            print(f"❌ [LLM] Fallback failed: {e}")
            # Ultimate fail-safe: return whole document with warning
            return SegmentationResult(
                chunks=[ProblemChunk(label="Full Submission", content=raw_latex)],
                segmentation_warning=True
            )