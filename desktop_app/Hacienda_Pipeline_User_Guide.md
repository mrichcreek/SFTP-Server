# Hacienda ERP Data Pipeline
## User Guide

---

## Table of Contents

1. [Overview](#overview)
2. [Getting Started](#getting-started)
3. [Logging In](#logging-in)
4. [Running the Pipeline](#running-the-pipeline)
5. [Understanding the Progress Bar](#understanding-the-progress-bar)
6. [Understanding the Results](#understanding-the-results)
7. [Understanding the Report](#understanding-the-report)
8. [Common Errors and How to Fix Them](#common-errors-and-how-to-fix-them)
9. [Sharing Reports for Corrections](#sharing-reports-for-corrections)

---

## Overview

The Hacienda ERP Data Pipeline is a desktop application that automates the process of:

1. **Downloading** HCM data files from the Sterling SFTP server
2. **Validating** the files for correct naming and data format
3. **Loading** the data into the SQL database
4. **Processing** the data through stored procedures
5. **Exporting** delta files for Oracle Cloud integration
6. **Uploading** the delta files back to the Sterling server

The program handles all of these steps automatically with a single click.

---

## Getting Started

### Prerequisites

Before running the program, ensure:

1. **FortiClient VPN is connected** - You must be connected to the VPN to access the Sterling SFTP server
2. **You have the program file** - `Hacienda Pipeline.exe`
3. **You have your login credentials** - Your username and password for the application

### Launching the Program

1. Double-click `Hacienda Pipeline.exe` to open the application
2. The **Login Screen** will appear first (see next section)

---

## Logging In

When you first start the program, you will see the Login Screen.

### Login Screen Elements

| Field | Description |
|-------|-------------|
| **Username** | Enter your assigned username |
| **Password** | Enter your password (characters will be hidden) |
| **Login Button** | Click to submit your credentials |

### How to Log In

1. **Enter your Username** - Type your assigned username in the Username field
2. **Enter your Password** - Type your password in the Password field (the characters will appear as dots for security)
3. **Click the Login button** - Click the "Login" button to authenticate

### After Successful Login

Once you successfully log in, you will see the main application window with:
- A header showing "Hacienda ERP Data Pipeline"
- A "Run Full Pipeline" button
- A progress bar area
- A results area at the bottom

### Login Errors

If you see an error message when trying to log in:

| Error | Meaning | Solution |
|-------|---------|----------|
| "Invalid username or password" | The credentials you entered are incorrect | Double-check your username and password and try again |
| "Connection error" | Cannot connect to the authentication server | Check that your VPN is connected and try again |
| "Account locked" | Too many failed login attempts | Contact your system administrator |

**Note:** If you have forgotten your password or need login credentials, contact your system administrator.

---

## Running the Pipeline

### Step 1: Connect to VPN

Before clicking Run, make sure you are connected to the FortiClient VPN. The program needs VPN access to:
- Download files from the Sterling server
- Upload processed files back to the Sterling server

### Step 2: Click "Run Full Pipeline"

Click the green **"Run Full Pipeline"** button to start the process.

**Important:** Once started, the pipeline will run through all steps automatically. Do not close the program while it is running.

### Step 3: Wait for Completion

The pipeline typically takes **15-20 minutes** to complete, depending on:
- The number of files being processed
- The amount of data being loaded
- Network speed

---

## Understanding the Progress Bar

While the pipeline is running, the progress bar shows you what step is currently being executed.

### Progress Bar Elements

| Element | Description |
|---------|-------------|
| **Percentage** | Shows overall progress (0% to 100%) |
| **Current Step** | Shows what the pipeline is currently doing |
| **Step Counter** | Shows "Step X of 10" to indicate position in the process |
| **Status Message** | Provides additional detail about the current action |

### Pipeline Steps (in order)

| Step | Description | What It Does |
|------|-------------|--------------|
| 1 | **SFTP Download** | Downloads HCM files from the Sterling server |
| 2 | **Create Folders** | Sets up folder structure for processing |
| 3 | **Duplicate Check** | Removes duplicate and older versions of files |
| 4 | **Name Validation** | Checks that file names follow the correct format |
| 5 | **Schema Validation** | Checks that files have the correct columns |
| 6 | **Completeness Check** | Ensures all required files are present for each entity |
| 7 | **SQL Load** | Loads the data into the SQL database |
| 8 | **Stored Procedure** | Processes the data in the database |
| 9 | **Delta Export** | Creates export files for Oracle Cloud |
| 10 | **SFTP Upload** | Uploads the delta files to the Sterling server |

---

## Understanding the Results

When the pipeline completes, results are displayed at the bottom of the window.

### Success Result

When the pipeline completes successfully, you will see:

```
Status: SUCCESS
Pipeline ID: 20260129_153404
Folder: 20260129_1534
```

All steps will show **[SUCCESS]** in green.

### Partial Success Result

When some files had issues but valid files were processed:

```
Status: PARTIAL
Pipeline ID: 20260129_153404
```

The pipeline continued with valid files. Check the report for details on which files had issues.

### Failed Result

When the pipeline could not complete:

```
Status: FAILED
Error: [Description of what went wrong]
```

Check the report for detailed error information.

### Saving the Report

After the pipeline completes (whether successful or failed), click **"Save Report"** to save a detailed text report. This report contains:

- Summary of all steps
- Details of any errors
- Information needed to fix problems

---

## Understanding the Report

The saved report contains several sections. Here's what each section means:

### Report Header

```
================================================================================
HACIENDA ERP DATA PIPELINE REPORT
================================================================================
Generated: 2026-01-29 16:02:13
Application Version: 3.1.0
Database: Hacienda_ERP
```

Shows when the report was created and which database was used.

### Pipeline Summary

```
--------------------------------------------------------------------------------
PIPELINE SUMMARY
--------------------------------------------------------------------------------
Status: SUCCESS (or FAILED or PARTIAL)
Pipeline ID: 20260129_153404
Folder: 20260129_1534
Error: [Only appears if there was an error]
```

Quick overview of the pipeline result.

### SFTP Download Details

```
--------------------------------------------------------------------------------
SFTP DOWNLOAD DETAILS
--------------------------------------------------------------------------------
SFTP Host: 10.3.3.146:22
Remote Folder: /GPR/HCM
Files Found on Server: 45
Files Downloaded: 45
Files Failed: 0
Total Bytes: 31,568,154
```

Shows how many files were downloaded from the Sterling server.

### Pipeline Steps

```
--------------------------------------------------------------------------------
PIPELINE STEPS
--------------------------------------------------------------------------------
[SUCCESS] sftp_download
        Message: Downloaded 45 files (31,568,154 bytes) from SFTP via local VPN
[SUCCESS] duplicate_check
        Message: Duplicate check complete - 23 files moved
[SUCCESS] name_validation
        Message: All 33 file names are valid
```

Shows the result of each pipeline step. Look for any **[FAILED]** steps.

### Duplicate/Superseded File Details

```
--------------------------------------------------------------------------------
DUPLICATE/SUPERSEDED FILE DETAILS
--------------------------------------------------------------------------------
Total Files Scanned: 45
Unique Files: 22
Superseded (older versions): 23

SUPERSEDED FILES (older versions moved, newest kept):
--------------------------------------------------
  Type: HCM_PERSON_INTF (HACIENDA)
  KEPT (newest): HCM_PERSON_INTF_HACIENDA_20251215001303.csv
  MOVED (older versions):
    - HCM_PERSON_INTF_HACIENDA_20251210001303.csv
    - HCM_PERSON_INTF_HACIENDA_20251205001303.csv
```

Shows which files were duplicates or older versions. Only the newest version of each file type is processed.

### Table Load Details

```
--------------------------------------------------------------------------------
TABLE LOAD DETAILS
--------------------------------------------------------------------------------
Tables Loaded Successfully: 16
Tables Failed: 0
Total Rows Loaded: 125,432
```

Shows how much data was loaded into the database.

---

## Common Errors and How to Fix Them

### Error: VPN Not Connected

**What you see:**
```
Error: SFTP download failed: Connection timed out
```
or
```
Error: Cannot reach SFTP server - FortiClient VPN may not be connected
```

**What it means:** The program cannot reach the Sterling server.

**How to fix:**
1. Open FortiClient VPN
2. Connect to the VPN
3. Wait for the connection to establish
4. Run the pipeline again

---

### Error: Invalid File Names

**What you see in the report:**
```
--------------------------------------------------------------------------------
FILE NAME VALIDATION ERRORS - ACTION REQUIRED
--------------------------------------------------------------------------------
Total Files Checked: 33
Valid Files: 31
Invalid Files: 2

>>> Pipeline CONTINUED processing valid files.
>>> 2 invalid files were moved to InvalidFiles folder.

EXPECTED FILE NAME FORMAT:
  Pattern: HCM_{SOURCE}_INTF_{ENTITY}_{DATE}.csv

  Valid SOURCES: PERSON, PERSON_NAME, PERSON_ASSIGNMENT, PERSON_ADDRESS,
                 PERSON_NID, PERSON_SUPERVISOR, PERSON_EMAIL, SENIORITY
  Valid ENTITIES: 911, RHUM, HACIENDA, FIMAS, DOE, KRONOSPOL, KRONOSDE,
                  SEPI, ADPPOLICIA
  Valid DATE formats: YYYYMMDD, YYYYMMDDHHMM, YYYYMMDDHHMMSS

============================================================
INVALID FILES - PLEASE CORRECT THESE:
============================================================

[1] FILE: hcm_person_address_rhum75_20260109.csv
    ERROR: Invalid entity 'address_rhum75'. Valid entities: 911, RHUM, HACIENDA...
    Detected Source: PERSON
    Detected Entity: address_rhum75
    Detected Date: 20260109
    >>> SUGGESTED FIX: Rename to -> HCM_PERSON_ADDRESS_INTF_RHUM75_20260109.csv
```

**What it means:** Some files do not follow the required naming format.

**How to fix:**
1. Send the report to the person who creates the files
2. They need to rename the files following the correct pattern
3. The pattern is: `HCM_{SOURCE}_INTF_{ENTITY}_{DATE}.csv`
4. The report shows exactly what's wrong and suggests the correct name

**Common file naming mistakes:**
- Missing `_INTF_` in the filename
- Misspelled source or entity names
- Invalid date format

---

### Error: Missing Files (Incomplete Entity)

**What you see in the report:**
```
--------------------------------------------------------------------------------
COMPLETENESS CHECK
--------------------------------------------------------------------------------
Complete Entities: HACIENDA, FIMAS
Incomplete Entities: 911

INCOMPLETE ENTITY: 911
Missing file types:
  - PERSON_ADDRESS
  - PERSON_EMAIL
```

**What it means:** An entity is missing some required file types. Each entity needs a complete set of files.

**How to fix:**
1. Send the report to the person who creates the files
2. They need to provide the missing file types
3. The report lists exactly which files are missing

**Note:** The pipeline will process complete entities (like HACIENDA and FIMAS in the example above) and skip incomplete ones (like 911).

---

### Error: Schema Validation Failed

**What you see in the report:**
```
SCHEMA VALIDATION ERRORS
------------------------
File: HCM_PERSON_INTF_HACIENDA_20260129.csv
  Missing columns: PERSON_NUMBER, EFFECTIVE_START_DATE
  Extra columns: PERSON_NUM, START_DATE
```

**What it means:** A file has incorrect column headers.

**How to fix:**
1. Send the report to the person who creates the files
2. They need to correct the column headers in their export
3. The report shows which columns are wrong or missing

---

### Error: Database Load Failed

**What you see in the report:**
```
FAILED TABLES - REQUIRES ATTENTION
==================================
TABLE: HCM_PERSON_INTF_HACIENDA
Source File: HCM_PERSON_INTF_HACIENDA_20260129.csv
Error: Violation of PRIMARY KEY constraint
```

**What it means:** The data could not be loaded due to a database error.

**How to fix:**
1. This may be a data quality issue (duplicate records, etc.)
2. Contact your database administrator with the report
3. The file may need to be corrected and re-uploaded

---

### Error: Stored Procedure Failed

**What you see in the report:**
```
[FAILED] run_procedure
        Message: Stored procedure failed: [error details]
```

**What it means:** The database processing step failed.

**How to fix:**
1. Contact your database administrator
2. Provide them with the full report
3. This is typically a database configuration or permissions issue

---

## Sharing Reports for Corrections

When files have errors, the report should be shared with the person who creates the HCM files so they can make corrections.

### What to Include

When sending the report, include:

1. **The full report file** - Save it using the "Save Report" button
2. **A summary of what needs to be fixed** - Highlight the key errors
3. **The date/time the pipeline was run** - This helps track which files were affected

### Example Email to File Creator

```
Subject: HCM File Corrections Needed - [Date]

Hi [Name],

I ran the Hacienda data pipeline and some files need corrections
before they can be processed.

Issues found:
- 2 files have invalid names (see INVALID FILES section)
- 911 entity is missing PERSON_ADDRESS and PERSON_EMAIL files

Please see the attached report for details. The report includes:
- The exact error for each file
- Suggested fixes for file naming issues
- List of missing files

Once corrections are made, please re-upload the files to the
Sterling server and let me know so I can run the pipeline again.

Thanks,
[Your Name]
```

### After Corrections

Once the file creator has:
1. Fixed the file names
2. Added any missing files
3. Re-uploaded to the Sterling server

You can run the pipeline again. The corrected files should process successfully.

---

## Tips for Success

1. **Always save the report** - Even on success, the report provides useful information
2. **Check VPN before running** - Most connection errors are due to VPN not being connected
3. **Allow enough time** - The pipeline can take 15-20 minutes to complete
4. **Don't close the program** - Let the pipeline finish completely before closing
5. **Review the report** - Even successful runs may show files that were skipped

---

## Support

If you encounter issues not covered in this guide:

1. Save the full report
2. Note the exact error message
3. Contact your system administrator with these details

---

*Hacienda ERP Data Pipeline - User Guide*
*Version 3.1.0*
