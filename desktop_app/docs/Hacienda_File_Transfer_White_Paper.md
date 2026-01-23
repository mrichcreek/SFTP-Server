# Hacienda File Transfer Application
## Technical White Paper

**Version:** 1.0.0
**Date:** January 2026
**Classification:** Internal Technical Documentation

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [System Architecture](#2-system-architecture)
3. [Connection Methodology](#3-connection-methodology)
4. [File Discovery and Selection](#4-file-discovery-and-selection)
5. [Data Transfer Process](#5-data-transfer-process)
6. [File Integrity Verification](#6-file-integrity-verification)
7. [Error Handling and Retry Logic](#7-error-handling-and-retry-logic)
8. [Security Considerations](#8-security-considerations)
9. [User Interface Design](#9-user-interface-design)
10. [System Requirements](#10-system-requirements)
11. [Configuration Reference](#11-configuration-reference)
12. [Troubleshooting Guide](#12-troubleshooting-guide)

---

## 1. Executive Summary

The Hacienda File Transfer Application is a Windows desktop application designed to securely transfer files from an internal SFTP server to Amazon Web Services (AWS) Simple Storage Service (S3). The application provides a graphical user interface for initiating file transfers, monitoring progress, and verifying successful uploads.

### Key Capabilities

- **Secure SFTP Connection**: Connects to internal SFTP server over SSH protocol
- **Direct S3 Upload**: Streams files directly from SFTP to S3 without local storage
- **File Integrity Verification**: Validates uploaded file sizes match source files
- **Automatic Retry**: Implements retry logic for failed transfers
- **Real-time Progress Tracking**: Visual feedback on transfer status
- **Audit Logging**: Comprehensive logging of all operations

### Use Case

This application replaces a manual process where users would:
1. Connect to a remote desktop via RDP
2. Connect to FortiClient VPN
3. Run a Python script to download files locally
4. Manually verify file integrity

The new application automates steps 3 and 4 while adding S3 cloud storage as the destination, enabling centralized file access and improved data management.

---

## 2. System Architecture

### 2.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        User's Remote Desktop                             │
│                         (10.0.151.32)                                   │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐     │
│  │   FortiClient   │    │   Hacienda      │    │   AWS CLI       │     │
│  │   VPN Client    │    │   File Transfer │    │   Credentials   │     │
│  │   (Connected)   │    │   Application   │    │   (~/.aws)      │     │
│  └────────┬────────┘    └────────┬────────┘    └────────┬────────┘     │
│           │                      │                      │               │
│           │         VPN Tunnel   │                      │               │
└───────────┼──────────────────────┼──────────────────────┼───────────────┘
            │                      │                      │
            ▼                      │                      ▼
┌─────────────────────┐            │         ┌─────────────────────┐
│   Corporate Network │            │         │   AWS Cloud         │
│   ┌─────────────┐   │            │         │   ┌─────────────┐   │
│   │ SFTP Server │◄──┼────────────┘         │   │  S3 Bucket  │   │
│   │ 10.3.3.146  │   │      SFTP            │   │ hacienda-   │   │
│   │ Port 22     │   │      Connection      │   │ sftp-       │   │
│   └─────────────┘   │                      │   │ downloads   │   │
└─────────────────────┘                      │   └─────────────┘   │
                                             └─────────────────────┘
```

### 2.2 Component Overview

| Component | Purpose | Technology |
|-----------|---------|------------|
| Desktop Application | User interface and orchestration | Python 3.x, Tkinter |
| SFTP Client | Secure file transfer from source | Paramiko (SSH2 protocol) |
| S3 Client | Cloud storage upload | Boto3 (AWS SDK) |
| VPN Client | Network connectivity | FortiClient |

### 2.3 Data Flow

```
1. User initiates download
        │
        ▼
2. Application connects to SFTP server (10.3.3.146:22)
        │
        ▼
3. Application lists files in remote directory (/GPR/HCM)
        │
        ▼
4. For each file:
   a. Open file stream from SFTP
   b. Stream directly to S3 (no local storage)
   c. Verify uploaded file size matches source
        │
        ▼
5. Report results to user
```

---

## 3. Connection Methodology

### 3.1 SFTP Connection

The application uses the Paramiko library to establish SSH connections to the SFTP server.

#### Connection Parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| Host | 10.3.3.146 | Internal SFTP server IP address |
| Port | 22 | Standard SSH port |
| Protocol | SSH-2 | Secure Shell version 2 |
| Authentication | Password | Username/password authentication |
| Timeout | 30 seconds | Connection timeout threshold |

#### Connection Sequence

```python
# 1. Create SSH client instance
ssh_client = paramiko.SSHClient()

# 2. Configure host key policy (auto-accept for internal server)
ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

# 3. Establish connection with credentials
ssh_client.connect(
    hostname="10.3.3.146",
    port=22,
    username="gprerpusr",
    password="[REDACTED]",
    timeout=30
)

# 4. Open SFTP session over SSH connection
sftp = ssh_client.open_sftp()
```

#### Host Key Policy

The application uses `AutoAddPolicy()` which automatically accepts unknown host keys. This is appropriate for internal servers where:
- The server IP is static and known
- Communication occurs over VPN
- Man-in-the-middle attacks are mitigated by network security

### 3.2 AWS S3 Connection

The application uses Boto3, the official AWS SDK for Python, to connect to S3.

#### Authentication Methods (Priority Order)

1. **Explicit Credentials**: If `AWS_ACCESS_KEY` and `AWS_SECRET_KEY` are configured in the application
2. **AWS CLI Profile**: Credentials from `~/.aws/credentials` file
3. **Environment Variables**: `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`
4. **IAM Instance Role**: If running on EC2 (not applicable for desktop use)

#### Connection Verification

```python
# Initialize S3 client
s3_client = boto3.client('s3', region_name='us-east-1')

# Verify bucket access with HEAD request
s3_client.head_bucket(Bucket='hacienda-sftp-downloads')
```

The `head_bucket` call verifies:
- Credentials are valid
- Bucket exists
- User has permission to access the bucket

---

## 4. File Discovery and Selection

### 4.1 Directory Scanning

The application recursively scans the remote SFTP directory to discover all files for transfer.

#### Source Directory Structure

```
/GPR/HCM/                    # Root download folder
├── file1.csv                # Files at root level
├── file2.xml
├── INPUT/                   # Subdirectory (included)
│   ├── data1.csv
│   └── data2.csv
├── OUTPUT/                  # Subdirectory (included)
│   └── report.pdf
└── PROCESADOS/              # Excluded directory
    └── archived.csv         # NOT downloaded
```

### 4.2 File Selection Criteria

#### Included Files

- All regular files (not directories) in `/GPR/HCM`
- All regular files in subdirectories (recursive)
- Any file type/extension

#### Excluded Items

| Exclusion | Reason |
|-----------|--------|
| `PROCESADOS` directory | Contains already-processed/archived files |
| Directories themselves | Only files are transferred |

#### Exclusion Implementation

```python
EXCLUDE_DIRS = ["PROCESADOS"]

def list_remote_files(sftp, remote_dir):
    files = []
    entries = sftp.listdir_attr(remote_dir)

    for entry in entries:
        remote_path = f"{remote_dir}/{entry.filename}"

        if stat.S_ISREG(entry.st_mode):
            # Regular file - include it
            files.append({
                'path': remote_path,
                'filename': entry.filename,
                'size': entry.st_size
            })
        elif stat.S_ISDIR(entry.st_mode):
            # Directory - recurse if not excluded
            if entry.filename not in EXCLUDE_DIRS:
                files.extend(list_remote_files(sftp, remote_path))

    return files
```

### 4.3 File Metadata Collection

For each discovered file, the application collects:

| Attribute | Source | Purpose |
|-----------|--------|---------|
| `path` | Full SFTP path | Used for download |
| `filename` | File name only | Used for S3 key |
| `size` | `st_size` from SFTP stat | Used for verification |

---

## 5. Data Transfer Process

### 5.1 Streaming Architecture

The application uses a **streaming transfer** model where file data flows directly from SFTP to S3 without being written to local disk.

```
SFTP Server                    Application                    S3 Bucket
     │                              │                              │
     │  ◄── Open file stream ──────│                              │
     │                              │                              │
     │  ═══ File data chunks ═════►│══════ Upload stream ════════►│
     │                              │                              │
     │  ◄── Close stream ──────────│                              │
     │                              │                              │
```

#### Benefits of Streaming

1. **No Local Storage Required**: Files never touch local disk
2. **Memory Efficient**: Only small chunks held in memory at a time
3. **Faster Transfers**: No write-then-read overhead
4. **Reduced Failure Points**: Fewer I/O operations

### 5.2 S3 Upload Process

```python
def download_and_upload(sftp, file_info, s3_prefix):
    s3_key = f"{s3_prefix}/{file_info['filename']}"

    # Open SFTP file as binary stream
    with sftp.open(file_info['path'], 'rb') as remote_file:
        # Stream directly to S3
        s3_client.upload_fileobj(
            remote_file,           # File-like object (SFTP stream)
            S3_BUCKET,             # Destination bucket
            s3_key,                # Object key (path in S3)
            ExtraArgs={
                'Metadata': {
                    'source_path': file_info['path'],
                    'download_time': datetime.utcnow().isoformat()
                }
            }
        )
```

### 5.3 S3 Object Organization

Files are organized in S3 with the following structure:

```
s3://hacienda-sftp-downloads/
└── downloads/
    └── {YYYYMMDD_HHMMSS}/      # Job ID (timestamp)
        ├── file1.csv
        ├── file2.xml
        └── data.csv
```

#### Naming Convention

| Component | Format | Example |
|-----------|--------|---------|
| Bucket | `hacienda-sftp-downloads` | - |
| Prefix | `downloads/` | Static prefix |
| Job ID | `YYYYMMDD_HHMMSS` | `20260122_143052` |
| Filename | Original filename | `export_data.csv` |

Full S3 URI example: `s3://hacienda-sftp-downloads/downloads/20260122_143052/export_data.csv`

### 5.4 Metadata Attached to S3 Objects

Each uploaded object includes custom metadata:

| Key | Value | Purpose |
|-----|-------|---------|
| `source_path` | Full SFTP path | Audit trail |
| `download_time` | ISO 8601 timestamp | Transfer timing |

---

## 6. File Integrity Verification

### 6.1 Verification Method

The application uses **file size comparison** to verify successful uploads.

```python
# After upload, query S3 for object metadata
response = s3_client.head_object(Bucket=S3_BUCKET, Key=s3_key)
uploaded_size = response['ContentLength']

# Compare with source file size
verified = (uploaded_size == file_info['size'])
```

### 6.2 Why Size-Based Verification

| Method | Pros | Cons |
|--------|------|------|
| **Size Comparison** | Fast, no additional transfer | Won't detect corruption if size matches |
| MD5 Checksum | Detects corruption | Requires computing hash on both ends |
| SHA-256 | Cryptographic integrity | Computationally expensive |

**Rationale for Size-Based Approach:**
- SFTP and S3 both use TCP with built-in error detection
- Stream transfer is atomic (succeeds or fails completely)
- Size mismatch indicates truncated or failed transfer
- Balance between verification confidence and performance

### 6.3 Verification Results

| Scenario | Result | Action |
|----------|--------|--------|
| Sizes match | `verified: True` | File counted as success |
| Sizes differ | `verified: False` | Added to verification errors |
| Upload exception | N/A | Added to failed files |

### 6.4 Verification Logging

```
[14:30:52] Transferred: export_data.csv (2.5 MB)     # Success
[14:30:55] Verification failed: broken_file.csv      # Size mismatch
[14:30:58] Failed: network_error.csv - Connection reset  # Exception
```

---

## 7. Error Handling and Retry Logic

### 7.1 Retry Configuration

| Parameter | Value | Description |
|-----------|-------|-------------|
| `max_attempts` | 2 | Maximum number of download attempts |
| Retry delay | 3 seconds | Wait time between attempts |

### 7.2 Retry Flow

```
Start Download
     │
     ▼
┌─────────────────┐
│ Attempt 1       │
│ (current = 1)   │
└────────┬────────┘
         │
    Success? ─── Yes ──► Complete
         │
         No
         │
         ▼
    Log warning
    Wait 3 seconds
         │
         ▼
┌─────────────────┐
│ Attempt 2       │
│ (current = 2)   │
└────────┬────────┘
         │
    Success? ─── Yes ──► Complete
         │
         No
         │
         ▼
    Show failure dialog
    Offer manual retry
```

### 7.3 Error Categories

#### Connection Errors
- SFTP connection timeout
- SFTP authentication failure
- VPN not connected (network unreachable)

#### Transfer Errors
- File read error on SFTP
- S3 upload failure
- Network interruption during transfer

#### Verification Errors
- Size mismatch after upload
- S3 object not found after upload

### 7.4 Error Presentation

Failed transfers present a dialog with:
- Number of attempts made
- Specific error message
- Checklist of things to verify
- Option to retry or cancel

```
┌─────────────────────────────────────┐
│         Transfer Failed             │
├─────────────────────────────────────┤
│ Transfer failed after 2 attempts.   │
│                                     │
│ Error: Connection timed out         │
│                                     │
│ Please check:                       │
│ 1. FortiClient VPN is connected     │
│ 2. AWS credentials are configured   │
│                                     │
│ Would you like to try again?        │
│                                     │
│     [Retry]        [Cancel]         │
└─────────────────────────────────────┘
```

---

## 8. Security Considerations

### 8.1 Credential Management

#### SFTP Credentials
- Currently embedded in application code
- **Recommendation**: Move to environment variables or secure configuration file
- Access limited to users who have the application

#### AWS Credentials
- Stored via AWS CLI (`aws configure`)
- Located in `~/.aws/credentials`
- Protected by Windows user account permissions
- **Recommendation**: Use IAM user with minimal required permissions

### 8.2 Network Security

| Layer | Protection |
|-------|------------|
| VPN | FortiClient encrypts all traffic to corporate network |
| SFTP | SSH-2 encrypts file transfers (AES-256) |
| S3 | HTTPS (TLS 1.2+) encrypts uploads |
| S3 Storage | Server-side encryption (AES-256) at rest |

### 8.3 Data Flow Security

```
Desktop App ──TLS──► AWS S3 (HTTPS)
     │
     │ (VPN Tunnel - Encrypted)
     │
     ▼
SFTP Server ──SSH──► Desktop App
```

### 8.4 Minimum Required AWS Permissions

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "S3UploadAccess",
            "Effect": "Allow",
            "Action": [
                "s3:PutObject",
                "s3:GetObject",
                "s3:ListBucket"
            ],
            "Resource": [
                "arn:aws:s3:::hacienda-sftp-downloads",
                "arn:aws:s3:::hacienda-sftp-downloads/*"
            ]
        }
    ]
}
```

### 8.5 Security Recommendations

1. **Rotate Credentials**: Regularly rotate AWS access keys
2. **Audit Logging**: Enable S3 access logging for audit trail
3. **Bucket Policy**: Restrict bucket access to specific IAM users/roles
4. **VPN Requirement**: Document that VPN must be connected for SFTP access

---

## 9. User Interface Design

### 9.1 UI Components

```
┌─────────────────────────────────────────────────────────────┐
│                  Hacienda File Transfer                      │
│          Secure SFTP to S3 file synchronization             │
├─────────────────────────────────────────────────────────────┤
│  ● VPN Connection Required                                   │
│  ● S3: Connected                                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│                   [ Download Files ]                         │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│  Progress                                                    │
│  ┌─────────────────────────────────────────────────┐        │
│  │ Transferring files...                      75%  │        │
│  │ ████████████████████████░░░░░░░░░░░░░░░░░░░░░  │        │
│  │ Files: 15 / 20                    data.csv     │        │
│  └─────────────────────────────────────────────────┘        │
├─────────────────────────────────────────────────────────────┤
│  Activity Log                                                │
│  ┌─────────────────────────────────────────────────┐        │
│  │ [14:30:45] S3 connection established            │        │
│  │ [14:30:48] Starting download (attempt 1/2)      │        │
│  │ [14:30:49] Connecting to SFTP 10.3.3.146:22    │        │
│  │ [14:30:50] Connected to SFTP server             │        │
│  │ [14:30:51] Found 20 files to download           │        │
│  │ [14:30:52] Transferred: file1.csv (1.2 MB)     │        │
│  └─────────────────────────────────────────────────┘        │
├─────────────────────────────────────────────────────────────┤
│  Version 1.0.0              Target: s3://hacienda-sftp-...  │
└─────────────────────────────────────────────────────────────┘
```

### 9.2 Status Indicators

| Indicator | Color | Meaning |
|-----------|-------|---------|
| ● Yellow | `#fbbc04` | Pending/checking |
| ● Green | `#34a853` | Connected/success |
| ● Red | `#ea4335` | Error/disconnected |
| ● Gray | `#9aa0a6` | Inactive/unknown |

### 9.3 Log Color Coding

| Level | Color | Usage |
|-------|-------|-------|
| Info | White | General information |
| Success | Green | Successful operations |
| Warning | Yellow | Retry attempts, non-critical issues |
| Error | Red | Failed operations |

---

## 10. System Requirements

### 10.1 Minimum Requirements

| Component | Requirement |
|-----------|-------------|
| Operating System | Windows 10 or later |
| Memory | 4 GB RAM |
| Disk Space | 100 MB (application) |
| Network | FortiClient VPN connectivity |
| Display | 1024x768 minimum resolution |

### 10.2 Software Dependencies (Executable)

The standalone executable (`Hacienda_File_Transfer.exe`) includes all dependencies:
- Python runtime
- Paramiko (SFTP)
- Boto3 (AWS SDK)
- Tkinter (GUI)
- Cryptography libraries

### 10.3 External Requirements

| Requirement | Purpose |
|-------------|---------|
| FortiClient VPN | Network access to SFTP server |
| AWS CLI credentials | S3 authentication |
| Internet connectivity | Access to AWS S3 |

---

## 11. Configuration Reference

### 11.1 Application Configuration

Located in `sftp_to_s3_app.py`:

```python
# SFTP Settings
SFTP_HOST = "10.3.3.146"          # SFTP server IP
SFTP_PORT = 22                     # SSH port
SFTP_USER = "gprerpusr"           # SFTP username
SFTP_PASS = "[REDACTED]"          # SFTP password
REMOTE_DOWNLOAD_FOLDER = "/GPR/HCM"  # Source directory
EXCLUDE_DIRS = ["PROCESADOS"]      # Directories to skip

# AWS Settings
AWS_REGION = "us-east-1"           # AWS region
S3_BUCKET = "hacienda-sftp-downloads"  # Target bucket
AWS_ACCESS_KEY = ""                # Optional: explicit credentials
AWS_SECRET_KEY = ""                # Optional: explicit credentials
```

### 11.2 AWS CLI Configuration

Location: `C:\Users\{username}\.aws\credentials`

```ini
[default]
aws_access_key_id = AKIAXXXXXXXXXXXXXXXXX
aws_secret_access_key = xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Location: `C:\Users\{username}\.aws\config`

```ini
[default]
region = us-east-1
output = json
```

---

## 12. Troubleshooting Guide

### 12.1 Common Issues

#### "S3: No credentials found"

**Cause**: AWS CLI not configured on this machine.

**Solution**:
```cmd
aws configure
# Enter Access Key ID
# Enter Secret Access Key
# Enter region: us-east-1
# Enter output: json
```

#### "Connection timed out" / VPN indicator red

**Cause**: FortiClient VPN not connected or SFTP server unreachable.

**Solution**:
1. Verify FortiClient VPN is connected (green icon in system tray)
2. Test connectivity: `ping 10.3.3.146`
3. Verify SFTP server is running

#### "Access Denied" on S3

**Cause**: IAM user lacks required S3 permissions.

**Solution**:
1. Verify IAM user has `s3:PutObject`, `s3:GetObject`, `s3:ListBucket` permissions
2. Verify permissions are for correct bucket ARN

#### Application won't start

**Cause**: Missing dependencies or antivirus blocking.

**Solution**:
1. Try running from command prompt to see errors
2. Add exception in antivirus for the executable
3. Re-download application if corrupted

### 12.2 Log File Location

```
C:\SFTPProgram\desktop_app\download_log.txt
```

Contains timestamped entries of all operations for debugging.

---

## Document Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0.0 | January 2026 | System | Initial release |

---

*This document is confidential and intended for internal use only.*
