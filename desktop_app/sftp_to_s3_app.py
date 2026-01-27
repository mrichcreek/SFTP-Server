"""
Hacienda SFTP to S3 Application with Cognito Authentication
A Windows desktop app that:
1. Authenticates via AWS Cognito
2. Downloads files from SFTP (via VPN) and uploads to S3
3. Runs validation and processing workflows via API Gateway

Requires: User must be connected to FortiClient VPN for SFTP downloads.
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
from datetime import datetime
from botocore.exceptions import ClientError, NoCredentialsError

# ============================================
# CONFIGURATION
# ============================================

# SQL Server Connection (for local execution - bypasses Lambda permissions)
SQL_SECRET_NAME = 'Hacienda_ERP_Test_MSSQL_text'
SQL_SECRET_REGION = 'us-east-1'

# Cognito Settings
COGNITO_REGION = "us-east-1"
COGNITO_USER_POOL_ID = "us-east-1_B9L2aprTj"
COGNITO_CLIENT_ID = "39dbtnt6f5s0li79erji1lqbps"

# API Gateway Settings
API_ENDPOINT = "https://oibtjhhyma.execute-api.us-east-1.amazonaws.com/prod"

# SFTP Settings
SFTP_HOST = "10.3.3.146"
SFTP_PORT = 22
SFTP_USER = "gprerpusr"
SFTP_PASS = "YExumikufR7g"
REMOTE_DOWNLOAD_FOLDER = "/GPR/HCM"
EXCLUDE_DIRS = ["PROCESADOS"]

# AWS Settings
AWS_REGION = "us-east-1"
S3_BUCKET = "hacienda-sftp-downloads"

# Application Settings
APP_VERSION = "2.0.0"
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "download_log.txt")

# Direct Lambda function URL for full-pipeline (bypasses API Gateway)
FULL_PIPELINE_URL = "https://5253fdqsppqvdveoyaeq6dl7ty0pmjep.lambda-url.us-east-1.on.aws"

# Color scheme
COLORS = {
    'primary': '#1a73e8',
    'primary_dark': '#1557b0',
    'primary_light': '#4285f4',
    'success': '#34a853',
    'warning': '#fbbc04',
    'error': '#ea4335',
    'bg_dark': '#202124',
    'bg_medium': '#303134',
    'bg_light': '#3c4043',
    'text_primary': '#e8eaed',
    'text_secondary': '#9aa0a6',
    'border': '#5f6368'
}


# ============================================
# COGNITO AUTHENTICATION
# ============================================

class CognitoAuth:
    """Handle AWS Cognito authentication."""

    def __init__(self):
        self.client = boto3.client('cognito-idp', region_name=COGNITO_REGION)
        self.id_token = None
        self.access_token = None
        self.refresh_token = None
        self.username = None

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

            return True, None

        except self.client.exceptions.NotAuthorizedException:
            return False, "Invalid email or password"
        except self.client.exceptions.UserNotFoundException:
            return False, "User not found"
        except self.client.exceptions.UserNotConfirmedException:
            return False, "User email not confirmed"
        except Exception as e:
            return False, str(e)

    def refresh_tokens(self):
        """Refresh the authentication tokens."""
        if not self.refresh_token:
            return False, "No refresh token available"

        try:
            response = self.client.initiate_auth(
                ClientId=COGNITO_CLIENT_ID,
                AuthFlow='REFRESH_TOKEN_AUTH',
                AuthParameters={
                    'REFRESH_TOKEN': self.refresh_token
                }
            )

            auth_result = response.get('AuthenticationResult', {})
            self.id_token = auth_result.get('IdToken')
            self.access_token = auth_result.get('AccessToken')

            return True, None

        except Exception as e:
            return False, str(e)

    def logout(self):
        """Clear authentication tokens."""
        self.id_token = None
        self.access_token = None
        self.refresh_token = None
        self.username = None

    def is_authenticated(self):
        """Check if user is authenticated."""
        return self.id_token is not None


# ============================================
# LOCAL SQL EXECUTOR (bypasses Lambda permissions)
# ============================================

class LocalSqlExecutor:
    """Execute stored procedures directly using local database connection via pyodbc."""

    def __init__(self):
        self.connection_string = None

    def get_connection_string(self):
        """Get connection string from AWS Secrets Manager."""
        if self.connection_string:
            return self.connection_string

        client = boto3.client('secretsmanager', region_name=SQL_SECRET_REGION)
        response = client.get_secret_value(SecretId=SQL_SECRET_NAME)
        self.connection_string = response.get('SecretString', '')
        return self.connection_string

    def get_available_odbc_driver(self):
        """
        Detect available SQL Server ODBC drivers on the system.
        Returns the best available driver name, or None if none found.
        """
        try:
            import pyodbc
            drivers = pyodbc.drivers()

            # Priority list of SQL Server drivers (newest/best first)
            preferred_drivers = [
                'ODBC Driver 18 for SQL Server',
                'ODBC Driver 17 for SQL Server',
                'ODBC Driver 13.1 for SQL Server',
                'ODBC Driver 13 for SQL Server',
                'ODBC Driver 11 for SQL Server',
                'SQL Server Native Client 11.0',
                'SQL Server Native Client 10.0',
                'SQL Server',  # Generic driver
            ]

            for driver in preferred_drivers:
                if driver in drivers:
                    return driver

            # If none of our preferred drivers, look for any SQL Server driver
            for driver in drivers:
                if 'sql server' in driver.lower():
                    return driver

            return None
        except Exception:
            return None

    def modify_connection_string(self, conn_str, database_override=None):
        """Modify connection string for pyodbc use, auto-detecting the ODBC driver."""
        parts = {}

        # Parse the connection string into key-value pairs
        for part in conn_str.split(';'):
            if '=' in part:
                key, value = part.split('=', 1)
                parts[key.strip().upper()] = value.strip()

        # Override database if specified
        if database_override:
            parts['DATABASE'] = database_override

        # Auto-detect and set the ODBC driver
        available_driver = self.get_available_odbc_driver()
        if available_driver:
            parts['DRIVER'] = '{' + available_driver + '}'
        elif 'DRIVER' not in parts:
            # Fall back to generic if nothing found
            parts['DRIVER'] = '{SQL Server}'

        # Rebuild connection string
        result_parts = []
        for key, value in parts.items():
            result_parts.append(f'{key}={value}')

        return ';'.join(result_parts)

    def execute_stored_procedure(self, test_mode=True, database_override=None, progress_callback=None):
        """
        Execute HCM_MAIN_INTF stored procedure directly using pyodbc.

        Args:
            test_mode: If True, passes 'Y' to @test_execution parameter
            database_override: Use 'Hacienda ERP' for production, None for test
            progress_callback: Function to call with progress updates

        Returns:
            Dict with execution results
        """
        try:
            import pyodbc
        except ImportError:
            return {
                'status': 'error',
                'error': 'pyodbc not installed. Run: pip install pyodbc'
            }

        test_flag = 'Y' if test_mode else 'N'
        execution_id = datetime.now().strftime('%Y%m%d_%H%M%S')

        result = {
            'status': 'error',
            'execution_id': execution_id,
            'test_mode': test_mode,
            'database': database_override or 'Hacienda ERP Test',
            'started_at': datetime.now().isoformat(),
            'completed_at': None,
            'steps_completed': [],
            'delta_counts': {},
            'run_status': None,
            'error': None
        }

        conn = None
        try:
            # Detect available ODBC driver first
            available_driver = self.get_available_odbc_driver()
            if progress_callback:
                if available_driver:
                    progress_callback(f"Found ODBC driver: {available_driver}")
                else:
                    progress_callback("Warning: No SQL Server ODBC driver found, using generic driver...")

            if progress_callback:
                progress_callback("Getting database credentials...")

            conn_str = self.get_connection_string()
            conn_str = self.modify_connection_string(conn_str, database_override)

            if progress_callback:
                progress_callback("Connecting to SQL Server...")

            # Connect using pyodbc with the ODBC connection string
            conn = pyodbc.connect(conn_str, timeout=30)
            conn.autocommit = True  # Stored procedures often need autocommit
            cursor = conn.cursor()

            # Set session options to match SQL Server defaults
            # This is critical for date parsing - the CSV files contain dates in
            # DD-MON-YYYY format (e.g., '22-SEP-2007') which requires us_english language
            if progress_callback:
                progress_callback("Setting session date/language options...")
            cursor.execute("SET LANGUAGE us_english")
            cursor.execute("SET DATEFORMAT mdy")

            # Clear ProcTrace for fresh logging
            if progress_callback:
                progress_callback("Clearing trace log...")
            try:
                cursor.execute("DELETE FROM dbo.ProcTrace")
            except:
                pass

            # Execute the stored procedure
            if progress_callback:
                progress_callback(f"Executing HCM_MAIN_INTF (test_mode={test_flag})...")

            try:
                cursor.execute(f"EXEC dbo.HCM_MAIN_INTF @test_execution = '{test_flag}'")

                # Consume all result sets
                while cursor.nextset():
                    pass

            except Exception as proc_error:
                result['error'] = str(proc_error)
                result['completed_at'] = datetime.now().isoformat()

                # Try to get partial results
                try:
                    result['steps_completed'] = self._get_proc_trace(cursor)
                    result['run_status'] = self._get_run_status(cursor)
                except:
                    pass

                return result

            if progress_callback:
                progress_callback("Getting execution results...")

            # Get execution results
            result['steps_completed'] = self._get_proc_trace(cursor)
            result['run_status'] = self._get_run_status(cursor)
            result['delta_counts'] = self._get_delta_counts(cursor)

            # Check status
            run_status = result['run_status']
            if run_status and run_status.get('status') == '02-Completed':
                result['status'] = 'success'
            elif run_status and run_status.get('status') == '01-InProgress':
                result['status'] = 'in_progress'
            else:
                if result['steps_completed']:
                    last_step = result['steps_completed'][0].get('step', '')
                    if 'finished' in last_step.lower():
                        result['status'] = 'success'
                    else:
                        result['status'] = 'error'
                        result['error'] = f'Procedure did not complete normally. Last step: {last_step}'
                else:
                    result['status'] = 'success'

            result['completed_at'] = datetime.now().isoformat()

        except Exception as e:
            result['error'] = str(e)
            result['completed_at'] = datetime.now().isoformat()

        finally:
            if conn:
                try:
                    conn.close()
                except:
                    pass

        return result

    def _get_proc_trace(self, cursor, limit=100):
        """Get recent entries from ProcTrace table."""
        try:
            cursor.execute(f"""
                SELECT TOP {limit} step,
                       COALESCE(timestamp, GETDATE()) as timestamp
                FROM dbo.ProcTrace
                ORDER BY COALESCE(timestamp, GETDATE()) DESC
            """)
            rows = cursor.fetchall()
            return [{'step': row[0], 'timestamp': str(row[1])} for row in rows]
        except:
            try:
                cursor.execute(f"SELECT TOP {limit} step FROM dbo.ProcTrace")
                rows = cursor.fetchall()
                return [{'step': row[0], 'timestamp': None} for row in rows]
            except:
                return []

    def _get_run_status(self, cursor):
        """Get latest execution status from RUN_INTF_STATUS table."""
        try:
            cursor.execute("""
                SELECT TOP 1 Instance, Status, DateStarted, DateCompleted
                FROM dbo.RUN_INTF_STATUS
                ORDER BY Instance DESC
            """)
            row = cursor.fetchone()
            if row:
                return {
                    'instance': row[0],
                    'status': row[1],
                    'date_started': str(row[2]) if row[2] else None,
                    'date_completed': str(row[3]) if row[3] else None
                }
        except:
            pass
        return None

    def _get_delta_counts(self, cursor):
        """Get record counts from all DELTA tables."""
        delta_tables = [
            'HCM_PERSON_ADDRESS_INTF_DELTA',
            'HCM_PERSON_ASSIGNMENT_INTF_DELTA',
            'HCM_PERSON_NAME_INTF_DELTA',
            'HCM_PERSON_NID_INTF_DELTA',
            'HCM_PERSON_SUPERVISOR_INTF_DELTA',
            'HCM_PERSON_EMAIL_INTF_DELTA',
            'HCM_EXTERNAL_IDENTIFIER_INTF_DELTA',
            'HCM_DEPARTMENT_INTF_DELTA',
            'HCM_JOBS_INTF_DELTA',
            'HCM_LOCATION_INTF_DELTA'
        ]

        counts = {}
        for table in delta_tables:
            try:
                cursor.execute(f"SELECT COUNT(*) FROM dbo.[{table}]")
                row = cursor.fetchone()
                counts[table] = row[0] if row else 0
            except:
                counts[table] = -1

        return counts


# ============================================
# API CLIENT
# ============================================

class APIClient:
    """Client for API Gateway calls."""

    def __init__(self, auth: CognitoAuth):
        self.auth = auth
        self.endpoint = API_ENDPOINT

    def _get_headers(self):
        """Get headers with authentication token."""
        return {
            'Content-Type': 'application/json',
            'Authorization': self.auth.id_token or ''
        }

    def _handle_response(self, response):
        """Handle API response and errors."""
        if response.status_code == 401:
            # Try to refresh token
            success, _ = self.auth.refresh_tokens()
            if not success:
                raise Exception("Session expired. Please log in again.")
            return None  # Caller should retry

        if not response.ok:
            try:
                error = response.json()
                raise Exception(error.get('message', f'API Error: {response.status_code}'))
            except json.JSONDecodeError:
                raise Exception(f'API Error: {response.status_code}')

        return response.json()

    def validate_files(self, prefix=''):
        """Validate file names in S3 bucket."""
        response = requests.post(
            f"{self.endpoint}/validate",
            headers=self._get_headers(),
            json={'prefix': prefix}
        )
        return self._handle_response(response)

    def check_completeness(self, prefix='', include_report=False):
        """Check file completeness."""
        response = requests.post(
            f"{self.endpoint}/completeness",
            headers=self._get_headers(),
            json={'prefix': prefix, 'include_report': include_report}
        )
        return self._handle_response(response)

    def check_duplicates(self, prefix=''):
        """Check for duplicate files."""
        response = requests.post(
            f"{self.endpoint}/duplicates",
            headers=self._get_headers(),
            json={'prefix': prefix}
        )
        return self._handle_response(response)

    def run_validation_workflow(self, load_to_sql=False):
        """Run the integrated validation workflow."""
        response = requests.post(
            f"{self.endpoint}/run-workflow",
            headers=self._get_headers(),
            json={'load_to_sql': load_to_sql}
        )
        return self._handle_response(response)

    def preview_sql_load(self, files):
        """Preview what tables would be created."""
        response = requests.post(
            f"{self.endpoint}/preview-load",
            headers=self._get_headers(),
            json={'files': files}
        )
        return self._handle_response(response)

    def load_to_sql(self, files, drop_existing=True):
        """Load files to SQL Server."""
        response = requests.post(
            f"{self.endpoint}/load-to-sql",
            headers=self._get_headers(),
            json={'files': files, 'drop_existing': drop_existing}
        )
        return self._handle_response(response)

    def run_stored_procedure(self, test_mode=True, environment='test'):
        """Run the HCM_MAIN_INTF stored procedure."""
        response = requests.post(
            f"{self.endpoint}/run-procedure",
            headers=self._get_headers(),
            json={'test_mode': test_mode, 'environment': environment}
        )
        return self._handle_response(response)

    def get_procedure_status(self, environment='test'):
        """Get the current status of stored procedure execution."""
        response = requests.get(
            f"{self.endpoint}/procedure-status?environment={environment}",
            headers=self._get_headers()
        )
        return self._handle_response(response)

    def list_files(self, job_id=None):
        """List files in S3."""
        url = f"{self.endpoint}/files"
        if job_id:
            url += f"?jobId={job_id}"
        response = requests.get(url, headers=self._get_headers())
        return self._handle_response(response)

    def run_full_pipeline(self, environment='test', test_mode=True, source_prefix='downloads/',
                          skip_sftp=False, skip_procedure=False):
        """Run the complete data processing pipeline using Lambda function URL."""
        # Use direct Lambda URL (no auth required)
        response = requests.post(
            FULL_PIPELINE_URL,
            headers={'Content-Type': 'application/json'},
            json={
                'environment': environment,
                'test_mode': test_mode,
                'source_prefix': source_prefix,
                'skip_sftp': skip_sftp,
                'skip_procedure': skip_procedure
            },
            timeout=900  # 15 minute timeout for long-running pipeline
        )
        return self._handle_response(response)

    def list_pipeline_folders(self):
        """List available pipeline folders (timestamped)."""
        response = requests.get(
            f"{self.endpoint}/pipeline-folders",
            headers=self._get_headers()
        )
        return self._handle_response(response)


# ============================================
# MAIN APPLICATION
# ============================================

class HaciendaApp:
    """Main application with login and tabbed interface."""

    def __init__(self, root):
        self.root = root
        self.root.title("Hacienda File Transfer")
        self.root.geometry("900x700")
        self.root.minsize(800, 600)
        self.root.configure(bg=COLORS['bg_dark'])

        # Authentication
        self.auth = CognitoAuth()
        self.api_client = None

        # S3 client for direct uploads
        self.s3_client = None

        # Local SQL executor (bypasses Lambda permissions)
        self.local_sql = LocalSqlExecutor()

        # State
        self.is_downloading = False

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

        # Notebook styles
        style.configure('TNotebook', background=COLORS['bg_dark'])
        style.configure('TNotebook.Tab',
            background=COLORS['bg_medium'],
            foreground=COLORS['text_primary'],
            padding=[15, 8],
            font=('Segoe UI', 10))
        style.map('TNotebook.Tab',
            background=[('selected', COLORS['primary'])],
            foreground=[('selected', 'white')])

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
        title = ttk.Label(center_frame, text="Hacienda File Transfer", style='Title.TLabel')
        title.pack(pady=(0, 5))

        subtitle = ttk.Label(center_frame, text="Secure SFTP & Data Processing", style='Subtitle.TLabel')
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
        email = self.email_entry.get().strip()
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
        """Initialize S3 client."""
        try:
            self.s3_client = boto3.client('s3', region_name=AWS_REGION)
            self.s3_client.head_bucket(Bucket=S3_BUCKET)
        except Exception:
            self.s3_client = None

    # ==========================================
    # MAIN APPLICATION
    # ==========================================

    def show_main_app(self):
        """Display the main application with tabs."""
        self.clear_window()

        # Main frame - simple layout without problematic scroll bindings
        main_frame = ttk.Frame(self.root, style='Main.TFrame')
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Header
        self.create_header(main_frame)

        # Notebook (tabs)
        self.notebook = ttk.Notebook(main_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=20, pady=(10, 20))

        # Create tabs
        self.create_full_pipeline_tab()  # New: Full Pipeline tab first
        self.create_download_tab()
        self.create_duplicates_tab()
        self.create_validate_tab()
        self.create_completeness_tab()
        self.create_workflow_tab()
        self.create_sql_load_tab()
        self.create_process_tab()

    def create_header(self, parent):
        """Create header with user info and logout."""
        header_frame = ttk.Frame(parent, style='Main.TFrame')
        header_frame.pack(fill=tk.X, padx=20, pady=15)

        # Title
        title = ttk.Label(header_frame, text="Hacienda File Transfer", style='Title.TLabel')
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

    def do_logout(self):
        """Log out and return to login screen."""
        self.auth.logout()
        self.api_client = None
        self.show_login_screen()

    # ==========================================
    # TAB: FULL PIPELINE
    # ==========================================

    def create_full_pipeline_tab(self):
        """Create the Full Pipeline tab - single button to run entire workflow."""
        frame = ttk.Frame(self.notebook, style='Card.TFrame', padding=20)
        self.notebook.add(frame, text=" Full Pipeline ")

        # Description
        desc = ttk.Label(frame, text="Run the complete data processing pipeline with a single click.\n"
            "Steps: Download → Duplicates → Validation → Schema Check → Completeness → SQL Load → Process",
            style='Info.TLabel', wraplength=700, justify=tk.LEFT)
        desc.pack(anchor=tk.W, pady=(0, 15))

        # Options frame
        options_frame = ttk.LabelFrame(frame, text=" Pipeline Options ", style='Card.TLabelframe', padding=15)
        options_frame.pack(fill=tk.X, pady=(0, 15))

        # Environment selector
        env_row = ttk.Frame(options_frame, style='Card.TFrame')
        env_row.pack(fill=tk.X, pady=5)

        ttk.Label(env_row, text="Database:", style='Info.TLabel').pack(side=tk.LEFT, padx=(0, 10))

        self.pipe_env_var = tk.StringVar(value='test')
        pipe_env_combo = ttk.Combobox(env_row, textvariable=self.pipe_env_var,
            values=['test', 'production'], state='readonly', width=25)
        pipe_env_combo.pack(side=tk.LEFT)
        pipe_env_combo.bind('<<ComboboxSelected>>', self.on_pipe_env_changed)

        self.pipe_env_label = ttk.Label(env_row, text="(Hacienda ERP Test)", style='Info.TLabel')
        self.pipe_env_label.pack(side=tk.LEFT, padx=(10, 0))

        # Production warning frame (hidden by default)
        self.pipe_prod_warning = ttk.Frame(options_frame, style='Card.TFrame')
        self.pipe_prod_warning_label = tk.Label(self.pipe_prod_warning,
            text="⚠️ WARNING: Production database - changes affect live data!",
            font=('Segoe UI', 10, 'bold'), bg=COLORS['error'], fg='white', padx=10, pady=8)
        self.pipe_prod_warning_label.pack(fill=tk.X)

        # Test mode checkbox
        test_row = ttk.Frame(options_frame, style='Card.TFrame')
        test_row.pack(fill=tk.X, pady=5)

        self.pipe_test_var = tk.BooleanVar(value=True)
        test_check = tk.Checkbutton(test_row, text="Test Mode (filter stored procedure to test SSNs only)",
            variable=self.pipe_test_var, font=('Segoe UI', 10),
            bg=COLORS['bg_medium'], fg=COLORS['text_primary'],
            selectcolor=COLORS['bg_light'], activebackground=COLORS['bg_medium'])
        test_check.pack(side=tk.LEFT)

        # Skip download checkbox
        skip_row = ttk.Frame(options_frame, style='Card.TFrame')
        skip_row.pack(fill=tk.X, pady=5)

        self.pipe_skip_download_var = tk.BooleanVar(value=True)
        skip_check = tk.Checkbutton(skip_row, text="Skip SFTP Download (use existing files in S3)",
            variable=self.pipe_skip_download_var, font=('Segoe UI', 10),
            bg=COLORS['bg_medium'], fg=COLORS['text_primary'],
            selectcolor=COLORS['bg_light'], activebackground=COLORS['bg_medium'])
        skip_check.pack(side=tk.LEFT)

        # Run procedure locally checkbox
        proc_row = ttk.Frame(options_frame, style='Card.TFrame')
        proc_row.pack(fill=tk.X, pady=5)

        self.pipe_run_proc_locally_var = tk.BooleanVar(value=True)  # Default to local
        proc_check = tk.Checkbutton(proc_row, text="Run Stored Procedure Locally (bypasses Lambda permissions)",
            variable=self.pipe_run_proc_locally_var, font=('Segoe UI', 10),
            bg=COLORS['bg_medium'], fg=COLORS['text_primary'],
            selectcolor=COLORS['bg_light'], activebackground=COLORS['bg_medium'])
        proc_check.pack(side=tk.LEFT)

        # Run button
        self.pipe_run_btn = tk.Button(frame, text="▶  Run Full Pipeline", command=self.run_full_pipeline,
            font=('Segoe UI', 14, 'bold'), bg=COLORS['success'], fg='white',
            activebackground='#2d8a43', relief=tk.FLAT, cursor='hand2',
            padx=50, pady=15)
        self.pipe_run_btn.pack(pady=20)

        # Progress frame
        progress_frame = ttk.LabelFrame(frame, text=" Pipeline Progress ", style='Card.TLabelframe', padding=15)
        progress_frame.pack(fill=tk.X, pady=(0, 15))

        # Current step label
        step_row = ttk.Frame(progress_frame, style='Card.TFrame')
        step_row.pack(fill=tk.X, pady=(0, 10))

        self.pipe_step_label = ttk.Label(step_row, text="Ready to start", style='Status.TLabel')
        self.pipe_step_label.pack(side=tk.LEFT)

        self.pipe_percent_label = ttk.Label(step_row, text="0%", style='Header.TLabel')
        self.pipe_percent_label.pack(side=tk.RIGHT)

        # Progress bar
        self.pipe_progress_var = tk.DoubleVar()
        self.pipe_progress_bar = ttk.Progressbar(progress_frame, variable=self.pipe_progress_var,
            maximum=100, style='Custom.Horizontal.TProgressbar', length=400)
        self.pipe_progress_bar.pack(fill=tk.X, pady=(0, 10))

        # Status label
        self.pipe_status_label = ttk.Label(progress_frame, text="", style='Info.TLabel')
        self.pipe_status_label.pack(anchor=tk.W)

        # Results frame (scrollable)
        results_frame = ttk.LabelFrame(frame, text=" Results ", style='Card.TLabelframe', padding=10)
        results_frame.pack(fill=tk.BOTH, expand=True)

        # Use a Text widget for results - simpler and more robust scrolling
        results_container = ttk.Frame(results_frame, style='Card.TFrame')
        results_container.pack(fill=tk.BOTH, expand=True)

        self.pipe_results_canvas = tk.Canvas(results_container, bg=COLORS['bg_medium'], highlightthickness=0, height=300)
        scrollbar = ttk.Scrollbar(results_container, orient=tk.VERTICAL, command=self.pipe_results_canvas.yview)
        self.pipe_results_inner = ttk.Frame(self.pipe_results_canvas, style='Card.TFrame')

        self.pipe_results_inner.bind('<Configure>',
            lambda e: self.pipe_results_canvas.configure(scrollregion=self.pipe_results_canvas.bbox('all')))

        self.pipe_canvas_window = self.pipe_results_canvas.create_window((0, 0), window=self.pipe_results_inner, anchor='nw')
        self.pipe_results_canvas.configure(yscrollcommand=scrollbar.set)

        # Update inner frame width when canvas resizes
        def on_canvas_configure(event):
            self.pipe_results_canvas.itemconfig(self.pipe_canvas_window, width=event.width)
        self.pipe_results_canvas.bind('<Configure>', on_canvas_configure)

        # Enable mousewheel scrolling on the results canvas
        def on_results_mousewheel(event):
            self.pipe_results_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        self.pipe_results_canvas.bind('<MouseWheel>', on_results_mousewheel)
        self.pipe_results_inner.bind('<MouseWheel>', on_results_mousewheel)

        self.pipe_results_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def on_pipe_env_changed(self, event=None):
        """Handle pipeline environment selection change."""
        env = self.pipe_env_var.get()
        if env == 'production':
            self.pipe_env_label.config(text="(Hacienda ERP)")
            self.pipe_prod_warning.pack(fill=tk.X, pady=(10, 0))
            self.pipe_run_btn.config(bg=COLORS['warning'])
        else:
            self.pipe_env_label.config(text="(Hacienda ERP Test)")
            self.pipe_prod_warning.pack_forget()
            self.pipe_run_btn.config(bg=COLORS['success'])

    def run_full_pipeline(self):
        """Run the complete data processing pipeline."""
        env = self.pipe_env_var.get()
        run_proc_locally = self.pipe_run_proc_locally_var.get()

        # Confirm if running on production
        if env == 'production':
            if not messagebox.askyesno("Confirm Production",
                "You are about to run the FULL PIPELINE on the PRODUCTION database.\n\n"
                "This will:\n"
                "• Download files from SFTP\n"
                "• Validate and load data to production SQL Server\n"
                "• Run HCM_MAIN_INTF on production data\n\n"
                "Are you absolutely sure you want to continue?"):
                return

        # Reset UI
        self.pipe_run_btn.config(state=tk.DISABLED, text="Running...")
        self.pipe_progress_var.set(0)
        self.pipe_percent_label.config(text="0%")
        self.pipe_step_label.config(text="Starting pipeline...")
        self.pipe_status_label.config(text="")

        # Clear results
        for w in self.pipe_results_inner.winfo_children():
            w.destroy()

        def task():
            try:
                # Run pipeline (skip procedure if we'll run it locally)
                result = self.api_client.run_full_pipeline(
                    environment=env,
                    test_mode=self.pipe_test_var.get(),
                    source_prefix='downloads/',
                    skip_sftp=self.pipe_skip_download_var.get(),
                    skip_procedure=run_proc_locally  # Skip in Lambda if running locally
                )

                # Parse result to check if pipeline succeeded
                data = result
                if isinstance(result.get('body'), str):
                    try:
                        data = json.loads(result['body'])
                    except:
                        data = result
                elif isinstance(result.get('body'), dict):
                    data = result['body']

                pipeline_status = data.get('status', 'unknown')

                # If pipeline succeeded (or reached SQL load) and we need to run procedure locally
                if run_proc_locally and pipeline_status == 'success':
                    self.root.after(0, lambda: self.pipe_step_label.config(
                        text="Running stored procedure locally..."))
                    self.root.after(0, lambda: self.pipe_status_label.config(
                        text="Lambda pipeline complete. Now running HCM_MAIN_INTF locally..."))

                    # Run stored procedure locally
                    database_override = 'Hacienda ERP' if env == 'production' else None
                    proc_result = self.local_sql.execute_stored_procedure(
                        test_mode=self.pipe_test_var.get(),
                        database_override=database_override,
                        progress_callback=lambda msg: self.root.after(0,
                            lambda m=msg: self.pipe_status_label.config(text=m))
                    )

                    # Add procedure result to the pipeline result
                    data['local_procedure_result'] = proc_result
                    data['ran_procedure_locally'] = True

                    # Update status if procedure failed
                    if proc_result.get('status') == 'error':
                        data['status'] = 'partial'
                        data['error'] = f"Pipeline succeeded but local procedure failed: {proc_result.get('error')}"

                self.root.after(0, self.show_pipeline_result, data, None)
            except Exception as e:
                self.root.after(0, self.show_pipeline_result, None, str(e))

        threading.Thread(target=task, daemon=True).start()

    def show_pipeline_result(self, result, error):
        """Display full pipeline results."""
        self.pipe_run_btn.config(state=tk.NORMAL, text="▶  Run Full Pipeline")

        if error:
            self.pipe_step_label.config(text="Pipeline Failed")
            self.pipe_status_label.config(text="Error occurred - see details below")
            # Use Text widget for error display (allows copy/paste and word wrap)
            error_frame = ttk.LabelFrame(self.pipe_results_inner, text=" Error Details ",
                style='Card.TLabelframe', padding=10)
            error_frame.pack(fill=tk.X, pady=10, padx=5)
            error_text = tk.Text(error_frame, height=8, wrap=tk.WORD,
                font=('Consolas', 10), bg='#fff0f0', fg=COLORS['error'],
                relief=tk.FLAT, padx=10, pady=10)
            error_text.insert('1.0', error)
            error_text.config(state=tk.DISABLED)  # Read-only but still selectable
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
        total_steps = data.get('total_steps', 0)
        steps = data.get('steps', [])
        report_url = data.get('report_url')
        error_msg = data.get('error')

        # Update progress bar to 100% if complete
        if status == 'success':
            self.pipe_progress_var.set(100)
            self.pipe_percent_label.config(text="100%")
            self.pipe_step_label.config(text="Pipeline Complete!")
        else:
            pct = int((completed_steps / total_steps) * 100) if total_steps > 0 else 0
            self.pipe_progress_var.set(pct)
            self.pipe_percent_label.config(text=f"{pct}%")
            self.pipe_step_label.config(text=f"Pipeline {status.title()}")

        # Summary
        summary_frame = ttk.Frame(self.pipe_results_inner, style='Card.TFrame')
        summary_frame.pack(fill=tk.X, pady=10, padx=5)

        status_color = COLORS['success'] if status == 'success' else COLORS['error']
        tk.Label(summary_frame, text=f"Status: {status.upper()}",
            font=('Segoe UI', 12, 'bold'), bg=COLORS['bg_medium'], fg=status_color).pack(anchor=tk.W)

        ttk.Label(summary_frame, text=f"Pipeline ID: {pipeline_id}", style='Info.TLabel').pack(anchor=tk.W)
        ttk.Label(summary_frame, text=f"Folder: {folder_name}", style='Info.TLabel').pack(anchor=tk.W)
        ttk.Label(summary_frame, text=f"Steps: {completed_steps}/{total_steps} completed",
            style='Info.TLabel').pack(anchor=tk.W)

        # Error message - use Text widget for copy/paste support
        if error_msg:
            error_frame = ttk.LabelFrame(self.pipe_results_inner, text=" Error Details ",
                style='Card.TLabelframe', padding=10)
            error_frame.pack(fill=tk.X, pady=10, padx=5)
            error_text = tk.Text(error_frame, height=6, wrap=tk.WORD,
                font=('Consolas', 10), bg='#fff0f0', fg=COLORS['error'],
                relief=tk.FLAT, padx=10, pady=10)
            error_text.insert('1.0', error_msg)
            error_text.config(state=tk.DISABLED)  # Read-only but still selectable
            error_text.pack(fill=tk.X)

        # Step details
        if steps:
            steps_frame = ttk.LabelFrame(self.pipe_results_inner, text=" Step Details ",
                style='Card.TLabelframe', padding=10)
            steps_frame.pack(fill=tk.X, pady=10, padx=5)

            for step in steps:
                step_name = step.get('step', 'Unknown')
                step_success = step.get('success', False)
                step_msg = step.get('message', '')
                step_report_key = step.get('report_key')

                icon = "✓" if step_success else "✗"
                color = COLORS['success'] if step_success else COLORS['error']

                step_row = ttk.Frame(steps_frame, style='Card.TFrame')
                step_row.pack(fill=tk.X, pady=2)

                tk.Label(step_row, text=f"{icon} {step_name}",
                    font=('Segoe UI', 10, 'bold'), bg=COLORS['bg_medium'], fg=color).pack(side=tk.LEFT)

                if step_msg:
                    ttk.Label(step_row, text=f" - {step_msg}", style='Info.TLabel').pack(side=tk.LEFT)

                # Show report key if available for this step
                if step_report_key:
                    ttk.Label(step_row, text=f" [Report: {step_report_key.split('/')[-1]}]",
                        style='Info.TLabel').pack(side=tk.LEFT, padx=(10, 0))

        # Show local procedure results if ran locally
        local_proc_result = data.get('local_procedure_result')
        if local_proc_result:
            proc_frame = ttk.LabelFrame(self.pipe_results_inner, text=" Local Stored Procedure Execution ",
                style='Card.TLabelframe', padding=10)
            proc_frame.pack(fill=tk.X, pady=10, padx=5)

            proc_status = local_proc_result.get('status', 'unknown')
            proc_icon = "✓" if proc_status == 'success' else "✗"
            proc_color = COLORS['success'] if proc_status == 'success' else COLORS['error']

            tk.Label(proc_frame, text=f"{proc_icon} HCM_MAIN_INTF (Local): {proc_status.upper()}",
                font=('Segoe UI', 11, 'bold'), bg=COLORS['bg_medium'], fg=proc_color).pack(anchor=tk.W)

            ttk.Label(proc_frame, text=f"Database: {local_proc_result.get('database', 'Unknown')}",
                style='Info.TLabel').pack(anchor=tk.W)
            ttk.Label(proc_frame, text=f"Test Mode: {'Yes' if local_proc_result.get('test_mode') else 'No'}",
                style='Info.TLabel').pack(anchor=tk.W)

            # Show delta counts
            delta_counts = local_proc_result.get('delta_counts', {})
            if delta_counts:
                total_delta = sum(v for v in delta_counts.values() if v >= 0)
                ttk.Label(proc_frame, text=f"Delta Records Created: {total_delta:,}",
                    style='Info.TLabel').pack(anchor=tk.W)

            # Show error if any
            proc_error = local_proc_result.get('error')
            if proc_error:
                error_text = tk.Text(proc_frame, height=4, wrap=tk.WORD,
                    font=('Consolas', 9), bg='#fff0f0', fg=COLORS['error'],
                    relief=tk.FLAT, padx=8, pady=8)
                error_text.insert('1.0', str(proc_error))
                error_text.config(state=tk.DISABLED)
                error_text.pack(fill=tk.X, pady=(5, 0))

        # Download report button - show prominently when there's an error
        if report_url:
            report_frame = ttk.LabelFrame(self.pipe_results_inner, text=" Download Report ",
                style='Card.TLabelframe', padding=15)
            report_frame.pack(fill=tk.X, pady=15, padx=5)

            # Add description of what the report contains
            report_desc = "Click below to download the detailed validation report."
            if status == 'failed':
                report_desc = "The pipeline failed. Download the report below for details on which files failed validation and why."

            tk.Label(report_frame, text=report_desc,
                font=('Segoe UI', 10), bg=COLORS['bg_medium'], fg=COLORS['text_primary'],
                wraplength=500, justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 10))

            download_btn = tk.Button(report_frame, text="📥 Download Validation Report",
                command=lambda url=report_url, pid=pipeline_id: self.download_report(url, f"pipeline_report_{pid}.txt"),
                font=('Segoe UI', 12, 'bold'), bg=COLORS['primary'], fg='white',
                activebackground=COLORS['primary_dark'], relief=tk.FLAT, cursor='hand2',
                padx=30, pady=12)
            download_btn.pack(pady=5)

            # Also show the S3 report key for reference
            ttk.Label(report_frame, text=f"Report location: {folder_name}/2_Validation_Reports/",
                style='Info.TLabel').pack(anchor=tk.W, pady=(10, 0))

    # ==========================================
    # TAB: DOWNLOAD (SFTP)
    # ==========================================

    def create_download_tab(self):
        """Create the SFTP Download tab."""
        frame = ttk.Frame(self.notebook, style='Card.TFrame', padding=20)
        self.notebook.add(frame, text=" Download ")

        # Description
        desc = ttk.Label(frame, text="Download files from SFTP server and upload to S3.\n"
            "Requires FortiClient VPN connection.", style='Info.TLabel',
            wraplength=600, justify=tk.LEFT)
        desc.pack(anchor=tk.W, pady=(0, 15))

        # Status indicators
        status_frame = ttk.Frame(frame, style='Card.TFrame')
        status_frame.pack(fill=tk.X, pady=(0, 15))

        # VPN Status
        vpn_row = ttk.Frame(status_frame, style='Card.TFrame')
        vpn_row.pack(fill=tk.X, pady=3)
        self.vpn_indicator = tk.Canvas(vpn_row, width=12, height=12,
            bg=COLORS['bg_medium'], highlightthickness=0)
        self.vpn_indicator.pack(side=tk.LEFT, padx=(0, 10))
        self.vpn_indicator.create_oval(2, 2, 10, 10, fill=COLORS['warning'], outline='')
        ttk.Label(vpn_row, text="VPN Connection Required", style='Info.TLabel').pack(side=tk.LEFT)

        # S3 Status
        s3_row = ttk.Frame(status_frame, style='Card.TFrame')
        s3_row.pack(fill=tk.X, pady=3)
        self.s3_indicator = tk.Canvas(s3_row, width=12, height=12,
            bg=COLORS['bg_medium'], highlightthickness=0)
        self.s3_indicator.pack(side=tk.LEFT, padx=(0, 10))
        s3_color = COLORS['success'] if self.s3_client else COLORS['error']
        self.s3_indicator.create_oval(2, 2, 10, 10, fill=s3_color, outline='')
        s3_text = "S3: Connected" if self.s3_client else "S3: Not connected"
        self.s3_status_label = ttk.Label(s3_row, text=s3_text, style='Info.TLabel')
        self.s3_status_label.pack(side=tk.LEFT)

        # Download button
        self.download_btn = tk.Button(frame, text="Download Files", command=self.start_download,
            font=('Segoe UI', 12, 'bold'), bg=COLORS['primary'], fg='white',
            activebackground=COLORS['primary_dark'], relief=tk.FLAT, cursor='hand2',
            padx=30, pady=10)
        self.download_btn.pack(pady=15)

        # Progress frame
        progress_frame = ttk.LabelFrame(frame, text=" Progress ", style='Card.TLabelframe', padding=15)
        progress_frame.pack(fill=tk.X, pady=(0, 15))

        status_row = ttk.Frame(progress_frame, style='Card.TFrame')
        status_row.pack(fill=tk.X, pady=(0, 10))
        self.dl_status_label = ttk.Label(status_row, text="Ready", style='Status.TLabel')
        self.dl_status_label.pack(side=tk.LEFT)
        self.dl_percent_label = ttk.Label(status_row, text="0%", style='Header.TLabel')
        self.dl_percent_label.pack(side=tk.RIGHT)

        self.dl_progress_var = tk.DoubleVar()
        self.dl_progress_bar = ttk.Progressbar(progress_frame, variable=self.dl_progress_var,
            maximum=100, style='Custom.Horizontal.TProgressbar')
        self.dl_progress_bar.pack(fill=tk.X, pady=(0, 10))

        self.dl_file_label = ttk.Label(progress_frame, text="Files: 0 / 0", style='Info.TLabel')
        self.dl_file_label.pack(anchor=tk.W)

        # Log area
        log_frame = ttk.LabelFrame(frame, text=" Activity Log ", style='Card.TLabelframe', padding=10)
        log_frame.pack(fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(log_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.dl_log = tk.Text(log_frame, height=10, font=('Consolas', 9),
            bg=COLORS['bg_light'], fg=COLORS['text_primary'],
            insertbackground=COLORS['text_primary'], relief=tk.FLAT,
            padx=10, pady=10, yscrollcommand=scrollbar.set, state=tk.DISABLED)
        self.dl_log.pack(fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.dl_log.yview)

        self.dl_log.tag_configure('info', foreground=COLORS['text_primary'])
        self.dl_log.tag_configure('success', foreground=COLORS['success'])
        self.dl_log.tag_configure('warning', foreground=COLORS['warning'])
        self.dl_log.tag_configure('error', foreground=COLORS['error'])

    def log_download(self, message, level='info'):
        """Add message to download log."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.dl_log.config(state=tk.NORMAL)
        self.dl_log.insert(tk.END, f"[{timestamp}] {message}\n", level)
        self.dl_log.see(tk.END)
        self.dl_log.config(state=tk.DISABLED)

    def start_download(self):
        """Start SFTP download."""
        if self.is_downloading:
            return
        if not self.s3_client:
            messagebox.showerror("Error", "S3 not connected. Check AWS credentials.")
            return

        self.is_downloading = True
        self.download_btn.config(state=tk.DISABLED)

        self.dl_log.config(state=tk.NORMAL)
        self.dl_log.delete(1.0, tk.END)
        self.dl_log.config(state=tk.DISABLED)

        threading.Thread(target=self.perform_download, daemon=True).start()

    def perform_download(self):
        """Perform SFTP download and S3 upload."""
        ssh_client = None
        sftp = None

        try:
            self.root.after(0, lambda: self.dl_status_label.config(text="Connecting to SFTP..."))
            self.root.after(0, lambda: self.log_download(f"Connecting to {SFTP_HOST}..."))

            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh_client.connect(hostname=SFTP_HOST, port=SFTP_PORT,
                username=SFTP_USER, password=SFTP_PASS, timeout=30)
            sftp = ssh_client.open_sftp()

            self.root.after(0, lambda: self.vpn_indicator.delete('all'))
            self.root.after(0, lambda: self.vpn_indicator.create_oval(2, 2, 10, 10, fill=COLORS['success'], outline=''))
            self.root.after(0, lambda: self.log_download("Connected to SFTP", 'success'))

            # List files
            self.root.after(0, lambda: self.dl_status_label.config(text="Scanning files..."))
            files = self.list_remote_files(sftp, REMOTE_DOWNLOAD_FOLDER)
            total = len(files)

            if total == 0:
                self.root.after(0, lambda: self.log_download("No files found", 'warning'))
                self.root.after(0, self.download_complete, True, "No files to download")
                return

            self.root.after(0, lambda: self.log_download(f"Found {total} files"))

            job_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            s3_prefix = f"downloads/{job_id}"

            downloaded = 0
            for file_info in files:
                progress = int((downloaded / total) * 100)
                self.root.after(0, lambda p=progress, d=downloaded, t=total, f=file_info['filename']:
                    self.update_dl_progress(p, "Transferring...", d, t, f))

                s3_key = f"{s3_prefix}/{file_info['filename']}"
                with sftp.open(file_info['path'], 'rb') as remote_file:
                    self.s3_client.upload_fileobj(remote_file, S3_BUCKET, s3_key)

                downloaded += 1
                self.root.after(0, lambda f=file_info['filename']: self.log_download(f"Transferred: {f}", 'success'))

            self.root.after(0, lambda: self.update_dl_progress(100, "Complete!", downloaded, total, ""))
            self.root.after(0, lambda: self.log_download(f"All {downloaded} files transferred", 'success'))
            self.root.after(0, self.download_complete, True, None)

        except Exception as e:
            self.root.after(0, lambda: self.vpn_indicator.delete('all'))
            self.root.after(0, lambda: self.vpn_indicator.create_oval(2, 2, 10, 10, fill=COLORS['error'], outline=''))
            self.root.after(0, lambda: self.log_download(f"Error: {e}", 'error'))
            self.root.after(0, self.download_complete, False, str(e))

        finally:
            if sftp:
                sftp.close()
            if ssh_client:
                ssh_client.close()

    def list_remote_files(self, sftp, remote_dir):
        """List files in remote directory."""
        files = []
        try:
            entries = sftp.listdir_attr(remote_dir)
        except:
            return files

        for entry in entries:
            path = f"{remote_dir}/{entry.filename}"
            if stat.S_ISREG(entry.st_mode):
                files.append({'path': path, 'filename': entry.filename, 'size': entry.st_size})
            elif stat.S_ISDIR(entry.st_mode) and entry.filename not in EXCLUDE_DIRS:
                files.extend(self.list_remote_files(sftp, path))
        return files

    def update_dl_progress(self, progress, status, done, total, current_file):
        """Update download progress UI."""
        self.dl_progress_var.set(progress)
        self.dl_percent_label.config(text=f"{progress}%")
        self.dl_status_label.config(text=status)
        self.dl_file_label.config(text=f"Files: {done} / {total}")

    def download_complete(self, success, message):
        """Handle download completion."""
        self.is_downloading = False
        self.download_btn.config(state=tk.NORMAL)

        if success:
            if message:
                messagebox.showinfo("Download", message)
            else:
                messagebox.showinfo("Complete", "All files transferred successfully!")
        else:
            messagebox.showerror("Error", f"Download failed: {message}")

    # ==========================================
    # TAB: FIND DUPLICATES
    # ==========================================

    def create_duplicates_tab(self):
        """Create the Find Duplicates tab."""
        frame = ttk.Frame(self.notebook, style='Card.TFrame', padding=20)
        self.notebook.add(frame, text=" Duplicates ")

        desc = ttk.Label(frame, text="Detect duplicate files in S3 and automatically move older versions\n"
            "to the superseded folder.", style='Info.TLabel', wraplength=600)
        desc.pack(anchor=tk.W, pady=(0, 15))

        self.dup_btn = tk.Button(frame, text="Find Duplicates", command=self.run_duplicates,
            font=('Segoe UI', 12, 'bold'), bg=COLORS['primary'], fg='white',
            activebackground=COLORS['primary_dark'], relief=tk.FLAT, cursor='hand2',
            padx=30, pady=10)
        self.dup_btn.pack(pady=15)

        # Results area
        self.dup_results_frame = ttk.Frame(frame, style='Card.TFrame')
        self.dup_results_frame.pack(fill=tk.BOTH, expand=True)

    def run_duplicates(self):
        """Run duplicate detection."""
        self.dup_btn.config(state=tk.DISABLED, text="Checking...")

        def task():
            try:
                result = self.api_client.check_duplicates()
                self.root.after(0, self.show_duplicates_result, result, None)
            except Exception as e:
                self.root.after(0, self.show_duplicates_result, None, str(e))

        threading.Thread(target=task, daemon=True).start()

    def show_duplicates_result(self, result, error):
        """Display duplicate detection results."""
        self.dup_btn.config(state=tk.NORMAL, text="Find Duplicates")

        for w in self.dup_results_frame.winfo_children():
            w.destroy()

        if error:
            ttk.Label(self.dup_results_frame, text=f"Error: {error}", style='Error.TLabel').pack()
            return

        # Parse body if it's a string
        data = result
        if isinstance(result.get('body'), str):
            try:
                data = json.loads(result['body'])
            except:
                data = result
        elif isinstance(result.get('body'), dict):
            data = result['body']

        # Stats
        stats_frame = ttk.Frame(self.dup_results_frame, style='Card.TFrame')
        stats_frame.pack(fill=tk.X, pady=10)

        total = data.get('total_files', 0)
        unique = data.get('unique_files', 0)
        exact_dups = data.get('total_exact_duplicates', 0)
        superseded = data.get('total_superseded', 0)
        storage_waste = data.get('storage_waste_mb', 0)

        ttk.Label(stats_frame, text=f"Total Files: {total}", style='Status.TLabel').pack(side=tk.LEFT, padx=10)
        ttk.Label(stats_frame, text=f"Unique: {unique}", style='Status.TLabel').pack(side=tk.LEFT, padx=10)
        ttk.Label(stats_frame, text=f"Exact Duplicates: {exact_dups}", style='Status.TLabel').pack(side=tk.LEFT, padx=10)
        ttk.Label(stats_frame, text=f"Superseded: {superseded}", style='Status.TLabel').pack(side=tk.LEFT, padx=10)

        if exact_dups == 0 and superseded == 0:
            ttk.Label(self.dup_results_frame, text="No duplicates found!", style='Success.TLabel').pack(pady=20)
        else:
            # Show storage waste
            if storage_waste > 0:
                ttk.Label(self.dup_results_frame, text=f"Storage waste: {storage_waste} MB",
                    style='Warning.TLabel').pack(pady=10)

    # ==========================================
    # TAB: VALIDATE NAMES
    # ==========================================

    def create_validate_tab(self):
        """Create the Validate Names tab."""
        frame = ttk.Frame(self.notebook, style='Card.TFrame', padding=20)
        self.notebook.add(frame, text=" Validate ")

        desc = ttk.Label(frame, text="Validate that file names match the expected pattern\n"
            "and extract metadata (entity, period, date).", style='Info.TLabel', wraplength=600)
        desc.pack(anchor=tk.W, pady=(0, 15))

        self.val_btn = tk.Button(frame, text="Validate Files", command=self.run_validate,
            font=('Segoe UI', 12, 'bold'), bg=COLORS['primary'], fg='white',
            activebackground=COLORS['primary_dark'], relief=tk.FLAT, cursor='hand2',
            padx=30, pady=10)
        self.val_btn.pack(pady=15)

        self.val_results_frame = ttk.Frame(frame, style='Card.TFrame')
        self.val_results_frame.pack(fill=tk.BOTH, expand=True)

    def run_validate(self):
        """Run file validation."""
        self.val_btn.config(state=tk.DISABLED, text="Validating...")

        def task():
            try:
                result = self.api_client.validate_files()
                self.root.after(0, self.show_validate_result, result, None)
            except Exception as e:
                self.root.after(0, self.show_validate_result, None, str(e))

        threading.Thread(target=task, daemon=True).start()

    def show_validate_result(self, result, error):
        """Display validation results."""
        self.val_btn.config(state=tk.NORMAL, text="Validate Files")

        for w in self.val_results_frame.winfo_children():
            w.destroy()

        if error:
            ttk.Label(self.val_results_frame, text=f"Error: {error}", style='Error.TLabel').pack()
            return

        # Parse body if it's a string
        data = result
        if isinstance(result.get('body'), str):
            try:
                data = json.loads(result['body'])
            except:
                data = result
        elif isinstance(result.get('body'), dict):
            data = result['body']

        total = data.get('total_files', 0)
        valid = data.get('valid_count', 0)
        invalid = data.get('invalid_count', 0)
        correctable = data.get('correctable_count', 0)

        stats_frame = ttk.Frame(self.val_results_frame, style='Card.TFrame')
        stats_frame.pack(fill=tk.X, pady=10)

        ttk.Label(stats_frame, text=f"Total: {total}", style='Status.TLabel').pack(side=tk.LEFT, padx=10)
        ttk.Label(stats_frame, text=f"Valid: {valid}", style='Success.TLabel').pack(side=tk.LEFT, padx=10)
        ttk.Label(stats_frame, text=f"Invalid: {invalid}",
            style='Error.TLabel' if invalid > 0 else 'Status.TLabel').pack(side=tk.LEFT, padx=10)
        if correctable > 0:
            ttk.Label(stats_frame, text=f"Correctable: {correctable}", style='Warning.TLabel').pack(side=tk.LEFT, padx=10)

        if invalid == 0:
            ttk.Label(self.val_results_frame, text="All files valid!", style='Success.TLabel').pack(pady=20)
        else:
            # Show invalid files
            invalid_files = data.get('invalid_files', [])
            if invalid_files:
                ttk.Label(self.val_results_frame, text="Invalid Files:", style='Status.TLabel').pack(anchor=tk.W, pady=(10, 5))
                for f in invalid_files[:10]:  # Show first 10
                    fname = f.get('file_name', 'Unknown')
                    err = f.get('error_message', '')
                    ttk.Label(self.val_results_frame, text=f"  • {fname}: {err}",
                        style='Info.TLabel', wraplength=500).pack(anchor=tk.W)
                if len(invalid_files) > 10:
                    ttk.Label(self.val_results_frame, text=f"  ... and {len(invalid_files) - 10} more",
                        style='Info.TLabel').pack(anchor=tk.W)

    # ==========================================
    # TAB: CHECK COMPLETENESS
    # ==========================================

    def create_completeness_tab(self):
        """Create the Check Completeness tab."""
        frame = ttk.Frame(self.notebook, style='Card.TFrame', padding=20)
        self.notebook.add(frame, text=" Completeness ")

        desc = ttk.Label(frame, text="Check that all expected entities and periods are present\n"
            "for each date in the dataset.", style='Info.TLabel', wraplength=600)
        desc.pack(anchor=tk.W, pady=(0, 15))

        self.comp_btn = tk.Button(frame, text="Check Completeness", command=self.run_completeness,
            font=('Segoe UI', 12, 'bold'), bg=COLORS['primary'], fg='white',
            activebackground=COLORS['primary_dark'], relief=tk.FLAT, cursor='hand2',
            padx=30, pady=10)
        self.comp_btn.pack(pady=15)

        self.comp_results_frame = ttk.Frame(frame, style='Card.TFrame')
        self.comp_results_frame.pack(fill=tk.BOTH, expand=True)

    def run_completeness(self):
        """Run completeness check."""
        self.comp_btn.config(state=tk.DISABLED, text="Checking...")

        def task():
            try:
                result = self.api_client.check_completeness()
                self.root.after(0, self.show_completeness_result, result, None)
            except Exception as e:
                self.root.after(0, self.show_completeness_result, None, str(e))

        threading.Thread(target=task, daemon=True).start()

    def show_completeness_result(self, result, error):
        """Display completeness results."""
        self.comp_btn.config(state=tk.NORMAL, text="Check Completeness")

        for w in self.comp_results_frame.winfo_children():
            w.destroy()

        if error:
            ttk.Label(self.comp_results_frame, text=f"Error: {error}", style='Error.TLabel').pack()
            return

        # Parse body if it's a string
        data = result
        if isinstance(result.get('body'), str):
            try:
                data = json.loads(result['body'])
            except:
                data = result
        elif isinstance(result.get('body'), dict):
            data = result['body']

        total_files = data.get('total_files', 0)
        entities_found = data.get('entities_found', 0)
        complete_sets = data.get('complete_sets', 0)
        incomplete_sets = data.get('incomplete_sets', 0)
        completeness_pct = data.get('completeness_percentage', 0)

        stats_frame = ttk.Frame(self.comp_results_frame, style='Card.TFrame')
        stats_frame.pack(fill=tk.X, pady=10)

        ttk.Label(stats_frame, text=f"Files: {total_files}", style='Status.TLabel').pack(side=tk.LEFT, padx=10)
        ttk.Label(stats_frame, text=f"Entities: {entities_found}", style='Status.TLabel').pack(side=tk.LEFT, padx=10)
        ttk.Label(stats_frame, text=f"Complete: {complete_sets}", style='Success.TLabel').pack(side=tk.LEFT, padx=10)
        ttk.Label(stats_frame, text=f"Incomplete: {incomplete_sets}",
            style='Error.TLabel' if incomplete_sets > 0 else 'Status.TLabel').pack(side=tk.LEFT, padx=10)

        # Completeness percentage
        pct_style = 'Success.TLabel' if completeness_pct >= 100 else 'Warning.TLabel'
        ttk.Label(self.comp_results_frame, text=f"Completeness: {completeness_pct:.1f}%",
            style=pct_style).pack(pady=10)

        if incomplete_sets == 0:
            ttk.Label(self.comp_results_frame, text="All file sets complete!", style='Success.TLabel').pack(pady=10)
        else:
            # Show incomplete sets
            file_sets = data.get('file_sets', [])
            incomplete = [fs for fs in file_sets if not fs.get('is_complete', True)]
            if incomplete:
                ttk.Label(self.comp_results_frame, text="Incomplete Sets:", style='Status.TLabel').pack(anchor=tk.W, pady=(10, 5))
                for fs in incomplete[:5]:  # Show first 5
                    entity = fs.get('entity', 'Unknown')
                    date = fs.get('date', '')
                    missing = ', '.join(fs.get('missing_sources', []))
                    ttk.Label(self.comp_results_frame,
                        text=f"  • {entity} ({date}): Missing {missing}",
                        style='Info.TLabel', wraplength=500).pack(anchor=tk.W)
                if len(incomplete) > 5:
                    ttk.Label(self.comp_results_frame, text=f"  ... and {len(incomplete) - 5} more",
                        style='Info.TLabel').pack(anchor=tk.W)

    # ==========================================
    # TAB: FULL WORKFLOW
    # ==========================================

    def create_workflow_tab(self):
        """Create the Full Workflow tab."""
        frame = ttk.Frame(self.notebook, style='Card.TFrame', padding=20)
        self.notebook.add(frame, text=" Workflow ")

        desc = ttk.Label(frame, text="Run the complete validation workflow:\n"
            "1. Check duplicates (auto-move) 2. Validate names 3. Check completeness\n"
            "4. Generate report if errors 5. Optionally load to SQL", style='Info.TLabel', wraplength=600)
        desc.pack(anchor=tk.W, pady=(0, 15))

        # Load to SQL checkbox
        self.wf_load_sql_var = tk.BooleanVar(value=False)
        sql_check = tk.Checkbutton(frame, text="Load to SQL if all checks pass",
            variable=self.wf_load_sql_var, font=('Segoe UI', 10),
            bg=COLORS['bg_medium'], fg=COLORS['text_primary'],
            selectcolor=COLORS['bg_light'], activebackground=COLORS['bg_medium'])
        sql_check.pack(pady=(0, 15))

        self.wf_btn = tk.Button(frame, text="Run Workflow", command=self.run_workflow,
            font=('Segoe UI', 12, 'bold'), bg=COLORS['primary'], fg='white',
            activebackground=COLORS['primary_dark'], relief=tk.FLAT, cursor='hand2',
            padx=30, pady=10)
        self.wf_btn.pack(pady=15)

        self.wf_results_frame = ttk.Frame(frame, style='Card.TFrame')
        self.wf_results_frame.pack(fill=tk.BOTH, expand=True)

    def run_workflow(self):
        """Run full validation workflow."""
        self.wf_btn.config(state=tk.DISABLED, text="Running...")

        def task():
            try:
                result = self.api_client.run_validation_workflow(self.wf_load_sql_var.get())
                self.root.after(0, self.show_workflow_result, result, None)
            except Exception as e:
                self.root.after(0, self.show_workflow_result, None, str(e))

        threading.Thread(target=task, daemon=True).start()

    def show_workflow_result(self, result, error):
        """Display workflow results."""
        self.wf_btn.config(state=tk.NORMAL, text="Run Workflow")

        for w in self.wf_results_frame.winfo_children():
            w.destroy()

        if error:
            ttk.Label(self.wf_results_frame, text=f"Error: {error}", style='Error.TLabel').pack()
            return

        data = result.get('body', result) if isinstance(result.get('body'), dict) else result

        status = data.get('status', 'unknown')
        steps = data.get('steps', {})
        has_errors = data.get('has_errors', False)
        report_url = data.get('report_url')
        report_name = data.get('report_name', 'validation_report.csv')

        # Status
        status_style = 'Success.TLabel' if status == 'success' and not has_errors else 'Error.TLabel'
        status_text = 'COMPLETED WITH ERRORS' if has_errors else status.upper()
        ttk.Label(self.wf_results_frame, text=f"Status: {status_text}", style=status_style).pack(pady=10)

        # Steps
        for step_name, step_data in steps.items():
            step_frame = ttk.Frame(self.wf_results_frame, style='Card.TFrame')
            step_frame.pack(fill=tk.X, pady=5)
            step_status = step_data.get('status', 'unknown')
            icon = "✓" if step_status == 'success' else "✗" if step_status == 'error' else "○"

            # Add more details for each step
            details = []
            if 'total_files' in step_data:
                details.append(f"Files: {step_data['total_files']}")
            if 'duplicates_found' in step_data:
                details.append(f"Duplicates: {step_data['duplicates_found']}")
            if 'valid_files' in step_data:
                details.append(f"Valid: {step_data['valid_files']}")
            if 'invalid_files' in step_data:
                details.append(f"Invalid: {step_data['invalid_files']}")
            if 'complete_sets' in step_data:
                details.append(f"Complete: {step_data['complete_sets']}")
            if 'incomplete_sets' in step_data:
                details.append(f"Incomplete: {step_data['incomplete_sets']}")

            detail_text = f" ({', '.join(details)})" if details else ""
            ttk.Label(step_frame, text=f"{icon} {step_name}: {step_status}{detail_text}",
                style='Status.TLabel').pack(anchor=tk.W)

        # Download Report Button (if report URL exists)
        if report_url:
            report_frame = ttk.Frame(self.wf_results_frame, style='Card.TFrame')
            report_frame.pack(fill=tk.X, pady=15)

            download_btn = tk.Button(report_frame, text="📥 Download Error Report",
                command=lambda: self.download_report(report_url, report_name),
                font=('Segoe UI', 11, 'bold'), bg=COLORS['warning'], fg='white',
                activebackground='#c9302c', relief=tk.FLAT, cursor='hand2',
                padx=20, pady=10)
            download_btn.pack()

            ttk.Label(report_frame, text=f"Report: {report_name}",
                style='Info.TLabel').pack(pady=(5, 0))

    def download_report(self, url, filename):
        """Download a report file from URL."""
        try:
            # Open the presigned URL in default browser to download
            webbrowser.open(url)
            messagebox.showinfo("Download Started",
                f"Report download started in your browser.\n\nFilename: {filename}")
        except Exception as e:
            messagebox.showerror("Download Error", f"Failed to download report: {e}")

    # ==========================================
    # TAB: LOAD TO SQL
    # ==========================================

    def create_sql_load_tab(self):
        """Create the Load to SQL tab."""
        frame = ttk.Frame(self.notebook, style='Card.TFrame', padding=20)
        self.notebook.add(frame, text=" SQL Load ")

        desc = ttk.Label(frame, text="Load validated CSV files to SQL Server staging tables.\n"
            "Files will be mapped to table names based on entity.", style='Info.TLabel', wraplength=600)
        desc.pack(anchor=tk.W, pady=(0, 15))

        btn_frame = ttk.Frame(frame, style='Card.TFrame')
        btn_frame.pack(pady=15)

        self.sql_preview_btn = tk.Button(btn_frame, text="Preview Tables", command=self.run_sql_preview,
            font=('Segoe UI', 11), bg=COLORS['bg_light'], fg=COLORS['text_primary'],
            activebackground=COLORS['bg_medium'], relief=tk.FLAT, cursor='hand2',
            padx=20, pady=8)
        self.sql_preview_btn.pack(side=tk.LEFT, padx=5)

        self.sql_load_btn = tk.Button(btn_frame, text="Load to SQL", command=self.run_sql_load,
            font=('Segoe UI', 12, 'bold'), bg=COLORS['primary'], fg='white',
            activebackground=COLORS['primary_dark'], relief=tk.FLAT, cursor='hand2',
            padx=30, pady=10)
        self.sql_load_btn.pack(side=tk.LEFT, padx=5)

        self.sql_results_frame = ttk.Frame(frame, style='Card.TFrame')
        self.sql_results_frame.pack(fill=tk.BOTH, expand=True)

    def run_sql_preview(self):
        """Preview SQL tables."""
        self.sql_preview_btn.config(state=tk.DISABLED, text="Loading...")

        def task():
            try:
                files = self.api_client.list_files()
                file_list = files.get('files', []) if isinstance(files, dict) else []
                result = self.api_client.preview_sql_load(file_list)
                self.root.after(0, self.show_sql_preview, result, None)
            except Exception as e:
                self.root.after(0, self.show_sql_preview, None, str(e))

        threading.Thread(target=task, daemon=True).start()

    def show_sql_preview(self, result, error):
        """Display SQL preview."""
        self.sql_preview_btn.config(state=tk.NORMAL, text="Preview Tables")

        for w in self.sql_results_frame.winfo_children():
            w.destroy()

        if error:
            ttk.Label(self.sql_results_frame, text=f"Error: {error}", style='Error.TLabel').pack()
            return

        data = result.get('body', result) if isinstance(result.get('body'), dict) else result
        tables = data.get('tables', [])

        ttk.Label(self.sql_results_frame, text=f"Tables to create: {len(tables)}", style='Status.TLabel').pack(pady=10)

        for table in tables[:10]:  # Show first 10
            ttk.Label(self.sql_results_frame, text=f"  - {table.get('table_name', 'Unknown')}",
                style='Info.TLabel').pack(anchor=tk.W)

    def run_sql_load(self):
        """Load to SQL Server."""
        self.sql_load_btn.config(state=tk.DISABLED, text="Loading...")

        def task():
            try:
                files = self.api_client.list_files()
                file_list = files.get('files', []) if isinstance(files, dict) else []
                result = self.api_client.load_to_sql(file_list)
                self.root.after(0, self.show_sql_load_result, result, None)
            except Exception as e:
                self.root.after(0, self.show_sql_load_result, None, str(e))

        threading.Thread(target=task, daemon=True).start()

    def show_sql_load_result(self, result, error):
        """Display SQL load results."""
        self.sql_load_btn.config(state=tk.NORMAL, text="Load to SQL")

        for w in self.sql_results_frame.winfo_children():
            w.destroy()

        if error:
            ttk.Label(self.sql_results_frame, text=f"Error: {error}", style='Error.TLabel').pack()
            return

        data = result.get('body', result) if isinstance(result.get('body'), dict) else result

        status = data.get('status', 'unknown')
        loaded = data.get('tables_loaded', 0)
        total_rows = data.get('total_rows', 0)

        status_style = 'Success.TLabel' if status == 'success' else 'Error.TLabel'
        ttk.Label(self.sql_results_frame, text=f"Status: {status.upper()}", style=status_style).pack(pady=10)
        ttk.Label(self.sql_results_frame, text=f"Tables loaded: {loaded}", style='Status.TLabel').pack()
        ttk.Label(self.sql_results_frame, text=f"Total rows: {total_rows:,}", style='Status.TLabel').pack()

    # ==========================================
    # TAB: PROCESS DATA
    # ==========================================

    def create_process_tab(self):
        """Create the Process Data tab."""
        frame = ttk.Frame(self.notebook, style='Card.TFrame', padding=20)
        self.notebook.add(frame, text=" Process ")

        desc = ttk.Label(frame, text="Execute the HCM_MAIN_INTF stored procedure to process\n"
            "loaded CSV data and generate delta records for Oracle.", style='Info.TLabel', wraplength=600)
        desc.pack(anchor=tk.W, pady=(0, 15))

        # Environment selector
        env_frame = ttk.Frame(frame, style='Card.TFrame')
        env_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(env_frame, text="Database:", style='Info.TLabel').pack(side=tk.LEFT, padx=(0, 10))

        self.proc_env_var = tk.StringVar(value='test')
        env_combo = ttk.Combobox(env_frame, textvariable=self.proc_env_var,
            values=['test', 'production'], state='readonly', width=25)
        env_combo.pack(side=tk.LEFT)
        env_combo.bind('<<ComboboxSelected>>', self.on_env_changed)

        self.env_label = ttk.Label(env_frame, text="(Hacienda ERP Test)", style='Info.TLabel')
        self.env_label.pack(side=tk.LEFT, padx=(10, 0))

        # Production warning
        self.prod_warning_frame = ttk.Frame(frame, style='Card.TFrame')
        self.prod_warning_label = tk.Label(self.prod_warning_frame,
            text="WARNING: Production database selected - changes affect live data!",
            font=('Segoe UI', 10, 'bold'), bg=COLORS['error'], fg='white', padx=10, pady=8)
        self.prod_warning_label.pack(fill=tk.X)

        # Test mode checkbox
        self.proc_test_var = tk.BooleanVar(value=True)
        test_check = tk.Checkbutton(frame, text="Test Mode (filter to test SSNs only)",
            variable=self.proc_test_var, font=('Segoe UI', 10),
            bg=COLORS['bg_medium'], fg=COLORS['text_primary'],
            selectcolor=COLORS['bg_light'], activebackground=COLORS['bg_medium'])
        test_check.pack(pady=(10, 15))

        btn_frame = ttk.Frame(frame, style='Card.TFrame')
        btn_frame.pack(pady=15)

        self.proc_status_btn = tk.Button(btn_frame, text="Check Status", command=self.run_proc_status,
            font=('Segoe UI', 11), bg=COLORS['bg_light'], fg=COLORS['text_primary'],
            activebackground=COLORS['bg_medium'], relief=tk.FLAT, cursor='hand2',
            padx=20, pady=8)
        self.proc_status_btn.pack(side=tk.LEFT, padx=5)

        self.proc_run_btn = tk.Button(btn_frame, text="Run via Lambda", command=self.run_procedure,
            font=('Segoe UI', 11), bg=COLORS['bg_light'], fg=COLORS['text_primary'],
            activebackground=COLORS['bg_medium'], relief=tk.FLAT, cursor='hand2',
            padx=20, pady=8)
        self.proc_run_btn.pack(side=tk.LEFT, padx=5)

        # NEW: Run Locally button (bypasses Lambda permissions)
        self.proc_local_btn = tk.Button(btn_frame, text="▶ Run Locally", command=self.run_procedure_locally,
            font=('Segoe UI', 12, 'bold'), bg=COLORS['success'], fg='white',
            activebackground='#2d8a43', relief=tk.FLAT, cursor='hand2',
            padx=30, pady=10)
        self.proc_local_btn.pack(side=tk.LEFT, padx=5)

        # Info about local execution
        local_info = ttk.Label(frame, text="💡 'Run Locally' executes the procedure directly from this machine,\n"
            "    bypassing Lambda user permissions. Use this if Lambda fails with permission errors.",
            style='Info.TLabel', wraplength=600)
        local_info.pack(anchor=tk.W, pady=(0, 10))

        # Progress/status for local execution
        self.proc_progress_label = ttk.Label(frame, text="", style='Status.TLabel')
        self.proc_progress_label.pack(anchor=tk.W)

        self.proc_results_frame = ttk.Frame(frame, style='Card.TFrame')
        self.proc_results_frame.pack(fill=tk.BOTH, expand=True)

    def on_env_changed(self, event=None):
        """Handle environment selection change."""
        env = self.proc_env_var.get()
        if env == 'production':
            self.env_label.config(text="(Hacienda ERP)")
            self.prod_warning_frame.pack(fill=tk.X, pady=(0, 10))
            self.proc_run_btn.config(bg=COLORS['error'])
        else:
            self.env_label.config(text="(Hacienda ERP Test)")
            self.prod_warning_frame.pack_forget()
            self.proc_run_btn.config(bg=COLORS['primary'])

    def run_proc_status(self):
        """Check procedure status."""
        self.proc_status_btn.config(state=tk.DISABLED, text="Checking...")

        def task():
            try:
                result = self.api_client.get_procedure_status(self.proc_env_var.get())
                self.root.after(0, self.show_proc_result, result, None)
            except Exception as e:
                self.root.after(0, self.show_proc_result, None, str(e))

        threading.Thread(target=task, daemon=True).start()

    def run_procedure(self):
        """Run stored procedure via Lambda."""
        env = self.proc_env_var.get()

        # Confirm if running on production
        if env == 'production':
            if not messagebox.askyesno("Confirm Production",
                "You are about to run the stored procedure on the PRODUCTION database.\n\n"
                "This will modify live data. Are you sure you want to continue?"):
                return

        self.proc_run_btn.config(state=tk.DISABLED, text="Running...")

        def task():
            try:
                result = self.api_client.run_stored_procedure(self.proc_test_var.get(), env)
                self.root.after(0, self.show_proc_result, result, None)
            except Exception as e:
                self.root.after(0, self.show_proc_result, None, str(e))

        threading.Thread(target=task, daemon=True).start()

    def run_procedure_locally(self):
        """Run stored procedure directly from this machine (bypasses Lambda permissions)."""
        env = self.proc_env_var.get()

        # Confirm if running on production
        if env == 'production':
            if not messagebox.askyesno("Confirm Production",
                "You are about to run the stored procedure LOCALLY on the PRODUCTION database.\n\n"
                "This will modify live data. Are you sure you want to continue?"):
                return

        self.proc_local_btn.config(state=tk.DISABLED, text="Running...")
        self.proc_progress_label.config(text="Starting local execution...")

        # Clear previous results
        for w in self.proc_results_frame.winfo_children():
            w.destroy()

        database_override = 'Hacienda ERP' if env == 'production' else None

        def progress_callback(message):
            self.root.after(0, lambda: self.proc_progress_label.config(text=message))

        def task():
            try:
                result = self.local_sql.execute_stored_procedure(
                    test_mode=self.proc_test_var.get(),
                    database_override=database_override,
                    progress_callback=progress_callback
                )
                self.root.after(0, self.show_local_proc_result, result)
            except Exception as e:
                self.root.after(0, self.show_local_proc_result, {'status': 'error', 'error': str(e)})

        threading.Thread(target=task, daemon=True).start()

    def show_local_proc_result(self, result):
        """Display local procedure execution results."""
        self.proc_local_btn.config(state=tk.NORMAL, text="▶ Run Locally")
        self.proc_progress_label.config(text="")

        for w in self.proc_results_frame.winfo_children():
            w.destroy()

        status = result.get('status', 'unknown')
        error = result.get('error')
        run_status = result.get('run_status', {})
        steps = result.get('steps_completed', [])
        delta_counts = result.get('delta_counts', {})
        database = result.get('database', 'Unknown')

        # Header
        header_frame = ttk.Frame(self.proc_results_frame, style='Card.TFrame')
        header_frame.pack(fill=tk.X, pady=(0, 10))

        tk.Label(header_frame, text="LOCAL EXECUTION RESULTS",
            font=('Segoe UI', 11, 'bold'), bg=COLORS['bg_medium'], fg=COLORS['text_primary']).pack(anchor=tk.W)
        ttk.Label(header_frame, text=f"Database: {database}", style='Info.TLabel').pack(anchor=tk.W)

        # Status
        status_style = 'Success.TLabel' if status == 'success' else 'Error.TLabel'
        ttk.Label(self.proc_results_frame, text=f"Status: {status.upper()}", style=status_style).pack(pady=5)

        # Error message
        if error:
            error_frame = ttk.LabelFrame(self.proc_results_frame, text=" Error ", style='Card.TLabelframe', padding=10)
            error_frame.pack(fill=tk.X, pady=5)
            error_text = tk.Text(error_frame, height=4, wrap=tk.WORD,
                font=('Consolas', 9), bg='#fff0f0', fg=COLORS['error'],
                relief=tk.FLAT, padx=10, pady=5)
            error_text.insert('1.0', error)
            error_text.config(state=tk.DISABLED)
            error_text.pack(fill=tk.X)

        # Run status
        if run_status:
            ttk.Label(self.proc_results_frame,
                text=f"Instance: {run_status.get('instance', 'N/A')} - {run_status.get('status', 'N/A')}",
                style='Status.TLabel').pack()

        # Delta counts
        if delta_counts:
            delta_frame = ttk.LabelFrame(self.proc_results_frame, text=" Delta Record Counts ",
                style='Card.TLabelframe', padding=10)
            delta_frame.pack(fill=tk.X, pady=10)

            total = 0
            for table, count in sorted(delta_counts.items()):
                if count >= 0:
                    total += count
                    short_name = table.replace('HCM_', '').replace('_INTF_DELTA', '')
                    ttk.Label(delta_frame, text=f"  {short_name}: {count:,}",
                        style='Info.TLabel').pack(anchor=tk.W)
            ttk.Label(delta_frame, text=f"  TOTAL: {total:,}", style='Header.TLabel').pack(anchor=tk.W, pady=(5, 0))

        # Steps completed
        if steps:
            steps_frame = ttk.LabelFrame(self.proc_results_frame, text=f" Steps Completed ({len(steps)}) ",
                style='Card.TLabelframe', padding=10)
            steps_frame.pack(fill=tk.X, pady=10)

            # Show last 10 steps in chronological order
            for step in reversed(steps[:10]):
                step_text = step.get('step', 'Unknown')
                ttk.Label(steps_frame, text=f"  ✓ {step_text}", style='Info.TLabel').pack(anchor=tk.W)

            if len(steps) > 10:
                ttk.Label(steps_frame, text=f"  ... and {len(steps) - 10} more steps",
                    style='Info.TLabel').pack(anchor=tk.W)

    def show_proc_result(self, result, error):
        """Display procedure results."""
        self.proc_status_btn.config(state=tk.NORMAL, text="Check Status")
        self.proc_run_btn.config(state=tk.NORMAL, text="Run Procedure")

        for w in self.proc_results_frame.winfo_children():
            w.destroy()

        if error:
            ttk.Label(self.proc_results_frame, text=f"Error: {error}", style='Error.TLabel').pack()
            return

        data = result.get('body', result) if isinstance(result.get('body'), dict) else result

        status = data.get('status', 'unknown')
        run_status = data.get('run_status', {})
        steps = data.get('steps_completed', [])
        delta_counts = data.get('delta_counts', {})

        # Status
        status_style = 'Success.TLabel' if status == 'success' else 'Error.TLabel'
        ttk.Label(self.proc_results_frame, text=f"Status: {status.upper()}", style=status_style).pack(pady=10)

        if run_status:
            ttk.Label(self.proc_results_frame,
                text=f"Instance: {run_status.get('instance', 'N/A')} - {run_status.get('status', 'N/A')}",
                style='Status.TLabel').pack()

        # Delta counts
        if delta_counts:
            ttk.Label(self.proc_results_frame, text="Delta Record Counts:", style='Header.TLabel').pack(pady=(15, 5))
            total = 0
            for table, count in delta_counts.items():
                if count >= 0:
                    total += count
                    short_name = table.replace('HCM_', '').replace('_INTF_DELTA', '')
                    ttk.Label(self.proc_results_frame, text=f"  {short_name}: {count:,}",
                        style='Info.TLabel').pack(anchor=tk.W)
            ttk.Label(self.proc_results_frame, text=f"  TOTAL: {total:,}", style='Status.TLabel').pack(anchor=tk.W)

        # Steps
        if steps:
            ttk.Label(self.proc_results_frame, text=f"Steps completed: {len(steps)}", style='Info.TLabel').pack(pady=(15, 0))


# ============================================
# MAIN ENTRY POINT
# ============================================

def main():
    root = tk.Tk()

    # Center window
    window_width = 900
    window_height = 700
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    x = (screen_width - window_width) // 2
    y = (screen_height - window_height) // 2
    root.geometry(f"{window_width}x{window_height}+{x}+{y}")

    app = HaciendaApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
