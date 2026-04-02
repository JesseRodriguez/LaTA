# LaTeX Submission Validator for Gradescope

This autograder validates LaTeX homework submissions before grading, catching common formatting issues early.

## What It Checks

1. **Single .tex file** - Exactly one .tex file must be submitted
2. **Document structure** - Must contain `\begin{document}...\end{document}`
3. **Problem markers** - Must use `\Prob{N}` where N is only digits
4. **No duplicates** - Each problem number appears exactly once
5. **Expected problem count** - Configurable check for correct number of problems (set in `config.yaml`)
6. **Solution length** - Warns if solutions are suspiciously short (<15 lines/problem - possible blank template)
7. **Basic syntax** - Warns about unbalanced braces, unescaped special characters, etc.

## Scoring

- **1.0 point**: Submission passes all validation checks
- **0.0 points**: Submission has formatting issues

Students see detailed feedback explaining any issues.

## Configuration for Each Assignment

**IMPORTANT:** Before uploading, configure the expected number of problems:

1. Edit `config.yaml`:
   ```yaml
   expected_problems: 3  # Change this for each assignment
   ```

2. See `CONFIGURATION.md` for detailed options

## Installation on Gradescope

1. Zip the autograder directory:
   ```bash
   cd gradescope_autograder
   zip -r autograder.zip setup.sh run_autograder validator.py requirements.txt config.yaml
   ```

   **Note:** Include `config.yaml` in the zip!

2. Upload to Gradescope:
   - Go to assignment settings
   - Upload autograder.zip
   - Wait for build to complete

3. Test with a sample submission

## Customization

To modify validation rules, edit `validator.py`:
- Add new checks in `validate_all()` method
- Modify regex patterns to match your template
- Adjust error messages in individual check methods

## Troubleshooting

If autograder fails to build:
- Check that files are at root of zip (not in subdirectory)
- Verify `setup.sh` and `run_autograder` have execute permissions
- Check logs in Gradescope autograder configuration page
