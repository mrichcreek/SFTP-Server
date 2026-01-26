"""
Column Schema Validator for HCM Interface Files

This module validates that CSV files have the expected columns
before loading them to SQL Server. It can:
1. Query SQL Server tables to get actual column definitions
2. Compare CSV headers against expected table columns
3. Generate validation reports

Rules:
- CSV columns must exist in the target table (extra CSV columns = ERROR)
- CSV can have fewer columns than the table (missing columns = OK, will be NULL)
"""

import boto3
from typing import Dict, List, Optional, Tuple, Set
from dataclasses import dataclass
import csv
import io
import re


@dataclass
class ColumnValidationResult:
    """Result of validating a single file's columns."""
    file_name: str
    table_name: str
    is_valid: bool
    csv_columns: List[str]
    table_columns: List[str]
    extra_csv_columns: List[str]  # Columns in CSV but not in table (ERROR)
    missing_table_columns: List[str]  # Columns in table but not in CSV (OK)
    error_message: Optional[str] = None


@dataclass
class SchemaValidationReport:
    """Complete schema validation report for multiple files."""
    total_files: int
    valid_files: int
    invalid_files: int
    results: List[ColumnValidationResult]

    @property
    def has_errors(self) -> bool:
        return self.invalid_files > 0


# Expected column definitions for each HCM interface table
# These are the columns that the staging tables expect
# Format: TABLE_BASE_NAME -> list of expected columns

EXPECTED_COLUMNS = {
    'HCM_PERSON_ADDRESS_INTF': [
        'SourceSystemOwner', 'SourceSystemId', 'EffectiveStartDate', 'EffectiveEndDate',
        'PersonId', 'AddressType', 'AddressLine1', 'AddressLine2', 'AddressLine3',
        'TownOrCity', 'Region1', 'Region2', 'Country', 'PostalCode', 'LongPostalCode',
        'PrimaryFlag', 'PersonNumber', 'LegislationCode', 'GUID'
    ],
    'HCM_PERSON_ASSIGNMENT_INTF': [
        'SourceSystemOwner', 'SourceSystemId', 'EffectiveStartDate', 'EffectiveEndDate',
        'EffectiveSequence', 'EffectiveLatestChange', 'ActionCode', 'ReasonCode',
        'WorkerType', 'LegalEmployerName', 'PersonNumber', 'PersonId', 'PeriodOfServiceId',
        'AssignmentName', 'AssignmentNumber', 'AssignmentStatusTypeCode', 'AssignmentStatusType',
        'BusinessUnitName', 'AssignmentType', 'WorkTermsAssignmentId', 'PrimaryAssignmentFlag',
        'PrimaryFlag', 'SystemPersonType', 'UserPersonType', 'JobCode', 'DepartmentName',
        'LocationCode', 'WorkerCategory', 'AssignmentCategory', 'FullPartTime', 'DateStart',
        'ManagerFlag', 'NormalHours', 'Frequency', 'GradeCode', 'PositionCode',
        'BargainingUnitCode', 'UnionId', 'HourlySalariedCode', 'ProjectedEndDate',
        'NoticePeriod', 'NoticePeriodUOM', 'ProbationEndDate', 'ProbationPeriod',
        'ProbationUnit', 'InternalBuilding', 'InternalFloor', 'InternalOfficeNumber',
        'InternalMailstop', 'DefaultExpenseAccount', 'PeopleGroup', 'GUID'
    ],
    'HCM_PERSON_NAME_INTF': [
        'SourceSystemOwner', 'SourceSystemId', 'PersonId', 'PersonNumber',
        'EffectiveStartDate', 'EffectiveEndDate', 'LegislationCode', 'NameType',
        'FirstName', 'MiddleNames', 'LastName', 'Suffix', 'Honors', 'KnownAs',
        'PreNameAdjunct', 'MilitaryRank', 'PreviousLastName', 'Title', 'GUID'
    ],
    'HCM_PERSON_NID_INTF': [
        'SourceSystemOwner', 'SourceSystemId', 'PersonId', 'PersonNumber',
        'LegislationCode', 'IssueDate', 'ExpirationDate', 'PlaceOfIssue',
        'NationalIdentifierType', 'NationalIdentifierNumber', 'PrimaryFlag', 'GUID'
    ],
    'HCM_PERSON_SUPERVISOR_INTF': [
        'SourceSystemOwner', 'SourceSystemId', 'EffectiveStartDate', 'EffectiveEndDate',
        'PersonNumber', 'AssignmentNumber', 'ManagerType', 'ManagerPersonNumber',
        'ManagerAssignmentNumber', 'PrimaryFlag', 'GUID'
    ],
    'HCM_PERSON_EMAIL_INTF': [
        'SourceSystemOwner', 'SourceSystemId', 'PersonId', 'PersonNumber',
        'DateFrom', 'DateTo', 'EmailType', 'EmailAddress', 'PrimaryFlag', 'GUID'
    ],
    'HCM_EXTERNAL_IDENTIFIER_INTF': [
        'SourceSystemOwner', 'SourceSystemId', 'PersonNumber', 'PersonId',
        'ExternalIdType', 'ExternalId', 'LegislationCode', 'GUID'
    ],
    'HCM_DEPARTMENT_INTF': [
        'SourceSystemOwner', 'SourceSystemId', 'EffectiveStartDate', 'EffectiveEndDate',
        'Name', 'DepartmentCode', 'OrganizationId', 'SetId', 'Status', 'GUID'
    ],
    'HCM_JOBS_INTF': [
        'SourceSystemOwner', 'SourceSystemId', 'EffectiveStartDate', 'EffectiveEndDate',
        'SetId', 'JobCode', 'Name', 'JobId', 'ActiveStatus', 'ApprovalAuthority',
        'JobFamilyId', 'JobFunctionCode', 'RegularTemporary', 'FullPartTime',
        'BenchmarkJobFlag', 'MedicalCheckupRequired', 'ProgressionJobId', 'GUID'
    ],
    'HCM_LOCATION_INTF': [
        'SourceSystemOwner', 'SourceSystemId', 'EffectiveStartDate', 'EffectiveEndDate',
        'LocationId', 'SetId', 'LocationCode', 'LocationName', 'Description', 'Status',
        'MainPhoneCountryCode', 'MainPhoneAreaCode', 'MainPhoneNumber',
        'FaxCountryCode', 'FaxAreaCode', 'FaxNumber', 'EmailAddress',
        'AddressLine1', 'AddressLine2', 'AddressLine3', 'TownOrCity',
        'Region1', 'Region2', 'Country', 'PostalCode', 'GUID'
    ],
    # Worker files (alternative naming)
    'Worker': [
        'SourceSystemOwner', 'SourceSystemId', 'PersonId', 'PersonNumber',
        'EffectiveStartDate', 'EffectiveEndDate', 'LegislationCode', 'NameType',
        'FirstName', 'MiddleNames', 'LastName', 'Suffix', 'Honors', 'KnownAs',
        'PreNameAdjunct', 'MilitaryRank', 'PreviousLastName', 'Title', 'GUID'
    ],
    'Worker_Assignment': [
        'SourceSystemOwner', 'SourceSystemId', 'EffectiveStartDate', 'EffectiveEndDate',
        'EffectiveSequence', 'EffectiveLatestChange', 'ActionCode', 'ReasonCode',
        'WorkerType', 'LegalEmployerName', 'PersonNumber', 'PersonId', 'PeriodOfServiceId',
        'AssignmentName', 'AssignmentNumber', 'AssignmentStatusTypeCode', 'AssignmentStatusType',
        'BusinessUnitName', 'AssignmentType', 'WorkTermsAssignmentId', 'PrimaryAssignmentFlag',
        'PrimaryFlag', 'SystemPersonType', 'UserPersonType', 'JobCode', 'DepartmentName',
        'LocationCode', 'WorkerCategory', 'AssignmentCategory', 'FullPartTime', 'DateStart',
        'ManagerFlag', 'NormalHours', 'Frequency', 'GradeCode', 'PositionCode',
        'BargainingUnitCode', 'UnionId', 'HourlySalariedCode', 'ProjectedEndDate',
        'NoticePeriod', 'NoticePeriodUOM', 'ProbationEndDate', 'ProbationPeriod',
        'ProbationUnit', 'InternalBuilding', 'InternalFloor', 'InternalOfficeNumber',
        'InternalMailstop', 'DefaultExpenseAccount', 'PeopleGroup', 'GUID'
    ]
}


def normalize_column_name(name: str) -> str:
    """Normalize a column name for comparison (case-insensitive, no spaces)."""
    return name.strip().lower().replace(' ', '_').replace('-', '_')


def get_base_entity_from_filename(filename: str) -> Optional[str]:
    """
    Extract the base entity type from a filename.

    Examples:
        HCM_PERSON_ADDRESS_INTF_FIMAS_20251209.csv -> HCM_PERSON_ADDRESS_INTF
        Worker_Assignment_HAC88_20251205.csv -> Worker_Assignment
    """
    # Remove extension
    name = filename.rsplit('.', 1)[0] if '.' in filename else filename

    # Remove date portion (last part after underscore if it's all digits)
    parts = name.rsplit('_', 1)
    if len(parts) == 2 and parts[1].isdigit():
        name = parts[0]

    # Try to match against known entity patterns
    name_upper = name.upper()

    # Check for HCM_*_INTF pattern
    for entity in EXPECTED_COLUMNS:
        entity_upper = entity.upper()
        if name_upper.startswith(entity_upper):
            return entity

    # Check if it's a Worker file
    if 'WORKER_ASSIGNMENT' in name_upper or 'WORKERASSIGNMENT' in name_upper:
        return 'Worker_Assignment'
    if 'WORKER' in name_upper:
        return 'Worker'

    return None


def get_csv_headers_from_s3(s3_client, bucket: str, key: str) -> List[str]:
    """Read the first line of a CSV file from S3 to get column headers."""
    response = s3_client.get_object(Bucket=bucket, Key=key, Range='bytes=0-10000')
    content = response['Body'].read().decode('utf-8', errors='ignore')

    # Parse first line as CSV
    first_line = content.split('\n')[0].strip()
    reader = csv.reader(io.StringIO(first_line))
    headers = next(reader, [])

    # Clean headers - remove quotes, extra spaces
    cleaned_headers = [h.strip().strip('"').strip() for h in headers]

    return cleaned_headers


def validate_file_columns(
    filename: str,
    csv_columns: List[str],
    expected_columns: Optional[List[str]] = None
) -> ColumnValidationResult:
    """
    Validate that a CSV file's columns match the expected table schema.

    Args:
        filename: Name of the CSV file
        csv_columns: List of column names from the CSV header
        expected_columns: Optional list of expected columns (if None, auto-detect from filename)

    Returns:
        ColumnValidationResult with validation details
    """
    # Determine the base entity type
    entity = get_base_entity_from_filename(filename)

    if expected_columns is None:
        if entity and entity in EXPECTED_COLUMNS:
            expected_columns = EXPECTED_COLUMNS[entity]
        else:
            # Can't validate - unknown entity type
            return ColumnValidationResult(
                file_name=filename,
                table_name=entity or 'UNKNOWN',
                is_valid=True,  # Pass validation if we don't know the schema
                csv_columns=csv_columns,
                table_columns=[],
                extra_csv_columns=[],
                missing_table_columns=[],
                error_message=f"Unknown entity type, skipping column validation"
            )

    # Normalize column names for comparison
    csv_cols_normalized = {normalize_column_name(c): c for c in csv_columns}
    expected_cols_normalized = {normalize_column_name(c): c for c in expected_columns}

    # Find extra columns in CSV that don't exist in table (ERROR)
    extra_csv = []
    for norm_name, orig_name in csv_cols_normalized.items():
        if norm_name and norm_name not in expected_cols_normalized:
            extra_csv.append(orig_name)

    # Find columns in table that don't exist in CSV (OK - will be NULL)
    missing_table = []
    for norm_name, orig_name in expected_cols_normalized.items():
        if norm_name and norm_name not in csv_cols_normalized:
            missing_table.append(orig_name)

    # Validation fails if there are extra CSV columns
    is_valid = len(extra_csv) == 0

    error_message = None
    if not is_valid:
        error_message = f"CSV has columns not in table: {', '.join(extra_csv)}"

    return ColumnValidationResult(
        file_name=filename,
        table_name=entity or 'UNKNOWN',
        is_valid=is_valid,
        csv_columns=csv_columns,
        table_columns=expected_columns,
        extra_csv_columns=extra_csv,
        missing_table_columns=missing_table,
        error_message=error_message
    )


def validate_files_schema(
    files: List[Dict],
    s3_client,
    bucket: str
) -> SchemaValidationReport:
    """
    Validate column schemas for multiple files.

    Args:
        files: List of file dicts with 'filename' and 's3_key'
        s3_client: Boto3 S3 client
        bucket: S3 bucket name

    Returns:
        SchemaValidationReport with all validation results
    """
    results = []

    for file_info in files:
        filename = file_info.get('filename', '')
        s3_key = file_info.get('s3_key', '')

        if not filename.lower().endswith('.csv'):
            continue

        try:
            # Get CSV headers
            csv_columns = get_csv_headers_from_s3(s3_client, bucket, s3_key)

            # Validate
            result = validate_file_columns(filename, csv_columns)
            results.append(result)

        except Exception as e:
            results.append(ColumnValidationResult(
                file_name=filename,
                table_name='ERROR',
                is_valid=False,
                csv_columns=[],
                table_columns=[],
                extra_csv_columns=[],
                missing_table_columns=[],
                error_message=f"Failed to read file: {str(e)}"
            ))

    valid_count = sum(1 for r in results if r.is_valid)
    invalid_count = len(results) - valid_count

    return SchemaValidationReport(
        total_files=len(results),
        valid_files=valid_count,
        invalid_files=invalid_count,
        results=results
    )


def generate_schema_validation_report(report: SchemaValidationReport) -> str:
    """Generate a human-readable schema validation report."""
    lines = []
    lines.append("=" * 80)
    lines.append("COLUMN SCHEMA VALIDATION REPORT")
    lines.append("=" * 80)
    lines.append("")
    lines.append(f"Total Files: {report.total_files}")
    lines.append(f"Valid Files: {report.valid_files}")
    lines.append(f"Invalid Files: {report.invalid_files}")
    lines.append("")

    if report.has_errors:
        lines.append("-" * 40)
        lines.append("VALIDATION ERRORS:")
        lines.append("-" * 40)

        for result in report.results:
            if not result.is_valid:
                lines.append(f"\nFile: {result.file_name}")
                lines.append(f"  Entity: {result.table_name}")
                lines.append(f"  Error: {result.error_message}")
                if result.extra_csv_columns:
                    lines.append(f"  Extra CSV columns: {', '.join(result.extra_csv_columns)}")

        lines.append("")

    lines.append("-" * 40)
    lines.append("FILE DETAILS:")
    lines.append("-" * 40)

    for result in report.results:
        status = "✓ VALID" if result.is_valid else "✗ INVALID"
        lines.append(f"\n{status}: {result.file_name}")
        lines.append(f"  Entity: {result.table_name}")
        lines.append(f"  CSV columns: {len(result.csv_columns)}")
        if result.table_columns:
            lines.append(f"  Table columns: {len(result.table_columns)}")
        if result.missing_table_columns:
            lines.append(f"  Missing in CSV (will be NULL): {len(result.missing_table_columns)}")
        if result.error_message:
            lines.append(f"  Note: {result.error_message}")

    lines.append("")
    lines.append("=" * 80)

    return "\n".join(lines)


def query_table_columns_from_sql(
    secret_name: str,
    table_name: str,
    database_override: Optional[str] = None
) -> List[str]:
    """
    Query SQL Server to get actual column names for a table.

    Args:
        secret_name: AWS Secrets Manager secret name
        table_name: Name of the table to query
        database_override: Optional database name override

    Returns:
        List of column names from the table
    """
    import pymssql

    # Get connection info
    def get_aws_secret(secret_name: str, region: str = 'us-east-1') -> str:
        client = boto3.client('secretsmanager', region_name=region)
        response = client.get_secret_value(SecretId=secret_name)
        return response['SecretString'] if 'SecretString' in response else response['SecretBinary'].decode('utf-8')

    def parse_connection_string(conn_str: str) -> Dict:
        params = {}
        for part in conn_str.split(';'):
            if '=' in part:
                key, value = part.split('=', 1)
                key = key.strip().upper()
                value = value.strip()
                if key == 'SERVER':
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

    connection_string = get_aws_secret(secret_name)
    conn_params = parse_connection_string(connection_string)

    if database_override:
        conn_params['database'] = database_override

    columns = []

    with pymssql.connect(
        server=conn_params['server'],
        port=conn_params.get('port', 1433),
        user=conn_params['user'],
        password=conn_params['password'],
        database=conn_params['database'],
        tds_version='7.3',
        autocommit=True
    ) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME = %s AND TABLE_SCHEMA = 'dbo'
            ORDER BY ORDINAL_POSITION
        """, (table_name,))

        for row in cursor.fetchall():
            columns.append(row[0])

    return columns


def refresh_schema_from_sql(
    secret_name: str = 'Hacienda_ERP_Test_MSSQL_text',
    database_override: Optional[str] = None
) -> Dict[str, List[str]]:
    """
    Query SQL Server to get current column definitions for all HCM tables.

    Returns:
        Dict mapping table name to list of column names
    """
    tables_to_check = [
        'HCM_PERSON_ADDRESS_INTF',
        'HCM_PERSON_ASSIGNMENT_INTF',
        'HCM_PERSON_NAME_INTF',
        'HCM_PERSON_NID_INTF',
        'HCM_PERSON_SUPERVISOR_INTF',
        'HCM_PERSON_EMAIL_INTF',
        'HCM_EXTERNAL_IDENTIFIER_INTF',
        'HCM_DEPARTMENT_INTF',
        'HCM_JOBS_INTF',
        'HCM_LOCATION_INTF'
    ]

    schema = {}

    for table in tables_to_check:
        try:
            columns = query_table_columns_from_sql(secret_name, table, database_override)
            if columns:
                schema[table] = columns
        except Exception as e:
            # Table might not exist, use default
            if table in EXPECTED_COLUMNS:
                schema[table] = EXPECTED_COLUMNS[table]

    return schema
