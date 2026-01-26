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
from datetime import datetime
from botocore.exceptions import ClientError, NoCredentialsError

# ============================================
# CONFIGURATION
# ============================================

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

        # Main frame
        main_frame = ttk.Frame(self.root, style='Main.TFrame')
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Header
        self.create_header(main_frame)

        # Notebook (tabs)
        self.notebook = ttk.Notebook(main_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=20, pady=(10, 20))

        # Create tabs
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

        data = result.get('body', result) if isinstance(result.get('body'), dict) else result

        # Stats
        stats_frame = ttk.Frame(self.dup_results_frame, style='Card.TFrame')
        stats_frame.pack(fill=tk.X, pady=10)

        total = data.get('total_files', 0)
        dups = data.get('duplicate_groups', 0)
        moved = data.get('files_moved', 0)

        ttk.Label(stats_frame, text=f"Total Files: {total}", style='Status.TLabel').pack(side=tk.LEFT, padx=10)
        ttk.Label(stats_frame, text=f"Duplicate Groups: {dups}", style='Status.TLabel').pack(side=tk.LEFT, padx=10)
        ttk.Label(stats_frame, text=f"Files Moved: {moved}", style='Status.TLabel').pack(side=tk.LEFT, padx=10)

        if dups == 0:
            ttk.Label(self.dup_results_frame, text="No duplicates found!", style='Success.TLabel').pack(pady=20)

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

        data = result.get('body', result) if isinstance(result.get('body'), dict) else result

        total = data.get('total_files', 0)
        valid = data.get('valid_count', 0)
        invalid = data.get('invalid_count', 0)

        stats_frame = ttk.Frame(self.val_results_frame, style='Card.TFrame')
        stats_frame.pack(fill=tk.X, pady=10)

        ttk.Label(stats_frame, text=f"Total: {total}", style='Status.TLabel').pack(side=tk.LEFT, padx=10)
        ttk.Label(stats_frame, text=f"Valid: {valid}", style='Success.TLabel').pack(side=tk.LEFT, padx=10)
        ttk.Label(stats_frame, text=f"Invalid: {invalid}",
            style='Error.TLabel' if invalid > 0 else 'Status.TLabel').pack(side=tk.LEFT, padx=10)

        if invalid == 0:
            ttk.Label(self.val_results_frame, text="All files valid!", style='Success.TLabel').pack(pady=20)

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

        data = result.get('body', result) if isinstance(result.get('body'), dict) else result

        is_complete = data.get('is_complete', False)
        total_dates = data.get('total_dates', 0)
        complete_dates = data.get('complete_dates', 0)
        missing = data.get('missing_count', 0)

        stats_frame = ttk.Frame(self.comp_results_frame, style='Card.TFrame')
        stats_frame.pack(fill=tk.X, pady=10)

        ttk.Label(stats_frame, text=f"Dates: {total_dates}", style='Status.TLabel').pack(side=tk.LEFT, padx=10)
        ttk.Label(stats_frame, text=f"Complete: {complete_dates}", style='Status.TLabel').pack(side=tk.LEFT, padx=10)
        ttk.Label(stats_frame, text=f"Missing Files: {missing}",
            style='Error.TLabel' if missing > 0 else 'Status.TLabel').pack(side=tk.LEFT, padx=10)

        if is_complete:
            ttk.Label(self.comp_results_frame, text="All files present!", style='Success.TLabel').pack(pady=20)

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

        # Status
        status_style = 'Success.TLabel' if status == 'success' else 'Error.TLabel'
        ttk.Label(self.wf_results_frame, text=f"Status: {status.upper()}", style=status_style).pack(pady=10)

        # Steps
        for step_name, step_data in steps.items():
            step_frame = ttk.Frame(self.wf_results_frame, style='Card.TFrame')
            step_frame.pack(fill=tk.X, pady=5)
            step_status = step_data.get('status', 'unknown')
            icon = "[OK]" if step_status == 'success' else "[!]"
            ttk.Label(step_frame, text=f"{icon} {step_name}: {step_status}", style='Status.TLabel').pack(anchor=tk.W)

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

        self.proc_run_btn = tk.Button(btn_frame, text="Run Procedure", command=self.run_procedure,
            font=('Segoe UI', 12, 'bold'), bg=COLORS['primary'], fg='white',
            activebackground=COLORS['primary_dark'], relief=tk.FLAT, cursor='hand2',
            padx=30, pady=10)
        self.proc_run_btn.pack(side=tk.LEFT, padx=5)

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
        """Run stored procedure."""
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
