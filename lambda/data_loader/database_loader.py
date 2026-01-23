"""
Database Loader for Hacienda SFTP Downloads
Loads CSV files from S3 into SQL Server database.

Based on the existing LoadIntfData.py patterns.
Supports both on-premises SQL Server (via Site-to-Site VPN)
and AWS RDS SQL Server.
"""

import boto3
import csv
import io
import os
import re
import pyodbc
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime


@dataclass
class LoadResult:
    """Result of loading a file into the database."""
    success: bool
    file_name: str
    table_name: str
    rows_loaded: int
    error_message: Optional[str] = None
    duration_seconds: float = 0.0


# Source aliases for entity name normalization
SOURCE_ALIASES = {
    'HAC88': 'HACIENDA',
    'HACIENDA': 'HACIENDA',
    'RHUM': 'RHUM',
    'RHUM75': 'RHUM',
    'FIMAS': 'FIMAS',
    'KRONOSPOL': 'KRONOSPOL',
    'DOE': 'DOE',
    'ADPPOLICIA': 'ADPPOLICIA',
    '911': '911',
    'KRONOSDE': 'KRONOSDE',
    'SEPI': 'SEPI'
}

# Mapping of file types to database tables
FILE_TYPE_TO_TABLE = {
    'PERSON': 'HCM_PERSON_INTF',
    'PERSON_NAME': 'HCM_PERSON_NAME_INTF',
    'PERSON_ASSIGNMENT': 'HCM_PERSON_ASSIGNMENT_INTF',
    'PERSON_ADDRESS': 'HCM_PERSON_ADDRESS_INTF',
    'PERSON_NID': 'HCM_PERSON_NID_INTF',
    'PERSON_SUPERVISOR': 'HCM_PERSON_SUPERVISOR_INTF',
    'PERSON_EMAIL': 'HCM_PERSON_EMAIL_INTF',
    'SENIORITY': 'HCM_SENIORITY_INTF'
}


def get_db_connection(
    server: str = None,
    database: str = "Hacienda_ERP",
    use_trusted: bool = True,
    username: str = None,
    password: str = None
) -> pyodbc.Connection:
    """
    Create database connection.

    Args:
        server: SQL Server hostname/IP (default from env or 10.0.151.32)
        database: Database name
        use_trusted: Use Windows Authentication
        username: SQL Server username (if not using trusted)
        password: SQL Server password (if not using trusted)

    Returns:
        pyodbc Connection object
    """
    if server is None:
        server = os.environ.get('DB_SERVER', '10.0.151.32')

    if use_trusted:
        connection_string = (
            f'Driver={{ODBC Driver 17 for SQL Server}};'
            f'Server={server};'
            f'Database={database};'
            f'Trusted_Connection=yes;'
        )
    else:
        # Get credentials from environment or Secrets Manager
        if username is None:
            username = os.environ.get('DB_USERNAME')
        if password is None:
            password = os.environ.get('DB_PASSWORD')

        connection_string = (
            f'Driver={{ODBC Driver 17 for SQL Server}};'
            f'Server={server};'
            f'Database={database};'
            f'UID={username};'
            f'PWD={password};'
        )

    return pyodbc.connect(connection_string)


def get_db_credentials_from_secrets_manager(secret_name: str = "hacienda-db-credentials") -> Dict:
    """
    Retrieve database credentials from AWS Secrets Manager.
    """
    client = boto3.client('secretsmanager')
    response = client.get_secret_value(SecretId=secret_name)

    import json
    return json.loads(response['SecretString'])


def extract_source_from_filename(filename: str) -> Optional[str]:
    """
    Extract the normalized source/entity name from filename.

    Examples:
        HCM_PERSON_INTF_FIMAS_20251126.csv -> FIMAS
        hcm_person_address_rhum75_20260109.csv -> RHUM
        HCM_PERSON_ASSIGNMENT_INTF_HAC88_20251205.csv -> HACIENDA
    """
    # Pattern 1: HCM_{type}_INTF_{source}_{date}.csv
    match = re.search(r'INTF_([A-Za-z0-9]+)_\d', filename, re.IGNORECASE)
    if match:
        raw_source = match.group(1).upper()
        return SOURCE_ALIASES.get(raw_source, raw_source)

    # Pattern 2: hcm_{type}_{source}_{date}.csv (without INTF)
    match = re.search(r'hcm_\w+?_([a-z0-9]+)_\d', filename, re.IGNORECASE)
    if match:
        raw_source = match.group(1).upper()
        return SOURCE_ALIASES.get(raw_source, raw_source)

    return None


def extract_file_type_from_filename(filename: str) -> Optional[str]:
    """
    Extract the file type from filename.

    Examples:
        HCM_PERSON_INTF_FIMAS_20251126.csv -> PERSON
        HCM_PERSON_ASSIGNMENT_INTF_HAC88_20251205.csv -> PERSON_ASSIGNMENT
        hcm_person_address_rhum75_20260109.csv -> PERSON_ADDRESS
    """
    upper_name = filename.upper()

    # Check each known file type (longer first to match PERSON_ASSIGNMENT before PERSON)
    for file_type in sorted(FILE_TYPE_TO_TABLE.keys(), key=len, reverse=True):
        if f'_{file_type}_' in upper_name or f'_{file_type.replace("_", "")}_' in upper_name:
            return file_type

    # Special cases for abbreviated names
    if '_ASSIGN_' in upper_name or '_ASSIGNMENT_' in upper_name:
        return 'PERSON_ASSIGNMENT'
    if '_SUPV_' in upper_name or '_SUPERVISOR_' in upper_name:
        return 'PERSON_SUPERVISOR'

    return None


def read_csv_from_s3(bucket: str, key: str, s3_client=None) -> Tuple[List[str], List[List[str]]]:
    """
    Read CSV file from S3 and return headers and rows.
    """
    if s3_client is None:
        s3_client = boto3.client('s3')

    response = s3_client.get_object(Bucket=bucket, Key=key)
    content = response['Body'].read().decode('utf-8-sig')  # Handle BOM

    reader = csv.reader(io.StringIO(content))
    rows = list(reader)

    if len(rows) < 2:
        return [], []

    headers = rows[0]
    data_rows = rows[1:]

    return headers, data_rows


def load_person_file(
    cursor: pyodbc.Cursor,
    headers: List[str],
    rows: List[List[str]],
    source: str
) -> int:
    """
    Load Person data into HCM_PERSON_INTF table.
    Based on LoadPersonFile from LoadIntfData.py
    """
    insert_sql = """
        INSERT INTO HCM_PERSON_INTF (
            PersonId, PersonNumber, DateOfBirth, DateOfDeath, CountryOfBirth,
            RegionOfBirth, TownOfBirth, ApplicantNumber, ATTRIBUTE1, SOURCE
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    count = 0
    for row in rows:
        if len(row) >= 8:
            try:
                cursor.execute(insert_sql, (
                    row[0],  # PersonId
                    row[1],  # PersonNumber
                    row[2] if row[2] else None,  # DateOfBirth
                    row[3] if row[3] else None,  # DateOfDeath
                    row[4] if row[4] else None,  # CountryOfBirth
                    row[5] if row[5] else None,  # RegionOfBirth
                    row[6] if row[6] else None,  # TownOfBirth
                    row[7] if row[7] else None,  # ApplicantNumber
                    row[8] if len(row) > 8 and row[8] else None,  # ATTRIBUTE1
                    source
                ))
                count += 1
            except Exception as e:
                print(f"Error inserting person row: {e}")

    return count


def load_person_name_file(
    cursor: pyodbc.Cursor,
    headers: List[str],
    rows: List[List[str]],
    source: str
) -> int:
    """
    Load Person Name data into HCM_PERSON_NAME_INTF table.
    """
    insert_sql = """
        INSERT INTO HCM_PERSON_NAME_INTF (
            PersonId, EffectiveStartDate, EffectiveEndDate, LegislationCode,
            NameType, FirstName, MiddleNames, LastName, Title, Suffix,
            DisplayName, KnownAs, SOURCE
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    count = 0
    for row in rows:
        if len(row) >= 11:
            try:
                cursor.execute(insert_sql, (
                    row[0],  # PersonId
                    row[1] if row[1] else None,  # EffectiveStartDate
                    row[2] if row[2] else None,  # EffectiveEndDate
                    row[3] if row[3] else None,  # LegislationCode
                    row[4] if row[4] else None,  # NameType
                    row[5] if row[5] else None,  # FirstName
                    row[6] if row[6] else None,  # MiddleNames
                    row[7] if row[7] else None,  # LastName
                    row[8] if row[8] else None,  # Title
                    row[9] if row[9] else None,  # Suffix
                    row[10] if row[10] else None,  # DisplayName
                    row[11] if len(row) > 11 and row[11] else None,  # KnownAs
                    source
                ))
                count += 1
            except Exception as e:
                print(f"Error inserting person name row: {e}")

    return count


def load_person_assignment_file(
    cursor: pyodbc.Cursor,
    headers: List[str],
    rows: List[List[str]],
    source: str
) -> int:
    """
    Load Person Assignment data into HCM_PERSON_ASSIGNMENT_INTF table.
    """
    # This is a larger table with many columns
    insert_sql = """
        INSERT INTO HCM_PERSON_ASSIGNMENT_INTF (
            PersonId, AssignmentNumber, AssignmentName, EffectiveStartDate,
            EffectiveEndDate, EffectiveSequence, EffectiveLatestChange,
            AssignmentType, AssignmentStatusType, WorkerType, LegalEmployerName,
            BusinessUnitName, DepartmentName, JobName, GradeName, LocationName,
            PositionName, ManagerPersonNumber, ActionCode, ActionReasonCode,
            SOURCE
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    count = 0
    for row in rows:
        if len(row) >= 19:
            try:
                cursor.execute(insert_sql, (
                    row[0],  # PersonId
                    row[1] if row[1] else None,  # AssignmentNumber
                    row[2] if row[2] else None,  # AssignmentName
                    row[3] if row[3] else None,  # EffectiveStartDate
                    row[4] if row[4] else None,  # EffectiveEndDate
                    row[5] if row[5] else None,  # EffectiveSequence
                    row[6] if row[6] else None,  # EffectiveLatestChange
                    row[7] if row[7] else None,  # AssignmentType
                    row[8] if row[8] else None,  # AssignmentStatusType
                    row[9] if row[9] else None,  # WorkerType
                    row[10] if row[10] else None,  # LegalEmployerName
                    row[11] if row[11] else None,  # BusinessUnitName
                    row[12] if row[12] else None,  # DepartmentName
                    row[13] if row[13] else None,  # JobName
                    row[14] if row[14] else None,  # GradeName
                    row[15] if row[15] else None,  # LocationName
                    row[16] if row[16] else None,  # PositionName
                    row[17] if row[17] else None,  # ManagerPersonNumber
                    row[18] if row[18] else None,  # ActionCode
                    row[19] if len(row) > 19 and row[19] else None,  # ActionReasonCode
                    source
                ))
                count += 1
            except Exception as e:
                print(f"Error inserting assignment row: {e}")

    return count


def load_person_address_file(
    cursor: pyodbc.Cursor,
    headers: List[str],
    rows: List[List[str]],
    source: str
) -> int:
    """
    Load Person Address data into HCM_PERSON_ADDRESS_INTF table.
    """
    insert_sql = """
        INSERT INTO HCM_PERSON_ADDRESS_INTF (
            PersonId, AddressId, EffectiveStartDate, EffectiveEndDate,
            AddressType, AddressLine1, AddressLine2, AddressLine3,
            City, Region, PostalCode, Country, SOURCE
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    count = 0
    for row in rows:
        if len(row) >= 11:
            try:
                cursor.execute(insert_sql, (
                    row[0],  # PersonId
                    row[1] if row[1] else None,  # AddressId
                    row[2] if row[2] else None,  # EffectiveStartDate
                    row[3] if row[3] else None,  # EffectiveEndDate
                    row[4] if row[4] else None,  # AddressType
                    row[5] if row[5] else None,  # AddressLine1
                    row[6] if row[6] else None,  # AddressLine2
                    row[7] if row[7] else None,  # AddressLine3
                    row[8] if row[8] else None,  # City
                    row[9] if row[9] else None,  # Region
                    row[10] if row[10] else None,  # PostalCode
                    row[11] if len(row) > 11 and row[11] else None,  # Country
                    source
                ))
                count += 1
            except Exception as e:
                print(f"Error inserting address row: {e}")

    return count


def load_person_nid_file(
    cursor: pyodbc.Cursor,
    headers: List[str],
    rows: List[List[str]],
    source: str
) -> int:
    """
    Load Person National ID data into HCM_PERSON_NID_INTF table.
    """
    insert_sql = """
        INSERT INTO HCM_PERSON_NID_INTF (
            PersonId, NationalIdentifierType, NationalIdentifierNumber,
            IssueDate, ExpirationDate, PlaceOfIssue, LegislationCode, SOURCE
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """

    count = 0
    for row in rows:
        if len(row) >= 6:
            try:
                cursor.execute(insert_sql, (
                    row[0],  # PersonId
                    row[1] if row[1] else None,  # NationalIdentifierType
                    row[2] if row[2] else None,  # NationalIdentifierNumber
                    row[3] if row[3] else None,  # IssueDate
                    row[4] if row[4] else None,  # ExpirationDate
                    row[5] if row[5] else None,  # PlaceOfIssue
                    row[6] if len(row) > 6 and row[6] else None,  # LegislationCode
                    source
                ))
                count += 1
            except Exception as e:
                print(f"Error inserting NID row: {e}")

    return count


def load_person_supervisor_file(
    cursor: pyodbc.Cursor,
    headers: List[str],
    rows: List[List[str]],
    source: str
) -> int:
    """
    Load Person Supervisor data into HCM_PERSON_SUPERVISOR_INTF table.
    """
    insert_sql = """
        INSERT INTO HCM_PERSON_SUPERVISOR_INTF (
            PersonId, AssignmentNumber, ManagerPersonNumber, ManagerType,
            EffectiveStartDate, EffectiveEndDate, SOURCE
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """

    count = 0
    for row in rows:
        if len(row) >= 5:
            try:
                cursor.execute(insert_sql, (
                    row[0],  # PersonId
                    row[1] if row[1] else None,  # AssignmentNumber
                    row[2] if row[2] else None,  # ManagerPersonNumber
                    row[3] if row[3] else None,  # ManagerType
                    row[4] if row[4] else None,  # EffectiveStartDate
                    row[5] if len(row) > 5 and row[5] else None,  # EffectiveEndDate
                    source
                ))
                count += 1
            except Exception as e:
                print(f"Error inserting supervisor row: {e}")

    return count


def load_person_email_file(
    cursor: pyodbc.Cursor,
    headers: List[str],
    rows: List[List[str]],
    source: str
) -> int:
    """
    Load Person Email data into HCM_PERSON_EMAIL_INTF table.
    """
    insert_sql = """
        INSERT INTO HCM_PERSON_EMAIL_INTF (
            PersonId, DateFrom, DateTo, EmailType, EmailAddress, SOURCE
        ) VALUES (?, ?, ?, ?, ?, ?)
    """

    count = 0
    for row in rows:
        if len(row) >= 4:
            try:
                cursor.execute(insert_sql, (
                    row[0],  # PersonId
                    row[1] if row[1] else None,  # DateFrom
                    row[2] if row[2] else None,  # DateTo
                    row[3] if row[3] else None,  # EmailType
                    row[4] if len(row) > 4 and row[4] else None,  # EmailAddress
                    source
                ))
                count += 1
            except Exception as e:
                print(f"Error inserting email row: {e}")

    return count


def load_seniority_file(
    cursor: pyodbc.Cursor,
    headers: List[str],
    rows: List[List[str]],
    source: str
) -> int:
    """
    Load Seniority data into HCM_SENIORITY_INTF table.
    """
    insert_sql = """
        INSERT INTO HCM_SENIORITY_INTF (
            PersonId, SeniorityDate, LegalEmployerName, SeniorityType,
            SeniorityValue, SOURCE
        ) VALUES (?, ?, ?, ?, ?, ?)
    """

    count = 0
    for row in rows:
        if len(row) >= 4:
            try:
                cursor.execute(insert_sql, (
                    row[0],  # PersonId
                    row[1] if row[1] else None,  # SeniorityDate
                    row[2] if row[2] else None,  # LegalEmployerName
                    row[3] if row[3] else None,  # SeniorityType
                    row[4] if len(row) > 4 and row[4] else None,  # SeniorityValue
                    source
                ))
                count += 1
            except Exception as e:
                print(f"Error inserting seniority row: {e}")

    return count


# Mapping of file types to loader functions
LOADER_FUNCTIONS = {
    'PERSON': load_person_file,
    'PERSON_NAME': load_person_name_file,
    'PERSON_ASSIGNMENT': load_person_assignment_file,
    'PERSON_ADDRESS': load_person_address_file,
    'PERSON_NID': load_person_nid_file,
    'PERSON_SUPERVISOR': load_person_supervisor_file,
    'PERSON_EMAIL': load_person_email_file,
    'SENIORITY': load_seniority_file
}


def load_file_to_database(
    bucket: str,
    s3_key: str,
    connection: pyodbc.Connection = None,
    s3_client=None
) -> LoadResult:
    """
    Load a single file from S3 into the database.

    Args:
        bucket: S3 bucket name
        s3_key: S3 object key
        connection: Database connection (optional, will create if not provided)
        s3_client: S3 client (optional)

    Returns:
        LoadResult with load status
    """
    filename = s3_key.split('/')[-1]
    start_time = datetime.now()

    # Extract file type and source from filename
    file_type = extract_file_type_from_filename(filename)
    source = extract_source_from_filename(filename)

    if not file_type:
        return LoadResult(
            success=False,
            file_name=filename,
            table_name="",
            rows_loaded=0,
            error_message=f"Could not determine file type from filename: {filename}"
        )

    if not source:
        return LoadResult(
            success=False,
            file_name=filename,
            table_name="",
            rows_loaded=0,
            error_message=f"Could not determine source from filename: {filename}"
        )

    table_name = FILE_TYPE_TO_TABLE.get(file_type)
    if not table_name:
        return LoadResult(
            success=False,
            file_name=filename,
            table_name="",
            rows_loaded=0,
            error_message=f"Unknown file type: {file_type}"
        )

    loader_func = LOADER_FUNCTIONS.get(file_type)
    if not loader_func:
        return LoadResult(
            success=False,
            file_name=filename,
            table_name=table_name,
            rows_loaded=0,
            error_message=f"No loader function for file type: {file_type}"
        )

    try:
        # Read CSV from S3
        headers, rows = read_csv_from_s3(bucket, s3_key, s3_client)

        if not rows:
            return LoadResult(
                success=True,
                file_name=filename,
                table_name=table_name,
                rows_loaded=0,
                duration_seconds=(datetime.now() - start_time).total_seconds()
            )

        # Create connection if not provided
        close_connection = False
        if connection is None:
            connection = get_db_connection()
            close_connection = True

        cursor = connection.cursor()

        # Load data
        rows_loaded = loader_func(cursor, headers, rows, source)
        connection.commit()

        if close_connection:
            connection.close()

        return LoadResult(
            success=True,
            file_name=filename,
            table_name=table_name,
            rows_loaded=rows_loaded,
            duration_seconds=(datetime.now() - start_time).total_seconds()
        )

    except Exception as e:
        return LoadResult(
            success=False,
            file_name=filename,
            table_name=table_name,
            rows_loaded=0,
            error_message=str(e),
            duration_seconds=(datetime.now() - start_time).total_seconds()
        )


def load_multiple_files(
    bucket: str,
    s3_keys: List[str],
    s3_client=None
) -> Dict:
    """
    Load multiple files from S3 into the database.

    Args:
        bucket: S3 bucket name
        s3_keys: List of S3 object keys

    Returns:
        Summary of load operations
    """
    results = []
    success_count = 0
    failure_count = 0
    total_rows = 0

    # Create single connection for all files
    connection = get_db_connection()

    for s3_key in s3_keys:
        result = load_file_to_database(bucket, s3_key, connection, s3_client)
        results.append(result)

        if result.success:
            success_count += 1
            total_rows += result.rows_loaded
        else:
            failure_count += 1

    connection.close()

    return {
        "total_files": len(s3_keys),
        "success_count": success_count,
        "failure_count": failure_count,
        "total_rows_loaded": total_rows,
        "results": [
            {
                "success": r.success,
                "file_name": r.file_name,
                "table_name": r.table_name,
                "rows_loaded": r.rows_loaded,
                "error_message": r.error_message,
                "duration_seconds": r.duration_seconds
            }
            for r in results
        ]
    }


def execute_hcm_main_interface(connection: pyodbc.Connection = None, test_mode: bool = False) -> Dict:
    """
    Execute the HCM_MAIN_INTF stored procedure.
    Equivalent to RunHCMInterface.sql: EXEC dbo.HCM_MAIN_INTF @test_execution = 'N';
    """
    close_connection = False
    if connection is None:
        connection = get_db_connection()
        close_connection = True

    try:
        cursor = connection.cursor()
        test_param = 'Y' if test_mode else 'N'
        cursor.execute(f"EXEC dbo.HCM_MAIN_INTF @test_execution = '{test_param}'")
        connection.commit()

        if close_connection:
            connection.close()

        return {
            "success": True,
            "message": f"HCM_MAIN_INTF executed successfully (test_mode={test_mode})"
        }
    except Exception as e:
        if close_connection:
            connection.close()

        return {
            "success": False,
            "error": str(e)
        }


# Lambda handler
def lambda_handler(event, context):
    """
    AWS Lambda handler for database loading.

    Expected event format:
    {
        "action": "load_file" | "load_multiple" | "execute_interface",
        "bucket": "s3-bucket-name",
        "s3_key": "path/to/file.csv" (for load_file),
        "s3_keys": ["list", "of", "keys"] (for load_multiple),
        "test_mode": false (for execute_interface)
    }
    """
    action = event.get("action", "load_file")

    if action == "load_file":
        bucket = event.get("bucket")
        s3_key = event.get("s3_key")

        if not bucket or not s3_key:
            return {
                "statusCode": 400,
                "body": {"error": "bucket and s3_key are required"}
            }

        result = load_file_to_database(bucket, s3_key)
        return {
            "statusCode": 200 if result.success else 500,
            "body": {
                "success": result.success,
                "file_name": result.file_name,
                "table_name": result.table_name,
                "rows_loaded": result.rows_loaded,
                "error_message": result.error_message,
                "duration_seconds": result.duration_seconds
            }
        }

    elif action == "load_multiple":
        bucket = event.get("bucket")
        s3_keys = event.get("s3_keys", [])

        if not bucket or not s3_keys:
            return {
                "statusCode": 400,
                "body": {"error": "bucket and s3_keys are required"}
            }

        result = load_multiple_files(bucket, s3_keys)
        return {
            "statusCode": 200,
            "body": result
        }

    elif action == "execute_interface":
        test_mode = event.get("test_mode", False)
        result = execute_hcm_main_interface(test_mode=test_mode)
        return {
            "statusCode": 200 if result["success"] else 500,
            "body": result
        }

    return {
        "statusCode": 400,
        "body": {"error": f"Unknown action: {action}"}
    }


if __name__ == "__main__":
    # Test source extraction
    test_files = [
        "HCM_PERSON_ADDRESS_INTF_FIMAS_20251126160556.csv",
        "HCM_PERSON_ASSIGNMENT_INTF_HAC88_20251205.csv",
        "hcm_person_address_rhum75_20260109.csv",
        "hcm_person_assign_intf_rhum75_20260116.csv",
    ]

    print("=" * 60)
    print("SOURCE AND FILE TYPE EXTRACTION TEST")
    print("=" * 60)

    for f in test_files:
        source = extract_source_from_filename(f)
        file_type = extract_file_type_from_filename(f)
        table = FILE_TYPE_TO_TABLE.get(file_type, "UNKNOWN")
        print(f"\n{f}")
        print(f"  Source: {source}")
        print(f"  File Type: {file_type}")
        print(f"  Table: {table}")
