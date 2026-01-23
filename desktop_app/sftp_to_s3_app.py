"""
Hacienda SFTP to S3 Download Application
A Windows desktop app that downloads files from SFTP and uploads to S3.
Requires: User must be connected to FortiClient VPN before running.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import paramiko
import boto3
import os
import stat
from datetime import datetime
from botocore.exceptions import ClientError, NoCredentialsError

# ============================================
# CONFIGURATION
# ============================================

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
AWS_ACCESS_KEY = ""  # Empty = use AWS CLI credentials
AWS_SECRET_KEY = ""  # Empty = use AWS CLI credentials

# Application Settings
APP_VERSION = "1.0.0"
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
# APPLICATION CODE
# ============================================

class SFTPtoS3App:
    def __init__(self, root):
        self.root = root
        self.root.title("Hacienda File Transfer")
        self.root.geometry("700x600")
        self.root.minsize(600, 500)
        self.root.configure(bg=COLORS['bg_dark'])

        # Try to set icon (will skip if not available)
        try:
            self.root.iconbitmap('icon.ico')
        except:
            pass

        # Variables
        self.is_downloading = False
        self.current_attempt = 0
        self.max_attempts = 2
        self.total_bytes = 0
        self.transferred_bytes = 0

        # Configure styles
        self.configure_styles()

        # Setup UI
        self.setup_ui()

        # Initialize S3 client
        self.init_s3_client()

    def configure_styles(self):
        """Configure ttk styles for modern dark theme."""
        style = ttk.Style()

        # Use clam as base theme
        style.theme_use('clam')

        # Configure frame styles
        style.configure('Main.TFrame', background=COLORS['bg_dark'])
        style.configure('Card.TFrame', background=COLORS['bg_medium'])

        # Configure label styles
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

        # Configure button styles
        style.configure('Primary.TButton',
            background=COLORS['primary'],
            foreground='white',
            font=('Segoe UI', 12, 'bold'),
            padding=(30, 15))

        style.map('Primary.TButton',
            background=[('active', COLORS['primary_dark']), ('disabled', COLORS['bg_light'])],
            foreground=[('disabled', COLORS['text_secondary'])])

        # Configure progress bar
        style.configure('Custom.Horizontal.TProgressbar',
            background=COLORS['primary'],
            troughcolor=COLORS['bg_light'],
            bordercolor=COLORS['bg_medium'],
            lightcolor=COLORS['primary'],
            darkcolor=COLORS['primary'])

        # Configure labelframe
        style.configure('Card.TLabelframe',
            background=COLORS['bg_medium'],
            foreground=COLORS['text_primary'],
            bordercolor=COLORS['border'])

        style.configure('Card.TLabelframe.Label',
            background=COLORS['bg_medium'],
            foreground=COLORS['text_primary'],
            font=('Segoe UI', 11, 'bold'))

    def setup_ui(self):
        """Create the professional user interface."""
        # Main container
        main_frame = ttk.Frame(self.root, style='Main.TFrame')
        main_frame.pack(fill=tk.BOTH, expand=True, padx=30, pady=20)

        # Header section
        self.create_header(main_frame)

        # Status card
        self.create_status_card(main_frame)

        # Action button
        self.create_action_section(main_frame)

        # Progress card
        self.create_progress_card(main_frame)

        # Log card
        self.create_log_card(main_frame)

        # Footer
        self.create_footer(main_frame)

    def create_header(self, parent):
        """Create header section with title and subtitle."""
        header_frame = ttk.Frame(parent, style='Main.TFrame')
        header_frame.pack(fill=tk.X, pady=(0, 20))

        # Title
        title = ttk.Label(
            header_frame,
            text="Hacienda File Transfer",
            style='Title.TLabel'
        )
        title.pack(anchor=tk.W)

        # Subtitle
        subtitle = ttk.Label(
            header_frame,
            text="Secure SFTP to S3 file synchronization",
            style='Subtitle.TLabel'
        )
        subtitle.pack(anchor=tk.W, pady=(5, 0))

    def create_status_card(self, parent):
        """Create connection status indicators."""
        status_frame = ttk.Frame(parent, style='Card.TFrame')
        status_frame.pack(fill=tk.X, pady=(0, 15))
        status_frame.configure(padding=15)

        # VPN Status
        vpn_frame = ttk.Frame(status_frame, style='Card.TFrame')
        vpn_frame.pack(fill=tk.X, pady=(0, 8))

        self.vpn_indicator = tk.Canvas(vpn_frame, width=12, height=12,
            bg=COLORS['bg_medium'], highlightthickness=0)
        self.vpn_indicator.pack(side=tk.LEFT, padx=(0, 10))
        self.vpn_indicator.create_oval(2, 2, 10, 10, fill=COLORS['warning'], outline='')

        vpn_label = ttk.Label(vpn_frame, text="VPN Connection Required", style='Info.TLabel')
        vpn_label.pack(side=tk.LEFT)

        # S3 Status
        s3_frame = ttk.Frame(status_frame, style='Card.TFrame')
        s3_frame.pack(fill=tk.X)

        self.s3_indicator = tk.Canvas(s3_frame, width=12, height=12,
            bg=COLORS['bg_medium'], highlightthickness=0)
        self.s3_indicator.pack(side=tk.LEFT, padx=(0, 10))
        self.s3_indicator.create_oval(2, 2, 10, 10, fill=COLORS['text_secondary'], outline='')

        self.s3_status_label = ttk.Label(s3_frame, text="S3: Checking...", style='Info.TLabel')
        self.s3_status_label.pack(side=tk.LEFT)

    def create_action_section(self, parent):
        """Create the main action button."""
        action_frame = ttk.Frame(parent, style='Main.TFrame')
        action_frame.pack(fill=tk.X, pady=(0, 20))

        self.download_btn = tk.Button(
            action_frame,
            text="Download Files",
            command=self.start_download,
            font=('Segoe UI', 14, 'bold'),
            bg=COLORS['primary'],
            fg='white',
            activebackground=COLORS['primary_dark'],
            activeforeground='white',
            relief=tk.FLAT,
            cursor='hand2',
            padx=40,
            pady=15
        )
        self.download_btn.pack(expand=True)

        # Bind hover effects
        self.download_btn.bind('<Enter>', lambda e: self.download_btn.configure(bg=COLORS['primary_dark']))
        self.download_btn.bind('<Leave>', lambda e: self.download_btn.configure(bg=COLORS['primary']) if self.download_btn['state'] != 'disabled' else None)

    def create_progress_card(self, parent):
        """Create progress tracking section."""
        self.progress_frame = ttk.LabelFrame(
            parent,
            text=" Progress ",
            style='Card.TLabelframe',
            padding=15
        )
        self.progress_frame.pack(fill=tk.X, pady=(0, 15))

        # Status row
        status_row = ttk.Frame(self.progress_frame, style='Card.TFrame')
        status_row.pack(fill=tk.X, pady=(0, 10))

        self.status_label = ttk.Label(
            status_row,
            text="Ready to download",
            style='Status.TLabel'
        )
        self.status_label.pack(side=tk.LEFT)

        self.progress_percent = ttk.Label(
            status_row,
            text="0%",
            style='Header.TLabel'
        )
        self.progress_percent.pack(side=tk.RIGHT)

        # Progress bar
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(
            self.progress_frame,
            variable=self.progress_var,
            maximum=100,
            mode='determinate',
            style='Custom.Horizontal.TProgressbar',
            length=400
        )
        self.progress_bar.pack(fill=tk.X, pady=(0, 10))

        # File counter row
        counter_row = ttk.Frame(self.progress_frame, style='Card.TFrame')
        counter_row.pack(fill=tk.X)

        self.file_counter = ttk.Label(
            counter_row,
            text="Files: 0 / 0",
            style='Info.TLabel'
        )
        self.file_counter.pack(side=tk.LEFT)

        self.current_file_label = ttk.Label(
            counter_row,
            text="",
            style='Info.TLabel'
        )
        self.current_file_label.pack(side=tk.RIGHT)

    def create_log_card(self, parent):
        """Create log output section."""
        log_frame = ttk.LabelFrame(
            parent,
            text=" Activity Log ",
            style='Card.TLabelframe',
            padding=10
        )
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 15))

        # Log text with scrollbar
        log_container = ttk.Frame(log_frame, style='Card.TFrame')
        log_container.pack(fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(log_container)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.log_text = tk.Text(
            log_container,
            height=8,
            font=('Consolas', 9),
            bg=COLORS['bg_light'],
            fg=COLORS['text_primary'],
            insertbackground=COLORS['text_primary'],
            selectbackground=COLORS['primary'],
            relief=tk.FLAT,
            padx=10,
            pady=10,
            yscrollcommand=scrollbar.set,
            state=tk.DISABLED
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.log_text.yview)

        # Configure log text tags for colored output
        self.log_text.tag_configure('info', foreground=COLORS['text_primary'])
        self.log_text.tag_configure('success', foreground=COLORS['success'])
        self.log_text.tag_configure('warning', foreground=COLORS['warning'])
        self.log_text.tag_configure('error', foreground=COLORS['error'])

    def create_footer(self, parent):
        """Create footer with version info."""
        footer_frame = ttk.Frame(parent, style='Main.TFrame')
        footer_frame.pack(fill=tk.X)

        version_label = ttk.Label(
            footer_frame,
            text=f"Version {APP_VERSION}",
            style='Info.TLabel'
        )
        version_label.pack(side=tk.LEFT)

        bucket_label = ttk.Label(
            footer_frame,
            text=f"Target: s3://{S3_BUCKET}",
            style='Info.TLabel'
        )
        bucket_label.pack(side=tk.RIGHT)

    def init_s3_client(self):
        """Initialize S3 client with credentials."""
        try:
            if AWS_ACCESS_KEY and AWS_SECRET_KEY:
                self.s3_client = boto3.client(
                    's3',
                    region_name=AWS_REGION,
                    aws_access_key_id=AWS_ACCESS_KEY,
                    aws_secret_access_key=AWS_SECRET_KEY
                )
            else:
                self.s3_client = boto3.client('s3', region_name=AWS_REGION)

            # Test connection
            self.s3_client.head_bucket(Bucket=S3_BUCKET)
            self.update_s3_status(True, "S3: Connected")
            self.log("S3 connection established", 'success')

        except NoCredentialsError:
            self.s3_client = None
            self.update_s3_status(False, "S3: No credentials found")
            self.log("AWS credentials not found. Please configure AWS CLI.", 'error')

        except ClientError as e:
            self.s3_client = None
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            self.update_s3_status(False, f"S3: Error ({error_code})")
            self.log(f"S3 connection failed: {e}", 'error')

        except Exception as e:
            self.s3_client = None
            self.update_s3_status(False, "S3: Connection failed")
            self.log(f"S3 initialization error: {e}", 'error')

    def update_s3_status(self, connected, message):
        """Update S3 connection status indicator."""
        color = COLORS['success'] if connected else COLORS['error']
        self.s3_indicator.delete('all')
        self.s3_indicator.create_oval(2, 2, 10, 10, fill=color, outline='')
        self.s3_status_label.config(text=message)

    def log(self, message, level='info'):
        """Add message to log display and file."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {message}"

        # Update UI log with color
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, log_entry + "\n", level)
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

        # Write to file
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{level.upper()}] {message}\n")
        except:
            pass

    def update_progress(self, progress, status, files_done=0, total_files=0, current_file=""):
        """Update progress bar and status."""
        self.progress_var.set(progress)
        self.progress_percent.config(text=f"{int(progress)}%")
        self.status_label.config(text=status)
        self.file_counter.config(text=f"Files: {files_done} / {total_files}")

        if current_file:
            # Truncate long filenames
            display_name = current_file if len(current_file) <= 30 else f"...{current_file[-27:]}"
            self.current_file_label.config(text=display_name)
        else:
            self.current_file_label.config(text="")

        self.root.update_idletasks()

    def start_download(self):
        """Start the download process in a separate thread."""
        if self.is_downloading:
            return

        if not self.s3_client:
            messagebox.showerror(
                "Connection Error",
                "Cannot connect to S3. Please ensure AWS CLI is configured with valid credentials.\n\n"
                "Run 'aws configure' in Command Prompt to set up credentials."
            )
            return

        self.is_downloading = True
        self.download_btn.config(state=tk.DISABLED, bg=COLORS['bg_light'])
        self.current_attempt = 0

        # Update VPN indicator to show checking
        self.vpn_indicator.delete('all')
        self.vpn_indicator.create_oval(2, 2, 10, 10, fill=COLORS['warning'], outline='')

        # Clear log
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)

        # Start download thread
        thread = threading.Thread(target=self.download_with_retry, daemon=True)
        thread.start()

    def download_with_retry(self):
        """Execute download with retry logic."""
        while self.current_attempt < self.max_attempts:
            self.current_attempt += 1
            self.log(f"Starting download (attempt {self.current_attempt}/{self.max_attempts})")

            success, error = self.perform_download()

            if success:
                self.root.after(0, self.download_complete, True, None)
                return

            if self.current_attempt < self.max_attempts:
                self.log(f"Attempt {self.current_attempt} failed: {error}", 'warning')
                self.log("Retrying in 3 seconds...", 'warning')
                self.root.after(0, self.update_progress, 0, "Retrying...", 0, 0)
                import time
                time.sleep(3)
            else:
                self.root.after(0, self.download_complete, False, error)

    def perform_download(self):
        """Perform the actual SFTP download and S3 upload."""
        ssh_client = None
        sftp = None

        try:
            # Connect to SFTP
            self.root.after(0, self.update_progress, 5, "Connecting to SFTP server...", 0, 0)
            self.log(f"Connecting to SFTP {SFTP_HOST}:{SFTP_PORT}")

            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh_client.connect(
                hostname=SFTP_HOST,
                port=SFTP_PORT,
                username=SFTP_USER,
                password=SFTP_PASS,
                timeout=30
            )
            sftp = ssh_client.open_sftp()

            # Update VPN indicator to green
            self.root.after(0, lambda: self.vpn_indicator.delete('all'))
            self.root.after(0, lambda: self.vpn_indicator.create_oval(2, 2, 10, 10, fill=COLORS['success'], outline=''))

            self.log("Connected to SFTP server", 'success')

            # List files
            self.root.after(0, self.update_progress, 10, "Scanning for files...", 0, 0)
            files = self.list_remote_files(sftp, REMOTE_DOWNLOAD_FOLDER)
            total_files = len(files)

            if total_files == 0:
                self.log("No files found to download", 'warning')
                return True, None

            self.log(f"Found {total_files} files to download")

            # Generate job ID for S3 prefix
            job_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            s3_prefix = f"downloads/{job_id}"

            # Download and upload each file
            downloaded = 0
            failed_files = []
            verification_errors = []

            for file_info in files:
                try:
                    progress = 10 + int((downloaded / total_files) * 80)
                    self.root.after(
                        0, self.update_progress, progress,
                        "Transferring files...", downloaded, total_files, file_info['filename']
                    )

                    result = self.download_and_upload(sftp, file_info, s3_prefix)

                    if result['verified']:
                        downloaded += 1
                        self.log(f"Transferred: {file_info['filename']} ({self.format_size(file_info['size'])})", 'success')
                    else:
                        verification_errors.append(f"{file_info['filename']}: Size mismatch")
                        self.log(f"Verification failed: {file_info['filename']}", 'error')

                except Exception as e:
                    failed_files.append({'filename': file_info['filename'], 'error': str(e)})
                    self.log(f"Failed: {file_info['filename']} - {e}", 'error')

            # Verification phase
            self.root.after(0, self.update_progress, 95, "Verifying transfers...", downloaded, total_files)

            if failed_files or verification_errors:
                error_msg = f"{len(failed_files)} failed, {len(verification_errors)} verification errors"
                return False, error_msg

            self.root.after(0, self.update_progress, 100, "Complete!", downloaded, total_files)
            self.log(f"Successfully transferred {downloaded} files", 'success')
            self.log(f"Location: s3://{S3_BUCKET}/{s3_prefix}/", 'info')

            return True, None

        except Exception as e:
            # Update VPN indicator to red on connection failure
            if "connect" in str(e).lower() or "timeout" in str(e).lower():
                self.root.after(0, lambda: self.vpn_indicator.delete('all'))
                self.root.after(0, lambda: self.vpn_indicator.create_oval(2, 2, 10, 10, fill=COLORS['error'], outline=''))
            return False, str(e)

        finally:
            if sftp:
                sftp.close()
            if ssh_client:
                ssh_client.close()

    def list_remote_files(self, sftp, remote_dir):
        """Recursively list all files in remote directory."""
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
            elif stat.S_ISDIR(entry.st_mode) and entry.filename not in EXCLUDE_DIRS:
                files.extend(self.list_remote_files(sftp, remote_path))

        return files

    def download_and_upload(self, sftp, file_info, s3_prefix):
        """Download file from SFTP and upload to S3."""
        s3_key = f"{s3_prefix}/{file_info['filename']}"

        with sftp.open(file_info['path'], 'rb') as remote_file:
            self.s3_client.upload_fileobj(
                remote_file,
                S3_BUCKET,
                s3_key,
                ExtraArgs={
                    'Metadata': {
                        'source_path': file_info['path'],
                        'download_time': datetime.utcnow().isoformat()
                    }
                }
            )

        response = self.s3_client.head_object(Bucket=S3_BUCKET, Key=s3_key)
        uploaded_size = response['ContentLength']

        return {
            'filename': file_info['filename'],
            's3_key': s3_key,
            'source_size': file_info['size'],
            'uploaded_size': uploaded_size,
            'verified': uploaded_size == file_info['size']
        }

    def format_size(self, size_bytes):
        """Format bytes to human readable string."""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} TB"

    def download_complete(self, success, error):
        """Handle download completion."""
        self.is_downloading = False
        self.download_btn.config(state=tk.NORMAL, bg=COLORS['primary'])

        if success:
            self.status_label.config(text="Transfer complete!")
            messagebox.showinfo(
                "Transfer Complete",
                "All files have been successfully transferred to S3.\n\n"
                "Files have been verified for integrity."
            )
        else:
            self.status_label.config(text="Transfer failed")
            retry = messagebox.askretrycancel(
                "Transfer Failed",
                f"Transfer failed after {self.max_attempts} attempts.\n\n"
                f"Error: {error}\n\n"
                "Please check:\n"
                "1. FortiClient VPN is connected\n"
                "2. AWS credentials are configured\n\n"
                "Would you like to try again?"
            )
            if retry:
                self.start_download()


def main():
    root = tk.Tk()

    # Center window on screen
    window_width = 700
    window_height = 600
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    x = (screen_width - window_width) // 2
    y = (screen_height - window_height) // 2
    root.geometry(f"{window_width}x{window_height}+{x}+{y}")

    app = SFTPtoS3App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
