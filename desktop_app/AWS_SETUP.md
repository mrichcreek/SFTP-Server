# AWS Setup for Desktop App

This guide walks you through creating the S3 bucket and IAM credentials needed for the desktop app.

## Step 1: Create S3 Bucket

### Using AWS Console:

1. Go to **S3** in AWS Console
2. Click **Create bucket**
3. Settings:
   - **Bucket name**: `hacienda-sftp-downloads-YOURACCOUNT` (replace YOURACCOUNT with your AWS account ID)
   - **Region**: `us-east-1`
   - **Block all public access**: Enabled (keep checked)
   - **Bucket Versioning**: Enable
   - **Default encryption**: Enable with SSE-S3
4. Click **Create bucket**

### Using AWS CLI:

```bash
# Get your account ID
aws sts get-caller-identity --query Account --output text

# Create bucket (replace ACCOUNTID)
aws s3api create-bucket \
    --bucket hacienda-sftp-downloads-ACCOUNTID \
    --region us-east-1

# Enable versioning
aws s3api put-bucket-versioning \
    --bucket hacienda-sftp-downloads-ACCOUNTID \
    --versioning-configuration Status=Enabled

# Enable encryption
aws s3api put-bucket-encryption \
    --bucket hacienda-sftp-downloads-ACCOUNTID \
    --server-side-encryption-configuration '{
        "Rules": [{
            "ApplyServerSideEncryptionByDefault": {
                "SSEAlgorithm": "AES256"
            }
        }]
    }'
```

## Step 2: Create IAM User for Desktop App

### Using AWS Console:

1. Go to **IAM** > **Users** > **Create user**
2. **User name**: `hacienda-sftp-desktop-user`
3. Click **Next**
4. Select **Attach policies directly**
5. Click **Create policy** and use this JSON:

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
                "arn:aws:s3:::hacienda-sftp-downloads-ACCOUNTID",
                "arn:aws:s3:::hacienda-sftp-downloads-ACCOUNTID/*"
            ]
        }
    ]
}
```

6. Name it `HaciendaSFTPDesktopPolicy`
7. Attach the policy to the user
8. Go to **Security credentials** tab
9. Click **Create access key**
10. Select **Application running outside AWS**
11. **Save the Access Key ID and Secret Access Key** - you'll need these!

### Using AWS CLI:

```bash
# Create the IAM policy (save this as policy.json first)
aws iam create-policy \
    --policy-name HaciendaSFTPDesktopPolicy \
    --policy-document file://policy.json

# Create the user
aws iam create-user --user-name hacienda-sftp-desktop-user

# Attach the policy (replace ACCOUNTID)
aws iam attach-user-policy \
    --user-name hacienda-sftp-desktop-user \
    --policy-arn arn:aws:iam::ACCOUNTID:policy/HaciendaSFTPDesktopPolicy

# Create access key
aws iam create-access-key --user-name hacienda-sftp-desktop-user
```

## Step 3: Configure Desktop App

Edit `sftp_to_s3_app.py` and update these values:

```python
# AWS Settings
AWS_REGION = "us-east-1"
S3_BUCKET = "hacienda-sftp-downloads-YOURACCOUNT"  # Your actual bucket name
AWS_ACCESS_KEY = "AKIAXXXXXXXXXXXXXXXX"  # Your access key ID
AWS_SECRET_KEY = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"  # Your secret key
```

**OR** configure AWS CLI on the user's machine:

```bash
aws configure
# Enter Access Key ID
# Enter Secret Access Key
# Enter region: us-east-1
# Enter output format: json
```

If using AWS CLI credentials, leave `AWS_ACCESS_KEY` and `AWS_SECRET_KEY` empty in the app.

## Step 4: Test the Setup

1. Connect to FortiClient VPN
2. Run the desktop app: `python sftp_to_s3_app.py`
3. Click "Download Files"
4. Check S3 bucket for uploaded files

## Security Notes

- **Never commit credentials** to source control
- Consider using **AWS Secrets Manager** for production
- Rotate access keys periodically
- The IAM policy restricts access to only the specific S3 bucket
