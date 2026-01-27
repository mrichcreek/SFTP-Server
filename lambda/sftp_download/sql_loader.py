"""
SQL Server Loader wrapper class for the full pipeline.
Wraps the functions in sql_table_loader.py with a class interface.
"""

import boto3
import os
from typing import Dict, List, Optional

try:
    from .sql_table_loader import load_file_to_sql, extract_table_name_from_filename, get_aws_secret, parse_connection_string
except ImportError:
    from sftp_download.sql_table_loader import load_file_to_sql, extract_table_name_from_filename, get_aws_secret, parse_connection_string

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
        secret_name: str = 'Hacienda_ERP_Test_MSSQL_text',
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
            'detailed_failures': []  # New: detailed failure info for reports
        }

        for file_info in files:
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

                # Add filename for reference
                load_result['filename'] = filename
                results['table_results'].append(load_result)

                if load_result.get('success'):
                    results['loaded_tables'] += 1
                    results['total_rows'] += load_result.get('rows_loaded', 0)
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
