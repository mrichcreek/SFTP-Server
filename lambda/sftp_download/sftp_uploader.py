"""
SFTP Uploader for Hacienda HCM Pipeline.
Uploads export files from S3 to the SFTP server for Oracle consumption.
Adapted from SFTP FilesPublish Upload.py for Lambda execution.
"""

import boto3
import paramiko
import io
import os
import json
from typing import Dict, List, Optional

try:
    from .sql_table_loader import get_aws_secret
except ImportError:
    from sftp_download.sql_table_loader import get_aws_secret


# Default SFTP settings (can be overridden via secrets or env vars)
# Note: VPN IP (10.3.3.146) is for desktop app with FortiClient VPN
# Direct connection uses public Sterling IP 64.185.194.33
DEFAULT_SFTP_HOST = os.environ.get('SFTP_HOST', '64.185.194.33')  # Sterling Production public IP
DEFAULT_SFTP_PORT = int(os.environ.get('SFTP_PORT', '22'))
DEFAULT_SFTP_USER = os.environ.get('SFTP_USER', 'gprerpusr')
DEFAULT_REMOTE_FOLDER = os.environ.get('SFTP_UPLOAD_FOLDER', '/GPR/HCM/INPUT')


class SftpUploader:
    """
    Uploads files from S3 to an SFTP server.
    Used as the final step in the pipeline to deliver export files to Oracle.
    """

    def __init__(
        self,
        bucket: str,
        sftp_secret_name: Optional[str] = None,
        sftp_host: str = DEFAULT_SFTP_HOST,
        sftp_port: int = DEFAULT_SFTP_PORT,
        sftp_user: str = DEFAULT_SFTP_USER,
        sftp_password: Optional[str] = None,
        remote_folder: str = DEFAULT_REMOTE_FOLDER
    ):
        """
        Initialize the SFTP Uploader.

        Args:
            bucket: S3 bucket containing files to upload
            sftp_secret_name: AWS Secrets Manager secret for SFTP credentials (optional)
            sftp_host: SFTP server hostname/IP
            sftp_port: SFTP server port
            sftp_user: SFTP username
            sftp_password: SFTP password (if not using secret)
            remote_folder: Remote folder path on SFTP server
        """
        self.bucket = bucket
        self.s3_client = boto3.client('s3')

        # Load SFTP credentials from secret if provided
        if sftp_secret_name:
            try:
                secret_string = get_aws_secret(sftp_secret_name)
                # Try to parse as JSON
                try:
                    secret = json.loads(secret_string)
                    self.sftp_host = secret.get('host', sftp_host)
                    self.sftp_port = int(secret.get('port', sftp_port))
                    self.sftp_user = secret.get('username', sftp_user)
                    self.sftp_password = secret.get('password', sftp_password)
                    if secret.get('remote_folder'):
                        remote_folder = secret.get('remote_folder')
                except json.JSONDecodeError:
                    # Secret is plain text (password only)
                    self.sftp_host = sftp_host
                    self.sftp_port = sftp_port
                    self.sftp_user = sftp_user
                    self.sftp_password = secret_string
            except Exception:
                # Fall back to provided credentials
                self.sftp_host = sftp_host
                self.sftp_port = sftp_port
                self.sftp_user = sftp_user
                self.sftp_password = sftp_password
        else:
            self.sftp_host = sftp_host
            self.sftp_port = sftp_port
            self.sftp_user = sftp_user
            self.sftp_password = sftp_password

        self.remote_folder = remote_folder.rstrip('/')
        self._sftp = None
        self._client = None

    def connect(self) -> bool:
        """
        Establish SFTP connection.

        Returns:
            True if connection successful
        """
        try:
            self._client = paramiko.SSHClient()
            self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self._client.connect(
                hostname=self.sftp_host,
                port=self.sftp_port,
                username=self.sftp_user,
                password=self.sftp_password,
                timeout=30
            )
            self._sftp = self._client.open_sftp()
            return True
        except Exception as e:
            self._sftp = None
            self._client = None
            raise ConnectionError(f"Failed to connect to SFTP: {str(e)}")

    def disconnect(self) -> None:
        """Close SFTP connection."""
        if self._sftp:
            try:
                self._sftp.close()
            except Exception:
                pass
            self._sftp = None

        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def ensure_remote_dir(self, remote_path: str) -> None:
        """
        Ensure remote directory exists, creating if necessary.

        Args:
            remote_path: Remote directory path
        """
        if not self._sftp:
            raise RuntimeError("Not connected to SFTP")

        dirs = remote_path.strip('/').split('/')
        current_path = ""

        for folder in dirs:
            current_path += "/" + folder
            try:
                self._sftp.stat(current_path)
            except FileNotFoundError:
                try:
                    self._sftp.mkdir(current_path)
                except Exception:
                    pass  # May already exist from race condition

    def upload_file(self, s3_key: str, remote_filename: Optional[str] = None) -> Dict:
        """
        Upload a single file from S3 to SFTP.

        Args:
            s3_key: S3 object key
            remote_filename: Optional override for remote filename

        Returns:
            Dict with upload result
        """
        if not self._sftp:
            raise RuntimeError("Not connected to SFTP")

        result = {
            's3_key': s3_key,
            'success': False
        }

        try:
            # Get file from S3
            response = self.s3_client.get_object(Bucket=self.bucket, Key=s3_key)
            file_content = response['Body'].read()
            file_size = len(file_content)

            # Determine remote filename
            if remote_filename is None:
                remote_filename = os.path.basename(s3_key)

            remote_path = f"{self.remote_folder}/{remote_filename}"
            result['remote_path'] = remote_path
            result['filename'] = remote_filename
            result['size'] = file_size

            # Upload to SFTP
            self.ensure_remote_dir(self.remote_folder)
            with io.BytesIO(file_content) as file_obj:
                self._sftp.putfo(file_obj, remote_path)

            result['success'] = True

        except Exception as e:
            result['error'] = str(e)

        return result

    def upload_files(self, s3_keys: List[str]) -> Dict:
        """
        Upload multiple files from S3 to SFTP.

        Args:
            s3_keys: List of S3 object keys to upload

        Returns:
            Dict with overall results and per-file details
        """
        result = {
            'success': False,
            'files_uploaded': 0,
            'files_failed': 0,
            'total_bytes': 0,
            'file_results': [],
            'errors': []
        }

        try:
            self.connect()

            for s3_key in s3_keys:
                file_result = self.upload_file(s3_key)
                result['file_results'].append(file_result)

                if file_result['success']:
                    result['files_uploaded'] += 1
                    result['total_bytes'] += file_result.get('size', 0)
                else:
                    result['files_failed'] += 1
                    if file_result.get('error'):
                        result['errors'].append(f"{s3_key}: {file_result['error']}")

            result['success'] = result['files_failed'] == 0

        except Exception as e:
            result['error'] = str(e)

        finally:
            self.disconnect()

        return result

    def upload_from_prefix(self, prefix: str, file_extension: str = '.csv') -> Dict:
        """
        Upload all files from an S3 prefix to SFTP.

        Args:
            prefix: S3 prefix to list files from
            file_extension: Only upload files with this extension

        Returns:
            Dict with upload results
        """
        # List files in S3 prefix
        paginator = self.s3_client.get_paginator('list_objects_v2')
        s3_keys = []

        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get('Contents', []):
                key = obj['Key']
                if key.endswith(file_extension):
                    s3_keys.append(key)

        if not s3_keys:
            return {
                'success': True,
                'files_uploaded': 0,
                'message': f'No {file_extension} files found in {prefix}'
            }

        return self.upload_files(s3_keys)

    def move_uploaded_to_sent(self, s3_keys: List[str], sent_prefix: str = 'sent/') -> Dict:
        """
        Move successfully uploaded files to a 'sent' folder in S3.

        Args:
            s3_keys: List of S3 keys to move
            sent_prefix: Destination prefix for sent files

        Returns:
            Dict with move results
        """
        result = {
            'moved': 0,
            'failed': 0,
            'errors': []
        }

        sent_prefix = sent_prefix.rstrip('/') + '/'

        for s3_key in s3_keys:
            try:
                filename = os.path.basename(s3_key)
                new_key = f"{sent_prefix}{filename}"

                # Copy to sent folder
                self.s3_client.copy_object(
                    Bucket=self.bucket,
                    CopySource={'Bucket': self.bucket, 'Key': s3_key},
                    Key=new_key
                )

                # Delete original
                self.s3_client.delete_object(Bucket=self.bucket, Key=s3_key)

                result['moved'] += 1

            except Exception as e:
                result['failed'] += 1
                result['errors'].append(f"{s3_key}: {str(e)}")

        return result


def upload_exports_to_sftp(
    bucket: str,
    export_prefix: str = 'exports/',
    sftp_secret_name: Optional[str] = None,
    sftp_host: str = DEFAULT_SFTP_HOST,
    sftp_port: int = DEFAULT_SFTP_PORT,
    sftp_user: str = DEFAULT_SFTP_USER,
    sftp_password: Optional[str] = None,
    remote_folder: str = DEFAULT_REMOTE_FOLDER,
    move_to_sent: bool = True
) -> Dict:
    """
    Convenience function to upload all export files to SFTP.

    Args:
        bucket: S3 bucket containing export files
        export_prefix: S3 prefix where export files are located
        sftp_secret_name: AWS secret name for SFTP credentials
        sftp_host: SFTP server hostname
        sftp_port: SFTP server port
        sftp_user: SFTP username
        sftp_password: SFTP password
        remote_folder: Remote folder on SFTP server
        move_to_sent: Whether to move uploaded files to sent folder

    Returns:
        Dict with upload results
    """
    uploader = SftpUploader(
        bucket=bucket,
        sftp_secret_name=sftp_secret_name,
        sftp_host=sftp_host,
        sftp_port=sftp_port,
        sftp_user=sftp_user,
        sftp_password=sftp_password,
        remote_folder=remote_folder
    )

    result = uploader.upload_from_prefix(export_prefix)

    # Move successfully uploaded files to sent folder
    if move_to_sent and result.get('success') and result.get('file_results'):
        uploaded_keys = [
            fr['s3_key']
            for fr in result['file_results']
            if fr.get('success')
        ]
        if uploaded_keys:
            move_result = uploader.move_uploaded_to_sent(uploaded_keys)
            result['move_result'] = move_result

    return result


def upload_handler(event, context):
    """
    Lambda handler for SFTP upload - called by Step Functions.

    Input (from Step Functions):
    {
        "sftp_secret": "Sterling_SFTP_Direct_Production",
        "remote_folder": "/GPR/HCM/INPUT",
        "export_folder": "20240115_1030/7_Export_Files/",
        "s3_bucket": "hacienda-sftp-downloads"
    }

    Returns:
    {
        "statusCode": 200,
        "body": {
            "status": "success",
            "files_uploaded": 12,
            "total_bytes": 150000,
            "remote_folder": "/GPR/HCM/INPUT"
        }
    }
    """
    import json

    try:
        # Support both direct invoke and API Gateway
        if 'body' in event and event['body']:
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        else:
            body = event

        sftp_secret = body.get('sftp_secret', os.environ.get('SFTP_SECRET_NAME', 'Sterling_SFTP_Direct_Production'))
        remote_folder = body.get('remote_folder', '/GPR/HCM/INPUT')
        export_folder = body.get('export_folder', body.get('output_prefix', 'exports/'))
        bucket = body.get('s3_bucket', os.environ.get('S3_BUCKET', 'hacienda-sftp-downloads'))

        uploader = SftpUploader(
            bucket=bucket,
            sftp_secret_name=sftp_secret,
            remote_folder=remote_folder
        )

        result = uploader.upload_from_prefix(
            prefix=export_folder,
            file_extension='.csv'
        )

        # Add context for reporting
        result['remote_folder'] = remote_folder
        result['export_folder'] = export_folder
        result['sftp_host'] = uploader.sftp_host

        # Determine overall status
        if result.get('files_uploaded', 0) > 0 and result.get('files_failed', 0) == 0:
            result['status'] = 'success'
        elif result.get('files_uploaded', 0) > 0:
            result['status'] = 'partial'
        else:
            result['status'] = 'no_files' if result.get('success') else 'error'

        # For Step Functions, return result directly
        if 'body' not in event:
            return result

        # For API Gateway
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps(result, default=str)
        }

    except Exception as e:
        error_result = {
            'status': 'error',
            'error': str(e),
            'error_type': type(e).__name__
        }

        if 'body' not in event:
            return error_result

        return {
            'statusCode': 500,
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'body': json.dumps(error_result)
        }
