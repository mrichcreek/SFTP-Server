"""
SFTP Downloader for Hacienda HCM Pipeline.
Downloads HCM files from the Sterling SFTP server to S3.
Adapted from SFTP Pre_Validation Reports Download.py for Lambda execution.
"""

import boto3
import paramiko
import stat
import os
import json
from typing import Dict, List, Optional
from datetime import datetime

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
# Remote folder to download from (Sterling HCM data folder)
# Note: /GPR/HCM contains HCM data files, /OCI/HCM/OUTPUT/ contains error reports
DEFAULT_REMOTE_DOWNLOAD_FOLDER = os.environ.get('SFTP_DOWNLOAD_FOLDER', '/GPR/HCM')

# Secret names for Sterling direct connection (no VPN required)
STERLING_PRODUCTION_SECRET = 'Sterling_SFTP_Direct_Production'
STERLING_TEST_SECRET = 'Sterling_SFTP_Direct_Test'

# Directories to exclude from recursive download
# Note: PROCESADOS contains already-processed files, so skip those
# RHUM files are NOT excluded - they have special handling (process all files oldest to newest)
EXCLUDED_DIRECTORIES = ['PROCESADOS']


class SftpDownloader:
    """
    Downloads files from an SFTP server to S3.
    Used at the start of the pipeline to retrieve HCM files from Sterling.
    """

    def __init__(
        self,
        bucket: str,
        sftp_secret_name: Optional[str] = None,
        sftp_host: str = DEFAULT_SFTP_HOST,
        sftp_port: int = DEFAULT_SFTP_PORT,
        sftp_user: str = DEFAULT_SFTP_USER,
        sftp_password: Optional[str] = None,
        remote_folder: str = DEFAULT_REMOTE_DOWNLOAD_FOLDER,
        excluded_dirs: Optional[List[str]] = None
    ):
        """
        Initialize the SFTP Downloader.

        Args:
            bucket: S3 bucket to upload downloaded files to
            sftp_secret_name: AWS Secrets Manager secret for SFTP credentials (optional)
            sftp_host: SFTP server hostname/IP
            sftp_port: SFTP server port
            sftp_user: SFTP username
            sftp_password: SFTP password (if not using secret)
            remote_folder: Remote folder path on SFTP server to download from
            excluded_dirs: List of directory names to exclude from download
        """
        self.bucket = bucket
        self.s3_client = boto3.client('s3')
        self.excluded_dirs = excluded_dirs or EXCLUDED_DIRECTORIES

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
                    if secret.get('download_folder'):
                        remote_folder = secret.get('download_folder')
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

        self.remote_folder = remote_folder.rstrip('/') + '/'
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

    def _list_remote_files(self, remote_dir: str) -> List[Dict]:
        """
        Recursively list all files in remote directory.

        Args:
            remote_dir: Remote directory path to list

        Returns:
            List of dicts with file info (path, filename, size)
        """
        if not self._sftp:
            raise RuntimeError("Not connected to SFTP")

        files = []

        try:
            entries = self._sftp.listdir_attr(remote_dir)
        except FileNotFoundError:
            return files

        for entry in entries:
            remote_path = f"{remote_dir.rstrip('/')}/{entry.filename}"

            if stat.S_ISREG(entry.st_mode):
                # It's a file
                files.append({
                    'path': remote_path,
                    'filename': entry.filename,
                    'size': entry.st_size
                })
            elif stat.S_ISDIR(entry.st_mode):
                # It's a directory - recurse if not excluded
                if entry.filename not in self.excluded_dirs:
                    files.extend(self._list_remote_files(remote_path))

        return files

    def _download_file_to_s3(
        self,
        remote_path: str,
        filename: str,
        file_size: int,
        s3_prefix: str
    ) -> Dict:
        """
        Download a file from SFTP and upload directly to S3.

        Args:
            remote_path: Full remote path to file
            filename: Filename for S3
            file_size: Expected file size
            s3_prefix: S3 prefix (folder) to upload to

        Returns:
            Dict with download/upload result
        """
        if not self._sftp:
            raise RuntimeError("Not connected to SFTP")

        s3_key = f"{s3_prefix.rstrip('/')}/{filename}"
        result = {
            'filename': filename,
            's3_key': s3_key,
            'source_path': remote_path,
            'source_size': file_size,
            'success': False
        }

        try:
            # Stream from SFTP directly to S3
            with self._sftp.open(remote_path, 'rb') as remote_file:
                self.s3_client.upload_fileobj(
                    remote_file,
                    self.bucket,
                    s3_key,
                    ExtraArgs={
                        'Metadata': {
                            'source_path': remote_path,
                            'downloaded_at': datetime.now().isoformat()
                        }
                    }
                )

            # Verify the upload
            response = self.s3_client.head_object(Bucket=self.bucket, Key=s3_key)
            uploaded_size = response['ContentLength']

            result['uploaded_size'] = uploaded_size
            result['verified'] = uploaded_size == file_size
            result['success'] = True

        except Exception as e:
            result['error'] = str(e)

        return result

    def download_all(
        self,
        s3_prefix: str = 'downloads/',
        progress_callback=None
    ) -> Dict:
        """
        Download all files from remote SFTP folder to S3.

        Args:
            s3_prefix: S3 prefix to upload files to
            progress_callback: Optional callback function(files_done, total_files, current_file)

        Returns:
            Dict with overall results and per-file details
        """
        result = {
            'success': False,
            'files_downloaded': 0,
            'files_failed': 0,
            'total_bytes': 0,
            'file_results': [],
            'errors': [],
            'started_at': datetime.now().isoformat()
        }

        try:
            # Connect to SFTP
            self.connect()
            result['connected'] = True

            # List all files to download
            remote_files = self._list_remote_files(self.remote_folder)
            result['total_files'] = len(remote_files)

            if not remote_files:
                result['success'] = True
                result['message'] = 'No files found to download'
                result['completed_at'] = datetime.now().isoformat()
                return result

            # Download each file
            for i, file_info in enumerate(remote_files):
                if progress_callback:
                    progress_callback(i, len(remote_files), file_info['filename'])

                file_result = self._download_file_to_s3(
                    file_info['path'],
                    file_info['filename'],
                    file_info['size'],
                    s3_prefix
                )

                result['file_results'].append(file_result)

                if file_result['success']:
                    result['files_downloaded'] += 1
                    result['total_bytes'] += file_result.get('uploaded_size', 0)
                else:
                    result['files_failed'] += 1
                    if file_result.get('error'):
                        result['errors'].append(f"{file_info['filename']}: {file_result['error']}")

            # Check for verification errors
            verification_errors = [
                fr['filename'] for fr in result['file_results']
                if fr.get('success') and not fr.get('verified', True)
            ]

            if verification_errors:
                result['verification_warnings'] = verification_errors

            result['success'] = result['files_failed'] == 0
            result['completed_at'] = datetime.now().isoformat()

        except Exception as e:
            result['error'] = str(e)
            result['completed_at'] = datetime.now().isoformat()

        finally:
            self.disconnect()

        return result

    def test_connection(self) -> Dict:
        """
        Test SFTP connection and list remote folder contents.

        Returns:
            Dict with connection test results
        """
        result = {
            'success': False,
            'host': self.sftp_host,
            'port': self.sftp_port,
            'user': self.sftp_user,
            'remote_folder': self.remote_folder
        }

        try:
            self.connect()
            result['connected'] = True

            # Try to list the remote folder
            remote_files = self._list_remote_files(self.remote_folder)
            result['files_found'] = len(remote_files)
            result['sample_files'] = [f['filename'] for f in remote_files[:10]]
            result['success'] = True

        except Exception as e:
            result['error'] = str(e)

        finally:
            self.disconnect()

        return result


def download_from_sftp(
    bucket: str,
    s3_prefix: str = 'downloads/',
    sftp_secret_name: Optional[str] = None,
    sftp_host: str = DEFAULT_SFTP_HOST,
    sftp_port: int = DEFAULT_SFTP_PORT,
    sftp_user: str = DEFAULT_SFTP_USER,
    sftp_password: Optional[str] = None,
    remote_folder: str = DEFAULT_REMOTE_DOWNLOAD_FOLDER,
    progress_callback=None
) -> Dict:
    """
    Convenience function to download all files from SFTP to S3.

    Args:
        bucket: S3 bucket to upload to
        s3_prefix: S3 prefix for uploaded files
        sftp_secret_name: AWS secret name for SFTP credentials
        sftp_host: SFTP server hostname
        sftp_port: SFTP server port
        sftp_user: SFTP username
        sftp_password: SFTP password
        remote_folder: Remote folder to download from
        progress_callback: Optional progress callback

    Returns:
        Dict with download results
    """
    downloader = SftpDownloader(
        bucket=bucket,
        sftp_secret_name=sftp_secret_name,
        sftp_host=sftp_host,
        sftp_port=sftp_port,
        sftp_user=sftp_user,
        sftp_password=sftp_password,
        remote_folder=remote_folder
    )

    return downloader.download_all(s3_prefix, progress_callback)
