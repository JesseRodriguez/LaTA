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

import sys
import yaml
import argparse
from pathlib import Path
# Import as `lata`, the same package name the modules use for each other. Importing as
# `src.lata` loaded a second copy of every module, so classes such as GradeItem existed
# twice -- harmless for duck-typed data, but it breaks exception matching.
from lata.pipeline import GradingOptions, grade_assignment
from lata.inbox import prepare_from_inbox


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--student", type=str, help="Target student name/ID")
    parser.add_argument(
        "--regrade",
        nargs='+',
        help="Force regrade for specific student(s) without versioning. Accepts multiple names/IDs separated by spaces."
    )
    parser.add_argument("--config", type=str, default="config.yaml",
                        help="Path to the configuration file (default: config.yaml)")
    parser.add_argument("--inbox", type=str, default=None,
                        help="Folder holding Gradescope export .zip files to unpack before "
                             "grading (overrides paths.inbox; default data/input/inbox)")
    parser.add_argument("--concurrency", type=int, default=None,
                        help="Grading requests to run in parallel (overrides grading.concurrency). "
                             "The LLM server must allow at least this many: for Ollama set "
                             "OLLAMA_NUM_PARALLEL >= N and restart it.")
    parser.add_argument("--watch-slot", type=int, default=1,
                        help="With concurrency > 1, which slot's thinking to show live on the dashboard (default: 1)")
    parser.add_argument("--no-dashboard", action="store_true",
                        help="With concurrency > 1, print plain log lines and a periodic status line "
                             "instead of the live dashboard (automatic when output is not a terminal)")
    watch = parser.add_argument_group(
        "watching", "Used when config.yaml lists assignments: under `assignments:` (see lata/watch.py)")
    watch.add_argument("--once", action="store_true",
                       help="Check every assignment once, do whatever is due, and exit")
    watch.add_argument("--send", dest="auto_send", action="store_true", default=None,
                       help="Email feedback as soon as a clean report is ready (overrides watch.auto_send_feedback)")
    watch.add_argument("--no-send", dest="auto_send", action="store_false",
                       help="Grade, but hold feedback until you type `send` (the default)")
    watch.add_argument("--dry-run", action="store_true",
                       help="One check that only reports what would happen: fetches exports, but "
                            "grades nothing, sends nothing, enters nothing on Gradescope, saves no state")
    args = parser.parse_args()

    # 1. Load Config & Assets
    config_path = args.config
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    concurrency = args.concurrency if args.concurrency is not None else config.get('grading', {}).get('concurrency', 1)
    concurrency = max(1, int(concurrency))
    options = GradingOptions(concurrency=concurrency, watch_slot=args.watch_slot,
                             dashboard=not args.no_dashboard,
                             regrade=args.regrade, student=args.student)

    # A list of assignments means: watch them all, grading submissions as they arrive
    if config.get('assignments'):
        if args.regrade or args.student:
            print("❌ --regrade and --student work on a single assignment. Point --config at a "
                  "config without an `assignments:` list.")
            return
        from lata.watch import run_watch
        run_watch(config, config_path, options, once=args.once, auto_send=args.auto_send,
                  dry_run=args.dry_run)
        return

    # Unpack anything waiting in the inbox: the Gradescope .zip exports go straight in,
    # no unzipping or renaming by hand
    export_dir = Path(config['paths']['export_dir'])
    inbox_dir = args.inbox or config.get('paths', {}).get('inbox', 'data/input/inbox')
    prepare_from_inbox(inbox_dir, export_dir)

    # Check for required PDF metadata file
    pdf_metadata_path = export_dir / 'pdf_submission_metadata.yml'

    if not pdf_metadata_path.exists():
        print(f"❌ ERROR: PDF submission metadata not found!")
        print(f"   Expected: {pdf_metadata_path}")
        print(f"   This file is required for accurate timestamp tracking and versioning.")
        print(f"   Late penalties use the later of the .tex and .pdf submission times.")
        print(f"   Drop the .pdf assignment's Gradescope export into {inbox_dir}/, or place")
        print(f"   its submission_metadata.yml at the path above.")
        return

    grade_assignment(config, config_path, options)


if __name__ == "__main__":
    main()
