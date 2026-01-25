"""
SQL Table Loader Module for Hacienda SFTP Downloads

This module creates SQL Server tables from CSV files and loads data.
It processes only the newest version of each file type (no duplicates or superseded files).
"""

import boto3
import io
import re
import csv
from datetime import datetime
from typing import Dict, List, Optional, Tuple


def get_aws_secret(secret_name: str, region: str = 'us-east-1') -> str:
    """Retrieve a secret from AWS Secrets Manager."""
    client = boto3.client('secretsmanager', region_name=region)
    response = client.get_secret_value(SecretId=secret_name)

    if 'SecretString' in response:
        return response['SecretString']
    else:
        return response['SecretBinary'].decode('utf-8')


def extract_table_name_from_filename(filename: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract the table name and date portion from a filename.

    Examples:
        HCM_PERSON_ADDRESS_INTF_FIMAS_20251209121226.csv -> (HCM_PERSON_ADDRESS_INTF_FIMAS, 20251209121226)
        HCM_PERSON_ADDRESS_INTF_HAC88_20251205.csv -> (HCM_PERSON_ADDRESS_INTF_HAC88, 20251205)
        hcm_person_address_rhum75_20260109.csv -> (HCM_PERSON_ADDRESS_RHUM75, 20260109)

    Returns:
        Tuple of (table_name, date_portion) or (None, None) if parsing fails
    """
    # Remove extension
    name = filename.rsplit('.', 1)[0] if '.' in filename else filename

    # Pattern: everything before the last underscore followed by digits
    # Matches: NAME_PARTS_SOURCE_YYYYMMDD or NAME_PARTS_SOURCE_YYYYMMDDHHMMSS
    match = re.match(r'^(.+?)_(\d{8,14})$', name)

    if match:
        table_name = match.group(1).upper()  # Normalize to uppercase
        date_portion = match.group(2)
        return table_name, date_portion

    return None, None


def get_csv_headers(s3_client, bucket: str, key: str) -> List[str]:
    """
    Read the first line of a CSV file to get column headers.

    Args:
        s3_client: Boto3 S3 client
        bucket: S3 bucket name
        key: S3 object key

    Returns:
        List of column header names
    """
    response = s3_client.get_object(Bucket=bucket, Key=key, Range='bytes=0-10000')
    content = response['Body'].read().decode('utf-8', errors='ignore')

    # Parse first line as CSV
    first_line = content.split('\n')[0].strip()
    reader = csv.reader(io.StringIO(first_line))
    headers = next(reader, [])

    # Clean headers - remove quotes, extra spaces
    cleaned_headers = [h.strip().strip('"').strip() for h in headers]

    return cleaned_headers


def sanitize_column_name(name: str) -> str:
    """
    Sanitize a column name for SQL Server.

    - Replace spaces and special characters with underscores
    - Ensure it starts with a letter or underscore
    - Limit length to 128 characters
    """
    # Replace problematic characters
    sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', name)

    # Ensure starts with letter or underscore
    if sanitized and sanitized[0].isdigit():
        sanitized = '_' + sanitized

    # Limit length
    return sanitized[:128] if sanitized else 'COLUMN'


def generate_create_table_sql(table_name: str, headers: List[str], drop_existing: bool = True) -> str:
    """
    Generate T-SQL CREATE TABLE statement from CSV headers.
    All columns are created as NVARCHAR(MAX) for flexibility.

    Args:
        table_name: Name for the SQL table
        headers: List of column names
        drop_existing: Whether to drop the table if it exists

    Returns:
        T-SQL CREATE TABLE statement
    """
    # Sanitize table name
    safe_table_name = sanitize_column_name(table_name)

    # Build column definitions
    columns = []
    for header in headers:
        col_name = sanitize_column_name(header)
        if col_name:
            columns.append(f"    [{col_name}] NVARCHAR(MAX)")

    if not columns:
        raise ValueError(f"No valid columns found for table {table_name}")

    sql_parts = []

    # Drop existing table if requested
    if drop_existing:
        sql_parts.append(f"IF OBJECT_ID(N'dbo.[{safe_table_name}]', N'U') IS NOT NULL")
        sql_parts.append(f"    DROP TABLE dbo.[{safe_table_name}];")
        sql_parts.append("")

    # Create table
    sql_parts.append(f"CREATE TABLE dbo.[{safe_table_name}] (")
    sql_parts.append(",\n".join(columns))
    sql_parts.append(");")

    return "\n".join(sql_parts)


def generate_insert_sql(table_name: str, headers: List[str]) -> str:
    """
    Generate parameterized T-SQL INSERT statement (pyodbc style with ?).

    Args:
        table_name: Name of the SQL table
        headers: List of column names

    Returns:
        T-SQL INSERT statement with ? placeholders
    """
    safe_table_name = sanitize_column_name(table_name)
    safe_columns = [sanitize_column_name(h) for h in headers if sanitize_column_name(h)]

    column_names = ", ".join(f"[{col}]" for col in safe_columns)
    placeholders = ", ".join("?" * len(safe_columns))

    return f"INSERT INTO dbo.[{safe_table_name}] ({column_names}) VALUES ({placeholders})"


def generate_insert_sql_pymssql(table_name: str, headers: List[str]) -> str:
    """
    Generate parameterized T-SQL INSERT statement (pymssql style with %s).

    Args:
        table_name: Name of the SQL table
        headers: List of column names

    Returns:
        T-SQL INSERT statement with %s placeholders
    """
    safe_table_name = sanitize_column_name(table_name)
    safe_columns = [sanitize_column_name(h) for h in headers if sanitize_column_name(h)]

    column_names = ", ".join(f"[{col}]" for col in safe_columns)
    placeholders = ", ".join(["%s"] * len(safe_columns))

    return f"INSERT INTO dbo.[{safe_table_name}] ({column_names}) VALUES ({placeholders})"


def read_csv_data(s3_client, bucket: str, key: str) -> Tuple[List[str], List[List[str]]]:
    """
    Read CSV data from S3.

    Args:
        s3_client: Boto3 S3 client
        bucket: S3 bucket name
        key: S3 object key

    Returns:
        Tuple of (headers, rows) where rows is a list of lists
    """
    response = s3_client.get_object(Bucket=bucket, Key=key)
    content = response['Body'].read().decode('utf-8', errors='ignore')

    reader = csv.reader(io.StringIO(content))
    rows = list(reader)

    if not rows:
        return [], []

    headers = [h.strip().strip('"').strip() for h in rows[0]]
    data_rows = []

    for row in rows[1:]:
        # Ensure row has same length as headers
        if len(row) < len(headers):
            row = row + [''] * (len(headers) - len(row))
        elif len(row) > len(headers):
            row = row[:len(headers)]

        # Clean values
        cleaned_row = [str(val).strip() if val else '' for val in row]
        data_rows.append(cleaned_row)

    return headers, data_rows


def identify_files_to_load(files: List[Dict]) -> Dict[str, Dict]:
    """
    Identify the newest version of each file type to load.
    Groups files by table name and selects the one with the newest date.

    Args:
        files: List of file info dicts with 'filename' and 's3_key' keys

    Returns:
        Dict mapping table_name to file info for the newest file of each type
    """
    # Group files by table name
    table_files = {}

    for file_info in files:
        filename = file_info.get('filename', '')
        table_name, date_portion = extract_table_name_from_filename(filename)

        if not table_name or not date_portion:
            continue

        if table_name not in table_files:
            table_files[table_name] = []

        table_files[table_name].append({
            **file_info,
            'table_name': table_name,
            'date_portion': date_portion
        })

    # Select newest file for each table
    result = {}
    for table_name, files_list in table_files.items():
        # Sort by date portion descending (newest first)
        sorted_files = sorted(files_list, key=lambda x: x['date_portion'], reverse=True)
        result[table_name] = sorted_files[0]

    return result


def parse_connection_string(conn_str: str) -> Dict:
    """
    Parse ODBC connection string into components for pymssql.

    Args:
        conn_str: ODBC-style connection string

    Returns:
        Dict with server, database, user, password
    """
    params = {}
    for part in conn_str.split(';'):
        if '=' in part:
            key, value = part.split('=', 1)
            key = key.strip().upper()
            value = value.strip()
            if key == 'SERVER':
                # Handle server,port format
                if ',' in value:
                    server, port = value.split(',')
                    params['server'] = server.strip()
                    params['port'] = int(port.strip())
                else:
                    params['server'] = value
                    params['port'] = 1433
            elif key == 'DATABASE':
                params['database'] = value
            elif key == 'UID':
                params['user'] = value
            elif key == 'PWD':
                params['password'] = value
    return params


def get_table_columns(cursor, table_name: str) -> List[str]:
    """
    Get the list of column names from a database table.

    Args:
        cursor: Database cursor
        table_name: Name of the table

    Returns:
        List of column names (uppercase for comparison)
    """
    safe_table_name = sanitize_column_name(table_name)
    cursor.execute(f"""
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = '{safe_table_name}' AND TABLE_SCHEMA = 'dbo'
        ORDER BY ORDINAL_POSITION
    """)
    return [row[0].upper() for row in cursor.fetchall()]


def match_csv_to_db_columns(csv_headers: List[str], db_columns: List[str]) -> Tuple[List[int], List[str], List[str]]:
    """
    Match CSV headers to database columns (case-insensitive).

    Args:
        csv_headers: List of CSV column headers
        db_columns: List of database column names

    Returns:
        Tuple of:
        - indices: List of CSV column indices that match DB columns
        - matched_db_cols: List of matched DB column names (in DB case)
        - skipped_csv_cols: List of CSV columns that don't exist in DB
    """
    # Create lookup dict: uppercase -> original DB column name
    db_col_lookup = {col.upper(): col for col in db_columns}

    indices = []
    matched_db_cols = []
    skipped_csv_cols = []

    for i, csv_col in enumerate(csv_headers):
        csv_col_upper = csv_col.strip().upper()
        if csv_col_upper in db_col_lookup:
            indices.append(i)
            matched_db_cols.append(db_col_lookup[csv_col_upper])
        else:
            skipped_csv_cols.append(csv_col)

    return indices, matched_db_cols, skipped_csv_cols


def load_file_to_sql(
    s3_client,
    connection_string: str,
    bucket: str,
    s3_key: str,
    table_name: str,
    clear_existing: bool = True
) -> Dict:
    """
    Load a single CSV file from S3 into an existing SQL Server table.

    NOTE: Tables must already exist in the database. This function does NOT
    create tables - it only clears existing data and inserts new rows.
    CSV columns that don't exist in the DB table are skipped.

    Args:
        s3_client: Boto3 S3 client
        connection_string: Connection string (ODBC format, will be parsed)
        bucket: S3 bucket name
        s3_key: S3 object key
        table_name: Name of the existing SQL table
        clear_existing: Whether to clear existing data before inserting

    Returns:
        Dict with load results
    """
    import pymssql

    safe_table_name = sanitize_column_name(table_name)

    result = {
        'table_name': table_name,
        's3_key': s3_key,
        'success': False,
        'rows_loaded': 0,
        'error': None,
        'columns_matched': 0,
        'columns_skipped': []
    }

    try:
        # Read CSV data
        headers, rows = read_csv_data(s3_client, bucket, s3_key)

        if not headers:
            result['error'] = 'No headers found in CSV'
            return result

        # Parse connection string and connect using pymssql
        conn_params = parse_connection_string(connection_string)
        with pymssql.connect(
            server=conn_params['server'],
            port=conn_params.get('port', 1433),
            user=conn_params['user'],
            password=conn_params['password'],
            database=conn_params['database'],
            tds_version='7.3',
            autocommit=False
        ) as conn:
            cursor = conn.cursor()

            # Get actual database columns for this table
            db_columns = get_table_columns(cursor, table_name)

            if not db_columns:
                result['error'] = f'Table {table_name} not found or has no columns'
                return result

            # Match CSV headers to DB columns
            col_indices, matched_db_cols, skipped_cols = match_csv_to_db_columns(headers, db_columns)

            result['columns_matched'] = len(matched_db_cols)
            result['columns_skipped'] = skipped_cols

            if not matched_db_cols:
                result['error'] = 'No CSV columns match database columns'
                return result

            # Clear existing data if requested
            if clear_existing:
                cursor.execute(f"DELETE FROM dbo.[{safe_table_name}]")
                conn.commit()

            # Insert data in batches using only matched columns
            if rows:
                # Build INSERT statement with only matched columns
                column_names = ", ".join(f"[{col}]" for col in matched_db_cols)
                placeholders = ", ".join(["%s"] * len(matched_db_cols))
                insert_sql = f"INSERT INTO dbo.[{safe_table_name}] ({column_names}) VALUES ({placeholders})"

                batch_size = 1000
                for i in range(0, len(rows), batch_size):
                    batch = rows[i:i + batch_size]
                    # Extract only the matched columns from each row
                    filtered_batch = [
                        tuple(row[idx] if idx < len(row) else '' for idx in col_indices)
                        for row in batch
                    ]
                    cursor.executemany(insert_sql, filtered_batch)
                conn.commit()
                result['rows_loaded'] = len(rows)

            result['success'] = True

    except Exception as e:
        result['error'] = str(e)

    return result


def load_all_newest_files(
    bucket: str,
    files: List[Dict],
    secret_name: str = 'Hacienda_ERP_Test_MSSQL_text',
    clear_existing: bool = True
) -> Dict:
    """
    Load all newest version files into existing SQL Server tables.

    NOTE: Tables must already exist in the database. This function does NOT
    create tables - it only clears existing data and inserts new rows.

    Args:
        bucket: S3 bucket name
        files: List of file info dicts
        secret_name: Name of AWS secret containing connection string
        clear_existing: Whether to clear existing data before inserting

    Returns:
        Dict with overall results and per-table results
    """
    s3_client = boto3.client('s3')

    # Get connection string
    connection_string = get_aws_secret(secret_name)

    # Identify files to load
    files_to_load = identify_files_to_load(files)

    results = {
        'total_tables': len(files_to_load),
        'successful': 0,
        'failed': 0,
        'tables': []
    }

    for table_name, file_info in files_to_load.items():
        s3_key = file_info.get('s3_key') or file_info.get('key', '')

        load_result = load_file_to_sql(
            s3_client=s3_client,
            connection_string=connection_string,
            bucket=bucket,
            s3_key=s3_key,
            table_name=table_name,
            clear_existing=clear_existing
        )

        load_result['source_file'] = file_info.get('filename', '')
        results['tables'].append(load_result)

        if load_result['success']:
            results['successful'] += 1
        else:
            results['failed'] += 1

    return results


# Lambda handler for loading files to SQL
def load_to_sql_handler(event, context):
    """
    Lambda handler for loading validated files to SQL Server.

    Expected event structure:
    {
        "files": [
            {"filename": "...", "s3_key": "..."},
            ...
        ],
        "bucket": "hacienda-sftp-downloads",  # optional, defaults from env
        "drop_existing": true  # optional, defaults to true
    }
    """
    import os
    import json

    try:
        # Parse request body if from API Gateway
        if 'body' in event and event['body']:
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        else:
            body = event

        files = body.get('files', [])
        bucket = body.get('bucket', os.environ.get('S3_BUCKET', 'hacienda-sftp-downloads'))
        clear_existing = body.get('clear_existing', True)

        if not files:
            return {
                'statusCode': 400,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({'error': 'No files provided'})
            }

        # Load files to SQL (tables must already exist in database)
        results = load_all_newest_files(
            bucket=bucket,
            files=files,
            clear_existing=clear_existing
        )

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps(results, default=str)
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


# Test/preview function - shows what would be loaded without actually loading
def preview_load_handler(event, context):
    """
    Lambda handler for previewing what files would be loaded.

    Expected event structure:
    {
        "files": [
            {"filename": "...", "s3_key": "..."},
            ...
        ]
    }
    """
    import json

    try:
        # Parse request body if from API Gateway
        if 'body' in event and event['body']:
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        else:
            body = event

        files = body.get('files', [])

        if not files:
            return {
                'statusCode': 400,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({'error': 'No files provided'})
            }

        # Identify files to load
        files_to_load = identify_files_to_load(files)

        preview = {
            'total_tables': len(files_to_load),
            'tables': []
        }

        for table_name, file_info in files_to_load.items():
            preview['tables'].append({
                'table_name': table_name,
                'source_file': file_info.get('filename', ''),
                's3_key': file_info.get('s3_key') or file_info.get('key', ''),
                'date_portion': file_info.get('date_portion', '')
            })

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps(preview, default=str)
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
