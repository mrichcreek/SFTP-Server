"""
SQL Server Loader wrapper class for the full pipeline.
Wraps the functions in sql_table_loader.py with a class interface.

Special handling for RHUM files:
- RHUM files may have partial data that builds up across multiple files
- All RHUM files must be downloaded and processed from oldest to newest
- After all RHUM files are loaded, then the stored procedure can run
"""

import boto3
import os
import re
from typing import Dict, List, Optional, Tuple

try:
    from .sql_table_loader import load_file_to_sql, extract_table_name_from_filename, get_aws_secret, parse_connection_string
except ImportError:
    from sftp_download.sql_table_loader import load_file_to_sql, extract_table_name_from_filename, get_aws_secret, parse_connection_string


def extract_date_from_filename(filename: str) -> str:
    """
    Extract the date portion from a filename for sorting.

    Args:
        filename: The filename to extract date from

    Returns:
        Date string (e.g., '20251215001303') or empty string if not found
    """
    # Pattern to match date portion at end of filename before .csv
    match = re.search(r'_(\d{8,14})\.csv$', filename, re.IGNORECASE)
    if match:
        return match.group(1)
    return ""


def is_rhum_file(filename: str) -> bool:
    """
    Check if a file is a RHUM file that needs special oldest-first processing.

    Args:
        filename: The filename to check

    Returns:
        True if this is a RHUM file
    """
    # RHUM files have RHUM in the entity portion (e.g., HCM_PERSON_INTF_RHUM75_20251215.csv)
    filename_upper = filename.upper()
    return '_RHUM' in filename_upper or 'RHUM_' in filename_upper


def sort_files_for_loading(files: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """
    Sort files for proper loading order.

    RHUM files need special handling:
    - They are processed separately from other files
    - They must be loaded oldest to newest (by date in filename)
    - This is because RHUM files may have partial data that builds up

    Args:
        files: List of file dicts with 'filename' and 's3_key'

    Returns:
        Tuple of (rhum_files_sorted_oldest_first, other_files)
    """
    rhum_files = []
    other_files = []

    for file_info in files:
        filename = file_info.get('filename', '')
        if is_rhum_file(filename):
            # Add date for sorting
            file_info['_sort_date'] = extract_date_from_filename(filename)
            rhum_files.append(file_info)
        else:
            other_files.append(file_info)

    # Sort RHUM files by date ASCENDING (oldest first)
    rhum_files_sorted = sorted(rhum_files, key=lambda x: x.get('_sort_date', ''))

    # Clean up temporary sort key
    for f in rhum_files_sorted:
        f.pop('_sort_date', None)

    return rhum_files_sorted, other_files

# Alias for backwards compatibility
def get_table_name_from_filename(filename: str) -> Optional[str]:
    """Extract just the table name (without date) from filename."""
    table_name, _ = extract_table_name_from_filename(filename)
    return table_name


class SqlServerLoader:
    """
    Wrapper class for loading files to SQL Server.
    Used by the full pipeline orchestrator.
    """

    def __init__(
        self,
        bucket: str,
        secret_name: str = 'Hacienda_ERP_MSSQL_Production',
        database_override: Optional[str] = None
    ):
        """
        Initialize the SQL Server loader.

        Args:
            bucket: S3 bucket containing the files
            secret_name: AWS Secrets Manager secret name for connection string
            database_override: Optional database name override (for prod vs test)
        """
        self.bucket = bucket
        self.secret_name = secret_name
        self.database_override = database_override
        self.s3_client = boto3.client('s3')

        # Get connection string from secrets
        self.connection_string = get_aws_secret(secret_name)

        # Override database if specified
        if database_override:
            conn_params = parse_connection_string(self.connection_string)
            conn_params['database'] = database_override
            # Reconstruct connection string
            self.connection_string = (
                f"DRIVER={{ODBC Driver 18 for SQL Server}};"
                f"SERVER={conn_params['server']},{conn_params.get('port', 1433)};"
                f"DATABASE={database_override};"
                f"UID={conn_params['user']};"
                f"PWD={conn_params['password']};"
                f"TrustServerCertificate=yes"
            )

    def load_files(
        self,
        files: List[Dict],
        drop_existing: bool = False
    ) -> Dict:
        """
        Load multiple files to SQL Server.

        RHUM files get special handling:
        - They are sorted by date (oldest first) and loaded sequentially
        - This is because RHUM files may have partial data that builds up
        - All RHUM files are processed before other files

        Args:
            files: List of dicts with 'filename' and 's3_key' keys
            drop_existing: Whether to clear existing data (default False)

        Returns:
            Dict with load results including loaded_tables, failed_tables, total_rows
        """
        results = {
            'loaded_tables': 0,
            'failed_tables': 0,
            'total_rows': 0,
            'table_results': [],
            'errors': [],
            'detailed_failures': [],  # Detailed failure info for reports
            'rhum_files_processed': 0,  # Track RHUM file processing
            'rhum_processing_order': []  # Show the order RHUM files were processed
        }

        # Sort files: RHUM files first (oldest to newest), then other files
        rhum_files, other_files = sort_files_for_loading(files)

        # Log RHUM file order for debugging
        if rhum_files:
            results['rhum_processing_order'] = [f.get('filename', '') for f in rhum_files]

        # Process RHUM files first (oldest to newest)
        # This ensures partial data builds up correctly
        ordered_files = rhum_files + other_files

        for file_info in ordered_files:
            filename = file_info.get('filename', '')
            s3_key = file_info.get('s3_key', '')

            # Get table name from filename
            table_name = get_table_name_from_filename(filename)
            if not table_name:
                results['errors'].append(f"Could not determine table name for: {filename}")
                results['failed_tables'] += 1
                results['detailed_failures'].append({
                    'filename': filename,
                    'table_name': None,
                    'error_type': 'INVALID_FILENAME',
                    'error': f"Could not determine table name from filename: {filename}"
                })
                continue

            try:
                load_result = load_file_to_sql(
                    s3_client=self.s3_client,
                    connection_string=self.connection_string,
                    bucket=self.bucket,
                    s3_key=s3_key,
                    table_name=table_name,
                    filename=filename,
                    clear_existing=drop_existing
                )

                # Add filename and RHUM flag for reference
                load_result['filename'] = filename
                load_result['is_rhum_file'] = is_rhum_file(filename)
                results['table_results'].append(load_result)

                if load_result.get('success'):
                    results['loaded_tables'] += 1
                    results['total_rows'] += load_result.get('rows_loaded', 0)
                    # Track RHUM file processing
                    if is_rhum_file(filename):
                        results['rhum_files_processed'] += 1
                else:
                    results['failed_tables'] += 1
                    if load_result.get('error'):
                        results['errors'].append(f"{table_name}: {load_result['error']}")

                    # Capture detailed failure information
                    results['detailed_failures'].append({
                        'filename': filename,
                        'table_name': table_name,
                        's3_key': s3_key,
                        'error_type': load_result.get('error_type', 'UNKNOWN'),
                        'error': load_result.get('error'),
                        'csv_columns': load_result.get('csv_columns', []),
                        'db_columns': load_result.get('db_columns', []),
                        'columns_matched': load_result.get('columns_matched', 0),
                        'columns_skipped': load_result.get('columns_skipped', []),
                        'missing_in_db': load_result.get('missing_in_db', []),
                        'missing_in_csv': load_result.get('missing_in_csv', []),
                        'failed_rows': load_result.get('failed_rows', []),
                        'csv_row_count': load_result.get('csv_row_count', 0),
                        'rows_loaded': load_result.get('rows_loaded', 0)
                    })

            except Exception as e:
                results['failed_tables'] += 1
                results['errors'].append(f"{table_name}: {str(e)}")
                results['table_results'].append({
                    'table_name': table_name,
                    's3_key': s3_key,
                    'filename': filename,
                    'success': False,
                    'error': str(e),
                    'error_type': 'EXCEPTION'
                })
                results['detailed_failures'].append({
                    'filename': filename,
                    'table_name': table_name,
                    's3_key': s3_key,
                    'error_type': 'EXCEPTION',
                    'error': str(e)
                })

        return results
