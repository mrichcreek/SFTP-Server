"""
Delta Exporter for Hacienda HCM Pipeline.
Exports delta records from SQL Server views to S3 as pipe-delimited CSV files.
Adapted from Export INTF Delta V4.py for Lambda execution.
"""

import boto3
import io
import time
from typing import Dict, List, Optional, Tuple

try:
    from .sql_table_loader import get_aws_secret, parse_connection_string
except ImportError:
    from sftp_download.sql_table_loader import get_aws_secret, parse_connection_string


# Views that need to be split into INT012 (Hire/Rehire) and NORMAL categories
INT012_VIEWS = {
    "HCM_PERSON_NAME_INTF_DELTA_VW",
    "HCM_PERSON_ADDRESS_INTF_DELTA_VW",
    "HCM_PERSON_NID_INTF_DELTA_VW",
    "HCM_PERSON_EMAIL_INTF_DELTA_VW",
    "HCM_EXTERNAL_IDENTIFIER_INTF_DELTA_VW"
}

# Mapping from view names to export file prefixes
EXPORT_NAME_MAP = {
    "HCM_PERSON_NAME_INTF_DELTA_VW": "PERSON_NAME",
    "HCM_PERSON_ADDRESS_INTF_DELTA_VW": "PERSON_ADDRESS",
    "HCM_PERSON_NID_INTF_DELTA_VW": "PERSON_NID",
    "HCM_PERSON_EMAIL_INTF_DELTA_VW": "PERSON_EMAIL",
    "HCM_EXTERNAL_IDENTIFIER_INTF_DELTA_VW": "PERSON_EXT_IDENTIFIER",
    "HCM_PERSON_ASSIGNMENT_INTF_DELTA_VW": "ASSIGNMENT",
    "HCM_PERSON_SUPERVISOR_INTF_DELTA_VW": "SUPERVISOR"
}


class DeltaExporter:
    """
    Exports delta records from SQL Server delta views to S3 as pipe-delimited CSV files.
    Used after the stored procedure completes to generate output files for Oracle.
    """

    def __init__(
        self,
        bucket: str,
        secret_name: str = 'Hacienda_ERP_MSSQL_Production',
        database_override: Optional[str] = None,
        output_prefix: str = 'exports/'
    ):
        """
        Initialize the Delta Exporter.

        Args:
            bucket: S3 bucket to write export files to
            secret_name: AWS Secrets Manager secret name for SQL connection
            database_override: Optional database name override
            output_prefix: S3 prefix for output files
        """
        self.bucket = bucket
        self.secret_name = secret_name
        self.database_override = database_override
        self.output_prefix = output_prefix.rstrip('/') + '/'
        self.s3_client = boto3.client('s3')

        # Get connection string
        self.connection_string = get_aws_secret(secret_name)
        conn_params = parse_connection_string(self.connection_string)

        if database_override:
            conn_params['database'] = database_override

        self.conn_params = conn_params

    def _get_connection(self):
        """Create a pymssql connection."""
        import pymssql
        return pymssql.connect(
            server=self.conn_params['server'],
            port=int(self.conn_params.get('port', 1433)),
            user=self.conn_params['user'],
            password=self.conn_params['password'],
            database=self.conn_params['database']
        )

    def check_status(self) -> Dict:
        """
        Check if the latest run is completed and ready for export.

        Returns:
            Dict with 'ready' boolean and 'instance' number
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT TOP 1 Instance, Status
                FROM RUN_INTF_STATUS
                ORDER BY Instance DESC
            """)
            row = cursor.fetchone()

            if row is None:
                return {'ready': False, 'error': 'RUN_INTF_STATUS is empty'}

            instance, status = row
            status = (status or '').strip()

            return {
                'ready': status == '02-Completed',
                'instance': instance,
                'status': status
            }
        finally:
            cursor.close()
            conn.close()

    def get_delta_views(self) -> List[str]:
        """
        Get list of delta views to export from SETUP_INTF_TABLE.

        Returns:
            List of view names
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT DISTINCT Delta_View_ForExtract
                FROM SETUP_INTF_TABLE
                WHERE dbo.CLEAN_STRING_V4(Delta_Table) <> ''
            """)
            rows = cursor.fetchall()
            return [row[0] for row in rows if row[0]]
        finally:
            cursor.close()
            conn.close()

    def get_hire_rehire_persons(self) -> set:
        """
        Get set of person numbers that are Hire/Rehire actions.
        Used to split INT012 views.

        Returns:
            Set of person numbers
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT DISTINCT PERSON_NUMBER
                FROM HCM_PERSON_ASSIGNMENT_INTF_DELTA_VW
                WHERE ACTION_CODE IN ('Hire', 'Rehire')
            """)
            return {str(row[0]).strip() for row in cursor.fetchall()}
        finally:
            cursor.close()
            conn.close()

    def export_view(
        self,
        view_name: str,
        hire_rehire_persons: set,
        timestamp: str
    ) -> List[Dict]:
        """
        Export a single delta view to S3.

        Args:
            view_name: Name of the delta view to export
            hire_rehire_persons: Set of person numbers for INT012 split
            timestamp: Timestamp string for filenames

        Returns:
            List of dicts with file info (s3_key, filename, row_count)
        """
        export_prefix = EXPORT_NAME_MAP.get(view_name)
        if not export_prefix:
            return []

        is_split_view = view_name in INT012_VIEWS
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            # Get header from title table
            title_table = view_name.replace('_VW', '') + '_TITLE'
            cursor.execute(f"SELECT * FROM {title_table}")
            header_rows = cursor.fetchall()
            header = '\n'.join('|'.join(str(col) for col in row) for row in header_rows) + '\n'

            # Get data from delta view
            cursor.execute(f"SELECT * FROM {view_name}")
            data_rows = cursor.fetchall()
            column_names = [desc[0].upper() for desc in cursor.description]
            person_idx = column_names.index("PERSON_NUMBER") if "PERSON_NUMBER" in column_names else None

            # Build output buffers
            if is_split_view:
                buffer_int012 = io.StringIO()
                buffer_normal = io.StringIO()
                buffer_int012.write(header)
                buffer_normal.write(header)
                count_int012 = 0
                count_normal = 0

                for row in data_rows:
                    row_txt = '|'.join(str(col) if col is not None else '' for col in row) + '\n'
                    if person_idx is not None and str(row[person_idx]).strip() in hire_rehire_persons:
                        buffer_int012.write(row_txt)
                        count_int012 += 1
                    else:
                        buffer_normal.write(row_txt)
                        count_normal += 1

                # Upload both files to S3
                files = []

                # INT012 file
                filename_int012 = f"{export_prefix}_INT012_{timestamp}.csv"
                s3_key_int012 = f"{self.output_prefix}{filename_int012}"
                self.s3_client.put_object(
                    Bucket=self.bucket,
                    Key=s3_key_int012,
                    Body=buffer_int012.getvalue().encode('utf-8'),
                    ContentType='text/csv'
                )
                files.append({
                    's3_key': s3_key_int012,
                    'filename': filename_int012,
                    'row_count': count_int012,
                    'category': 'INT012'
                })

                # NORMAL file
                filename_normal = f"{export_prefix}_{timestamp}.csv"
                s3_key_normal = f"{self.output_prefix}{filename_normal}"
                self.s3_client.put_object(
                    Bucket=self.bucket,
                    Key=s3_key_normal,
                    Body=buffer_normal.getvalue().encode('utf-8'),
                    ContentType='text/csv'
                )
                files.append({
                    's3_key': s3_key_normal,
                    'filename': filename_normal,
                    'row_count': count_normal,
                    'category': 'NORMAL'
                })

                return files

            else:
                # Single file export
                buffer = io.StringIO()
                buffer.write(header)
                row_count = 0

                for row in data_rows:
                    row_txt = '|'.join(str(col) if col is not None else '' for col in row) + '\n'
                    buffer.write(row_txt)
                    row_count += 1

                filename = f"{export_prefix}_{timestamp}.csv"
                s3_key = f"{self.output_prefix}{filename}"
                self.s3_client.put_object(
                    Bucket=self.bucket,
                    Key=s3_key,
                    Body=buffer.getvalue().encode('utf-8'),
                    ContentType='text/csv'
                )

                return [{
                    's3_key': s3_key,
                    'filename': filename,
                    'row_count': row_count,
                    'category': 'SINGLE'
                }]

        finally:
            cursor.close()
            conn.close()

    def log_files_to_db(self, instance: int, files: List[Dict]) -> None:
        """
        Log exported files to RUN_INTF_FILES_SENT table.

        Args:
            instance: The run instance number
            files: List of file info dicts
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            for file_info in files:
                cursor.execute("""
                    INSERT INTO RUN_INTF_FILES_SENT
                           (Instance, FileName, RecordCount, DateCreated)
                    VALUES (%s, %s, %s, SYSDATETIME())
                """, (instance, file_info['filename'], file_info['row_count']))
            conn.commit()
        finally:
            cursor.close()
            conn.close()

    def update_status(self, instance: int, status: str = '03-File Sent') -> None:
        """
        Update the run status in RUN_INTF_STATUS.

        Args:
            instance: The run instance number
            status: New status string
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                UPDATE RUN_INTF_STATUS
                SET Status = %s,
                    DateCompleted = SYSDATETIME()
                WHERE Instance = %s
            """, (status, instance))
            conn.commit()
        finally:
            cursor.close()
            conn.close()

    def export_all(self, update_status: bool = True) -> Dict:
        """
        Export all delta views to S3.

        Args:
            update_status: Whether to update RUN_INTF_STATUS after export

        Returns:
            Dict with export results
        """
        result = {
            'success': False,
            'files_exported': [],
            'total_files': 0,
            'total_rows': 0,
            'errors': []
        }

        # Check if ready for export
        status_check = self.check_status()
        if not status_check.get('ready'):
            result['error'] = f"Not ready for export. Status: {status_check.get('status', 'unknown')}"
            return result

        instance = status_check['instance']
        result['instance'] = instance

        try:
            # Get views to export
            views = self.get_delta_views()
            if not views:
                result['error'] = 'No delta views found to export'
                return result

            # Get hire/rehire persons for splitting
            hire_rehire_persons = self.get_hire_rehire_persons()

            # Generate timestamp for filenames
            timestamp = time.strftime('%Y.%m.%d-%H.%M.%S')

            # Export each view
            all_files = []
            for view_name in views:
                try:
                    files = self.export_view(view_name, hire_rehire_persons, timestamp)
                    all_files.extend(files)
                except Exception as e:
                    result['errors'].append(f"{view_name}: {str(e)}")

            result['files_exported'] = all_files
            result['total_files'] = len(all_files)
            result['total_rows'] = sum(f['row_count'] for f in all_files)

            # Log files to database
            if all_files:
                self.log_files_to_db(instance, all_files)

            # Update status
            if update_status and all_files:
                self.update_status(instance, '03-File Sent')
                result['status_updated'] = True

            result['success'] = len(result['errors']) == 0

        except Exception as e:
            result['error'] = str(e)

        return result


def export_delta_files(
    bucket: str,
    secret_name: str = 'Hacienda_ERP_MSSQL_Production',
    database_override: Optional[str] = None,
    output_prefix: str = 'exports/',
    update_status: bool = True
) -> Dict:
    """
    Convenience function to export all delta files.

    Args:
        bucket: S3 bucket for output
        secret_name: AWS secret name for SQL connection
        database_override: Optional database name override
        output_prefix: S3 prefix for output files
        update_status: Whether to update status after export

    Returns:
        Dict with export results
    """
    exporter = DeltaExporter(
        bucket=bucket,
        secret_name=secret_name,
        database_override=database_override,
        output_prefix=output_prefix
    )
    return exporter.export_all(update_status=update_status)


def export_handler(event, context):
    """
    Lambda handler for delta export - called by Step Functions.

    Input (from Step Functions):
    {
        "sql_secret": "Hacienda_ERP_MSSQL_Production",
        "database": "Hacienda_ERP",
        "folder": "20240115_1030",
        "s3_bucket": "hacienda-sftp-downloads"
    }

    Returns:
    {
        "statusCode": 200,
        "body": {
            "status": "success",
            "files_exported": 12,
            "total_rows": 1500,
            "output_prefix": "20240115_1030/7_Export_Files/"
        }
    }
    """
    import json
    import os

    try:
        # Support both direct invoke and API Gateway
        if 'body' in event and event['body']:
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        else:
            body = event

        sql_secret = body.get('sql_secret', os.environ.get('SQL_SECRET_NAME', 'Hacienda_ERP_MSSQL_Production'))
        database = body.get('database', 'Hacienda_ERP')
        folder = body.get('folder', 'exports')
        bucket = body.get('s3_bucket', os.environ.get('S3_BUCKET', 'hacienda-sftp-downloads'))

        # Build output prefix with folder structure
        output_prefix = f"{folder}/7_Export_Files/"

        exporter = DeltaExporter(
            bucket=bucket,
            secret_name=sql_secret,
            database_override=database,
            output_prefix=output_prefix
        )

        result = exporter.export_all(update_status=True)

        # Add output prefix to result for next step
        result['output_prefix'] = output_prefix
        result['s3_bucket'] = bucket

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
