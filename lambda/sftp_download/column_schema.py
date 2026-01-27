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
from typing import Dict, List, Optional, Tuple, Set, Union
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


# Source aliases mapping (from LoadIntfData.py)
SOURCE_ALIASES = {
    'HAC88': 'HACIENDA',
    'HACIENDA': 'HACIENDA',
    'RHUM': 'RHUM',
    'FIMAS': 'FIMAS',
    'KRONOSPOL': 'KRONOSPOL',
    'DOE': 'DOE',
    'ADPPOLICIA': 'ADPPOLICIA',
    '911': '911',
    'KRONOSDE': 'KRONOSDE',
    'SEPI': 'SEPI'
}

# Source groups - which sources use which column naming convention
# PeopleSoft-style sources use different column names than Oracle HCM-style
PEOPLESOFT_SOURCES = ['RHUM', 'FIMAS', 'HACIENDA']
ORACLE_HCM_SOURCES = ['KRONOSPOL', 'DOE', 'ADPPOLICIA', 'KRONOSDE', '911', 'SEPI']


# =============================================================================
# SOURCE-SPECIFIC COLUMN DEFINITIONS
# =============================================================================
# Different sources (RHUM/FIMAS/HACIENDA vs KRONOSPOL/DOE/ADPPOLICIA) use
# different column names for the same logical entity. This section defines
# the expected CSV columns for each source type.

# Format: (source_group, entity) -> list of expected CSV columns
# source_group: 'PEOPLESOFT' or 'ORACLE_HCM'

SOURCE_SPECIFIC_COLUMNS = {
    # =========================================================================
    # HCM_PERSON_INTF - Person/Worker base file
    # =========================================================================
    ('PEOPLESOFT', 'HCM_PERSON_INTF'): [
        'COUNTRY', 'EMPLID', 'EFFDT', 'START_DATE', 'BIRTHDATE'
    ],
    ('ORACLE_HCM', 'HCM_PERSON_INTF'): [
        'COUNTRY_CODE', 'SWIFT_PERSON_TYPE', 'PERSON_NUMBER',
        'EFFECTIVE_START_DATE', 'START_DATE', 'DATE_OF_BIRTH',
        'EFFECTIVE_END_DATE', 'ATTRIBUTE_CATEGORY', 'ATTRIBUTE1'
    ],
    ('ADPPOLICIA', 'HCM_PERSON_INTF'): [
        'COUNTRY_CODE', 'SWIFT_PERSON_TYPE', 'PERSON_NUMBER',
        'EFFECTIVE_START_DATE', 'EFFECTIVE_END_DATE', 'START_DATE',
        'DATE_OF_BIRTH', 'SOURCE_SYSTEM_OWNER'
    ],
    ('DOE', 'HCM_PERSON_INTF'): [
        'COUNTRY_CODE', 'SWIFT_PERSON_TYPE', 'PERSON_NUMBER',
        'EFFECTIVE_START_DATE', 'START_DATE', 'DATE_OF_BIRTH',
        'ATTRIBUTE_CATEGORY', 'ATTRIBUTE1'
    ],

    # =========================================================================
    # HCM_PERSON_ADDRESS_INTF - Address file
    # =========================================================================
    ('PEOPLESOFT', 'HCM_PERSON_ADDRESS_INTF'): [
        'COUNTRY', 'EMPLID', 'ADDRESS_TYPE', 'EFFDT',
        'ADDRESS1', 'ADDRESS2', 'ADDRESS3', 'ADDRESS4',
        'COUNTY', 'STATE', 'REG_REGION', 'CITY', 'POSTAL',
        'EFF_STATUS', 'PER_TYPE', 'EMPL_STATUS', 'X'
    ],
    ('ORACLE_HCM', 'HCM_PERSON_ADDRESS_INTF'): [
        'COUNTRY_CODE', 'SWIFT_PERSON_TYPE', 'PERSON_NUMBER', 'ADDRESS_TYPE',
        'EFFECTIVE_START_DATE', 'EFFECTIVE_END_DATE', 'COUNTRY',
        'ADDRESS_LINE_1', 'ADDRESS_LINE_2', 'ADDRESS_LINE_3', 'ADDRESS_LINE_4',
        'REGION_1', 'REGION_2', 'REGION_3', 'TOWN_OR_CITY',
        'POSTAL_CODE', 'LONG_POSTAL_CODE', 'PRIMARY_FLAG',
        'ATTRIBUTE_CATEGORY', 'ATTRIBUTE1'
    ],
    ('DOE', 'HCM_PERSON_ADDRESS_INTF'): [
        'COUNTRY_CODE', 'SWIFT_PERSON_TYPE', 'PERSON_NUMBER', 'ADDRESS_TYPE',
        'EFFECTIVE_START_DATE', 'COUNTRY', 'ADDRESS_LINE_1', 'ADDRESS_LINE_2',
        'REGION_1', 'REGION_2', 'TOWN_OR_CITY', 'POSTAL_CODE',
        'PRIMARY_FLAG', 'ATTRIBUTE_CATEGORY', 'ATTRIBUTE1'
    ],
    ('ADPPOLICIA', 'HCM_PERSON_ADDRESS_INTF'): [
        'COUNTRY_CODE', 'SWIFT_PERSON_TYPE', 'PERSON_NUMBER', 'ADDRESS_TYPE',
        'EFFECTIVE_START_DATE', 'EFFECTIVE_END_DATE', 'COUNTRY',
        'ADDRESS_LINE_1', 'ADDRESS_LINE_2', 'REGION_2',
        'TOWN_OR_CITY', 'POSTAL_CODE', 'SOURCE_SYSTEM_OWNER'
    ],

    # =========================================================================
    # HCM_PERSON_ASSIGNMENT_INTF - Assignment file
    # =========================================================================
    ('PEOPLESOFT', 'HCM_PERSON_ASSIGNMENT_INTF'): [
        'COUNTRY', 'EMPLID', 'GEO_CODE', 'HIRE_DT', 'REHIRE_RECOMMENDATION',
        'TERMINATION_DT', 'PIN_NUM', 'PER_TYPE', 'PER_STATUS',
        'ACTION', 'ACTION_REASON', 'EFFDT', 'EFFSEQ', 'EMPL_STATUS',
        'LAST_DATE_WORKED', 'BUSINESS_UNIT', 'ORIG_HIRE_DT', 'POSITION_NBR',
        'POSITION_OVERRIDE', 'JOBCODE', 'SAL_ADMIN_PLAN', 'GRADE', 'STEP',
        'DEPTID', 'JOB_REPORTING', 'LOCATION', 'PAYGROUP', 'COMPANY',
        'REG_TEMP', 'FULL_PART_TIME', 'REHIRE_DT', 'HOURLY_RT', 'STD_HOURS',
        'COMP_FREQUENCY', 'LANG_CD', 'EMPL_TYPE', 'FTE', 'EMPL_CLASS', 'ACTION_DT'
    ],
    ('FIMAS', 'HCM_PERSON_ASSIGNMENT_INTF'): [
        'COUNTRY', 'EMPLID', 'GEO_CODE', 'HIRE_DT', 'REHIRE_RECOMMENDATION',
        'TERMINATION_DT', 'PIN_NUM', 'PER_TYPE', 'PER_STATUS',
        'ACTION', 'ACTION_REASON', 'EFFDT', 'EFFSEQ', 'EMPL_STATUS',
        'LAST_DATE_WORKED', 'BUSINESS_UNIT', 'ORIG_HIRE_DT', 'POSITION_NBR',
        'POSITION_OVERRIDE', 'JOBCODE', 'SAL_ADMIN_PLAN', 'GRADE', 'STEP',
        'DEPTID', 'JOB_REPORTING', 'LOCATION', 'PAYGROUP', 'COMPANY',
        'REG_TEMP', 'FULL_PART_TIME', 'REHIRE_DT', 'HOURLY_RT', 'STD_HOURS',
        'COMP_FREQUENCY', 'LANG_CD', 'EMPL_TYPE', 'FTE', 'EMPL_CLASS',
        'ACTION_DT', 'WORKER_CATEGORY'  # FIMAS has extra WORKER_CATEGORY
    ],
    ('ORACLE_HCM', 'HCM_PERSON_ASSIGNMENT_INTF'): [
        'COUNTRY_CODE', 'SWIFT_PERSON_TYPE', 'PERSON_NUMBER',
        'HIRE_DATE', 'REHIRE_RECOMMENDATION', 'TERMINATION_DATE',
        'ACTION_CODE', 'ACTION_REASON', 'EFFECTIVE_DATE', 'EFFECTIVE_SEQUENCE',
        'ASSIGNMENT_STATUS_TYPE', 'LAST_WORKING_DATE', 'BUSINESS_UNIT',
        'POSITION_CODE', 'POSITION_OVERRIDE', 'JOB_CODE', 'GRADE_CODE',
        'DEPARTMENT_NAME', 'LOCATION_CODE', 'REGULAR_TEMPORARY',
        'FULL_PART_TIME', 'FREQUENCY', 'ASSIGNMENT_CATEGORY', 'FTE',
        'HOURLY_SALARIED_CODE', 'WORKING_HOURS'
    ],
    ('DOE', 'HCM_PERSON_ASSIGNMENT_INTF'): [
        'COUNTRY_CODE', 'SWIFT_PERSON_TYPE', 'PERSON_NUMBER', 'LEGAL_ENTITY',
        'HIRE_DATE', 'REHIRE_RECOMMENDATION_FLAG', 'PROJECTED_TERMINATION_DATE',
        'ASSIGNMENT_NUMBER', 'SYSTEM_PERSON_TYPE', 'USER_PERSON_TYPE',
        'PRIMARY_WORK_RELATION', 'PRIMARY_ASSIGNMENT', 'ACTION_CODE',
        'EFFECTIVE_DATE', 'EFFECTIVE_SEQUENCE', 'ASSIGNMENT_STATUS_TYPE',
        'ACTUAL_TERMINATION_DATE', 'LAST_WORKING_DATE', 'BUSINESS_UNIT',
        'POSITION_CODE', 'JOB_CODE', 'GRADE_CODE', 'DEPARTMENT_NAME',
        'LOCATION_CODE', 'WORKER_CATEGORY', 'ASSIGNMENT_CATEGORY',
        'PERMANENT_TEMPORARY', 'FULL_PART_TIME', 'HOURLY_SALARIED_CODE',
        'NORMAL_HOURS', 'FREQUENCY', 'FTE', 'UNION_NAME', 'CONTRACT_NUMBER',
        'ATTRIBUTE_CATEGORY', 'ATTRIBUTE1', 'X'
    ],

    # =========================================================================
    # HCM_PERSON_NAME_INTF - Name file
    # =========================================================================
    ('PEOPLESOFT', 'HCM_PERSON_NAME_INTF'): [
        'COUNTRY', 'EMPLID', 'EFFDT', 'FIRST_NAME', 'LAST_NAME', 'X'
    ],
    ('ORACLE_HCM', 'HCM_PERSON_NAME_INTF'): [
        'COUNTRY_CODE', 'SWIFT_PERSON_TYPE', 'PERSON_NUMBER', 'NAME_TYPE',
        'EFFECTIVE_START_DATE', 'EFFECTIVE_END_DATE', 'LAST_NAME', 'FIRST_NAME',
        'MIDDLE_NAMES', 'TITLE', 'PRE_NAME_ADJUNCT', 'SUFFIX', 'KNOWN_AS',
        'PREVIOUS_LAST_NAME', 'HONORS', 'MILITARY_RANK', 'CHAR_SET_CONTEXT',
        'ACADEMIC_TITLE', 'BIRTHNAME_PREFIX', 'BIRTHNAME', 'BIRTHNAME_SUFFIX',
        'STD_MANNER_OF_ADDRESS', 'EXTEND_MANNER_ADDRESS', 'REPORT_FIRST_NAME',
        'REPORT_LAST_NAME', 'MAIDEN_NAME', 'ATTRIBUTE_CATEGORY', 'ATTRIBUTE1'
    ],
    ('DOE', 'HCM_PERSON_NAME_INTF'): [
        'COUNTRY_CODE', 'SWIFT_PERSON_TYPE', 'PERSON_NUMBER', 'NAME_TYPE',
        'EFFECTIVE_START_DATE', 'LAST_NAME', 'FIRST_NAME',
        'ATTRIBUTE_CATEGORY', 'ATTRIBUTE1', 'X'
    ],
    ('ADPPOLICIA', 'HCM_PERSON_NAME_INTF'): [
        'COUNTRY_CODE', 'SWIFT_PERSON_TYPE', 'PERSON_NUMBER', 'NAME_TYPE',
        'EFFECTIVE_START_DATE', 'EFFECTIVE_END_DATE', 'LAST_NAME', 'FIRST_NAME',
        'MIDDLE_NAME', 'SUFFIX', 'PREVIOUS_LAST_NAME', 'SOURCE_SYSTEM_OWNER'
    ],

    # =========================================================================
    # HCM_PERSON_NID_INTF - National ID file
    # =========================================================================
    ('PEOPLESOFT', 'HCM_PERSON_NID_INTF'): [
        'COUNTRY', 'EMPLID', 'NATIONAL_ID_TYPE', 'NATIONAL_ID',
        'NID_COUNTRY', 'US_WORK_ELIGIBILTY', 'PAYGROUP', 'X'
    ],
    ('ORACLE_HCM', 'HCM_PERSON_NID_INTF'): [
        'COUNTRY_CODE', 'SWIFT_PERSON_TYPE', 'PERSON_NUMBER',
        'LEGISLATION_CODE', 'NATIONAL_IDENTIFIER_TYPE',
        'NATIONAL_IDENTIFIER_NUMBER', 'PRIMARY_FLAG'
    ],
    ('KRONOSPOL', 'HCM_PERSON_NID_INTF'): [
        'Country Code', 'Person Number', 'Identifier Number'
    ],
    ('KRONOSDE', 'HCM_PERSON_NID_INTF'): [
        'Country Code', 'Person Number', 'Identifier Number'
    ],
    ('DOE', 'HCM_PERSON_NID_INTF'): [
        'COUNTRY_CODE', 'SWIFT_PERSON_TYPE', 'PERSON_NUMBER',
        'LEGISLATION_CODE', 'NATIONAL_IDENTIFIER_TYPE',
        'NATIONAL_IDENTIFIER_NUMBER', 'PRIMARY_FLAG', 'X'
    ],
    ('ADPPOLICIA', 'HCM_PERSON_NID_INTF'): [
        'COUNTRY_CODE', 'SWIFT_PERSON_TYPE', 'PERSON_NUMBER',
        'LEGISLATION_CODE', 'NATIONAL_IDENTIFIER_TYPE',
        'NATIONAL_IDENTIFIER_NUMBER', 'PRIMARY_FLAG', 'SOURCE_SYSTEM_OWNER'
    ],

    # =========================================================================
    # HCM_PERSON_SUPERVISOR_INTF - Supervisor file
    # =========================================================================
    ('PEOPLESOFT', 'HCM_PERSON_SUPERVISOR_INTF'): [
        'COUNTRY', 'EMPLID', 'EFFDT', 'SUPERVISOR_ID', 'X'
    ],
    ('ORACLE_HCM', 'HCM_PERSON_SUPERVISOR_INTF'): [
        'COUNTRY_CODE', 'SWIFT_PERSON_TYPE', 'PERSON_NUMBER',
        'EFFECTIVE_START_DATE', 'EFFECTIVE_END_DATE', 'MANAGER_TYPE',
        'MANAGER_PERSON_NUMBER', 'MANAGER_ASSIGNMENT_NUMBER', 'PRIMARY_FLAG'
    ],

    # =========================================================================
    # HCM_PERSON_EMAIL_INTF - Email file
    # =========================================================================
    ('PEOPLESOFT', 'HCM_PERSON_EMAIL_INTF'): [
        'COUNTRY', 'EMPLID', 'E_ADDR_TYPE', 'EMAIL_ADDR', 'X'
    ],
    ('ORACLE_HCM', 'HCM_PERSON_EMAIL_INTF'): [
        'COUNTRY_CODE', 'SWIFT_PERSON_TYPE', 'PERSON_NUMBER',
        'DATE_FROM', 'DATE_TO', 'EMAIL_TYPE', 'EMAIL_ADDRESS', 'PRIMARY_FLAG'
    ],

    # =========================================================================
    # HCM_EXTERNAL_IDENTIFIER_INTF - External identifier file
    # =========================================================================
    ('PEOPLESOFT', 'HCM_EXTERNAL_IDENTIFIER_INTF'): [
        'COUNTRY', 'EMPLID', 'EXT_ID_TYPE', 'EXT_ID', 'X'
    ],
    ('ORACLE_HCM', 'HCM_EXTERNAL_IDENTIFIER_INTF'): [
        'COUNTRY_CODE', 'SWIFT_PERSON_TYPE', 'PERSON_NUMBER',
        'EXTERNAL_ID_TYPE', 'EXTERNAL_ID', 'LEGISLATION_CODE'
    ],

    # =========================================================================
    # HCM_DEPARTMENT_INTF - Department file
    # =========================================================================
    ('PEOPLESOFT', 'HCM_DEPARTMENT_INTF'): [
        'SETID', 'DEPTID', 'EFFDT', 'EFF_STATUS', 'DESCR', 'DESCRSHORT',
        'COMPANY', 'MANAGER_ID', 'LOCATION', 'X'
    ],
    ('ORACLE_HCM', 'HCM_DEPARTMENT_INTF'): [
        'ORGANIZATION_ID', 'SET_ID', 'EFFECTIVE_START_DATE', 'EFFECTIVE_END_DATE',
        'NAME', 'DEPARTMENT_CODE', 'STATUS', 'LOCATION_CODE', 'MANAGER_ID'
    ],

    # =========================================================================
    # HCM_JOBS_INTF - Job file
    # =========================================================================
    ('PEOPLESOFT', 'HCM_JOBS_INTF'): [
        'SETID', 'JOBCODE', 'EFFDT', 'EFF_STATUS', 'DESCR', 'DESCRSHORT',
        'JOB_FAMILY', 'JOB_FUNCTION', 'REG_TEMP', 'FULL_PART_TIME',
        'SAL_ADMIN_PLAN', 'GRADE', 'X'
    ],
    ('ORACLE_HCM', 'HCM_JOBS_INTF'): [
        'JOB_ID', 'SET_ID', 'JOB_CODE', 'NAME', 'EFFECTIVE_START_DATE',
        'EFFECTIVE_END_DATE', 'ACTIVE_STATUS', 'JOB_FAMILY_ID',
        'JOB_FUNCTION_CODE', 'REGULAR_TEMPORARY', 'FULL_PART_TIME'
    ],

    # =========================================================================
    # HCM_LOCATION_INTF - Location file
    # =========================================================================
    ('PEOPLESOFT', 'HCM_LOCATION_INTF'): [
        'SETID', 'LOCATION', 'EFFDT', 'EFF_STATUS', 'DESCR', 'DESCRSHORT',
        'COUNTRY', 'ADDRESS1', 'ADDRESS2', 'CITY', 'STATE', 'POSTAL', 'X'
    ],
    ('ORACLE_HCM', 'HCM_LOCATION_INTF'): [
        'LOCATION_ID', 'SET_ID', 'LOCATION_CODE', 'LOCATION_NAME',
        'EFFECTIVE_START_DATE', 'EFFECTIVE_END_DATE', 'STATUS', 'DESCRIPTION',
        'COUNTRY', 'ADDRESS_LINE_1', 'ADDRESS_LINE_2', 'TOWN_OR_CITY',
        'REGION_1', 'POSTAL_CODE'
    ],

    # =========================================================================
    # HCM_EMPLOYEES_TO_SYNCH - Employee sync file (generic)
    # =========================================================================
    ('GENERIC', 'HCM_EMPLOYEES_TO_SYNCH'): [
        'PERSON_NUMBER', 'ASSIGNMENT_NUMBER', 'STATUS'
    ],
}

# Legacy EXPECTED_COLUMNS for backward compatibility (Oracle HCM target format)
# These represent the SQL Server table columns (the target format after transformation)
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


def extract_source_from_filename(filename: str) -> Optional[str]:
    """
    Extract the source system from a filename.

    File naming convention: HCM_{ENTITY}_INTF_{SOURCE}_{DATE}.csv
    or: HCM_PERSON_INTF_{SOURCE}_{DATE}.csv (3 parts before source)

    Examples:
        HCM_PERSON_ADDRESS_INTF_FIMAS_20251209.csv -> FIMAS
        HCM_PERSON_INTF_RHUM_20251209.csv -> RHUM
        Worker_Assignment_HAC88_20251205.csv -> HACIENDA (via alias)

    Returns:
        Normalized source name (e.g., 'RHUM', 'FIMAS', 'HACIENDA', 'KRONOSPOL')
    """
    try:
        fname_nopath = filename.split('/')[-1].split('\\')[-1]  # Handle both path separators
        parts = fname_nopath.replace('.csv', '').replace('.CSV', '').split('_')

        # Try position 3 (0-indexed) first: HCM_PERSON_INTF_{SOURCE}_DATE
        # Then try position 4: HCM_PERSON_ADDRESS_INTF_{SOURCE}_DATE
        for source_pos in [3, 4, 5]:
            if len(parts) > source_pos:
                raw_source = parts[source_pos].upper()

                # Skip if this looks like a date (all digits)
                if raw_source.isdigit():
                    continue

                # Check if it's a known source or alias
                if raw_source in SOURCE_ALIASES:
                    return SOURCE_ALIASES[raw_source]

        # Fallback: look for known source names anywhere in filename
        filename_upper = fname_nopath.upper()
        for source_name in SOURCE_ALIASES.keys():
            if f'_{source_name}_' in filename_upper or filename_upper.endswith(f'_{source_name}.CSV'):
                return SOURCE_ALIASES[source_name]

    except Exception:
        pass

    return None


def get_source_group(source: str) -> str:
    """
    Determine the source group for column schema lookup.

    Returns:
        'PEOPLESOFT' for RHUM/FIMAS/HACIENDA
        'ORACLE_HCM' for KRONOSPOL and similar
        Or the specific source name if it has custom columns
    """
    if source in PEOPLESOFT_SOURCES:
        return 'PEOPLESOFT'
    elif source in ORACLE_HCM_SOURCES:
        return 'ORACLE_HCM'
    return source  # Return specific source for custom schemas


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

    # Also remove source name if present
    for source in list(SOURCE_ALIASES.keys()) + list(SOURCE_ALIASES.values()):
        if name.upper().endswith(f'_{source}'):
            name = name[:-len(source)-1]
            break

    # Try to match against known entity patterns
    name_upper = name.upper()

    # Check for HCM_*_INTF pattern
    for entity in EXPECTED_COLUMNS:
        entity_upper = entity.upper()
        if name_upper.startswith(entity_upper) or name_upper == entity_upper:
            return entity

    # Check if it's a Worker file
    if 'WORKER_ASSIGNMENT' in name_upper or 'WORKERASSIGNMENT' in name_upper:
        return 'Worker_Assignment'
    if 'WORKER' in name_upper:
        return 'Worker'

    # Check for HCM_PERSON_INTF (without other qualifiers)
    if name_upper.startswith('HCM_PERSON_INTF') or name_upper == 'HCM_PERSON':
        return 'HCM_PERSON_INTF'

    return None


def get_expected_columns_for_file(filename: str) -> Tuple[Optional[List[str]], str, str]:
    """
    Get the expected columns for a file based on its entity type and source.

    Args:
        filename: The CSV filename

    Returns:
        Tuple of (expected_columns, entity_name, source_name)
        expected_columns may be None if no schema is found
    """
    entity = get_base_entity_from_filename(filename)
    source = extract_source_from_filename(filename)

    if not entity:
        return None, 'UNKNOWN', source or 'UNKNOWN'

    if not source:
        # If no source detected, try to use generic Oracle HCM schema
        source = 'ORACLE_HCM'

    # Try specific source first, then source group
    for lookup_source in [source, get_source_group(source)]:
        key = (lookup_source, entity)
        if key in SOURCE_SPECIFIC_COLUMNS:
            return SOURCE_SPECIFIC_COLUMNS[key], entity, source

    # Fallback to EXPECTED_COLUMNS (Oracle HCM target format)
    if entity in EXPECTED_COLUMNS:
        return EXPECTED_COLUMNS[entity], entity, source

    return None, entity, source


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
    expected_columns: Optional[List[str]] = None,
    strict_mode: bool = False
) -> ColumnValidationResult:
    """
    Validate that a CSV file's columns match the expected schema for its source.

    This function is SOURCE-AWARE: it extracts the source system from the filename
    (e.g., FIMAS, RHUM, KRONOSPOL) and applies the appropriate column schema.

    Different sources have different column naming conventions:
    - RHUM/FIMAS/HACIENDA: PeopleSoft-style (EMPLID, EFFDT, COUNTRY, etc.)
    - KRONOSPOL/DOE/ADPPOLICIA: Oracle HCM-style (PERSON_NUMBER, EFFECTIVE_START_DATE, etc.)

    Args:
        filename: Name of the CSV file (used to detect entity and source)
        csv_columns: List of column names from the CSV header
        expected_columns: Optional list of expected columns (if None, auto-detect from filename)
        strict_mode: If True, also fail when CSV is missing expected columns

    Returns:
        ColumnValidationResult with validation details
    """
    # Get expected columns based on source and entity
    detected_columns, entity, source = get_expected_columns_for_file(filename)

    if expected_columns is None:
        expected_columns = detected_columns

    if expected_columns is None:
        # Can't validate - unknown entity type or source
        return ColumnValidationResult(
            file_name=filename,
            table_name=entity or 'UNKNOWN',
            is_valid=True,  # Pass validation if we don't know the schema
            csv_columns=csv_columns,
            table_columns=[],
            extra_csv_columns=[],
            missing_table_columns=[],
            error_message=f"Unknown entity/source ({entity}/{source}), skipping column validation"
        )

    # Normalize column names for comparison
    csv_cols_normalized = {normalize_column_name(c): c for c in csv_columns}
    expected_cols_normalized = {normalize_column_name(c): c for c in expected_columns}

    # Find extra columns in CSV that don't exist in expected schema
    # This is a WARNING, not an error - extra columns are often OK
    extra_csv = []
    for norm_name, orig_name in csv_cols_normalized.items():
        if norm_name and norm_name not in expected_cols_normalized:
            extra_csv.append(orig_name)

    # Find columns in expected schema that don't exist in CSV
    # This could indicate missing required data
    missing_table = []
    for norm_name, orig_name in expected_cols_normalized.items():
        if norm_name and norm_name not in csv_cols_normalized:
            missing_table.append(orig_name)

    # In strict mode, fail if there are extra columns OR missing required columns
    # In normal mode, only warn about extra columns (they usually get ignored)
    # For source-specific validation, we want to be lenient - extra columns
    # are usually OK since CSV files may have metadata columns (like 'X')
    if strict_mode:
        is_valid = len(extra_csv) == 0 and len(missing_table) == 0
    else:
        # Normal mode: just check that CSV has the minimum required columns
        # Extra columns are OK (they'll be ignored during load)
        is_valid = True  # Be lenient - as long as we can identify the file

    error_message = None
    notes = []

    if extra_csv:
        notes.append(f"Extra columns in CSV (will be ignored): {', '.join(extra_csv[:5])}")
        if len(extra_csv) > 5:
            notes.append(f"  ... and {len(extra_csv) - 5} more")

    if missing_table:
        notes.append(f"Expected columns not in CSV (will be NULL): {', '.join(missing_table[:5])}")
        if len(missing_table) > 5:
            notes.append(f"  ... and {len(missing_table) - 5} more")

    if notes:
        error_message = f"Source: {source}. " + "; ".join(notes)

    return ColumnValidationResult(
        file_name=filename,
        table_name=f"{entity}_{source}" if source != 'UNKNOWN' else entity,
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
    lines.append("This report validates CSV columns against source-specific schemas.")
    lines.append("Different sources (RHUM/FIMAS vs KRONOSPOL/DOE) use different column names.")
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
                lines.append(f"  Entity/Source: {result.table_name}")
                lines.append(f"  Error: {result.error_message}")
                if result.extra_csv_columns:
                    lines.append(f"  Extra CSV columns: {', '.join(result.extra_csv_columns[:10])}")
                    if len(result.extra_csv_columns) > 10:
                        lines.append(f"    ... and {len(result.extra_csv_columns) - 10} more")

        lines.append("")

    lines.append("-" * 40)
    lines.append("FILE DETAILS:")
    lines.append("-" * 40)

    # Group files by source for better readability
    files_by_source = {}
    for result in report.results:
        # Extract source from table_name (format: ENTITY_SOURCE)
        if '_' in result.table_name:
            parts = result.table_name.rsplit('_', 1)
            source = parts[-1] if parts[-1] in SOURCE_ALIASES or parts[-1] in ['PEOPLESOFT', 'ORACLE_HCM'] else 'UNKNOWN'
        else:
            source = 'UNKNOWN'
        if source not in files_by_source:
            files_by_source[source] = []
        files_by_source[source].append(result)

    for source, results in sorted(files_by_source.items()):
        lines.append(f"\n--- Source: {source} ({len(results)} files) ---")

        for result in results:
            status = "OK" if result.is_valid else "FAIL"
            lines.append(f"\n  [{status}] {result.file_name}")
            lines.append(f"    Entity: {result.table_name}")
            lines.append(f"    CSV columns: {len(result.csv_columns)}")
            if result.table_columns:
                lines.append(f"    Expected columns: {len(result.table_columns)}")
            if result.missing_table_columns:
                lines.append(f"    Missing (will be NULL): {len(result.missing_table_columns)}")
            if result.extra_csv_columns:
                lines.append(f"    Extra (will be ignored): {len(result.extra_csv_columns)}")
            if result.error_message:
                lines.append(f"    Note: {result.error_message}")

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
