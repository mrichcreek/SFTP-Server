import json
import os
import uuid
import time
import stat
from datetime import datetime, timedelta
from decimal import Decimal
import boto3
import paramiko
from botocore.exceptions import ClientError

# Initialize AWS clients
s3_client = boto3.client('s3')
secrets_client = boto3.client('secretsmanager')
dynamodb = boto3.resource('dynamodb')

# Environment variables
S3_BUCKET = os.environ.get('S3_BUCKET')
SECRETS_ARN = os.environ.get('SECRETS_ARN')
JOBS_TABLE = os.environ.get('JOBS_TABLE')
REMOTE_DOWNLOAD_FOLDER = os.environ.get('REMOTE_DOWNLOAD_FOLDER', '/GPR/HCM')

# Constants
MAX_RETRIES = 2
RETRY_DELAY = 2  # seconds


def get_sftp_credentials():
    """Retrieve SFTP credentials from Secrets Manager."""
    try:
        response = secrets_client.get_secret_value(SecretId=SECRETS_ARN)
        return json.loads(response['SecretString'])
    except ClientError as e:
        raise Exception(f"Failed to retrieve SFTP credentials: {str(e)}")


def create_sftp_connection(credentials):
    """Create and return an SFTP connection."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    client.connect(
        hostname=credentials['host'],
        port=int(credentials['port']),
        username=credentials['username'],
        password=credentials['password'],
        timeout=30
    )

    return client, client.open_sftp()


def update_job_status(job_id, status, progress=0, message="", files_downloaded=0,
                      total_files=0, error=None, files_info=None):
    """Update job status in DynamoDB."""
    table = dynamodb.Table(JOBS_TABLE)

    update_expr = "SET #status = :status, progress = :progress, message = :message, " \
                  "filesDownloaded = :filesDownloaded, totalFiles = :totalFiles, " \
                  "updatedAt = :updatedAt"

    expr_values = {
        ':status': status,
        ':progress': Decimal(str(progress)),
        ':message': message,
        ':filesDownloaded': files_downloaded,
        ':totalFiles': total_files,
        ':updatedAt': datetime.utcnow().isoformat()
    }

    if error:
        update_expr += ", errorDetails = :error"
        expr_values[':error'] = error

    if files_info:
        update_expr += ", filesInfo = :filesInfo"
        expr_values[':filesInfo'] = json.dumps(files_info)

    table.update_item(
        Key={'jobId': job_id},
        UpdateExpression=update_expr,
        ExpressionAttributeNames={'#status': 'status'},
        ExpressionAttributeValues=expr_values
    )


def list_remote_files(sftp, remote_dir, exclude_dirs=None):
    """Recursively list all files in remote directory."""
    if exclude_dirs is None:
        exclude_dirs = ['PROCESADOS']

    files = []

    try:
        entries = sftp.listdir_attr(remote_dir)
    except FileNotFoundError:
        return files

    for entry in entries:
        remote_path = f"{remote_dir}/{entry.filename}"

        if stat.S_ISREG(entry.st_mode):
            files.append({
                'path': remote_path,
                'filename': entry.filename,
                'size': entry.st_size
            })
        elif stat.S_ISDIR(entry.st_mode) and entry.filename not in exclude_dirs:
            files.extend(list_remote_files(sftp, remote_path, exclude_dirs))

    return files


def download_file_to_s3(sftp, remote_path, filename, file_size, job_id, s3_prefix):
    """Download a file from SFTP and upload directly to S3."""
    s3_key = f"{s3_prefix}/{filename}"

    # Create a file-like object to stream from SFTP to S3
    with sftp.open(remote_path, 'rb') as remote_file:
        s3_client.upload_fileobj(
            remote_file,
            S3_BUCKET,
            s3_key,
            ExtraArgs={'Metadata': {'source_path': remote_path, 'job_id': job_id}}
        )

    # Verify the upload
    response = s3_client.head_object(Bucket=S3_BUCKET, Key=s3_key)
    uploaded_size = response['ContentLength']

    return {
        'filename': filename,
        's3_key': s3_key,
        'source_size': file_size,
        'uploaded_size': uploaded_size,
        'verified': uploaded_size == file_size
    }


def perform_download(job_id, attempt=1):
    """Main download logic with retry support."""
    s3_prefix = f"downloads/{job_id}"
    files_info = []

    try:
        update_job_status(job_id, 'connecting', 0, f'Connecting to SFTP server (attempt {attempt})...')

        # Get credentials and connect
        credentials = get_sftp_credentials()
        ssh_client, sftp = create_sftp_connection(credentials)

        update_job_status(job_id, 'listing', 5, 'Connected. Listing files...')

        # List all files to download
        remote_files = list_remote_files(sftp, REMOTE_DOWNLOAD_FOLDER)
        total_files = len(remote_files)

        if total_files == 0:
            update_job_status(job_id, 'completed', 100, 'No files found to download.',
                            0, 0, files_info=[])
            sftp.close()
            ssh_client.close()
            return True

        update_job_status(job_id, 'downloading', 10, f'Found {total_files} files. Starting download...',
                         0, total_files)

        # Download each file
        files_downloaded = 0
        failed_files = []

        for i, file_info in enumerate(remote_files):
            try:
                result = download_file_to_s3(
                    sftp,
                    file_info['path'],
                    file_info['filename'],
                    file_info['size'],
                    job_id,
                    s3_prefix
                )
                files_info.append(result)
                files_downloaded += 1

                # Calculate progress (10-90% for downloads, 90-100% for verification)
                progress = 10 + int((files_downloaded / total_files) * 80)
                update_job_status(
                    job_id, 'downloading', progress,
                    f'Downloaded {files_downloaded}/{total_files}: {file_info["filename"]}',
                    files_downloaded, total_files
                )

            except Exception as e:
                failed_files.append({
                    'filename': file_info['filename'],
                    'error': str(e)
                })

        # Close connections
        sftp.close()
        ssh_client.close()

        # Verification phase
        update_job_status(job_id, 'verifying', 90, 'Verifying downloaded files...',
                         files_downloaded, total_files)

        # Check all files were downloaded and sizes match
        verification_errors = []
        for file_result in files_info:
            if not file_result['verified']:
                verification_errors.append(
                    f"{file_result['filename']}: Size mismatch "
                    f"(expected {file_result['source_size']}, got {file_result['uploaded_size']})"
                )

        if failed_files or verification_errors:
            error_details = {
                'failed_downloads': failed_files,
                'verification_errors': verification_errors
            }

            if attempt < MAX_RETRIES:
                update_job_status(job_id, 'retrying', 0,
                                f'Some files failed. Retrying (attempt {attempt + 1})...',
                                files_downloaded, total_files, error=json.dumps(error_details))
                time.sleep(RETRY_DELAY)
                return perform_download(job_id, attempt + 1)
            else:
                update_job_status(job_id, 'failed', 0,
                                'Download failed after maximum retries.',
                                files_downloaded, total_files,
                                error=json.dumps(error_details), files_info=files_info)
                return False

        # Success
        update_job_status(job_id, 'completed', 100,
                         f'Successfully downloaded and verified {files_downloaded} files.',
                         files_downloaded, total_files, files_info=files_info)
        return True

    except Exception as e:
        error_msg = str(e)

        if attempt < MAX_RETRIES:
            update_job_status(job_id, 'retrying', 0,
                            f'Error occurred: {error_msg}. Retrying (attempt {attempt + 1})...',
                            error=error_msg)
            time.sleep(RETRY_DELAY)
            return perform_download(job_id, attempt + 1)
        else:
            update_job_status(job_id, 'failed', 0,
                            f'Download failed: {error_msg}',
                            error=error_msg)
            return False


def lambda_handler(event, context):
    """Main Lambda handler for starting downloads."""
    try:
        # Generate unique job ID
        job_id = str(uuid.uuid4())

        # Get user info from Cognito authorizer
        user_id = "unknown"
        if event.get('requestContext', {}).get('authorizer', {}).get('claims'):
            user_id = event['requestContext']['authorizer']['claims'].get('sub', 'unknown')

        # Create initial job record
        table = dynamodb.Table(JOBS_TABLE)
        ttl = int((datetime.utcnow() + timedelta(days=7)).timestamp())

        table.put_item(Item={
            'jobId': job_id,
            'userId': user_id,
            'status': 'starting',
            'progress': Decimal('0'),
            'message': 'Initializing download...',
            'filesDownloaded': 0,
            'totalFiles': 0,
            'createdAt': datetime.utcnow().isoformat(),
            'updatedAt': datetime.utcnow().isoformat(),
            'ttl': ttl
        })

        # Start the download process
        success = perform_download(job_id)

        # Return the job ID so client can poll for status
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type,Authorization',
                'Access-Control-Allow-Methods': 'GET,POST,OPTIONS'
            },
            'body': json.dumps({
                'jobId': job_id,
                'message': 'Download completed' if success else 'Download failed',
                'success': success
            })
        }

    except Exception as e:
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'error': str(e),
                'message': 'Failed to start download'
            })
        }


def status_handler(event, context):
    """Lambda handler for checking download status."""
    try:
        job_id = event['pathParameters']['jobId']

        table = dynamodb.Table(JOBS_TABLE)
        response = table.get_item(Key={'jobId': job_id})

        if 'Item' not in response:
            return {
                'statusCode': 404,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({'error': 'Job not found'})
            }

        item = response['Item']

        # Convert Decimal to float for JSON serialization
        if 'progress' in item:
            item['progress'] = float(item['progress'])

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type,Authorization',
                'Access-Control-Allow-Methods': 'GET,POST,OPTIONS'
            },
            'body': json.dumps(item)
        }

    except Exception as e:
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({'error': str(e)})
        }


def list_files_handler(event, context):
    """Lambda handler for listing downloaded files in S3."""
    try:
        # Get optional prefix from query parameters
        prefix = 'downloads/'
        if event.get('queryStringParameters'):
            job_id = event['queryStringParameters'].get('jobId')
            if job_id:
                prefix = f'downloads/{job_id}/'

        # List objects in S3
        response = s3_client.list_objects_v2(
            Bucket=S3_BUCKET,
            Prefix=prefix
        )

        files = []
        if 'Contents' in response:
            for obj in response['Contents']:
                files.append({
                    'key': obj['Key'],
                    'size': obj['Size'],
                    'lastModified': obj['LastModified'].isoformat()
                })

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type,Authorization',
                'Access-Control-Allow-Methods': 'GET,POST,OPTIONS'
            },
            'body': json.dumps({
                'files': files,
                'count': len(files)
            })
        }

    except Exception as e:
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({'error': str(e)})
        }
