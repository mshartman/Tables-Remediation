# Table Lab Student Worker Kit

This kit lets a student worker run the PDF Table Lab on a Windows workstation.
The lab extracts PDF tables to HTML, audits the output, and runs only local,
rule-based table remediation. It does not use AWS, Amazon Bedrock, S3, or ILLiad.

## What the student needs

- Windows 10 or 11.
- Python 3.12 or later, including the `py` launcher.
- A copy of the Table Lab ZIP created with `build-table-lab-package.ps1`.

## First-time setup

1. Extract the ZIP to a writable folder, such as `C:\TableLab`.
2. Open PowerShell in that folder.
3. Run:

   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass
   .\student_table_lab\setup-table-lab.ps1
   ```

## Run a table test

```powershell
.\student_table_lab\run-table-lab.ps1
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000), upload
a PDF, and select `Run Table Lab`. When processing finishes, open the generated
HTML and download the ZIP if you need the conversion source, audit JSON, and
remediation report for comparison.

## Where to customize tables

- `local_table_customization.py`
  controls local Table Lab settings, including whether the first row becomes a
  column-header row, whether page headings are included, and how visible table
  grid lines are detected.
- `local_table_extractor.py`
  contains the local `pdfplumber` extraction and accessible HTML generation.
Start with `local_table_customization.py`. Change one thing at a time and keep a test
PDF plus its before/after HTML and remediation report for every experiment.

## Important limits

- The Table Lab runs entirely on the workstation. Its PDF table extraction is
  rule-based, so complex, scanned, or visually irregular tables may need manual
  tuning in the local table files.
- Do not use production ILLiad PDFs unless the worker is authorized to handle
  them on that workstation.
