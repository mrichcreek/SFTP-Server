"""
Hacienda ERP Data Pipeline Application
Final Production Version - Single Pipeline Interface

This application runs the complete HCM data processing pipeline:
1. Downloads files from SFTP (via VPN)
2. Uploads to S3
3. Validates files (duplicates, names, schema, completeness)
4. Loads data to SQL Server
5. Runs HCM_MAIN_INTF stored procedure
6. Exports delta files
7. Uploads to Sterling SFTP

IMPORTANT: User must be connected to FortiClient VPN before running.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import paramiko
import boto3
import requests
import os
import stat
import json
import webbrowser
import time
from datetime import datetime, timezone, timedelta
from botocore.exceptions import ClientError, NoCredentialsError

# Puerto Rico timezone (UTC-4, no daylight saving time)
PR_TIMEZONE = timezone(timedelta(hours=-4))

# ============================================
# CONFIGURATION
# ============================================

# SQL Server Connection (for local execution)
SQL_SECRET_NAME = 'Hacienda_ERP_Test_MSSQL_text'
SQL_SECRET_REGION = 'us-east-1'

# Production Database (hardcoded)
PRODUCTION_DATABASE = 'Hacienda_ERP'

# Cognito Settings
COGNITO_REGION = "us-east-1"
COGNITO_USER_POOL_ID = "us-east-1_B9L2aprTj"
COGNITO_CLIENT_ID = "39dbtnt6f5s0li79erji1lqbps"
COGNITO_IDENTITY_POOL_ID = "us-east-1:25ce0ade-ee1b-43fb-90d4-ae08606ee95d"

# API Gateway Settings
API_ENDPOINT = "https://oibtjhhyma.execute-api.us-east-1.amazonaws.com/prod"

# SFTP Settings (Download - from Sterling)
SFTP_HOST = "10.3.3.146"
SFTP_PORT = 22
SFTP_USER = "gprerpusr"
SFTP_PASS = "YExumikufR7g"
# Download HCM data files from /GPR/HCM (based on SFTP FilesPublish Download.py)
# Note: /OCI/HCM/OUTPUT/ is for error reports, NOT HCM data files
REMOTE_DOWNLOAD_FOLDER = "/GPR/HCM"
REMOTE_UPLOAD_FOLDER = "/GPR/HCM/INPUT"      # Upload delta files to this folder
# Note: Only exclude PROCESADOS (already-processed files)
# RHUM files are special: download ALL of them, then process oldest to newest
EXCLUDE_DIRS = ["PROCESADOS"]

# AWS Settings
AWS_REGION = "us-east-1"
S3_BUCKET = "hacienda-sftp-downloads"

# Application Settings
APP_VERSION = "3.3.2"
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "download_log.txt")

# Direct Lambda function URL for full-pipeline
FULL_PIPELINE_URL = "https://5253fdqsppqvdveoyaeq6dl7ty0pmjep.lambda-url.us-east-1.on.aws"

# Color scheme
COLORS = {
    'primary': '#1a73e8',
    'primary_dark': '#1557b0',
    'primary_light': '#4285f4',
    'primary_hover': '#1557b0',
    'success': '#34a853',
    'warning': '#fbbc04',
    'error': '#ea4335',
    'bg_dark': '#202124',
    'bg_medium': '#303134',
    'bg_light': '#3c4043',
    'text_primary': '#e8eaed',
    'text_secondary': '#9aa0a6',
    'border': '#5f6368',
    'vpn_warning': '#ff9800',
    'disabled': '#5f6368'
}


# ============================================
# COGNITO AUTHENTICATION
# ============================================

class CognitoAuth:
    """Handle AWS Cognito authentication and get AWS credentials via Identity Pool."""

    def __init__(self):
        self.client = boto3.client('cognito-idp', region_name=COGNITO_REGION)
        self.identity_client = boto3.client('cognito-identity', region_name=COGNITO_REGION)
        self.id_token = None
        self.access_token = None
        self.refresh_token = None
        self.username = None
        self.aws_credentials = None
        self.identity_id = None

    def authenticate(self, username, password):
        """Authenticate user with Cognito using USER_PASSWORD_AUTH flow."""
        try:
            response = self.client.initiate_auth(
                ClientId=COGNITO_CLIENT_ID,
                AuthFlow='USER_PASSWORD_AUTH',
                AuthParameters={
                    'USERNAME': username,
                    'PASSWORD': password
                }
            )

            auth_result = response.get('AuthenticationResult', {})
            self.id_token = auth_result.get('IdToken')
            self.access_token = auth_result.get('AccessToken')
            self.refresh_token = auth_result.get('RefreshToken')
            self.username = username

            # Get AWS credentials from Identity Pool
            try:
                self._get_aws_credentials()
            except Exception as cred_error:
                # Log but don't fail - user is authenticated
                print(f"Warning: Could not get AWS credentials: {cred_error}")

            return True, None

        except self.client.exceptions.NotAuthorizedException:
            return False, "Invalid email or password"
        except self.client.exceptions.UserNotFoundException:
            return False, "User not found"
        except self.client.exceptions.UserNotConfirmedException:
            return False, "User email not confirmed"
        except Exception as e:
            return False, str(e)

    def _get_aws_credentials(self):
        """Get temporary AWS credentials from Cognito Identity Pool."""
        if not self.id_token:
            return

        # Get Identity ID
        provider_name = f"cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}"

        id_response = self.identity_client.get_id(
            IdentityPoolId=COGNITO_IDENTITY_POOL_ID,
            Logins={
                provider_name: self.id_token
            }
        )
        self.identity_id = id_response['IdentityId']

        # Get credentials for this identity
        creds_response = self.identity_client.get_credentials_for_identity(
            IdentityId=self.identity_id,
            Logins={
                provider_name: self.id_token
            }
        )

        self.aws_credentials = creds_response['Credentials']

    def get_boto3_session(self):
        """Get a boto3 session using the Cognito Identity credentials."""
        if not self.aws_credentials:
            # Return default session if no credentials
            return boto3.Session(region_name=AWS_REGION)

        return boto3.Session(
            aws_access_key_id=self.aws_credentials['AccessKeyId'],
            aws_secret_access_key=self.aws_credentials['SecretKey'],
            aws_session_token=self.aws_credentials['SessionToken'],
            region_name=AWS_REGION
        )

    def logout(self):
        """Clear authentication tokens."""
        self.id_token = None
        self.access_token = None
        self.refresh_token = None
        self.username = None
        self.aws_credentials = None
        self.identity_id = None

    def is_authenticated(self):
        """Check if user is authenticated."""
        return self.id_token is not None


# ============================================
# LOCAL SQL EXECUTOR
# ============================================

class LocalSqlExecutor:
    """Execute stored procedures directly using local database connection via pyodbc."""

    def __init__(self):
        self.connection_string = None
        self.secrets_client = None  # Will be set by HaciendaApp with authenticated credentials

    def get_connection_string(self):
        """Get connection string from AWS Secrets Manager."""
        if self.connection_string:
            return self.connection_string

        # Use the authenticated secrets client if available, otherwise create default
        client = self.secrets_client or boto3.client('secretsmanager', region_name=SQL_SECRET_REGION)
        response = client.get_secret_value(SecretId=SQL_SECRET_NAME)
        self.connection_string = response.get('SecretString', '')
        return self.connection_string

    def get_available_odbc_driver(self):
        """Detect available SQL Server ODBC drivers on the system."""
        try:
            import pyodbc
            drivers = pyodbc.drivers()

            preferred_drivers = [
                'ODBC Driver 18 for SQL Server',
                'ODBC Driver 17 for SQL Server',
                'ODBC Driver 13.1 for SQL Server',
                'ODBC Driver 13 for SQL Server',
                'ODBC Driver 11 for SQL Server',
                'SQL Server Native Client 11.0',
                'SQL Server Native Client 10.0',
                'SQL Server',
            ]

            for driver in preferred_drivers:
                if driver in drivers:
                    return driver

            sql_drivers = [d for d in drivers if 'sql' in d.lower()]
            if sql_drivers:
                return sql_drivers[0]

            return None

        except ImportError:
            return None
        except Exception:
            return None

    def parse_connection_string(self, conn_str):
        """Parse ODBC connection string into components."""
        params = {}
        for part in conn_str.split(';'):
            if '=' in part:
                key, value = part.split('=', 1)
                params[key.strip().upper()] = value.strip()
        return params

    def execute_stored_procedure(self, database_override=None, progress_callback=None, test_mode=False):
        """Execute the HCM_MAIN_INTF stored procedure.

        This uses a two-connection approach:
        1. One connection runs the stored procedure (blocking call)
        2. A separate thread with another connection monitors progress via Integration_Log

        The procedure can run for 60+ minutes, so we don't impose artificial timeouts.

        Args:
            database_override: Override the database name
            progress_callback: Callback function for progress updates
            test_mode: If True, runs with @test_execution='Y', else 'N' for production
        """
        import pyodbc
        import time
        import threading

        result = {
            'status': 'running',
            'started_at': datetime.now().isoformat(),
            'delta_counts': {},
            'steps_completed': [],
            'procedure_logs': [],
            'error': None,
            'error_details': None
        }

        # Shared state between threads
        proc_state = {
            'completed': False,
            'error': None,
            'start_time': None
        }

        try:
            if progress_callback:
                progress_callback("Getting database connection...")

            conn_str = self.get_connection_string()
            params = self.parse_connection_string(conn_str)

            driver = self.get_available_odbc_driver()
            if not driver:
                raise Exception("No SQL Server ODBC driver found. Please install ODBC Driver 17 or 18 for SQL Server.")

            server = params.get('SERVER', '')
            database = database_override or params.get('DATABASE', 'Hacienda_ERP_Test')
            uid = params.get('UID', '')
            pwd = params.get('PWD', '')

            result['database'] = database

            pyodbc_conn_str = (
                f"DRIVER={{{driver}}};"
                f"SERVER={server};"
                f"DATABASE={database};"
                f"UID={uid};"
                f"PWD={pwd};"
                "TrustServerCertificate=yes;"
                "Connection Timeout=30;"
            )

            if progress_callback:
                progress_callback(f"Connecting to {database}...")

            # Check and clear any stuck RUN_INTF_STATUS before running procedure
            # The stored procedure won't run if status is '01-InProgress' or '02-Completed'
            try:
                setup_conn = pyodbc.connect(pyodbc_conn_str, timeout=30)
                setup_conn.autocommit = True
                setup_cursor = setup_conn.cursor()

                # Check current status
                setup_cursor.execute("""
                    SELECT TOP 1 Instance, Status
                    FROM RUN_INTF_STATUS
                    ORDER BY Instance DESC
                """)
                status_row = setup_cursor.fetchone()

                if status_row:
                    instance, status = status_row
                    result['previous_instance'] = instance
                    result['previous_status'] = status

                    if status and status.strip() in ('01-InProgress', '02-Completed'):
                        if progress_callback:
                            progress_callback(f"Clearing stuck status '{status}' for Instance {instance}...")

                        # Update to '03-File Sent' to allow new run
                        setup_cursor.execute("""
                            UPDATE RUN_INTF_STATUS
                            SET Status = '03-File Sent', DateCompleted = SYSDATETIME()
                            WHERE Instance = ?
                        """, instance)

                        result['steps_completed'].append({
                            'step': 'cleared_stuck_status',
                            'timestamp': datetime.now().isoformat(),
                            'instance': instance,
                            'old_status': status,
                            'new_status': '03-File Sent'
                        })

                        if progress_callback:
                            progress_callback(f"Status cleared. Ready to run procedure.")

                setup_cursor.close()
                setup_conn.close()

            except Exception as status_check_error:
                # Not fatal - procedure might still work
                result['status_check_error'] = str(status_check_error)
                if progress_callback:
                    progress_callback(f"Warning: Could not check RUN_INTF_STATUS: {status_check_error}")

            # Execute stored procedure with required @test_execution parameter
            # 'N' = production mode (process all records), 'Y' = test mode
            test_execution_value = 'Y' if test_mode else 'N'

            result['steps_completed'].append({
                'step': 'procedure_started',
                'timestamp': datetime.now().isoformat(),
                'test_execution': test_execution_value
            })

            proc_state['start_time'] = datetime.now()

            # Function to run stored procedure in separate thread
            def run_procedure():
                try:
                    # Create dedicated connection for stored procedure execution
                    # No timeout - let it run as long as needed (like sqlcmd -t 0)
                    proc_conn = pyodbc.connect(pyodbc_conn_str, timeout=0)
                    proc_conn.autocommit = True
                    proc_cursor = proc_conn.cursor()

                    # Execute the stored procedure - this blocks until complete
                    proc_cursor.execute(f"EXEC dbo.HCM_MAIN_INTF @test_execution = '{test_execution_value}'")

                    # Consume any result sets to ensure procedure fully completes
                    while proc_cursor.nextset():
                        pass

                    proc_cursor.close()
                    proc_conn.close()
                    proc_state['completed'] = True

                except Exception as e:
                    proc_state['error'] = str(e)
                    proc_state['completed'] = True

            # Start stored procedure in background thread
            if progress_callback:
                progress_callback(f"Executing: EXEC dbo.HCM_MAIN_INTF @test_execution = '{test_execution_value}'...")

            proc_thread = threading.Thread(target=run_procedure, daemon=True)
            proc_thread.start()

            if progress_callback:
                progress_callback("Procedure started. Monitoring progress...")

            # Monitor progress using a separate connection
            monitor_conn = pyodbc.connect(pyodbc_conn_str, timeout=30)
            monitor_cursor = monitor_conn.cursor()

            last_log_check = None
            poll_interval = 10  # Check every 10 seconds

            while not proc_state['completed']:
                time.sleep(poll_interval)

                elapsed = datetime.now() - proc_state['start_time']
                elapsed_mins = elapsed.total_seconds() / 60

                # Try to get progress from Integration_Log table
                try:
                    monitor_cursor.execute("""
                        SELECT TOP 1 job_name, started_at, finished_at, status, error_message,
                               DATEDIFF(SECOND, started_at, ISNULL(finished_at, GETDATE())) as elapsed_seconds
                        FROM dbo.Integration_Log
                        WHERE job_name = 'HCM_MAIN_INTF'
                          AND started_at >= ?
                        ORDER BY started_at DESC
                    """, proc_state['start_time'])

                    log_row = monitor_cursor.fetchone()
                    if log_row:
                        job_name, started_at, finished_at, status, error_message, elapsed_sec = log_row

                        # Only add to logs if this is new information
                        log_entry = {
                            'job_name': job_name,
                            'started_at': str(started_at) if started_at else None,
                            'finished_at': str(finished_at) if finished_at else None,
                            'status': status,
                            'error_message': error_message
                        }

                        if log_entry != last_log_check:
                            result['procedure_logs'].append(log_entry)
                            last_log_check = log_entry

                        if status:
                            if progress_callback:
                                progress_callback(f"Procedure running: {status} ({elapsed_mins:.1f} min)")
                        else:
                            if progress_callback:
                                progress_callback(f"Procedure executing... ({elapsed_mins:.1f} min elapsed)")
                    else:
                        if progress_callback:
                            progress_callback(f"Procedure executing... ({elapsed_mins:.1f} min elapsed)")

                except Exception as monitor_error:
                    # Integration_Log might not exist or be accessible, just show elapsed time
                    if progress_callback:
                        progress_callback(f"Procedure running... ({elapsed_mins:.1f} min elapsed)")

            monitor_cursor.close()
            monitor_conn.close()

            # Wait for thread to fully complete
            proc_thread.join(timeout=5)

            # Check for errors from the procedure thread
            if proc_state['error']:
                result['error'] = proc_state['error']
                result['error_details'] = f"SQL Error during procedure execution: {proc_state['error']}"
                result['status'] = 'error'
                result['completed_at'] = datetime.now().isoformat()

                if progress_callback:
                    progress_callback(f"Procedure error: {proc_state['error']}")

                return result

            result['steps_completed'].append({
                'step': 'procedure_completed',
                'timestamp': datetime.now().isoformat()
            })

            elapsed = datetime.now() - proc_state['start_time']
            elapsed_mins = elapsed.total_seconds() / 60

            if progress_callback:
                progress_callback(f"Procedure complete ({elapsed_mins:.1f} min). Counting delta records...")

            # Get delta table counts using a fresh connection
            count_conn = pyodbc.connect(pyodbc_conn_str, timeout=30)
            count_cursor = count_conn.cursor()

            delta_tables = [
                'HCM_DEPARTMENT_INTF_DELTA',
                'HCM_JOBS_INTF_DELTA',
                'HCM_LOCATION_INTF_DELTA',
                'HCM_PERSON_ADDRESS_INTF_DELTA',
                'HCM_PERSON_ASSIGNMENT_INTF_DELTA',
                'HCM_PERSON_EMAIL_INTF_DELTA',
                'HCM_PERSON_NAME_INTF_DELTA',
                'HCM_PERSON_NID_INTF_DELTA',
                'HCM_PERSON_SUPERVISOR_INTF_DELTA',
                'HCM_EXTERNAL_IDENTIFIER_INTF_DELTA',
                'HCM_SENIORITY_INTF_DELTA',
            ]

            for i, table in enumerate(delta_tables):
                try:
                    if progress_callback and i % 3 == 0:
                        progress_callback(f"Counting delta records... ({i+1}/{len(delta_tables)} tables)")
                    count_cursor.execute(f"SELECT COUNT(*) FROM dbo.{table}")
                    count = count_cursor.fetchone()[0]
                    result['delta_counts'][table] = count
                except Exception as count_error:
                    result['delta_counts'][table] = -1

            count_cursor.close()
            count_conn.close()

            result['status'] = 'success'
            result['completed_at'] = datetime.now().isoformat()
            result['elapsed_minutes'] = elapsed_mins

            total_deltas = sum(c for c in result['delta_counts'].values() if c > 0)
            if progress_callback:
                progress_callback(f"Stored procedure complete! {total_deltas:,} delta records in {elapsed_mins:.1f} min.")

        except Exception as e:
            result['status'] = 'error'
            result['error'] = str(e)
            result['error_details'] = f"Exception type: {type(e).__name__}, Message: {str(e)}"
            result['completed_at'] = datetime.now().isoformat()

            if progress_callback:
                progress_callback(f"Error: {str(e)}")

        return result


# ============================================
# LOCAL SFTP DOWNLOADER
# ============================================

class LocalSftpDownloader:
    """Download files from Sterling SFTP via local VPN connection and upload to S3.

    The SFTP server (10.3.3.146) is only accessible via FortiClient VPN,
    so the download must happen locally (not from Lambda).

    Based on the original 'SFTP Pre_Validation Reports Download.py' program.
    Downloads all HCM files from SFTP and uploads them to S3.
    """

    def __init__(self):
        self.s3_client = boto3.client('s3', region_name=AWS_REGION)

    def test_vpn_connection(self):
        """Test if we can reach the SFTP server (VPN must be connected).

        Returns:
            Dict with connection test result
        """
        import socket

        result = {
            'connected': False,
            'host': SFTP_HOST,
            'port': SFTP_PORT,
            'error': None
        }

        try:
            # Try to open a socket connection to the SFTP port
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)  # 5 second timeout
            sock.connect((SFTP_HOST, SFTP_PORT))
            sock.close()
            result['connected'] = True
        except socket.timeout:
            result['error'] = "Connection timed out - FortiClient VPN may not be connected"
        except socket.error as e:
            result['error'] = f"Cannot reach SFTP server - FortiClient VPN may not be connected: {str(e)}"
        except Exception as e:
            result['error'] = f"Connection test failed: {str(e)}"

        return result

    def _list_remote_files(self, sftp, remote_dir, exclude_dirs=None):
        """Recursively list all files in remote directory.

        Args:
            sftp: SFTP client connection
            remote_dir: Remote directory path to list
            exclude_dirs: List of directory names to exclude

        Returns:
            List of dicts with file info (path, filename, size)
        """
        if exclude_dirs is None:
            exclude_dirs = EXCLUDE_DIRS

        files = []

        try:
            entries = sftp.listdir_attr(remote_dir)
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
                if entry.filename not in exclude_dirs:
                    files.extend(self._list_remote_files(sftp, remote_path, exclude_dirs))

        return files

    def download_and_upload_to_s3(self, s3_prefix='downloads/', progress_callback=None):
        """Download files from SFTP and upload to S3.

        Args:
            s3_prefix: S3 prefix to upload files to
            progress_callback: Callback function for progress updates

        Returns:
            Dict with download/upload results
        """
        result = {
            'status': 'running',
            'started_at': datetime.now().isoformat(),
            'sftp_host': SFTP_HOST,
            'sftp_port': SFTP_PORT,
            'remote_folder': REMOTE_DOWNLOAD_FOLDER,
            'excluded_dirs': EXCLUDE_DIRS,
            's3_bucket': S3_BUCKET,
            's3_prefix': s3_prefix,
            'files_found_on_server': 0,  # Total files found on Sterling server
            'files_downloaded': 0,
            'files_failed': 0,
            'total_bytes': 0,
            'file_results': [],
            'error': None
        }

        try:
            # Test VPN connection first
            if progress_callback:
                progress_callback("Testing VPN connection to Sterling SFTP...")

            vpn_test = self.test_vpn_connection()
            if not vpn_test['connected']:
                result['status'] = 'error'
                result['error'] = vpn_test['error']
                result['completed_at'] = datetime.now().isoformat()
                if progress_callback:
                    progress_callback(f"VPN Error: {vpn_test['error']}")
                return result

            if progress_callback:
                progress_callback(f"VPN connected! Connecting to SFTP at {SFTP_HOST}...")

            # Connect to SFTP
            transport = paramiko.Transport((SFTP_HOST, SFTP_PORT))
            transport.connect(username=SFTP_USER, password=SFTP_PASS)
            sftp = paramiko.SFTPClient.from_transport(transport)

            if progress_callback:
                progress_callback(f"Connected! Listing files in {REMOTE_DOWNLOAD_FOLDER}...")

            # List all files to download
            remote_files = self._list_remote_files(sftp, REMOTE_DOWNLOAD_FOLDER)
            result['files_found_on_server'] = len(remote_files)
            result['total_files'] = len(remote_files)

            if not remote_files:
                sftp.close()
                transport.close()
                result['status'] = 'success'
                result['message'] = 'No files found to download'
                result['completed_at'] = datetime.now().isoformat()
                if progress_callback:
                    progress_callback("No files found in remote folder.")
                return result

            if progress_callback:
                progress_callback(f"Found {len(remote_files)} files. Starting download...")

            # Download each file and upload to S3 immediately (real-time, one at a time)
            # This ensures files appear in S3 as they're downloaded, not batched at the end
            for i, file_info in enumerate(remote_files, 1):
                file_result = {
                    'filename': file_info['filename'],
                    'remote_path': file_info['path'],
                    'size': file_info['size'],
                    'success': False,
                    'error': None
                }

                try:
                    if progress_callback:
                        progress_callback(f"Downloading {i}/{len(remote_files)}: {file_info['filename']}")

                    # Download file content from SFTP
                    with sftp.open(file_info['path'], 'rb') as remote_file:
                        content = remote_file.read()

                    # Upload to S3 IMMEDIATELY after download (real-time upload)
                    # Use Puerto Rico time for metadata
                    pr_time = datetime.now(PR_TIMEZONE)
                    s3_key = f"{s3_prefix.rstrip('/')}/{file_info['filename']}"
                    self.s3_client.put_object(
                        Bucket=S3_BUCKET,
                        Key=s3_key,
                        Body=content,
                        Metadata={
                            'source_path': file_info['path'],
                            'downloaded_at': pr_time.isoformat()
                        }
                    )

                    file_result['success'] = True
                    file_result['s3_key'] = s3_key
                    file_result['uploaded_size'] = len(content)
                    result['files_downloaded'] += 1
                    result['total_bytes'] += len(content)

                    # File is now visible in S3 immediately
                    if progress_callback:
                        progress_callback(f"Uploaded {i}/{len(remote_files)}: {file_info['filename']} to S3")

                except Exception as e:
                    file_result['error'] = str(e)
                    result['files_failed'] += 1

                result['file_results'].append(file_result)

            sftp.close()
            transport.close()

            result['status'] = 'success' if result['files_failed'] == 0 else 'partial'
            result['completed_at'] = datetime.now().isoformat()

            if progress_callback:
                progress_callback(f"Download complete! {result['files_downloaded']} files uploaded to S3.")

        except Exception as e:
            result['status'] = 'error'
            result['error'] = str(e)
            result['completed_at'] = datetime.now().isoformat()

            if progress_callback:
                progress_callback(f"SFTP Error: {str(e)}")

        return result


# ============================================
# LOCAL SFTP UPLOADER
# ============================================

class LocalSftpUploader:
    """Upload files to Sterling SFTP via local VPN connection.

    Based on the original 'SFTP FilesPublish Upload.py' program.
    Reads files from local export folder and uploads to SFTP.
    Copies uploaded files to OutputSFTPFilesPublish folder for verification.
    """

    # Local folders
    LOCAL_EXPORT_FOLDER = r"D:\Hacienda ERP Temporary Integrations\Import Programs\OutputExportINTFDelta"
    LOCAL_SFTP_OUTPUT_FOLDER = r"D:\Hacienda ERP Temporary Integrations\Import Programs\OutputSFTPFilesPublish"

    def __init__(self):
        self.s3_client = boto3.client('s3', region_name=AWS_REGION)
        # Ensure output folders exist
        os.makedirs(self.LOCAL_SFTP_OUTPUT_FOLDER, exist_ok=True)

    def upload_export_files(self, export_folder=None, progress_callback=None):
        """Upload export files from local folder to Sterling SFTP.

        Args:
            export_folder: Local folder containing files to upload (defaults to LOCAL_EXPORT_FOLDER)
            progress_callback: Callback function for progress updates
        """
        import shutil

        # Use provided folder or default
        source_folder = export_folder if export_folder and os.path.isdir(export_folder) else self.LOCAL_EXPORT_FOLDER

        result = {
            'status': 'running',
            'started_at': datetime.now().isoformat(),
            'source_folder': source_folder,
            'output_folder': self.LOCAL_SFTP_OUTPUT_FOLDER,
            'files_uploaded': 0,
            'files_failed': 0,
            'total_bytes': 0,
            'file_results': [],
            'error': None
        }

        try:
            if progress_callback:
                progress_callback(f"Scanning {source_folder} for files...")

            # List CSV files in export folder
            files_to_upload = []
            if os.path.isdir(source_folder):
                for filename in os.listdir(source_folder):
                    if filename.lower().endswith('.csv'):
                        local_path = os.path.join(source_folder, filename)
                        file_size = os.path.getsize(local_path)
                        files_to_upload.append({
                            'local_path': local_path,
                            'filename': filename,
                            'size': file_size
                        })

            if not files_to_upload:
                result['status'] = 'success'
                result['message'] = 'No files to upload'
                result['completed_at'] = datetime.now().isoformat()
                if progress_callback:
                    progress_callback("No export files found to upload.")
                return result

            if progress_callback:
                progress_callback(f"Found {len(files_to_upload)} files. Connecting to SFTP...")

            # Connect to SFTP
            transport = paramiko.Transport((SFTP_HOST, SFTP_PORT))
            transport.connect(username=SFTP_USER, password=SFTP_PASS)
            sftp = paramiko.SFTPClient.from_transport(transport)

            if progress_callback:
                progress_callback(f"Connected to SFTP. Uploading {len(files_to_upload)} files...")

            # Upload each file
            for i, file_info in enumerate(files_to_upload, 1):
                file_result = {
                    'filename': file_info['filename'],
                    'local_path': file_info['local_path'],
                    'size': file_info['size'],
                    'success': False,
                    'error': None
                }

                try:
                    if progress_callback:
                        progress_callback(f"Uploading {i}/{len(files_to_upload)}: {file_info['filename']}")

                    # Read local file
                    with open(file_info['local_path'], 'rb') as f:
                        content = f.read()

                    # Upload to SFTP
                    remote_path = f"{REMOTE_UPLOAD_FOLDER}/{file_info['filename']}"
                    with sftp.file(remote_path, 'wb') as remote_file:
                        remote_file.write(content)

                    file_result['success'] = True
                    file_result['remote_path'] = remote_path
                    result['files_uploaded'] += 1
                    result['total_bytes'] += file_info['size']

                    # Copy to SFTP output folder for verification
                    output_path = os.path.join(self.LOCAL_SFTP_OUTPUT_FOLDER, file_info['filename'])
                    shutil.copy2(file_info['local_path'], output_path)
                    file_result['output_path'] = output_path

                except Exception as e:
                    file_result['error'] = str(e)
                    result['files_failed'] += 1

                result['file_results'].append(file_result)

            sftp.close()
            transport.close()

            result['status'] = 'success' if result['files_failed'] == 0 else 'partial'
            result['completed_at'] = datetime.now().isoformat()

            if progress_callback:
                progress_callback(f"Upload complete! {result['files_uploaded']} files to SFTP, copied to {self.LOCAL_SFTP_OUTPUT_FOLDER}")

        except Exception as e:
            result['status'] = 'error'
            result['error'] = str(e)
            result['completed_at'] = datetime.now().isoformat()

            if progress_callback:
                progress_callback(f"SFTP Error: {str(e)}")

        return result


# ============================================
# LOCAL DELTA EXPORTER
# ============================================

class LocalDeltaExporter:
    """Export delta tables to CSV files locally.

    Based on the original 'Export INTF Delta V4.py' program.
    Uses the actual delta views from SETUP_INTF_TABLE configuration.
    Writes files to local folder instead of S3.

    ALWAYS generates all 12 files (even empty ones with headers only):
    - 7 single files: ASSIGNMENT, SUPERVISOR (+ SENIORITY if configured)
    - 5 split views that produce 2 files each (normal + INT012):
      PERSON_NAME, PERSON_ADDRESS, PERSON_NID, PERSON_EMAIL, PERSON_EXT_IDENTIFIER

    Header format: 3 rows from *_TITLE tables:
    - Row 1: Human-readable column names
    - Row 2: Database column names
    - Row 3: Data types
    """

    # Local output folder for delta exports
    LOCAL_EXPORT_FOLDER = r"D:\Hacienda ERP Temporary Integrations\Import Programs\OutputExportINTFDelta"

    def __init__(self):
        self.s3_client = boto3.client('s3', region_name=AWS_REGION)
        self.secrets_client = None  # Will be set by HaciendaApp with authenticated credentials
        # Ensure local export folder exists
        os.makedirs(self.LOCAL_EXPORT_FOLDER, exist_ok=True)

        # Export filename mapping (from original program)
        # These are the 7 views that should ALWAYS be exported
        self.export_name_map = {
            "HCM_PERSON_NAME_INTF_DELTA_VW": "PERSON_NAME",
            "HCM_PERSON_ADDRESS_INTF_DELTA_VW": "PERSON_ADDRESS",
            "HCM_PERSON_NID_INTF_DELTA_VW": "PERSON_NID",
            "HCM_PERSON_EMAIL_INTF_DELTA_VW": "PERSON_EMAIL",
            "HCM_EXTERNAL_IDENTIFIER_INTF_DELTA_VW": "PERSON_EXT_IDENTIFIER",
            "HCM_PERSON_ASSIGNMENT_INTF_DELTA_VW": "ASSIGNMENT",
            "HCM_PERSON_SUPERVISOR_INTF_DELTA_VW": "SUPERVISOR",
        }

        # Views that need to be split for INT012 (Hire/Rehire)
        # These 5 views produce 2 files each: normal + INT012 = 10 files
        # Plus ASSIGNMENT and SUPERVISOR = 12 files total
        self.int012_views = {
            "HCM_PERSON_NAME_INTF_DELTA_VW",
            "HCM_PERSON_ADDRESS_INTF_DELTA_VW",
            "HCM_PERSON_NID_INTF_DELTA_VW",
            "HCM_PERSON_EMAIL_INTF_DELTA_VW",
            "HCM_EXTERNAL_IDENTIFIER_INTF_DELTA_VW"
        }

    def export_delta_files(self, database_override=None, output_prefix=None, progress_callback=None):
        """Export delta views to local CSV files.

        This replicates the logic from 'Export INTF Delta V4.py':
        1. ALWAYS exports all 7 views (12 files total due to INT012 splitting)
        2. Gets 3-row headers from *_TITLE tables
        3. Splits INT012 views based on Hire/Rehire action codes
        4. Uses pipe delimiter
        5. Creates files even if empty (header-only)
        """
        import pyodbc

        result = {
            'status': 'running',
            'started_at': datetime.now().isoformat(),
            'files_exported': [],
            'files': [],  # For backward compatibility
            'total_files': 0,
            'total_rows': 0,
            'errors': [],
            'error': None
        }

        try:
            if progress_callback:
                progress_callback("Getting database connection...")

            executor = LocalSqlExecutor()
            # Use authenticated secrets client if available
            if self.secrets_client:
                executor.secrets_client = self.secrets_client
            conn_str = executor.get_connection_string()
            params = executor.parse_connection_string(conn_str)

            driver = executor.get_available_odbc_driver()
            if not driver:
                raise Exception("No SQL Server ODBC driver found.")

            server = params.get('SERVER', '')
            database = database_override or PRODUCTION_DATABASE
            uid = params.get('UID', '')
            pwd = params.get('PWD', '')

            result['database'] = database

            pyodbc_conn_str = (
                f"DRIVER={{{driver}}};"
                f"SERVER={server};"
                f"DATABASE={database};"
                f"UID={uid};"
                f"PWD={pwd};"
                "TrustServerCertificate=yes;"
            )

            if progress_callback:
                progress_callback(f"Connecting to {database}...")

            conn = pyodbc.connect(pyodbc_conn_str, timeout=30)
            cursor = conn.cursor()

            # Check RUN_INTF_STATUS for logging purposes
            latest_instance = None
            try:
                cursor.execute("""
                    SELECT TOP 1 Instance, Status
                    FROM RUN_INTF_STATUS
                    ORDER BY Instance DESC
                """)
                status_row = cursor.fetchone()
                if status_row:
                    latest_instance, latest_status = status_row
                    result['instance'] = latest_instance
                    result['run_status'] = latest_status

                    if progress_callback:
                        progress_callback(f"Instance {latest_instance}, Status: {latest_status}")
            except Exception as status_error:
                result['errors'].append(f"Could not check RUN_INTF_STATUS: {str(status_error)}")

            # Get Hire/Rehire person numbers for INT012 splitting
            hire_rehire_persons = set()
            try:
                cursor.execute("""
                    SELECT DISTINCT PERSON_NUMBER
                    FROM HCM_PERSON_ASSIGNMENT_INTF_DELTA_VW
                    WHERE ACTION_CODE IN ('Hire', 'Rehire')
                """)
                hire_rehire_persons = {str(row[0]).strip() for row in cursor.fetchall()}
                if progress_callback:
                    progress_callback(f"Found {len(hire_rehire_persons)} Hire/Rehire persons for INT012 splitting")
            except Exception:
                pass  # View might not exist or be empty

            timestamp = datetime.now().strftime('%Y.%m.%d-%H.%M.%S')
            output_prefix = output_prefix or f"exports/{timestamp}/"
            result['output_prefix'] = output_prefix
            result['timestamp'] = timestamp

            # Track files for RUN_INTF_FILES_SENT logging
            files_for_logging = []

            # ALWAYS process all 7 views from export_name_map (produces 12 files)
            for view_name, export_prefix in self.export_name_map.items():
                is_split_view = view_name in self.int012_views

                try:
                    if progress_callback:
                        progress_callback(f"Exporting {view_name}...")

                    # Get 3-row header from TITLE table (like original program)
                    # Format: Row1=Human names, Row2=DB names, Row3=Data types
                    title_table = view_name.replace('_VW', '') + '_TITLE'
                    header_lines = []
                    try:
                        cursor.execute(f"SELECT * FROM dbo.{title_table}")
                        title_rows = cursor.fetchall()
                        for title_row in title_rows:
                            header_lines.append('|'.join(str(col) if col is not None else '' for col in title_row))
                    except Exception as title_error:
                        result['errors'].append(f"Could not read {title_table}: {str(title_error)}")

                    # Get data from view
                    rows = []
                    columns = []
                    try:
                        cursor.execute(f"SELECT * FROM dbo.{view_name}")
                        columns = [desc[0] for desc in cursor.description]
                        rows = cursor.fetchall()
                    except Exception as view_error:
                        result['errors'].append(f"Could not read {view_name}: {str(view_error)}")

                    # If no title table, use column names as fallback header
                    if not header_lines and columns:
                        header_lines = ['|'.join(columns)]

                    # Find PERSON_NUMBER column index for splitting
                    person_idx = None
                    for i, col in enumerate(columns):
                        if col.upper() == 'PERSON_NUMBER':
                            person_idx = i
                            break

                    # Build header string (multiple lines joined with newline)
                    header_str = '\n'.join(header_lines)

                    if is_split_view:
                        # Split views produce TWO files: INT012 and normal
                        int012_data_lines = []
                        normal_data_lines = []

                        for row in rows:
                            line = '|'.join(str(v) if v is not None else '' for v in row)
                            if person_idx is not None and str(row[person_idx]).strip() in hire_rehire_persons:
                                int012_data_lines.append(line)
                            else:
                                normal_data_lines.append(line)

                        # ALWAYS write INT012 file (even if empty - header only)
                        filename_int012 = f"{export_prefix}_INT012_{timestamp}.csv"
                        local_path_int012 = os.path.join(self.LOCAL_EXPORT_FOLDER, filename_int012)
                        csv_content = header_str + '\n' + '\n'.join(int012_data_lines) if int012_data_lines else header_str + '\n'

                        with open(local_path_int012, 'w', encoding='utf-8') as f:
                            f.write(csv_content)

                        file_info = {
                            'view': view_name,
                            'filename': filename_int012,
                            'local_path': local_path_int012,
                            'row_count': len(int012_data_lines),
                            'type': 'INT012'
                        }
                        result['files_exported'].append(file_info)
                        result['files'].append(file_info)
                        result['total_rows'] += len(int012_data_lines)
                        result['total_files'] += 1
                        files_for_logging.append((filename_int012, len(int012_data_lines)))

                        # ALWAYS write normal file (even if empty - header only)
                        filename_normal = f"{export_prefix}_{timestamp}.csv"
                        local_path_normal = os.path.join(self.LOCAL_EXPORT_FOLDER, filename_normal)
                        csv_content = header_str + '\n' + '\n'.join(normal_data_lines) if normal_data_lines else header_str + '\n'

                        with open(local_path_normal, 'w', encoding='utf-8') as f:
                            f.write(csv_content)

                        file_info = {
                            'view': view_name,
                            'filename': filename_normal,
                            'local_path': local_path_normal,
                            'row_count': len(normal_data_lines),
                            'type': 'NORMAL'
                        }
                        result['files_exported'].append(file_info)
                        result['files'].append(file_info)
                        result['total_rows'] += len(normal_data_lines)
                        result['total_files'] += 1
                        files_for_logging.append((filename_normal, len(normal_data_lines)))

                    else:
                        # Single file export (ASSIGNMENT, SUPERVISOR)
                        # ALWAYS write file (even if empty - header only)
                        data_lines = []
                        for row in rows:
                            line = '|'.join(str(v) if v is not None else '' for v in row)
                            data_lines.append(line)

                        filename = f"{export_prefix}_{timestamp}.csv"
                        local_path = os.path.join(self.LOCAL_EXPORT_FOLDER, filename)
                        csv_content = header_str + '\n' + '\n'.join(data_lines) if data_lines else header_str + '\n'

                        with open(local_path, 'w', encoding='utf-8') as f:
                            f.write(csv_content)

                        file_info = {
                            'view': view_name,
                            'filename': filename,
                            'local_path': local_path,
                            'row_count': len(data_lines),
                            'type': 'SINGLE'
                        }
                        result['files_exported'].append(file_info)
                        result['files'].append(file_info)
                        result['total_rows'] += len(data_lines)
                        result['total_files'] += 1
                        files_for_logging.append((filename, len(data_lines)))

                except Exception as e:
                    result['errors'].append(f"{view_name}: {str(e)}")

            # Log files to RUN_INTF_FILES_SENT table (like original program)
            if latest_instance and files_for_logging:
                try:
                    for filename, rec_count in files_for_logging:
                        cursor.execute("""
                            INSERT INTO RUN_INTF_FILES_SENT
                                   (Instance, FileName, RecordCount, DateCreated)
                            VALUES (?, ?, ?, SYSDATETIME())
                        """, latest_instance, filename, rec_count)
                    conn.commit()
                    result['files_logged'] = len(files_for_logging)
                except Exception as log_error:
                    result['errors'].append(f"Could not log files to RUN_INTF_FILES_SENT: {str(log_error)}")

            # Update status to '03-File Sent' (like original program)
            if latest_instance:
                try:
                    cursor.execute("""
                        UPDATE RUN_INTF_STATUS
                        SET Status = '03-File Sent',
                            DateCompleted = SYSDATETIME()
                        WHERE Instance = ?
                    """, latest_instance)
                    conn.commit()
                    result['status_updated'] = True
                except Exception as status_error:
                    result['errors'].append(f"Could not update RUN_INTF_STATUS: {str(status_error)}")

            cursor.close()
            conn.close()

            # Determine final status - we always create 12 files now
            if result['total_files'] >= 12:
                result['status'] = 'success'
            elif result['total_files'] > 0:
                result['status'] = 'partial'
            else:
                result['status'] = 'error'
                if not result['error']:
                    result['error'] = 'No files exported'

            result['completed_at'] = datetime.now().isoformat()
            result['export_folder'] = self.LOCAL_EXPORT_FOLDER

            if progress_callback:
                progress_callback(f"Export complete! {result['total_files']} files ({result['total_rows']} data rows) to {self.LOCAL_EXPORT_FOLDER}")

        except Exception as e:
            result['status'] = 'error'
            result['error'] = str(e)
            result['completed_at'] = datetime.now().isoformat()

            if progress_callback:
                progress_callback(f"Export Error: {str(e)}")

        return result

    def upload_delta_files_to_s3(self, s3_folder_name, progress_callback=None):
        """Upload exported delta files to S3 Delta Files folder.

        Args:
            s3_folder_name: The timestamped folder name in S3 (e.g., '20260130_1430')
            progress_callback: Callback function for progress updates

        Returns:
            Dict with upload results
        """
        result = {
            'status': 'running',
            'started_at': datetime.now(PR_TIMEZONE).isoformat(),
            'files_uploaded': 0,
            'files_failed': 0,
            'total_bytes': 0,
            'file_results': [],
            'error': None
        }

        try:
            # List CSV files in local export folder
            files_to_upload = []
            if os.path.isdir(self.LOCAL_EXPORT_FOLDER):
                for filename in os.listdir(self.LOCAL_EXPORT_FOLDER):
                    if filename.lower().endswith('.csv'):
                        local_path = os.path.join(self.LOCAL_EXPORT_FOLDER, filename)
                        file_size = os.path.getsize(local_path)
                        files_to_upload.append({
                            'local_path': local_path,
                            'filename': filename,
                            'size': file_size
                        })

            if not files_to_upload:
                result['status'] = 'success'
                result['message'] = 'No delta files to upload'
                result['completed_at'] = datetime.now(PR_TIMEZONE).isoformat()
                return result

            if progress_callback:
                progress_callback(f"Uploading {len(files_to_upload)} delta files to S3...")

            # Upload each file to S3 Delta Files folder
            s3_delta_prefix = f"{s3_folder_name}/6_Delta_Files/"

            for i, file_info in enumerate(files_to_upload, 1):
                file_result = {
                    'filename': file_info['filename'],
                    'size': file_info['size'],
                    'success': False,
                    'error': None
                }

                try:
                    if progress_callback:
                        progress_callback(f"Uploading {i}/{len(files_to_upload)}: {file_info['filename']}")

                    # Read local file
                    with open(file_info['local_path'], 'rb') as f:
                        content = f.read()

                    # Upload to S3
                    s3_key = f"{s3_delta_prefix}{file_info['filename']}"
                    self.s3_client.put_object(
                        Bucket=S3_BUCKET,
                        Key=s3_key,
                        Body=content,
                        Metadata={
                            'source_folder': self.LOCAL_EXPORT_FOLDER,
                            'uploaded_at': datetime.now(PR_TIMEZONE).isoformat()
                        }
                    )

                    file_result['success'] = True
                    file_result['s3_key'] = s3_key
                    result['files_uploaded'] += 1
                    result['total_bytes'] += file_info['size']

                except Exception as e:
                    file_result['error'] = str(e)
                    result['files_failed'] += 1

                result['file_results'].append(file_result)

            result['status'] = 'success' if result['files_failed'] == 0 else 'partial'
            result['completed_at'] = datetime.now(PR_TIMEZONE).isoformat()
            result['s3_prefix'] = s3_delta_prefix

            if progress_callback:
                progress_callback(f"Uploaded {result['files_uploaded']} delta files to S3")

        except Exception as e:
            result['status'] = 'error'
            result['error'] = str(e)
            result['completed_at'] = datetime.now(PR_TIMEZONE).isoformat()

            if progress_callback:
                progress_callback(f"S3 Upload Error: {str(e)}")

        return result


# ============================================
# API CLIENT
# ============================================

class APIClient:
    """Client for API Gateway calls."""

    def __init__(self, auth):
        self.auth = auth

    def _get_headers(self):
        """Get headers with auth token."""
        return {
            'Authorization': f'Bearer {self.auth.id_token}',
            'Content-Type': 'application/json'
        }

    def run_full_pipeline(self, skip_sftp=True, skip_procedure=True, skip_sftp_upload=True):
        """Run the full data processing pipeline (production only).

        Note: SFTP download is now handled locally by the desktop app (via VPN),
        so skip_sftp should always be True when calling Lambda.
        """
        try:
            payload = {
                'environment': 'production',
                'test_mode': False,  # Production mode
                'source_prefix': 'downloads/',
                'skip_sftp': skip_sftp,  # Always True - download happens locally via VPN
                'skip_procedure': skip_procedure,
                'skip_sftp_upload': skip_sftp_upload
                # Note: SFTP credentials removed - download happens locally via VPN
            }

            response = requests.post(
                FULL_PIPELINE_URL,
                json=payload,
                headers=self._get_headers(),
                timeout=900
            )

            if response.status_code == 200:
                return response.json()
            else:
                return {
                    'statusCode': response.status_code,
                    'error': response.text
                }

        except requests.exceptions.Timeout:
            return {'error': 'Request timeout - pipeline may still be running'}
        except Exception as e:
            return {'error': str(e)}


# ============================================
# MAIN APPLICATION
# ============================================

class HaciendaApp:
    """Main application with simplified production pipeline interface."""

    def __init__(self, root):
        self.root = root
        self.root.title("Hacienda ERP Data Pipeline")
        self.root.geometry("900x900")
        self.root.minsize(800, 800)
        self.root.configure(bg=COLORS['bg_dark'])

        # Authentication
        self.auth = CognitoAuth()
        self.api_client = None

        # S3 client
        self.s3_client = None

        # Local executors
        self.local_sql = LocalSqlExecutor()
        self.local_sftp = LocalSftpUploader()
        self.local_exporter = LocalDeltaExporter()
        self.local_downloader = LocalSftpDownloader()  # Download from Sterling SFTP via VPN

        # Timer variables
        self.timer_running = False
        self.timer_start_time = None
        self.timer_elapsed = 0
        self.timer_job = None

        # Pipeline steps for live progress tracking
        self.pipeline_steps = []
        self.current_step_index = -1

        # Configure styles
        self.configure_styles()

        # Show login screen
        self.show_login_screen()

    def configure_styles(self):
        """Configure ttk styles for modern dark theme."""
        style = ttk.Style()
        style.theme_use('clam')

        # Frame styles
        style.configure('Main.TFrame', background=COLORS['bg_dark'])
        style.configure('Card.TFrame', background=COLORS['bg_medium'])

        # Label styles
        style.configure('Title.TLabel',
            background=COLORS['bg_dark'],
            foreground=COLORS['text_primary'],
            font=('Segoe UI', 24, 'bold'))

        style.configure('Subtitle.TLabel',
            background=COLORS['bg_dark'],
            foreground=COLORS['text_secondary'],
            font=('Segoe UI', 11))

        style.configure('Header.TLabel',
            background=COLORS['bg_medium'],
            foreground=COLORS['text_primary'],
            font=('Segoe UI', 12, 'bold'))

        style.configure('Status.TLabel',
            background=COLORS['bg_medium'],
            foreground=COLORS['text_primary'],
            font=('Segoe UI', 11))

        style.configure('Info.TLabel',
            background=COLORS['bg_medium'],
            foreground=COLORS['text_secondary'],
            font=('Segoe UI', 10))

        style.configure('Success.TLabel',
            background=COLORS['bg_medium'],
            foreground=COLORS['success'],
            font=('Segoe UI', 11, 'bold'))

        style.configure('Error.TLabel',
            background=COLORS['bg_medium'],
            foreground=COLORS['error'],
            font=('Segoe UI', 11))

        style.configure('Warning.TLabel',
            background=COLORS['bg_medium'],
            foreground=COLORS['vpn_warning'],
            font=('Segoe UI', 11, 'bold'))

        # Progress bar
        style.configure('Custom.Horizontal.TProgressbar',
            background=COLORS['primary'],
            troughcolor=COLORS['bg_light'])

        # Labelframe
        style.configure('Card.TLabelframe',
            background=COLORS['bg_medium'],
            foreground=COLORS['text_primary'])
        style.configure('Card.TLabelframe.Label',
            background=COLORS['bg_medium'],
            foreground=COLORS['text_primary'],
            font=('Segoe UI', 11, 'bold'))

    def clear_window(self):
        """Clear all widgets from the window."""
        for widget in self.root.winfo_children():
            widget.destroy()

    # ==========================================
    # LOGIN SCREEN
    # ==========================================

    def show_login_screen(self):
        """Display the login screen."""
        self.clear_window()

        # Center frame
        center_frame = ttk.Frame(self.root, style='Main.TFrame')
        center_frame.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        # Title
        title = ttk.Label(center_frame, text="Hacienda ERP Data Pipeline", style='Title.TLabel')
        title.pack(pady=(0, 5))

        subtitle = ttk.Label(center_frame, text="Production Data Processing System", style='Subtitle.TLabel')
        subtitle.pack(pady=(0, 30))

        # Login card
        login_card = ttk.Frame(center_frame, style='Card.TFrame', padding=30)
        login_card.pack()

        # Email field
        email_label = ttk.Label(login_card, text="Email", style='Info.TLabel')
        email_label.pack(anchor=tk.W, pady=(0, 5))

        self.email_entry = tk.Entry(login_card, width=35, font=('Segoe UI', 11),
            bg=COLORS['bg_light'], fg=COLORS['text_primary'],
            insertbackground=COLORS['text_primary'], relief=tk.FLAT)
        self.email_entry.pack(pady=(0, 15), ipady=8)
        self.email_entry.focus()

        # Password field
        pass_label = ttk.Label(login_card, text="Password", style='Info.TLabel')
        pass_label.pack(anchor=tk.W, pady=(0, 5))

        self.pass_entry = tk.Entry(login_card, width=35, font=('Segoe UI', 11), show='*',
            bg=COLORS['bg_light'], fg=COLORS['text_primary'],
            insertbackground=COLORS['text_primary'], relief=tk.FLAT)
        self.pass_entry.pack(pady=(0, 20), ipady=8)

        # Bind Enter key
        self.pass_entry.bind('<Return>', lambda e: self.do_login())

        # Login button
        self.login_btn = tk.Button(login_card, text="Sign In", command=self.do_login,
            font=('Segoe UI', 12, 'bold'), bg=COLORS['primary'], fg='white',
            activebackground=COLORS['primary_dark'], activeforeground='white',
            relief=tk.FLAT, cursor='hand2', padx=40, pady=10)
        self.login_btn.pack(pady=(0, 15))

        # Error message
        self.login_error = ttk.Label(login_card, text="", style='Error.TLabel')
        self.login_error.pack()

        # Version
        version_label = ttk.Label(center_frame, text=f"Version {APP_VERSION}", style='Subtitle.TLabel')
        version_label.pack(pady=(20, 0))

    def do_login(self):
        """Perform login."""
        email = self.email_entry.get().strip().lower()  # Convert to lowercase for case-insensitive login
        password = self.pass_entry.get()

        if not email or not password:
            self.login_error.config(text="Please enter email and password")
            return

        self.login_btn.config(state=tk.DISABLED, text="Signing in...")
        self.login_error.config(text="")
        self.root.update()

        def login_task():
            success, error = self.auth.authenticate(email, password)
            self.root.after(0, self.login_complete, success, error)

        threading.Thread(target=login_task, daemon=True).start()

    def login_complete(self, success, error):
        """Handle login completion."""
        if success:
            self.api_client = APIClient(self.auth)
            self.init_s3_client()
            self.show_main_app()
        else:
            self.login_btn.config(state=tk.NORMAL, text="Sign In")
            self.login_error.config(text=error or "Login failed")

    def init_s3_client(self):
        """Initialize S3 client using Cognito Identity credentials."""
        self.refresh_aws_credentials()

    def refresh_aws_credentials(self):
        """Refresh AWS credentials from Cognito Identity Pool.

        This should be called before any long-running operation that may
        exceed the 1-hour credential expiration window.
        """
        try:
            # Refresh the Cognito Identity credentials
            self.auth._get_aws_credentials()

            # Use the refreshed session
            session = self.auth.get_boto3_session()
            self.s3_client = session.client('s3', region_name=AWS_REGION)
            self.s3_client.head_bucket(Bucket=S3_BUCKET)

            # Update the local executors to use the refreshed session
            self.local_downloader.s3_client = self.s3_client
            self.local_exporter.s3_client = self.s3_client
            self.local_sftp.s3_client = self.s3_client

            # Update LocalSqlExecutor and LocalDeltaExporter to use refreshed session for Secrets Manager
            secrets_client = session.client('secretsmanager', region_name=SQL_SECRET_REGION)
            self.local_sql.secrets_client = secrets_client
            self.local_exporter.secrets_client = secrets_client

            # Clear any cached connection strings so they get fetched with new credentials
            self.local_sql.connection_string = None

            print("AWS credentials refreshed successfully")
            return True
        except Exception as e:
            print(f"Error refreshing AWS credentials: {e}")
            return False

    def do_logout(self):
        """Log out and return to login screen."""
        self.auth.logout()
        self.api_client = None
        self.show_login_screen()

    # ==========================================
    # MAIN APPLICATION
    # ==========================================

    def show_main_app(self):
        """Display the main application - simplified single pipeline view."""
        self.clear_window()

        # Main frame with scrolling capability
        main_frame = ttk.Frame(self.root, style='Main.TFrame')
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Header (fixed at top)
        self.create_header(main_frame)

        # Scrollable content area
        content_canvas = tk.Canvas(main_frame, bg=COLORS['bg_medium'], highlightthickness=0)
        content_scrollbar = ttk.Scrollbar(main_frame, orient=tk.VERTICAL, command=content_canvas.yview)
        content_frame = ttk.Frame(content_canvas, style='Card.TFrame', padding=20)

        content_frame.bind('<Configure>',
            lambda e: content_canvas.configure(scrollregion=content_canvas.bbox('all')))

        content_canvas_window = content_canvas.create_window((0, 0), window=content_frame, anchor='nw')
        content_canvas.configure(yscrollcommand=content_scrollbar.set)

        # Update content frame width when canvas resizes
        def on_content_canvas_configure(event):
            content_canvas.itemconfig(content_canvas_window, width=event.width)
        content_canvas.bind('<Configure>', on_content_canvas_configure)

        # Enable mousewheel scrolling for main content
        def on_content_mousewheel(event):
            content_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        content_canvas.bind('<MouseWheel>', on_content_mousewheel)
        content_frame.bind('<MouseWheel>', on_content_mousewheel)

        content_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=20, pady=(10, 20))
        content_scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=(10, 20))

        # VPN Warning Banner
        vpn_frame = tk.Frame(content_frame, bg=COLORS['vpn_warning'], padx=15, pady=12)
        vpn_frame.pack(fill=tk.X, pady=(0, 20))

        vpn_icon = tk.Label(vpn_frame, text="🔒", font=('Segoe UI', 16), bg=COLORS['vpn_warning'], fg='white')
        vpn_icon.pack(side=tk.LEFT, padx=(0, 10))

        vpn_text = tk.Label(vpn_frame,
            text="IMPORTANT: Connect to FortiClient VPN before running the pipeline",
            font=('Segoe UI', 11, 'bold'), bg=COLORS['vpn_warning'], fg='white')
        vpn_text.pack(side=tk.LEFT)

        # Database info
        db_frame = ttk.Frame(content_frame, style='Card.TFrame')
        db_frame.pack(fill=tk.X, pady=(0, 15))

        db_label = ttk.Label(db_frame, text=f"Database: {PRODUCTION_DATABASE}", style='Header.TLabel')
        db_label.pack(side=tk.LEFT)

        prod_badge = tk.Label(db_frame, text=" PRODUCTION ", font=('Segoe UI', 9, 'bold'),
            bg=COLORS['success'], fg='white', padx=8, pady=2)
        prod_badge.pack(side=tk.LEFT, padx=(10, 0))

        # Description
        desc = ttk.Label(content_frame,
            text="This pipeline processes HCM data through the following steps:\n\n"
                 "1. Download files from SFTP server (or use existing S3 files)\n"
                 "2. Validate files (duplicates, names, schema, completeness)\n"
                 "3. Load validated data to SQL Server\n"
                 "4. Run HCM_MAIN_INTF stored procedure\n"
                 "5. Export delta files and upload to Sterling SFTP",
            style='Info.TLabel', wraplength=700, justify=tk.LEFT)
        desc.pack(anchor=tk.W, pady=(0, 15))

        # Options frame
        options_frame = ttk.LabelFrame(content_frame, text=" Pipeline Options ", style='Card.TLabelframe', padding=10)
        options_frame.pack(fill=tk.X, pady=(0, 15))

        # Skip SFTP Download checkbox
        self.skip_download_var = tk.BooleanVar(value=True)  # Default to skip (use existing files)
        skip_download_check = tk.Checkbutton(options_frame,
            text="Skip SFTP Download (use existing files in S3)",
            variable=self.skip_download_var, font=('Segoe UI', 10),
            bg=COLORS['bg_medium'], fg=COLORS['text_primary'],
            selectcolor=COLORS['bg_light'], activebackground=COLORS['bg_medium'],
            cursor='hand2')
        skip_download_check.pack(anchor=tk.W)

        # Help text for the option
        skip_help = ttk.Label(options_frame,
            text="Check this to skip downloading new files and process files already in the S3 bucket.",
            style='Info.TLabel')
        skip_help.pack(anchor=tk.W, padx=(20, 0))

        # Button frame to hold both buttons side by side
        btn_frame = ttk.Frame(content_frame, style='Card.TFrame')
        btn_frame.pack(pady=15)

        # Run button
        self.run_btn = tk.Button(btn_frame, text="▶  Run Full Pipeline", command=self.run_pipeline,
            font=('Segoe UI', 14, 'bold'), bg=COLORS['success'], fg='white',
            activebackground='#2d8a43', relief=tk.FLAT, cursor='hand2',
            padx=30, pady=12)
        self.run_btn.pack(side=tk.LEFT, padx=10)

        # Save Report button (always visible, next to Run button)
        self.save_report_btn = tk.Button(btn_frame, text="📄  Download Report", command=self._save_report_to_file,
            font=('Segoe UI', 14, 'bold'), bg=COLORS['primary'], fg='white',
            activebackground=COLORS['primary_hover'], relief=tk.FLAT, cursor='hand2',
            padx=30, pady=12, state=tk.DISABLED, disabledforeground=COLORS['text_secondary'])
        self.save_report_btn.pack(side=tk.LEFT, padx=10)

        # Progress frame - ALWAYS VISIBLE
        progress_frame = ttk.LabelFrame(content_frame, text=" Pipeline Progress ", style='Card.TLabelframe', padding=15)
        progress_frame.pack(fill=tk.X, pady=(0, 15))

        # Timer row (shows elapsed time)
        timer_row = ttk.Frame(progress_frame, style='Card.TFrame')
        timer_row.pack(fill=tk.X, pady=(0, 10))

        timer_label_text = ttk.Label(timer_row, text="Time Running:", style='Info.TLabel')
        timer_label_text.pack(side=tk.LEFT)

        self.timer_label = tk.Label(timer_row, text="00:00:00", font=('Consolas', 14, 'bold'),
            bg=COLORS['bg_medium'], fg=COLORS['primary'])
        self.timer_label.pack(side=tk.LEFT, padx=(10, 0))

        self.percent_label = ttk.Label(timer_row, text="0%", style='Header.TLabel')
        self.percent_label.pack(side=tk.RIGHT)

        # Current step label
        step_row = ttk.Frame(progress_frame, style='Card.TFrame')
        step_row.pack(fill=tk.X, pady=(0, 5))

        self.step_label = ttk.Label(step_row, text="Ready to start", style='Status.TLabel')
        self.step_label.pack(side=tk.LEFT)

        # Progress bar
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var,
            maximum=100, style='Custom.Horizontal.TProgressbar', length=400)
        self.progress_bar.pack(fill=tk.X, pady=(5, 10))

        # Detailed step indicator (shows current step out of total)
        self.step_detail_label = ttk.Label(progress_frame, text="Step 0 of 10", style='Info.TLabel')
        self.step_detail_label.pack(anchor=tk.W)

        # Status label for detailed messages
        self.status_label = ttk.Label(progress_frame, text="Click 'Run Full Pipeline' to begin processing", style='Info.TLabel')
        self.status_label.pack(anchor=tk.W, pady=(5, 0))

        # Results frame (scrollable) - ALWAYS VISIBLE - Shows live step progress
        results_frame = ttk.LabelFrame(content_frame, text=" Results ", style='Card.TLabelframe', padding=10)
        results_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        # Set minimum height for results frame
        results_frame.configure(height=300)

        # Scrollable results area
        results_container = ttk.Frame(results_frame, style='Card.TFrame')
        results_container.pack(fill=tk.BOTH, expand=True)

        self.results_canvas = tk.Canvas(results_container, bg=COLORS['bg_medium'], highlightthickness=0, height=250)
        scrollbar = ttk.Scrollbar(results_container, orient=tk.VERTICAL, command=self.results_canvas.yview)
        self.results_inner = ttk.Frame(self.results_canvas, style='Card.TFrame')

        self.results_inner.bind('<Configure>',
            lambda e: self.results_canvas.configure(scrollregion=self.results_canvas.bbox('all')))

        self.canvas_window = self.results_canvas.create_window((0, 0), window=self.results_inner, anchor='nw')
        self.results_canvas.configure(yscrollcommand=scrollbar.set)

        # Update inner frame width when canvas resizes
        def on_canvas_configure(event):
            self.results_canvas.itemconfig(self.canvas_window, width=event.width)
        self.results_canvas.bind('<Configure>', on_canvas_configure)

        # Enable mousewheel scrolling
        def on_mousewheel(event):
            self.results_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        self.results_canvas.bind('<MouseWheel>', on_mousewheel)
        self.results_inner.bind('<MouseWheel>', on_mousewheel)

        self.results_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Initialize pipeline steps list for live progress tracking
        self._init_pipeline_steps()

    def _init_pipeline_steps(self):
        """Initialize the pipeline steps display in the results area."""
        # Clear existing content
        for w in self.results_inner.winfo_children():
            w.destroy()

        # Define all pipeline steps
        self.step_definitions = [
            ('sftp_download', 'SFTP Download from Sterling'),
            ('s3_upload', 'Upload to S3 Bucket'),
            ('duplicate_check', 'Duplicate File Check'),
            ('name_validation', 'File Name Validation'),
            ('schema_validation', 'Column Schema Validation'),
            ('completeness_check', 'Completeness Check'),
            ('sql_load', 'Load Data to SQL Server'),
            ('stored_procedure', 'Run HCM_MAIN_INTF Procedure'),
            ('delta_export', 'Export Delta Files'),
            ('sftp_upload', 'Upload to Sterling SFTP'),
        ]

        # Store step label widgets for updating
        self.step_widgets = {}

        # Create header
        header = ttk.Label(self.results_inner, text="Pipeline Steps:", style='Header.TLabel')
        header.pack(anchor=tk.W, pady=(5, 10), padx=10)

        # Create a row for each step
        for step_id, step_name in self.step_definitions:
            step_frame = ttk.Frame(self.results_inner, style='Card.TFrame')
            step_frame.pack(fill=tk.X, pady=2, padx=10)

            # Status icon (pending = gray circle, running = spinning, complete = green check, failed = red X)
            icon_label = tk.Label(step_frame, text="○", font=('Segoe UI', 12),
                bg=COLORS['bg_medium'], fg=COLORS['text_secondary'], width=3)
            icon_label.pack(side=tk.LEFT)

            # Step name
            name_label = tk.Label(step_frame, text=step_name, font=('Segoe UI', 10),
                bg=COLORS['bg_medium'], fg=COLORS['text_secondary'])
            name_label.pack(side=tk.LEFT)

            # Status text (optional - shows additional info)
            status_label = tk.Label(step_frame, text="", font=('Segoe UI', 9),
                bg=COLORS['bg_medium'], fg=COLORS['text_secondary'])
            status_label.pack(side=tk.RIGHT, padx=(10, 0))

            self.step_widgets[step_id] = {
                'frame': step_frame,
                'icon': icon_label,
                'name': name_label,
                'status': status_label,
                'state': 'pending'
            }

    def _update_step_status(self, step_id, state, status_text=''):
        """Update a specific step's status in the results area.

        Args:
            step_id: The step identifier (e.g., 'sftp_download', 'sql_load')
            state: 'pending', 'running', 'completed', 'failed', 'skipped'
            status_text: Optional status message to display
        """
        if step_id not in self.step_widgets:
            return

        widget = self.step_widgets[step_id]
        widget['state'] = state

        # Update icon and colors based on state
        if state == 'pending':
            widget['icon'].config(text='○', fg=COLORS['text_secondary'])
            widget['name'].config(fg=COLORS['text_secondary'])
        elif state == 'running':
            widget['icon'].config(text='◉', fg=COLORS['primary'])  # Filled circle for running
            widget['name'].config(fg=COLORS['text_primary'])
        elif state == 'completed':
            widget['icon'].config(text='✓', fg=COLORS['success'])
            widget['name'].config(fg=COLORS['success'])
        elif state == 'failed':
            widget['icon'].config(text='✗', fg=COLORS['error'])
            widget['name'].config(fg=COLORS['error'])
        elif state == 'skipped':
            widget['icon'].config(text='⊘', fg=COLORS['text_secondary'])
            widget['name'].config(fg=COLORS['text_secondary'])

        # Update status text
        widget['status'].config(text=status_text)

        # Scroll to show this step
        self.results_canvas.yview_moveto(0)

    def _start_timer(self):
        """Start the elapsed time timer."""
        self.timer_running = True
        self.timer_start_time = time.time()
        self.timer_elapsed = 0
        self._update_timer()

    def _stop_timer(self):
        """Stop the elapsed time timer and return total elapsed time."""
        self.timer_running = False
        if self.timer_job:
            self.root.after_cancel(self.timer_job)
            self.timer_job = None
        if self.timer_start_time:
            self.timer_elapsed = time.time() - self.timer_start_time
        return self.timer_elapsed

    def _update_timer(self):
        """Update the timer display every second."""
        if not self.timer_running:
            return

        elapsed = time.time() - self.timer_start_time
        hours, remainder = divmod(int(elapsed), 3600)
        minutes, seconds = divmod(remainder, 60)
        time_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        if hasattr(self, 'timer_label'):
            self.timer_label.config(text=time_str)

        # Schedule next update
        self.timer_job = self.root.after(1000, self._update_timer)

    def _format_elapsed_time(self, seconds):
        """Format elapsed seconds into a human-readable string."""
        hours, remainder = divmod(int(seconds), 3600)
        minutes, secs = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}h {minutes}m {secs}s"
        elif minutes > 0:
            return f"{minutes}m {secs}s"
        else:
            return f"{secs}s"

    def create_header(self, parent):
        """Create header with user info and logout."""
        header_frame = ttk.Frame(parent, style='Main.TFrame')
        header_frame.pack(fill=tk.X, padx=20, pady=15)

        # Title
        title = ttk.Label(header_frame, text="Hacienda ERP Data Pipeline", style='Title.TLabel')
        title.pack(side=tk.LEFT)

        # Right side - user info and logout
        right_frame = ttk.Frame(header_frame, style='Main.TFrame')
        right_frame.pack(side=tk.RIGHT)

        user_label = ttk.Label(right_frame, text=self.auth.username or "User", style='Subtitle.TLabel')
        user_label.pack(side=tk.LEFT, padx=(0, 15))

        logout_btn = tk.Button(right_frame, text="Sign Out", command=self.do_logout,
            font=('Segoe UI', 10), bg=COLORS['bg_light'], fg=COLORS['text_primary'],
            activebackground=COLORS['bg_medium'], relief=tk.FLAT, cursor='hand2',
            padx=15, pady=5)
        logout_btn.pack(side=tk.LEFT)

    # ==========================================
    # PIPELINE EXECUTION
    # ==========================================

    def run_pipeline(self):
        """Run the complete data processing pipeline."""
        skip_download = self.skip_download_var.get()

        # Build confirmation message based on options
        if skip_download:
            download_msg = "• Use existing files in S3 (skip SFTP download)"
        else:
            download_msg = "• Download new files from SFTP"

        # Confirm before running
        if not messagebox.askyesno("Confirm Pipeline Run",
            f"You are about to run the FULL PIPELINE on the PRODUCTION database.\n\n"
            f"This will:\n"
            f"{download_msg}\n"
            f"• Validate and load data to SQL Server\n"
            f"• Run HCM_MAIN_INTF stored procedure\n"
            f"• Export and upload delta files\n\n"
            f"Make sure you are connected to FortiClient VPN.\n\n"
            f"Continue?"):
            return

        # Reset UI
        self.run_btn.config(state=tk.DISABLED, text="Running Pipeline...")
        self.save_report_btn.config(state=tk.DISABLED)
        self.progress_var.set(0)
        self.percent_label.config(text="0%")
        self.step_label.config(text="Starting pipeline...")
        self.step_detail_label.config(text="Step 0 of 10")
        self.status_label.config(text="Initializing...")

        # Initialize pipeline steps display
        self._init_pipeline_steps()

        # Start the timer
        self._start_timer()

        def task():
            try:
                # Define pipeline steps for progress tracking
                # Steps: 1-SFTP/S3, 2-Validate Names, 3-Duplicates, 4-Schema, 5-Completeness,
                # 6-SQL Load, 7-Stored Proc, 8-Delta Export, 9-SFTP Upload, 10-Complete
                total_steps = 10

                # Step 1: SFTP Download (if not skipped)
                # IMPORTANT: SFTP download happens LOCALLY because the Sterling server
                # (10.3.3.146) is only accessible via FortiClient VPN
                if skip_download:
                    self.root.after(0, lambda: self._update_step_status('sftp_download', 'skipped', 'Skipped'))
                    self.root.after(0, lambda: self._update_step_status('s3_upload', 'skipped', 'Using existing files'))
                    self.root.after(0, lambda: self.update_progress_detailed(5, "Using Existing S3 Files", 1, total_steps, "Skipping SFTP download - using files already in S3..."))
                else:
                    self.root.after(0, lambda: self._update_step_status('sftp_download', 'running', 'Connecting...'))
                    self.root.after(0, lambda: self.update_progress_detailed(2, "Testing VPN Connection", 1, total_steps, "Checking FortiClient VPN connection..."))

                    # Download from SFTP locally and upload to S3
                    download_result = self.local_downloader.download_and_upload_to_s3(
                        s3_prefix='downloads/',
                        progress_callback=lambda msg: self.root.after(0,
                            lambda m=msg: self.status_label.config(text=m))
                    )

                    if download_result.get('status') == 'error':
                        # VPN or SFTP connection failed
                        self.root.after(0, lambda: self._update_step_status('sftp_download', 'failed', download_result.get('error', 'Failed')))
                        self.root.after(0, lambda: self.show_result({
                            'status': 'failed',
                            'error': download_result.get('error', 'SFTP download failed'),
                            'sftp_download_result': download_result
                        }, None))
                        return

                    files_downloaded = download_result.get('files_downloaded', 0)
                    files_found = download_result.get('files_found_on_server', 0)
                    total_bytes = download_result.get('total_bytes', 0)
                    self.root.after(0, lambda: self._update_step_status('sftp_download', 'completed', f'{files_downloaded} files'))
                    self.root.after(0, lambda: self._update_step_status('s3_upload', 'completed', f'{total_bytes:,} bytes'))
                    self.root.after(0, lambda: self.update_progress_detailed(8, "SFTP Download Complete", 1, total_steps,
                        f"Found {files_found} files, downloaded {files_downloaded} ({total_bytes:,} bytes) to S3"))

                # Step 2: Run Lambda pipeline for validation and SQL load
                # Lambda always uses existing S3 files (download happened locally above)
                self.root.after(0, lambda: self._update_step_status('duplicate_check', 'running', 'Processing...'))
                self.root.after(0, lambda: self.update_progress_detailed(10, "Running Validation Pipeline", 2, total_steps, "Lambda processing files from S3..."))

                result = self.api_client.run_full_pipeline(
                    skip_sftp=True,  # Always skip - download already done locally if needed
                    skip_procedure=True,  # Run stored procedure locally
                    skip_sftp_upload=True  # Upload locally via VPN
                )

                # Parse result
                data = result
                if isinstance(result.get('body'), str):
                    try:
                        data = json.loads(result['body'])
                    except:
                        data = result
                elif isinstance(result.get('body'), dict):
                    data = result['body']

                # Add SFTP download result to data (for report)
                if not skip_download:
                    data['sftp_download_result'] = download_result

                pipeline_status = data.get('status', 'unknown')

                # Update progress based on Lambda steps completed
                steps_completed = data.get('completed_steps', 0)
                lambda_steps = data.get('steps', [])

                # Map lambda steps to our step widgets and update status
                step_mapping = {
                    'duplicate_check': 'duplicate_check',
                    'name_validation': 'name_validation',
                    'schema_validation': 'schema_validation',
                    'completeness_check': 'completeness_check',
                    'sql_load': 'sql_load'
                }

                if lambda_steps:
                    for i, step in enumerate(lambda_steps):
                        step_name = step.get('step', f'Step {i+1}')
                        step_success = step.get('success', False)
                        step_message = step.get('message', '')
                        progress_pct = min(10 + (i * 5), 50)  # Progress from 10% to 50%
                        status_icon = "✓" if step_success else "✗"

                        # Update the corresponding step widget
                        if step_name in step_mapping:
                            widget_id = step_mapping[step_name]
                            state = 'completed' if step_success else 'failed'
                            # Extract short status from message
                            short_status = step_message[:30] + '...' if len(step_message) > 30 else step_message
                            self.root.after(0, lambda wid=widget_id, st=state, msg=short_status:
                                self._update_step_status(wid, st, msg))

                        self.root.after(0, lambda p=progress_pct, s=step_name, n=i+2, t=total_steps, icon=status_icon:
                            self.update_progress_detailed(p, f"{icon} {s}", n, t, f"Completed: {s}"))

                # Update to 55% after Lambda pipeline
                self.root.after(0, lambda: self.update_progress_detailed(55, "Lambda Pipeline Complete", 6, total_steps,
                    f"Pipeline status: {pipeline_status}"))

                # Step 7: Run stored procedure locally
                if pipeline_status == 'success':
                    self.root.after(0, lambda: self._update_step_status('stored_procedure', 'running', 'Executing...'))
                    self.root.after(0, lambda: self.update_progress_detailed(60, "Running Stored Procedure", 7, total_steps,
                        "Executing HCM_MAIN_INTF on local database..."))

                    proc_result = self.local_sql.execute_stored_procedure(
                        database_override=PRODUCTION_DATABASE,
                        progress_callback=lambda msg: self.root.after(0,
                            lambda m=msg: self.status_label.config(text=m))
                    )

                    data['local_procedure_result'] = proc_result
                    data['ran_procedure_locally'] = True

                    if proc_result.get('status') == 'error':
                        data['status'] = 'partial'
                        data['error'] = f"Pipeline succeeded but stored procedure failed: {proc_result.get('error')}"
                        self.root.after(0, lambda: self._update_step_status('stored_procedure', 'failed', 'Error'))
                        self.root.after(0, lambda: self.update_progress_detailed(65, "Stored Procedure Failed", 7, total_steps,
                            f"Error: {proc_result.get('error', 'Unknown error')}"))
                    else:
                        # Step 8: Export delta files
                        delta_count = sum(c for c in proc_result.get('delta_counts', {}).values() if c > 0)
                        self.root.after(0, lambda: self._update_step_status('stored_procedure', 'completed', f'{delta_count:,} records'))
                        self.root.after(0, lambda: self.update_progress_detailed(70, "Stored Procedure Complete", 7, total_steps,
                            f"Generated {delta_count:,} delta records"))

                        # Refresh AWS credentials before delta export (stored procedure can take 60+ minutes)
                        self.root.after(0, lambda: self.status_label.config(text="Refreshing AWS credentials..."))
                        self.refresh_aws_credentials()

                        self.root.after(0, lambda: self._update_step_status('delta_export', 'running', 'Exporting...'))
                        self.root.after(0, lambda: self.update_progress_detailed(75, "Exporting Delta Files", 8, total_steps,
                            "Creating export files from delta views..."))

                        export_result = self.local_exporter.export_delta_files(
                            database_override=PRODUCTION_DATABASE,
                            progress_callback=lambda msg: self.root.after(0,
                                lambda m=msg: self.status_label.config(text=m))
                        )

                        data['local_export_result'] = export_result

                        if export_result.get('status') in ['success', 'partial']:
                            files_exported = export_result.get('total_files', 0)
                            rows_exported = export_result.get('total_rows', 0)
                            self.root.after(0, lambda: self._update_step_status('delta_export', 'completed', f'{files_exported} files'))
                            self.root.after(0, lambda: self.update_progress_detailed(78, "Delta Export Complete", 8, total_steps,
                                f"Exported {files_exported} files ({rows_exported:,} rows)"))

                            # Upload delta files to S3 Delta Files folder
                            folder_name = data.get('folder_name', '')
                            if folder_name:
                                self.root.after(0, lambda: self.status_label.config(text="Uploading delta files to S3..."))
                                s3_delta_result = self.local_exporter.upload_delta_files_to_s3(
                                    s3_folder_name=folder_name,
                                    progress_callback=lambda msg: self.root.after(0,
                                        lambda m=msg: self.status_label.config(text=m))
                                )
                                data['s3_delta_upload_result'] = s3_delta_result
                                self.root.after(0, lambda: self.update_progress_detailed(80, "Delta Files Saved to S3", 8, total_steps,
                                    f"Uploaded {s3_delta_result.get('files_uploaded', 0)} delta files to S3"))

                            # Step 9: Upload to SFTP
                            self.root.after(0, lambda: self._update_step_status('sftp_upload', 'running', 'Connecting...'))
                            self.root.after(0, lambda: self.update_progress_detailed(85, "Uploading to Sterling SFTP", 9, total_steps,
                                "Connecting to Sterling SFTP server..."))

                            upload_result = self.local_sftp.upload_export_files(
                                export_folder=export_result.get('output_prefix', ''),
                                progress_callback=lambda msg: self.root.after(0,
                                    lambda m=msg: self.status_label.config(text=m))
                            )

                            data['local_sftp_upload_result'] = upload_result

                            if upload_result.get('status') == 'error':
                                data['status'] = 'partial'
                                data['error'] = f"SFTP upload failed: {upload_result.get('error')}"
                                self.root.after(0, lambda: self._update_step_status('sftp_upload', 'failed', 'Error'))
                                self.root.after(0, lambda: self.update_progress_detailed(90, "SFTP Upload Failed", 9, total_steps,
                                    f"Error: {upload_result.get('error', 'Unknown error')}"))
                            else:
                                files_uploaded = upload_result.get('files_uploaded', 0)
                                self.root.after(0, lambda: self._update_step_status('sftp_upload', 'completed', f'{files_uploaded} files'))
                                self.root.after(0, lambda: self.update_progress_detailed(95, "SFTP Upload Complete", 9, total_steps,
                                    f"Uploaded {files_uploaded} files to Sterling SFTP"))
                        else:
                            self.root.after(0, lambda: self._update_step_status('delta_export', 'failed', 'Error'))
                            self.root.after(0, lambda: self.update_progress_detailed(80, "Delta Export Failed", 8, total_steps,
                                f"Error: {export_result.get('error', 'Unknown error')}"))

                self.root.after(0, self.show_result, data, None)

            except Exception as e:
                self.root.after(0, self.show_result, None, str(e))

        threading.Thread(target=task, daemon=True).start()

    def update_progress_detailed(self, percent, step_text, current_step, total_steps, detail_text):
        """Update progress bar with detailed step information."""
        self.progress_var.set(percent)
        self.percent_label.config(text=f"{percent}%")
        self.step_label.config(text=step_text)
        self.step_detail_label.config(text=f"Step {current_step} of {total_steps}")
        self.status_label.config(text=detail_text)

    def update_progress(self, percent, step_text):
        """Update progress bar and step label (simple version)."""
        self.progress_var.set(percent)
        self.percent_label.config(text=f"{percent}%")
        self.step_label.config(text=step_text)
        # Also update detailed labels if they exist
        if hasattr(self, 'step_detail_label'):
            self.step_detail_label.config(text="")
        if hasattr(self, 'status_label'):
            self.status_label.config(text="")

    def show_result(self, result, error):
        """Display pipeline results."""
        # Stop the timer and capture elapsed time
        elapsed_seconds = self._stop_timer()
        elapsed_formatted = self._format_elapsed_time(elapsed_seconds)

        self.run_btn.config(state=tk.NORMAL, text="▶  Run Full Pipeline")

        # Don't clear results - keep the step progress visible
        # But we'll add a summary section at the top

        if error:
            self.update_progress_detailed(0, "Pipeline Failed", 0, 10, "Error occurred - see details below")

            # Store error for report with elapsed time
            self._last_pipeline_result = {
                'status': 'error',
                'error': error,
                'elapsed_seconds': elapsed_seconds,
                'elapsed_formatted': elapsed_formatted
            }
            self.save_report_btn.config(state=tk.NORMAL)

            error_frame = ttk.LabelFrame(self.results_inner, text=" Error Details ",
                style='Card.TLabelframe', padding=10)
            error_frame.pack(fill=tk.X, pady=10, padx=5)

            error_text = tk.Text(error_frame, height=6, wrap=tk.WORD,
                font=('Consolas', 10), bg='#fff0f0', fg=COLORS['error'],
                relief=tk.FLAT, padx=10, pady=10)
            error_text.insert('1.0', error)
            error_text.config(state=tk.DISABLED)
            error_text.pack(fill=tk.X)
            return

        # Parse result
        data = result
        if isinstance(result.get('body'), str):
            try:
                data = json.loads(result['body'])
            except:
                data = result
        elif isinstance(result.get('body'), dict):
            data = result['body']

        status = data.get('status', 'unknown')
        pipeline_id = data.get('pipeline_id', '')
        folder_name = data.get('folder_name', '')
        completed_steps = data.get('completed_steps', 0)
        total_steps_from_data = data.get('total_steps', 0)
        steps = data.get('steps', [])
        error_msg = data.get('error')

        # Update progress
        if status == 'success':
            self.update_progress_detailed(100, "✓ Pipeline Complete!", 10, 10, "All steps completed successfully")
        elif status == 'partial':
            pct = 90  # Partial success
            self.update_progress_detailed(pct, "⚠ Pipeline Partial Success", 10, 10, "Some steps had warnings or errors - review details below")
        else:
            pct = int((completed_steps / total_steps_from_data) * 100) if total_steps_from_data > 0 else 50
            self.update_progress_detailed(pct, f"✗ Pipeline {status.title()}", completed_steps, 10, "Review errors below")

        # Summary
        summary_frame = ttk.Frame(self.results_inner, style='Card.TFrame')
        summary_frame.pack(fill=tk.X, pady=10, padx=5)

        status_color = COLORS['success'] if status == 'success' else COLORS['error']
        tk.Label(summary_frame, text=f"Status: {status.upper()}",
            font=('Segoe UI', 12, 'bold'), bg=COLORS['bg_medium'], fg=status_color).pack(anchor=tk.W)

        ttk.Label(summary_frame, text=f"Pipeline ID: {pipeline_id}", style='Info.TLabel').pack(anchor=tk.W)
        ttk.Label(summary_frame, text=f"Folder: {folder_name}", style='Info.TLabel').pack(anchor=tk.W)

        # Error message
        if error_msg:
            error_frame = ttk.Frame(self.results_inner, style='Card.TFrame')
            error_frame.pack(fill=tk.X, pady=5, padx=5)
            tk.Label(error_frame, text=f"Error: {error_msg}",
                font=('Segoe UI', 10), bg=COLORS['bg_medium'], fg=COLORS['error'],
                wraplength=650, justify=tk.LEFT).pack(anchor=tk.W)

        # Steps
        steps_frame = ttk.LabelFrame(self.results_inner, text=" Pipeline Steps ",
            style='Card.TLabelframe', padding=10)
        steps_frame.pack(fill=tk.X, pady=10, padx=5)

        for step in steps:
            step_name = step.get('step', 'Unknown')
            step_success = step.get('success', False)
            step_message = step.get('message', '')

            icon = "✓" if step_success else "✗"
            color = COLORS['success'] if step_success else COLORS['error']

            step_row = ttk.Frame(steps_frame, style='Card.TFrame')
            step_row.pack(fill=tk.X, pady=2)

            tk.Label(step_row, text=icon, font=('Segoe UI', 10, 'bold'),
                bg=COLORS['bg_medium'], fg=color, width=3).pack(side=tk.LEFT)
            tk.Label(step_row, text=f"{step_name}: {step_message}",
                font=('Segoe UI', 9), bg=COLORS['bg_medium'], fg=COLORS['text_primary'],
                wraplength=600, justify=tk.LEFT).pack(side=tk.LEFT, fill=tk.X)

        # Local procedure result
        if data.get('ran_procedure_locally'):
            proc_result = data.get('local_procedure_result', {})
            proc_frame = ttk.LabelFrame(self.results_inner, text=" Stored Procedure (Local) ",
                style='Card.TLabelframe', padding=10)
            proc_frame.pack(fill=tk.X, pady=10, padx=5)

            proc_status = proc_result.get('status', 'unknown')
            proc_color = COLORS['success'] if proc_status == 'success' else COLORS['error']

            tk.Label(proc_frame, text=f"Status: {proc_status.upper()}",
                font=('Segoe UI', 10, 'bold'), bg=COLORS['bg_medium'], fg=proc_color).pack(anchor=tk.W)

            delta_counts = proc_result.get('delta_counts', {})
            if delta_counts:
                total_deltas = sum(c for c in delta_counts.values() if c > 0)
                tk.Label(proc_frame, text=f"Total delta records: {total_deltas:,}",
                    font=('Segoe UI', 10), bg=COLORS['bg_medium'], fg=COLORS['text_primary']).pack(anchor=tk.W)

        # Export result
        if data.get('local_export_result'):
            export_result = data.get('local_export_result', {})
            export_frame = ttk.LabelFrame(self.results_inner, text=" Delta Export ",
                style='Card.TLabelframe', padding=10)
            export_frame.pack(fill=tk.X, pady=10, padx=5)

            export_status = export_result.get('status', 'unknown')
            export_color = COLORS['success'] if export_status == 'success' else COLORS['error']

            tk.Label(export_frame, text=f"Status: {export_status.upper()}",
                font=('Segoe UI', 10, 'bold'), bg=COLORS['bg_medium'], fg=export_color).pack(anchor=tk.W)
            tk.Label(export_frame, text=f"Files exported: {export_result.get('total_files', 0)}",
                font=('Segoe UI', 10), bg=COLORS['bg_medium'], fg=COLORS['text_primary']).pack(anchor=tk.W)
            tk.Label(export_frame, text=f"Total rows: {export_result.get('total_rows', 0):,}",
                font=('Segoe UI', 10), bg=COLORS['bg_medium'], fg=COLORS['text_primary']).pack(anchor=tk.W)

        # SFTP upload result
        if data.get('local_sftp_upload_result'):
            upload_result = data.get('local_sftp_upload_result', {})
            upload_frame = ttk.LabelFrame(self.results_inner, text=" SFTP Upload ",
                style='Card.TLabelframe', padding=10)
            upload_frame.pack(fill=tk.X, pady=10, padx=5)

            upload_status = upload_result.get('status', 'unknown')
            upload_color = COLORS['success'] if upload_status == 'success' else COLORS['error']

            tk.Label(upload_frame, text=f"Status: {upload_status.upper()}",
                font=('Segoe UI', 10, 'bold'), bg=COLORS['bg_medium'], fg=upload_color).pack(anchor=tk.W)
            tk.Label(upload_frame, text=f"Files uploaded: {upload_result.get('files_uploaded', 0)}",
                font=('Segoe UI', 10), bg=COLORS['bg_medium'], fg=COLORS['text_primary']).pack(anchor=tk.W)
            tk.Label(upload_frame, text=f"Total bytes: {upload_result.get('total_bytes', 0):,}",
                font=('Segoe UI', 10), bg=COLORS['bg_medium'], fg=COLORS['text_primary']).pack(anchor=tk.W)

        # Store the data for report generation and enable the Save Report button
        # Add elapsed time to the result
        data['elapsed_seconds'] = elapsed_seconds
        data['elapsed_formatted'] = elapsed_formatted
        self._last_pipeline_result = data
        self.save_report_btn.config(state=tk.NORMAL)

        # Table Results Details (show which tables succeeded/failed)
        self._show_table_results_details(data)

    def _show_table_results_details(self, data):
        """Show detailed table load results - which tables succeeded/failed."""
        # Check for table results in steps
        for step in data.get('steps', []):
            details = step.get('details', {})
            if details and 'table_results' in details:
                table_results = details.get('table_results', [])
                failed_tables = details.get('failed_tables', 0)
                loaded_tables = details.get('loaded_tables', 0)

                if table_results:
                    tables_frame = ttk.LabelFrame(self.results_inner, text=" Table Load Details ",
                        style='Card.TLabelframe', padding=10)
                    tables_frame.pack(fill=tk.X, pady=10, padx=5)

                    # Summary line
                    summary_text = f"Loaded: {loaded_tables} tables  |  Failed: {failed_tables} tables"
                    summary_color = COLORS['success'] if failed_tables == 0 else COLORS['error']
                    tk.Label(tables_frame, text=summary_text,
                        font=('Segoe UI', 10, 'bold'), bg=COLORS['bg_medium'], fg=summary_color).pack(anchor=tk.W, pady=(0, 10))

                    # Categorize tables
                    failed = [t for t in table_results if not t.get('success') and t.get('rows_loaded', 0) == 0]
                    partial = [t for t in table_results if t.get('error') and t.get('rows_loaded', 0) > 0]
                    successful = [t for t in table_results if t.get('success') and not t.get('error')]

                    # Show completely failed tables first
                    if failed:
                        tk.Label(tables_frame, text="❌ Failed Tables:",
                            font=('Segoe UI', 10, 'bold'), bg=COLORS['bg_medium'], fg=COLORS['error']).pack(anchor=tk.W, pady=(5, 2))

                        for table in failed:
                            table_name = table.get('table_name', table.get('filename', 'Unknown'))
                            error_msg = table.get('error', 'Unknown error')

                            error_frame = ttk.Frame(tables_frame, style='Card.TFrame')
                            error_frame.pack(fill=tk.X, pady=2, padx=10)

                            tk.Label(error_frame, text=f"• {table_name}",
                                font=('Segoe UI', 9, 'bold'), bg=COLORS['bg_medium'], fg=COLORS['error']).pack(anchor=tk.W)
                            tk.Label(error_frame, text=f"  Error: {error_msg}",
                                font=('Consolas', 8), bg=COLORS['bg_medium'], fg=COLORS['text_secondary'],
                                wraplength=600, justify=tk.LEFT).pack(anchor=tk.W)

                    # Show partial success tables (some rows loaded, some failed)
                    if partial:
                        tk.Label(tables_frame, text="⚠️ Partial Success (some rows failed):",
                            font=('Segoe UI', 10, 'bold'), bg=COLORS['bg_medium'], fg='#e6a700').pack(anchor=tk.W, pady=(10, 2))

                        for table in partial:
                            table_name = table.get('table_name', table.get('filename', 'Unknown'))
                            error_msg = table.get('error', 'Unknown error')
                            rows_loaded = table.get('rows_loaded', 0)
                            failed_rows = table.get('failed_rows') or []

                            error_frame = ttk.Frame(tables_frame, style='Card.TFrame')
                            error_frame.pack(fill=tk.X, pady=2, padx=10)

                            tk.Label(error_frame, text=f"• {table_name}",
                                font=('Segoe UI', 9, 'bold'), bg=COLORS['bg_medium'], fg='#e6a700').pack(anchor=tk.W)
                            tk.Label(error_frame, text=f"  {rows_loaded:,} rows loaded successfully, {len(failed_rows)} rows failed",
                                font=('Segoe UI', 8), bg=COLORS['bg_medium'], fg=COLORS['text_secondary']).pack(anchor=tk.W)

                            # Show first few row-level errors
                            if failed_rows:
                                for row_err in failed_rows[:3]:  # Show first 3 row errors
                                    row_num = row_err.get('row_number', '?')
                                    row_error = row_err.get('error', 'Unknown')
                                    # Truncate long error messages
                                    if len(row_error) > 80:
                                        row_error = row_error[:77] + "..."
                                    tk.Label(error_frame, text=f"    Row {row_num}: {row_error}",
                                        font=('Consolas', 7), bg=COLORS['bg_medium'], fg='#cc6666',
                                        wraplength=580, justify=tk.LEFT).pack(anchor=tk.W)
                                if len(failed_rows) > 3:
                                    tk.Label(error_frame, text=f"    ... and {len(failed_rows) - 3} more row errors (see saved report for details)",
                                        font=('Segoe UI', 7, 'italic'), bg=COLORS['bg_medium'], fg=COLORS['text_secondary']).pack(anchor=tk.W)
                    if successful:
                        tk.Label(tables_frame, text=f"✓ Successful Tables ({len(successful)}):",
                            font=('Segoe UI', 10, 'bold'), bg=COLORS['bg_medium'], fg=COLORS['success']).pack(anchor=tk.W, pady=(10, 2))

                        # Show first 5, then a "and X more" message
                        for i, table in enumerate(successful[:5]):
                            table_name = table.get('table_name', table.get('filename', 'Unknown'))
                            rows = table.get('rows_loaded', 0)
                            tk.Label(tables_frame, text=f"  • {table_name} ({rows:,} rows)",
                                font=('Segoe UI', 9), bg=COLORS['bg_medium'], fg=COLORS['text_secondary']).pack(anchor=tk.W)

                        if len(successful) > 5:
                            tk.Label(tables_frame, text=f"  ... and {len(successful) - 5} more tables",
                                font=('Segoe UI', 9, 'italic'), bg=COLORS['bg_medium'], fg=COLORS['text_secondary']).pack(anchor=tk.W)

                break  # Only show one table results section

    def _open_report(self, url):
        """Open report URL in default browser."""
        import webbrowser
        try:
            webbrowser.open(url)
        except Exception as e:
            messagebox.showerror("Error", f"Could not open browser: {e}")

    def _copy_to_clipboard(self, text):
        """Copy text to clipboard."""
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()
            messagebox.showinfo("Copied", "Report URL copied to clipboard!")
        except Exception as e:
            messagebox.showerror("Error", f"Could not copy to clipboard: {e}")

    def _save_report_to_file(self):
        """Save detailed pipeline report to a file."""
        from tkinter import filedialog

        if not hasattr(self, '_last_pipeline_result') or not self._last_pipeline_result:
            messagebox.showerror("Error", "No pipeline results available to save.")
            return

        data = self._last_pipeline_result

        # Generate timestamp for filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_filename = f"HaciendaERP_Pipeline_Report_{timestamp}.txt"

        # Ask user where to save
        file_path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile=default_filename,
            title="Save Pipeline Report"
        )

        if not file_path:
            return  # User cancelled

        try:
            report_content = self._generate_report_content(data)

            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(report_content)

            messagebox.showinfo("Report Saved",
                f"Report saved successfully!\n\nLocation: {file_path}")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to save report: {e}")

    def _generate_report_content(self, data):
        """Generate detailed text report content."""
        lines = []

        # Header
        lines.append("=" * 80)
        lines.append("HACIENDA ERP DATA PIPELINE REPORT")
        lines.append("=" * 80)
        # Use Puerto Rico time for report timestamp
        pr_time = datetime.now(PR_TIMEZONE)
        lines.append(f"Generated: {pr_time.strftime('%Y-%m-%d %H:%M:%S')} (Puerto Rico Time)")
        lines.append(f"Application Version: {APP_VERSION}")
        lines.append(f"Database: {PRODUCTION_DATABASE}")

        # Add total time running
        elapsed_formatted = data.get('elapsed_formatted', 'N/A')
        elapsed_seconds = data.get('elapsed_seconds', 0)
        if elapsed_seconds > 0:
            lines.append(f"Total Time Running: {elapsed_formatted} ({elapsed_seconds:.1f} seconds)")
        lines.append("")

        # Overall Status
        status = data.get('status', 'unknown').upper()
        pipeline_id = data.get('pipeline_id', 'N/A')
        folder_name = data.get('folder_name', 'N/A')
        error_msg = data.get('error', '')

        lines.append("-" * 80)
        lines.append("PIPELINE SUMMARY")
        lines.append("-" * 80)
        lines.append(f"Status: {status}")
        lines.append(f"Pipeline ID: {pipeline_id}")
        lines.append(f"Folder: {folder_name}")

        if error_msg:
            lines.append(f"Error: {error_msg}")
        lines.append("")

        # SFTP Download Details (if download was performed)
        sftp_download = data.get('sftp_download_result')
        if sftp_download:
            lines.append("-" * 80)
            lines.append("SFTP DOWNLOAD DETAILS")
            lines.append("-" * 80)
            lines.append(f"SFTP Host: {sftp_download.get('sftp_host', 'N/A')}:{sftp_download.get('sftp_port', 22)}")
            lines.append(f"Remote Folder: {sftp_download.get('remote_folder', 'N/A')}")
            lines.append(f"Excluded Directories: {', '.join(sftp_download.get('excluded_dirs', []))}")
            lines.append(f"Files Found on Server: {sftp_download.get('files_found_on_server', 'N/A')}")
            lines.append(f"Files Downloaded: {sftp_download.get('files_downloaded', 0)}")
            lines.append(f"Files Failed: {sftp_download.get('files_failed', 0)}")
            lines.append(f"Total Bytes: {sftp_download.get('total_bytes', 0):,}")
            lines.append(f"S3 Destination: s3://{sftp_download.get('s3_bucket', 'N/A')}/{sftp_download.get('s3_prefix', '')}")
            if sftp_download.get('error'):
                lines.append(f"Error: {sftp_download.get('error')}")
            lines.append("")

        # Pipeline Steps
        steps = data.get('steps', [])
        if steps:
            lines.append("-" * 80)
            lines.append("PIPELINE STEPS")
            lines.append("-" * 80)

            # Get local SFTP download result for accurate reporting
            sftp_download_result = data.get('sftp_download_result')

            for step in steps:
                step_name = step.get('step', 'Unknown')
                step_success = "SUCCESS" if step.get('success', False) else "FAILED"
                step_message = step.get('message', '')

                # Override SFTP download message if we did local download
                if step_name == 'sftp_download' and sftp_download_result:
                    files_downloaded = sftp_download_result.get('files_downloaded', 0)
                    total_bytes = sftp_download_result.get('total_bytes', 0)
                    if sftp_download_result.get('status') == 'success':
                        step_message = f"Downloaded {files_downloaded} files ({total_bytes:,} bytes) from SFTP via local VPN"
                    elif sftp_download_result.get('error'):
                        step_message = f"SFTP download failed: {sftp_download_result.get('error')}"

                lines.append(f"[{step_success}] {step_name}")
                if step_message:
                    lines.append(f"        Message: {step_message}")
            lines.append("")

        # Duplicate Check Details (if files were moved)
        for step in data.get('steps', []):
            if step.get('step') == 'duplicate_check':
                details = step.get('details', {})
                total_moved = details.get('total_moved', 0)
                superseded_groups = details.get('superseded_groups', [])

                if total_moved > 0 or superseded_groups:
                    lines.append("-" * 80)
                    lines.append("DUPLICATE/SUPERSEDED FILE DETAILS")
                    lines.append("-" * 80)
                    lines.append(f"Total Files Scanned: {details.get('total_files', 0)}")
                    lines.append(f"Unique Files: {details.get('unique_files', 0)}")
                    lines.append(f"Exact Duplicates: {details.get('total_exact_duplicates', 0)}")
                    lines.append(f"Superseded (older versions): {details.get('total_superseded', 0)}")
                    lines.append(f"Total Files Moved to DuplicateCheck: {total_moved}")
                    lines.append("")

                    # Show superseded groups (files with older versions removed)
                    if superseded_groups:
                        lines.append("SUPERSEDED FILES (older versions moved, newest kept):")
                        lines.append("-" * 50)
                        for group in superseded_groups:
                            file_type = group.get('file_type', 'Unknown')
                            entity = group.get('entity', 'Unknown')
                            kept_file = group.get('recommended_keep', 'Unknown')
                            superseded_files = group.get('superseded_files', [])

                            # Extract just filename from s3_key for readability
                            kept_filename = kept_file.split('/')[-1] if '/' in kept_file else kept_file

                            lines.append("")
                            lines.append(f"  Type: {file_type} ({entity})")
                            lines.append(f"  KEPT (newest): {kept_filename}")
                            if superseded_files:
                                lines.append(f"  MOVED (older versions):")
                                for sf in superseded_files:
                                    sf_filename = sf.split('/')[-1] if '/' in sf else sf
                                    lines.append(f"    - {sf_filename}")

                        lines.append("")

                    # Show excluded files (e.g., RHUM files kept for special processing)
                    excluded_count = details.get('excluded_files_count', 0)
                    if excluded_count > 0:
                        lines.append(f"Note: {excluded_count} RHUM files excluded from superseded removal")
                        lines.append("      (RHUM files process all versions oldest to newest)")
                        lines.append("")

        # File Name Validation Details (show if there were ANY invalid files, even if pipeline continued)
        for step in data.get('steps', []):
            if step.get('step') == 'name_validation':
                details = step.get('details', {})
                invalid_file_details = details.get('invalid_file_details', [])

                if invalid_file_details:
                    lines.append("-" * 80)
                    lines.append("FILE NAME VALIDATION ERRORS - ACTION REQUIRED")
                    lines.append("-" * 80)
                    lines.append(f"Total Files Checked: {details.get('total_files', 0)}")
                    lines.append(f"Valid Files: {details.get('valid_files', 0)}")
                    lines.append(f"Invalid Files: {details.get('invalid_files', 0)}")
                    lines.append(f"Auto-Correctable: {details.get('correctable_files', 0)}")

                    # Note if pipeline continued with valid files
                    if details.get('continuing_with_valid'):
                        lines.append("")
                        lines.append(">>> Pipeline CONTINUED processing valid files.")
                        lines.append(f">>> {details.get('invalid_files_moved', 0)} invalid files were moved to InvalidFiles folder.")

                    lines.append("")
                    lines.append("EXPECTED FILE NAME FORMAT:")
                    lines.append("  Pattern: HCM_{SOURCE}_INTF_{ENTITY}_{DATE}.csv")
                    lines.append("")
                    valid_sources = details.get('valid_sources', [])
                    valid_entities = details.get('valid_entities', [])
                    if valid_sources:
                        lines.append(f"  Valid SOURCES: {', '.join(valid_sources)}")
                    if valid_entities:
                        lines.append(f"  Valid ENTITIES: {', '.join(valid_entities)}")
                    lines.append("  Valid DATE formats: YYYYMMDD, YYYYMMDDHHMM, YYYYMMDDHHMMSS")
                    lines.append("")
                    lines.append("=" * 60)
                    lines.append("INVALID FILES - PLEASE CORRECT THESE:")
                    lines.append("=" * 60)

                    for i, file_detail in enumerate(invalid_file_details, 1):
                        lines.append("")
                        lines.append(f"[{i}] FILE: {file_detail.get('file_name', 'Unknown')}")
                        lines.append(f"    ERROR: {file_detail.get('error_message', 'Unknown error')}")
                        if file_detail.get('detected_source'):
                            lines.append(f"    Detected Source: {file_detail.get('detected_source')}")
                        if file_detail.get('detected_entity'):
                            lines.append(f"    Detected Entity: {file_detail.get('detected_entity')}")
                        if file_detail.get('detected_date'):
                            lines.append(f"    Detected Date: {file_detail.get('detected_date')}")
                        if file_detail.get('suggested_correction'):
                            lines.append(f"    >>> SUGGESTED FIX: Rename to -> {file_detail.get('suggested_correction')}")

                    lines.append("")

        # Table Load Details
        for step in data.get('steps', []):
            details = step.get('details', {})
            if details and 'table_results' in details:
                table_results = details.get('table_results', [])
                loaded_tables = details.get('loaded_tables', 0)
                failed_tables = details.get('failed_tables', 0)
                total_rows = details.get('total_rows', 0)

                lines.append("-" * 80)
                lines.append("TABLE LOAD DETAILS")
                lines.append("-" * 80)
                lines.append(f"Tables Loaded Successfully: {loaded_tables}")
                lines.append(f"Tables Failed: {failed_tables}")
                lines.append(f"Total Rows Loaded: {total_rows:,}")
                lines.append("")

                # Categorize tables
                failed = [t for t in table_results if not t.get('success') and t.get('rows_loaded', 0) == 0]
                partial = [t for t in table_results if t.get('error') and t.get('rows_loaded', 0) > 0]

                # Completely Failed Tables (detailed)
                if failed:
                    lines.append("=" * 60)
                    lines.append("FAILED TABLES - REQUIRES ATTENTION")
                    lines.append("=" * 60)

                    for table in failed:
                        table_name = table.get('table_name', table.get('filename', 'Unknown'))
                        filename = table.get('source_file', table.get('filename', 'Unknown'))
                        error = table.get('error', 'Unknown error')
                        error_type = table.get('error_type', '')
                        entity = table.get('entity', '')
                        source = table.get('source', '')

                        lines.append("")
                        lines.append(f"  TABLE: {table_name}")
                        lines.append(f"  Source File: {filename}")
                        if entity:
                            lines.append(f"  Entity Type: {entity}")
                        if source:
                            lines.append(f"  Source System: {source}")
                        if error_type:
                            lines.append(f"  Error Type: {error_type}")
                        lines.append(f"  Error: {error}")

                        # Additional error details
                        if table.get('missing_headers'):
                            lines.append(f"  Missing Headers: {', '.join(table['missing_headers'])}")
                        if table.get('expected_headers'):
                            lines.append(f"  Expected Headers: {', '.join(table['expected_headers'][:10])}")
                            if len(table.get('expected_headers', [])) > 10:
                                lines.append(f"                    ... and {len(table['expected_headers']) - 10} more")
                        if table.get('csv_columns'):
                            lines.append(f"  CSV Columns Found: {', '.join(table['csv_columns'][:10])}")
                            if len(table.get('csv_columns', [])) > 10:
                                lines.append(f"                     ... and {len(table['csv_columns']) - 10} more")

                        # Row-level errors (check both 'failed_rows' and 'row_errors')
                        row_errors = table.get('failed_rows') or table.get('row_errors') or []
                        if row_errors:
                            lines.append(f"  Row Errors ({len(row_errors)} errors):")
                            for i, row_err in enumerate(row_errors[:20]):  # Show first 20
                                row_num = row_err.get('row_number', row_err.get('row', i+1))
                                err_msg = row_err.get('error', str(row_err))
                                lines.append(f"    Row {row_num}: {err_msg}")
                            if len(row_errors) > 20:
                                lines.append(f"    ... and {len(row_errors) - 20} more errors")

                        # Partial success info
                        if table.get('rows_loaded', 0) > 0:
                            lines.append(f"  Rows Successfully Loaded: {table['rows_loaded']:,}")
                        if table.get('rows_failed', 0) > 0:
                            lines.append(f"  Rows Failed: {table['rows_failed']:,}")

                        lines.append("  " + "-" * 40)

                    lines.append("")

                # Partial Success Tables (some rows loaded, some failed)
                if partial:
                    lines.append("=" * 60)
                    lines.append("PARTIAL SUCCESS - SOME ROWS FAILED")
                    lines.append("=" * 60)

                    for table in partial:
                        table_name = table.get('table_name', table.get('filename', 'Unknown'))
                        filename = table.get('source_file', table.get('filename', 'Unknown'))
                        error = table.get('error', 'Unknown error')
                        rows_loaded = table.get('rows_loaded', 0)
                        failed_rows = table.get('failed_rows') or []

                        lines.append("")
                        lines.append(f"  TABLE: {table_name}")
                        lines.append(f"  Source File: {filename}")
                        lines.append(f"  Rows Loaded Successfully: {rows_loaded:,}")
                        lines.append(f"  Rows Failed: {len(failed_rows)}")
                        lines.append(f"  Status: {error}")

                        # Row-level errors
                        if failed_rows:
                            lines.append("")
                            lines.append(f"  ROW-LEVEL ERRORS:")
                            for i, row_err in enumerate(failed_rows[:20]):  # Show first 20
                                row_num = row_err.get('row_number', row_err.get('row', i+1))
                                err_msg = row_err.get('error', str(row_err))
                                lines.append(f"    Row {row_num}: {err_msg}")
                            if len(failed_rows) > 20:
                                lines.append(f"    ... and {len(failed_rows) - 20} more errors")

                        lines.append("  " + "-" * 40)

                    lines.append("")

                # Successful Tables (no errors)
                successful = [t for t in table_results if t.get('success') and not t.get('error')]
                if successful:
                    lines.append("-" * 60)
                    lines.append("SUCCESSFUL TABLES")
                    lines.append("-" * 60)

                    for table in successful:
                        table_name = table.get('table_name', table.get('filename', 'Unknown'))
                        rows = table.get('rows_loaded', 0)
                        lines.append(f"  [OK] {table_name}: {rows:,} rows")

                    lines.append("")

                break  # Only process one table results section

        # Local Procedure Results
        if data.get('ran_procedure_locally'):
            proc_result = data.get('local_procedure_result', {})
            lines.append("-" * 80)
            lines.append("STORED PROCEDURE RESULTS")
            lines.append("-" * 80)

            proc_status = proc_result.get('status', 'unknown').upper()
            lines.append(f"Status: {proc_status}")
            lines.append(f"Database: {proc_result.get('database', 'N/A')}")
            lines.append(f"Started: {proc_result.get('started_at', 'N/A')}")
            lines.append(f"Completed: {proc_result.get('completed_at', 'N/A')}")

            # Show error details
            if proc_result.get('error'):
                lines.append("")
                lines.append("ERROR:")
                lines.append(f"  {proc_result['error']}")

            if proc_result.get('error_details'):
                lines.append("")
                lines.append("ERROR DETAILS:")
                lines.append(f"  {proc_result['error_details']}")

            # Show procedure execution steps
            steps_completed = proc_result.get('steps_completed', [])
            if steps_completed:
                lines.append("")
                lines.append("Execution Steps:")
                for step in steps_completed:
                    step_name = step.get('step', 'Unknown')
                    step_time = step.get('timestamp', '')
                    test_exec = step.get('test_execution', '')
                    if test_exec:
                        lines.append(f"  [{step_time}] {step_name} (test_execution={test_exec})")
                    else:
                        lines.append(f"  [{step_time}] {step_name}")

            # Show Integration_Log entries if captured
            procedure_logs = proc_result.get('procedure_logs', [])
            if procedure_logs:
                lines.append("")
                lines.append("Integration Log Entries:")
                for log_entry in procedure_logs:
                    lines.append(f"  Job: {log_entry.get('job_name', 'N/A')}")
                    lines.append(f"  Started: {log_entry.get('started_at', 'N/A')}")
                    lines.append(f"  Finished: {log_entry.get('finished_at', 'N/A')}")
                    lines.append(f"  Status: {log_entry.get('status', 'N/A')}")
                    if log_entry.get('error_message'):
                        lines.append(f"  Error: {log_entry.get('error_message')}")

            # Show delta counts
            delta_counts = proc_result.get('delta_counts', {})
            if delta_counts:
                lines.append("")
                lines.append("Delta Table Counts:")
                total_deltas = 0
                for table_name, count in delta_counts.items():
                    if count > 0:
                        lines.append(f"  {table_name}: {count:,} records")
                        total_deltas += count
                    elif count == -1:
                        lines.append(f"  {table_name}: (error reading count)")
                lines.append(f"  TOTAL: {total_deltas:,} delta records")

            lines.append("")

        # Export Results
        if data.get('local_export_result'):
            export_result = data.get('local_export_result', {})
            lines.append("-" * 80)
            lines.append("DELTA EXPORT RESULTS")
            lines.append("-" * 80)

            export_status = export_result.get('status', 'unknown').upper()
            lines.append(f"Status: {export_status}")
            lines.append(f"Database: {export_result.get('database', 'N/A')}")
            lines.append(f"Export Folder: {export_result.get('export_folder', 'N/A')}")
            lines.append(f"Files Exported: {export_result.get('total_files', 0)}")
            lines.append(f"Total Rows: {export_result.get('total_rows', 0):,}")

            if export_result.get('instance'):
                lines.append(f"Instance: {export_result.get('instance')}")
                lines.append(f"Run Status: {export_result.get('run_status', 'N/A')}")

            # Show error if present
            if export_result.get('error'):
                lines.append("")
                lines.append("ERROR:")
                lines.append(f"  {export_result['error']}")

            # Show individual view errors
            if export_result.get('errors'):
                lines.append("")
                lines.append("View Errors/Warnings:")
                for err in export_result.get('errors', []):
                    lines.append(f"  - {err}")

            # Show exported files
            files_list = export_result.get('files_exported') or export_result.get('files', [])
            if files_list:
                lines.append("")
                lines.append("Exported Files:")
                for f in files_list:
                    if isinstance(f, dict):
                        filename = f.get('filename', 'unknown')
                        row_count = f.get('row_count', 0)
                        file_type = f.get('type', '')
                        type_suffix = f" ({file_type})" if file_type else ""
                        lines.append(f"  - {filename}: {row_count:,} rows{type_suffix}")
                    else:
                        lines.append(f"  - {f}")

            lines.append("")

        # S3 Delta Files Upload Results
        if data.get('s3_delta_upload_result'):
            s3_delta_result = data.get('s3_delta_upload_result', {})
            lines.append("-" * 80)
            lines.append("S3 DELTA FILES UPLOAD")
            lines.append("-" * 80)

            s3_delta_status = s3_delta_result.get('status', 'unknown').upper()
            lines.append(f"Status: {s3_delta_status}")
            lines.append(f"S3 Prefix: {s3_delta_result.get('s3_prefix', 'N/A')}")
            lines.append(f"Files Uploaded: {s3_delta_result.get('files_uploaded', 0)}")
            lines.append(f"Total Bytes: {s3_delta_result.get('total_bytes', 0):,}")

            if s3_delta_result.get('error'):
                lines.append(f"Error: {s3_delta_result['error']}")

            lines.append("")

        # SFTP Upload Results
        if data.get('local_sftp_upload_result'):
            upload_result = data.get('local_sftp_upload_result', {})
            lines.append("-" * 80)
            lines.append("SFTP UPLOAD RESULTS")
            lines.append("-" * 80)

            upload_status = upload_result.get('status', 'unknown').upper()
            lines.append(f"Status: {upload_status}")
            lines.append(f"Source Folder: {upload_result.get('source_folder', 'N/A')}")
            lines.append(f"Output Folder: {upload_result.get('output_folder', 'N/A')}")
            lines.append(f"Files Uploaded: {upload_result.get('files_uploaded', 0)}")
            lines.append(f"Total Bytes: {upload_result.get('total_bytes', 0):,}")

            if upload_result.get('error'):
                lines.append(f"Error: {upload_result['error']}")

            lines.append("")

        # Footer
        lines.append("=" * 80)
        lines.append("END OF REPORT")
        lines.append("=" * 80)

        return "\n".join(lines)


# ============================================
# MAIN ENTRY POINT
# ============================================

def main():
    root = tk.Tk()
    app = HaciendaApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
