# Hacienda SFTP to S3 Download Application

Download files from an internal SFTP server (10.3.3.146) to AWS S3 with progress tracking, retry logic, and file verification.

## Two Solutions Available

| Solution | Status | Best For |
|----------|--------|----------|
| **Desktop App** | Ready Now | Immediate use - requires FortiClient VPN on user's PC |
| **Amplify Web App** | Requires Site-to-Site VPN | Long-term solution - once AWS VPN is configured |

---

## Solution 1: Desktop App (Ready Now)

A Windows desktop application that runs on the user's PC after they connect to FortiClient VPN.

### How It Works
```
User's PC (FortiClient VPN Connected)
    └── Desktop App
            ├── Connects to SFTP (10.3.3.146)
            ├── Downloads files
            └── Uploads to S3 bucket
```

### Quick Start

1. **Setup AWS resources** (one-time):
   - Follow instructions in `desktop_app/AWS_SETUP.md`
   - Creates S3 bucket and IAM credentials

2. **Configure the app**:
   - Edit `desktop_app/sftp_to_s3_app.py`
   - Update `S3_BUCKET` with your bucket name
   - Add AWS credentials (or configure AWS CLI)

3. **Run the app**:
   ```bash
   cd desktop_app
   pip install -r requirements.txt
   python sftp_to_s3_app.py
   ```

4. **Build executable** (optional):
   ```bash
   cd desktop_app
   build_exe.bat
   # Creates: dist/Hacienda_SFTP_Download.exe
   ```

### User Workflow
1. Connect to FortiClient VPN
2. Run the desktop app (or .exe)
3. Click "Download Files"
4. Monitor progress bar
5. Files uploaded to S3

### Desktop App Files
```
desktop_app/
├── sftp_to_s3_app.py    # Main application
├── requirements.txt      # Python dependencies
├── build_exe.bat        # Build to .exe
└── AWS_SETUP.md         # AWS setup instructions
```

---

## Solution 2: Amplify Web App (Long-term)

A web application hosted on AWS Amplify with Cognito authentication. Requires AWS Site-to-Site VPN to corporate network.

### How It Works
```
User (any browser)
    └── Amplify Web App
            └── API Gateway
                    └── Lambda (in VPC)
                            └── Site-to-Site VPN
                                    └── SFTP Server (10.3.3.146)
```

### Setup Requirements

1. **Site-to-Site VPN**: Must be configured between AWS and corporate network
   - See `SITE_TO_SITE_VPN_SETUP.md` for detailed instructions
   - Requires coordination with IT/Network team

2. **Deploy Backend**:
   ```bash
   cd infrastructure
   # Update samconfig.toml with VPC/Subnet/Security Group IDs
   sam build
   sam deploy --guided
   ```

3. **Deploy Frontend**:
   ```bash
   cd frontend
   # Update src/aws-exports.js with deployment outputs
   npm install
   npm start  # or deploy to Amplify Hosting
   ```

### Amplify App Files
```
infrastructure/
├── template.yaml        # SAM/CloudFormation template
└── samconfig.toml       # Deployment config

lambda/sftp_download/
├── handler.py           # Lambda functions
└── requirements.txt

frontend/
├── src/
│   ├── App.js
│   ├── aws-exports.js   # AWS configuration
│   ├── components/      # React components
│   └── services/api.js  # API calls
├── package.json
└── amplify.yml
```

### AWS Resources Created
| Resource | Name | Purpose |
|----------|------|---------|
| S3 Bucket | hacienda-sftp-downloads-{account-id} | Store downloaded files |
| Cognito User Pool | hacienda-sftp-client-users | User authentication |
| DynamoDB Table | hacienda-sftp-download-jobs-prod | Track download jobs |
| Secrets Manager | hacienda-sftp-credentials | SFTP credentials |
| Lambda Functions | hacienda-sftp-download-* | SFTP/S3 operations |
| API Gateway | hacienda-sftp-api-prod | REST API |

---

## Features (Both Solutions)

- **Progress Tracking**: Real-time progress bar with status messages
- **Retry Logic**: Automatic retry on failure (2 attempts)
- **File Verification**: Size verification after download
- **Error Handling**: Detailed error messages with failure reasons
- **S3 Storage**: Files organized by download job with timestamps

---

## NEW: File Validation & Processing Workflow

The Amplify Web App now includes a complete validation and processing workflow:

### Validation Features

1. **File Name Validation**
   - Validates files against official naming conventions
   - Pattern: `HCM_{SOURCE}_INTF_{ENTITY}_{DATE}.csv`
   - Valid Sources: PERSON, PERSON_NAME, PERSON_ASSIGNMENT, PERSON_ADDRESS, PERSON_NID, PERSON_SUPERVISOR, PERSON_EMAIL, SENIORITY
   - Valid Entities: 911, RHUM, HACIENDA, FIMAS, DOE, KRONOSPOL, KRONOSDE, SEPI, ADPPOLICIA
   - Suggests corrections for misnamed files

2. **Completeness Checking**
   - Verifies all required files are present for each entity/date
   - Reports missing sources per entity
   - Calculates overall completeness percentage

3. **Duplicate Detection**
   - Finds exact duplicates by file hash (ETag)
   - Identifies storage waste
   - Recommends which files to keep

4. **Database Loading**
   - Loads CSV files into SQL Server tables
   - Extracts source/entity from file names
   - Supports all HCM interface tables

5. **HCM Interface Execution**
   - Runs the HCM_MAIN_INTF stored procedure
   - Supports test mode

### New API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/validate` | POST | Validate file names |
| `/completeness` | POST | Check file completeness |
| `/duplicates` | POST | Find duplicate files |
| `/load` | POST | Load files to database |
| `/workflow` | POST | Run complete workflow |
| `/workflow/status/{jobId}` | GET | Get workflow status |

### New Lambda Functions

| Function | Purpose |
|----------|---------|
| `hacienda-validate-files` | File name validation |
| `hacienda-check-completeness` | Completeness checking |
| `hacienda-check-duplicates` | Duplicate detection |
| `hacienda-load-database` | Database loading (VPC) |
| `hacienda-workflow` | Complete workflow orchestration |
| `hacienda-workflow-status` | Workflow status |

---

## Project Structure

```
C:\SFTPProgram\
├── desktop_app/                    # SOLUTION 1: Desktop App
│   ├── sftp_to_s3_app.py
│   ├── requirements.txt
│   ├── build_exe.bat
│   └── AWS_SETUP.md
│
├── infrastructure/                 # SOLUTION 2: Amplify Backend
│   ├── template.yaml
│   └── samconfig.toml
│
├── lambda/                         # SOLUTION 2: Lambda Code
│   ├── sftp_download/
│   │   ├── handler.py              # SFTP download functions
│   │   └── workflow_handler.py     # Validation workflow
│   ├── file_validation/            # Validation modules
│   │   ├── file_naming_validator.py
│   │   ├── completeness_checker.py
│   │   └── duplicate_detector.py
│   ├── data_loader/                # Database loading
│   │   └── database_loader.py
│   └── requirements.txt
│
├── frontend/                       # SOLUTION 2: React Frontend
│   ├── src/
│   ├── package.json
│   └── amplify.yml
│
├── SITE_TO_SITE_VPN_SETUP.md      # VPN Setup Guide
└── README.md                       # This file
```

---

## Next Steps

### Immediate (Desktop App)
1. Create S3 bucket following `desktop_app/AWS_SETUP.md`
2. Configure and test desktop app
3. Distribute to users

### Long-term (Amplify + Site-to-Site VPN)
1. Share `SITE_TO_SITE_VPN_SETUP.md` with IT team
2. Coordinate VPN configuration
3. Deploy Amplify solution once VPN is active
4. Migrate users from desktop app to web app

---

## Troubleshooting

### Desktop App Issues

**"Cannot connect to SFTP"**
- Ensure FortiClient VPN is connected
- Verify you can ping 10.3.3.146

**"Access Denied to S3"**
- Check AWS credentials are configured
- Verify IAM policy allows S3 access
- Check bucket name is correct

**"Module not found"**
- Run `pip install -r requirements.txt`

### Amplify App Issues

**Lambda cannot reach SFTP**
- Site-to-Site VPN must be active
- Check VPN tunnel status in AWS Console
- Verify route tables point to VGW

**Authentication errors**
- Verify aws-exports.js has correct Cognito IDs
- User must exist in User Pool
