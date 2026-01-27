"""
SQL Table Loader Module for Hacienda SFTP Downloads

This module loads CSV files into SQL Server tables with proper column mapping.
Different source systems (RHUM, FIMAS, HACIENDA, KRONOSPOL, DOE, etc.) have
different CSV column names that must be mapped to the standard database column names.

Based on the original LoadIntfData.py implementation.
"""

import boto3
import io
import re
import csv
from datetime import datetime
from typing import Dict, List, Optional, Tuple


# =============================================================================
# SOURCE ALIASES
# =============================================================================
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

# PeopleSoft-style sources that need column name transformation
PEOPLESOFT_SOURCES = ['RHUM', 'FIMAS', 'HACIENDA']


# =============================================================================
# COLUMN MAPPINGS BY ENTITY AND SOURCE
# =============================================================================
# Format: (entity_type, source) -> {csv_column: db_column, ...}
# These mappings define how CSV columns map to database columns for each combination

COLUMN_MAPPINGS = {
    # =========================================================================
    # HCM_PERSON_INTF - Person/Worker base file
    # =========================================================================
    ('HCM_PERSON_INTF', 'RHUM'): {
        'COUNTRY': 'COUNTRY_CODE',
        'EMPLID': 'PERSON_NUMBER',
        'EFFDT': 'EFFECTIVE_START_DATE',
        'START_DATE': 'START_DATE',
        'BIRTHDATE': 'DATE_OF_BIRTH',
    },
    ('HCM_PERSON_INTF', 'FIMAS'): {
        'COUNTRY': 'COUNTRY_CODE',
        'EMPLID': 'PERSON_NUMBER',
        'EFFDT': 'EFFECTIVE_START_DATE',
        'START_DATE': 'START_DATE',
        'BIRTHDATE': 'DATE_OF_BIRTH',
    },
    ('HCM_PERSON_INTF', 'HACIENDA'): {
        'COUNTRY': 'COUNTRY_CODE',
        'EMPLID': 'PERSON_NUMBER',
        'EFFDT': 'EFFECTIVE_START_DATE',
        'START_DATE': 'START_DATE',
        'BIRTHDATE': 'DATE_OF_BIRTH',
    },
    ('HCM_PERSON_INTF', 'KRONOSPOL'): {
        # Direct mapping - CSV columns match DB columns
        'COUNTRY_CODE': 'COUNTRY_CODE',
        'SWIFT_PERSON_TYPE': 'SWIFT_PERSON_TYPE',
        'PERSON_NUMBER': 'PERSON_NUMBER',
        'EFFECTIVE_START_DATE': 'EFFECTIVE_START_DATE',
        'START_DATE': 'START_DATE',
        'DATE_OF_BIRTH': 'DATE_OF_BIRTH',
        'EFFECTIVE_END_DATE': 'EFFECTIVE_END_DATE',
        'ATTRIBUTE_CATEGORY': 'ATTRIBUTE_CATEGORY',
        'ATTRIBUTE1': 'ATTRIBUTE1',
    },
    ('HCM_PERSON_INTF', 'DOE'): {
        'COUNTRY_CODE': 'COUNTRY_CODE',
        'SWIFT_PERSON_TYPE': 'SWIFT_PERSON_TYPE',
        'PERSON_NUMBER': 'PERSON_NUMBER',
        'EFFECTIVE_START_DATE': 'EFFECTIVE_START_DATE',
        'START_DATE': 'START_DATE',
        'DATE_OF_BIRTH': 'DATE_OF_BIRTH',
        'ATTRIBUTE_CATEGORY': 'ATTRIBUTE_CATEGORY',
        'ATTRIBUTE1': 'ATTRIBUTE1',
    },
    ('HCM_PERSON_INTF', 'ADPPOLICIA'): {
        'COUNTRY_CODE': 'COUNTRY_CODE',
        'SWIFT_PERSON_TYPE': 'SWIFT_PERSON_TYPE',
        'PERSON_NUMBER': 'PERSON_NUMBER',
        'EFFECTIVE_START_DATE': 'EFFECTIVE_START_DATE',
        'EFFECTIVE_END_DATE': 'EFFECTIVE_END_DATE',
        'START_DATE': 'START_DATE',
        'DATE_OF_BIRTH': 'DATE_OF_BIRTH',
        'SOURCE_SYSTEM_OWNER': 'SOURCE_SYSTEM_OWNER',
    },

    # =========================================================================
    # HCM_PERSON_ADDRESS_INTF - Address file
    # =========================================================================
    ('HCM_PERSON_ADDRESS_INTF', 'RHUM'): {
        'COUNTRY': 'COUNTRY_CODE',
        'EMPLID': 'PERSON_NUMBER',
        'ADDRESS_TYPE': 'ADDRESS_TYPE',
        'EFFDT': 'EFFECTIVE_START_DATE',
        'ADDRESS1': 'ADDRESS_LINE_1',
        'ADDRESS2': 'ADDRESS_LINE_2',
        'ADDRESS3': 'ADDRESS_LINE_3',
        'ADDRESS4': 'ADDRESS_LINE_4',
        'COUNTY': 'REGION_1',
        'STATE': 'REGION_2',
        'REG_REGION': 'COUNTRY',
        'CITY': 'TOWN_OR_CITY',
        'POSTAL': 'POSTAL_CODE',
        'EFF_STATUS': 'EFF_STATUS',
        'EMPL_STATUS': 'EMPL_STATUS',
    },
    ('HCM_PERSON_ADDRESS_INTF', 'FIMAS'): {
        'COUNTRY': 'COUNTRY_CODE',
        'EMPLID': 'PERSON_NUMBER',
        'ADDRESS_TYPE': 'ADDRESS_TYPE',
        'EFFDT': 'EFFECTIVE_START_DATE',
        'ADDRESS1': 'ADDRESS_LINE_1',
        'ADDRESS2': 'ADDRESS_LINE_2',
        'ADDRESS3': 'ADDRESS_LINE_3',
        'ADDRESS4': 'ADDRESS_LINE_4',
        'COUNTY': 'REGION_1',
        'STATE': 'REGION_2',
        'REG_REGION': 'COUNTRY',
        'CITY': 'TOWN_OR_CITY',
        'POSTAL': 'POSTAL_CODE',
        'EFF_STATUS': 'EFF_STATUS',
        'EMPL_STATUS': 'EMPL_STATUS',
    },
    ('HCM_PERSON_ADDRESS_INTF', 'HACIENDA'): {
        'COUNTRY': 'COUNTRY_CODE',
        'EMPLID': 'PERSON_NUMBER',
        'ADDRESS_TYPE': 'ADDRESS_TYPE',
        'EFFDT': 'EFFECTIVE_START_DATE',
        'ADDRESS1': 'ADDRESS_LINE_1',
        'ADDRESS2': 'ADDRESS_LINE_2',
        'ADDRESS3': 'ADDRESS_LINE_3',
        'ADDRESS4': 'ADDRESS_LINE_4',
        'COUNTY': 'REGION_1',
        'STATE': 'REGION_2',
        'REG_REGION': 'COUNTRY',
        'CITY': 'TOWN_OR_CITY',
        'POSTAL': 'POSTAL_CODE',
        'EFF_STATUS': 'EFF_STATUS',
        'EMPL_STATUS': 'EMPL_STATUS',
    },
    ('HCM_PERSON_ADDRESS_INTF', 'KRONOSPOL'): {
        'COUNTRY_CODE': 'COUNTRY_CODE',
        'PERSON_NUMBER': 'PERSON_NUMBER',
        'ADDRESS_TYPE': 'ADDRESS_TYPE',
        'EFFECTIVE_START_DATE': 'EFFECTIVE_START_DATE',
        'COUNTRY': 'COUNTRY',
        'ADDRESS_LINE_1': 'ADDRESS_LINE_1',
        'REGION_1': 'REGION_1',
        'REGION_2': 'REGION_2',
        'TOWN_OR_CITY': 'TOWN_OR_CITY',
        'POSTAL_CODE': 'POSTAL_CODE',
        'ADDRESS_LINE_2': 'ADDRESS_LINE_2',
        'ADDRESS_LINE_3': 'ADDRESS_LINE_3',
        'ADDRESS_LINE_4': 'ADDRESS_LINE_4',
        'SWIFT_PERSON_TYPE': 'SWIFT_PERSON_TYPE',
        'EFFECTIVE_END_DATE': 'EFFECTIVE_END_DATE',
        'LONG_POSTAL_CODE': 'LONG_POSTAL_CODE',
        'PRIMARY_FLAG': 'PRIMARY_FLAG',
        'ATTRIBUTE_CATEGORY': 'ATTRIBUTE_CATEGORY',
        'ATTRIBUTE1': 'ATTRIBUTE1',
    },
    ('HCM_PERSON_ADDRESS_INTF', 'DOE'): {
        'COUNTRY_CODE': 'COUNTRY_CODE',
        'PERSON_NUMBER': 'PERSON_NUMBER',
        'ADDRESS_TYPE': 'ADDRESS_TYPE',
        'EFFECTIVE_START_DATE': 'EFFECTIVE_START_DATE',
        'COUNTRY': 'COUNTRY',
        'ADDRESS_LINE_1': 'ADDRESS_LINE_1',
        'REGION_1': 'REGION_1',
        'REGION_2': 'REGION_2',
        'TOWN_OR_CITY': 'TOWN_OR_CITY',
        'POSTAL_CODE': 'POSTAL_CODE',
        'ADDRESS_LINE_2': 'ADDRESS_LINE_2',
        'SWIFT_PERSON_TYPE': 'SWIFT_PERSON_TYPE',
        'EFFECTIVE_END_DATE': 'EFFECTIVE_END_DATE',
        'PRIMARY_FLAG': 'PRIMARY_FLAG',
        'ATTRIBUTE_CATEGORY': 'ATTRIBUTE_CATEGORY',
        'ATTRIBUTE1': 'ATTRIBUTE1',
    },
    ('HCM_PERSON_ADDRESS_INTF', 'ADPPOLICIA'): {
        'COUNTRY_CODE': 'COUNTRY_CODE',
        'SWIFT_PERSON_TYPE': 'SWIFT_PERSON_TYPE',
        'PERSON_NUMBER': 'PERSON_NUMBER',
        'ADDRESS_TYPE': 'ADDRESS_TYPE',
        'EFFECTIVE_START_DATE': 'EFFECTIVE_START_DATE',
        'EFFECTIVE_END_DATE': 'EFFECTIVE_END_DATE',
        'COUNTRY': 'COUNTRY',
        'ADDRESS_LINE_1': 'ADDRESS_LINE_1',
        'ADDRESS_LINE_2': 'ADDRESS_LINE_2',
        'REGION_2': 'REGION_2',
        'TOWN_OR_CITY': 'TOWN_OR_CITY',
        'POSTAL_CODE': 'POSTAL_CODE',
        'SOURCE_SYSTEM_OWNER': 'SOURCE_SYSTEM_OWNER',
    },

    # =========================================================================
    # HCM_PERSON_ASSIGNMENT_INTF - Assignment file
    # =========================================================================
    ('HCM_PERSON_ASSIGNMENT_INTF', 'RHUM'): {
        'COUNTRY': 'COUNTRY_CODE',
        'PER_TYPE': 'SWIFT_PERSON_TYPE',
        'EMPLID': 'PERSON_NUMBER',
        'HIRE_DT': 'HIRE_DATE',
        'EFFDT': 'EFFECTIVE_DATE',
        'EFFSEQ': 'EFFECTIVE_SEQUENCE',
        'EMPL_STATUS': 'ASSIGNMENT_STATUS_TYPE',
        'BUSINESS_UNIT': 'BUSINESS_UNIT',
        'LOCATION': 'LOCATION_CODE',
        'ACTION': 'ACTION_CODE',
        'ACTION_REASON': 'ACTION_REASON',
        'FULL_PART_TIME': 'FULL_PART_TIME',
        'REG_TEMP': 'REGULAR_TEMPORARY',
        'HOURLY_RT': 'HOURLY_RATE',
        'REHIRE_DT': 'REHIRE_DATE',
        'LAST_DATE_WORKED': 'LAST_WORKING_DATE',
        'POSITION_NBR': 'POSITION_CODE',
        'TERMINATION_DT': 'TERMINATION_DATE',
        'STD_HOURS': 'WORKING_HOURS',
        'PAYGROUP': 'PAY_GROUP',
        'JOBCODE': 'JOB_CODE',
        'POSITION_OVERRIDE': 'POSITION_OVERRIDE',
        'SAL_ADMIN_PLAN': 'SAL_ADMIN_PLAN',
        'EMPL_TYPE': 'HOURLY_SALARIED_CODE',
        'COMP_FREQUENCY': 'FREQUENCY',
        'COMPANY': 'COMPANY',
        'FTE': 'FTE',
        'ORIG_HIRE_DT': 'ORIG_HIRE_DT',
        'DEPTID': 'DEPTID',
        'GRADE': 'GRADE',
        'EMPL_CLASS': 'ASSIGNMENT_CATEGORY',
        'ACTION_DT': 'ACTION_DT',
        # Note: WORKER_CATEGORY maps from EMPL_CLASS in original code
    },
    ('HCM_PERSON_ASSIGNMENT_INTF', 'FIMAS'): {
        'COUNTRY': 'COUNTRY_CODE',
        'PER_TYPE': 'SWIFT_PERSON_TYPE',
        'EMPLID': 'PERSON_NUMBER',
        'HIRE_DT': 'HIRE_DATE',
        'EFFDT': 'EFFECTIVE_DATE',
        'EFFSEQ': 'EFFECTIVE_SEQUENCE',
        'EMPL_STATUS': 'ASSIGNMENT_STATUS_TYPE',
        'BUSINESS_UNIT': 'BUSINESS_UNIT',
        'LOCATION': 'LOCATION_CODE',
        'ACTION': 'ACTION_CODE',
        'ACTION_REASON': 'ACTION_REASON',
        'FULL_PART_TIME': 'FULL_PART_TIME',
        'REG_TEMP': 'REGULAR_TEMPORARY',
        'HOURLY_RT': 'HOURLY_RATE',
        'REHIRE_DT': 'REHIRE_DATE',
        'LAST_DATE_WORKED': 'LAST_WORKING_DATE',
        'POSITION_NBR': 'POSITION_CODE',
        'TERMINATION_DT': 'TERMINATION_DATE',
        'STD_HOURS': 'WORKING_HOURS',
        'PAYGROUP': 'PAY_GROUP',
        'JOBCODE': 'JOB_CODE',
        'POSITION_OVERRIDE': 'POSITION_OVERRIDE',
        'SAL_ADMIN_PLAN': 'SAL_ADMIN_PLAN',
        'EMPL_TYPE': 'HOURLY_SALARIED_CODE',
        'COMP_FREQUENCY': 'FREQUENCY',
        'COMPANY': 'COMPANY',
        'FTE': 'FTE',
        'ORIG_HIRE_DT': 'ORIG_HIRE_DT',
        'DEPTID': 'DEPTID',
        'GRADE': 'GRADE',
        'EMPL_CLASS': 'ASSIGNMENT_CATEGORY',
        'WORKER_CATEGORY': 'WORKER_CATEGORY',
        'ACTION_DT': 'ACTION_DT',
    },
    ('HCM_PERSON_ASSIGNMENT_INTF', 'HACIENDA'): {
        'COUNTRY': 'COUNTRY_CODE',
        'PER_TYPE': 'SWIFT_PERSON_TYPE',
        'EMPLID': 'PERSON_NUMBER',
        'HIRE_DT': 'HIRE_DATE',
        'REHIRE_RECOMMENDATION': 'REHIRE_RECOMMENDATION',
        'EFFDT': 'EFFECTIVE_DATE',
        'EFFSEQ': 'EFFECTIVE_SEQUENCE',
        'EMPL_STATUS': 'ASSIGNMENT_STATUS_TYPE',
        'BUSINESS_UNIT': 'BUSINESS_UNIT',
        'LOCATION': 'LOCATION_CODE',
        'ACTION': 'ACTION_CODE',
        'ACTION_REASON': 'ACTION_REASON',
        'FULL_PART_TIME': 'FULL_PART_TIME',
        'REG_TEMP': 'REGULAR_TEMPORARY',
        'HOURLY_RT': 'HOURLY_RATE',
        'REHIRE_DT': 'REHIRE_DATE',
        'LAST_DATE_WORKED': 'LAST_WORKING_DATE',
        'POSITION_NBR': 'POSITION_CODE',
        'TERMINATION_DT': 'TERMINATION_DATE',
        'STD_HOURS': 'WORKING_HOURS',
        'PAYGROUP': 'PAY_GROUP',
        'JOBCODE': 'JOB_CODE',
        'POSITION_OVERRIDE': 'POSITION_OVERRIDE',
        'SAL_ADMIN_PLAN': 'SAL_ADMIN_PLAN',
        'EMPL_TYPE': 'HOURLY_SALARIED_CODE',
        'COMP_FREQUENCY': 'FREQUENCY',
        'COMPANY': 'COMPANY',
        'FTE': 'FTE',
        'ORIG_HIRE_DT': 'ORIG_HIRE_DT',
        'DEPTID': 'DEPTID',
        'GRADE': 'GRADE',
        'EMPL_CLASS': 'ASSIGNMENT_CATEGORY',
        'ACTION_DT': 'ACTION_DT',
    },
    ('HCM_PERSON_ASSIGNMENT_INTF', 'KRONOSPOL'): {
        # Direct mapping for Oracle HCM style columns
        'COUNTRY_CODE': 'COUNTRY_CODE',
        'SWIFT_PERSON_TYPE': 'SWIFT_PERSON_TYPE',
        'PERSON_NUMBER': 'PERSON_NUMBER',
        'HIRE_DATE': 'HIRE_DATE',
        'ACTION_CODE': 'ACTION_CODE',
        'EFFECTIVE_DATE': 'EFFECTIVE_DATE',
        'EFFECTIVE_SEQUENCE': 'EFFECTIVE_SEQUENCE',
        'ASSIGNMENT_STATUS_TYPE': 'ASSIGNMENT_STATUS_TYPE',
        'BUSINESS_UNIT': 'BUSINESS_UNIT',
        'LOCATION_CODE': 'LOCATION_CODE',
        'ACTION_REASON': 'ACTION_REASON',
        'FULL_PART_TIME': 'FULL_PART_TIME',
        'PERMANENT_TEMPORARY': 'REGULAR_TEMPORARY',
        'LAST_WORKING_DATE': 'LAST_WORKING_DATE',
        'POSITION_CODE': 'POSITION_CODE',
        'TERMINATION_DATE': 'TERMINATION_DATE',
        'WORKING_HOURS': 'WORKING_HOURS',
        'JOB_CODE': 'JOB_CODE',
        'POSITION_OVERRIDE': 'POSITION_OVERRIDE',
        'HOURLY_SALARIED_CODE': 'HOURLY_SALARIED_CODE',
        'FREQUENCY': 'FREQUENCY',
        'FTE': 'FTE',
        'DEPARTMENT_NAME': 'DEPTID',
        'GRADE_CODE': 'GRADE',
        'ASSIGNMENT_CATEGORY': 'ASSIGNMENT_CATEGORY',
        'WORKER_CATEGORY': 'WORKER_CATEGORY',
        'MANAGER_FLAG': 'MANAGER_FLAG',
        'ATTRIBUTE_CATEGORY': 'ATTRIBUTE_CATEGORY',
        'ATTRIBUTE1': 'ATTRIBUTE1',
    },
    ('HCM_PERSON_ASSIGNMENT_INTF', 'DOE'): {
        'COUNTRY_CODE': 'COUNTRY_CODE',
        'SWIFT_PERSON_TYPE': 'SWIFT_PERSON_TYPE',
        'PERSON_NUMBER': 'PERSON_NUMBER',
        'LEGAL_ENTITY': 'LEGAL_ENTITY',
        'HIRE_DATE': 'HIRE_DATE',
        'ASSIGNMENT_NUMBER': 'ASSIGNMENT_NUMBER',
        'SYSTEM_PERSON_TYPE': 'SYSTEM_PERSON_TYPE',
        'USER_PERSON_TYPE': 'USER_PERSON_TYPE',
        'PRIMARY_WORK_RELATION': 'PRIMARY_WORK_RELATION',
        'PRIMARY_ASSIGNMENT': 'PRIMARY_ASSIGNMENT',
        'ACTION_CODE': 'ACTION_CODE',
        'EFFECTIVE_DATE': 'EFFECTIVE_DATE',
        'EFFECTIVE_SEQUENCE': 'EFFECTIVE_SEQUENCE',
        'ASSIGNMENT_STATUS_TYPE': 'ASSIGNMENT_STATUS_TYPE',
        'LAST_WORKING_DATE': 'LAST_WORKING_DATE',
        'BUSINESS_UNIT': 'BUSINESS_UNIT',
        'LOCATION_CODE': 'LOCATION_CODE',
        'FULL_PART_TIME': 'FULL_PART_TIME',
        'JOB_CODE': 'JOB_CODE',
        'HOURLY_SALARIED_CODE': 'HOURLY_SALARIED_CODE',
        'FREQUENCY': 'FREQUENCY',
        'FTE': 'FTE',
        'UNION_NAME': 'UNION_NAME',
        'CONTRACT_NUMBER': 'CONTRACT_NUMBER',
        'GRADE_CODE': 'GRADE_CODE',
        'DEPARTMENT_NAME': 'DEPARTMENT_NAME',
        'WORKER_CATEGORY': 'WORKER_CATEGORY',
        'ASSIGNMENT_CATEGORY': 'ASSIGNMENT_CATEGORY',
        'PROJECTED_TERMINATION_DATE': 'PROJECTED_TERMINATION_DATE',
        'ACTUAL_TERMINATION_DATE': 'ACTUAL_TERMINATION_DATE',
        'POSITION_CODE': 'POSITION_CODE',
        'PERMANENT_TEMPORARY': 'REGULAR_TEMPORARY',
        'NORMAL_HOURS': 'WORKING_HOURS',
    },

    # =========================================================================
    # HCM_PERSON_NAME_INTF - Name file
    # =========================================================================
    ('HCM_PERSON_NAME_INTF', 'RHUM'): {
        'COUNTRY': 'COUNTRY_CODE',
        'EMPLID': 'PERSON_NUMBER',
        'EFFDT': 'EFFECTIVE_START_DATE',
        'FIRST_NAME': 'FIRST_NAME',
        'LAST_NAME': 'LAST_NAME',
    },
    ('HCM_PERSON_NAME_INTF', 'FIMAS'): {
        'COUNTRY': 'COUNTRY_CODE',
        'EMPLID': 'PERSON_NUMBER',
        'EFFDT': 'EFFECTIVE_START_DATE',
        'FIRST_NAME': 'FIRST_NAME',
        'LAST_NAME': 'LAST_NAME',
    },
    ('HCM_PERSON_NAME_INTF', 'HACIENDA'): {
        'COUNTRY': 'COUNTRY_CODE',
        'EMPLID': 'PERSON_NUMBER',
        'EFFDT': 'EFFECTIVE_START_DATE',
        'FIRST_NAME': 'FIRST_NAME',
        'LAST_NAME': 'LAST_NAME',
    },
    ('HCM_PERSON_NAME_INTF', 'KRONOSPOL'): {
        'COUNTRY_CODE': 'COUNTRY_CODE',
        'SWIFT_PERSON_TYPE': 'SWIFT_PERSON_TYPE',
        'PERSON_NUMBER': 'PERSON_NUMBER',
        'NAME_TYPE': 'NAME_TYPE',
        'EFFECTIVE_START_DATE': 'EFFECTIVE_START_DATE',
        'EFFECTIVE_END_DATE': 'EFFECTIVE_END_DATE',
        'LAST_NAME': 'LAST_NAME',
        'FIRST_NAME': 'FIRST_NAME',
        'MIDDLE_NAMES': 'MIDDLE_NAME',
        'TITLE': 'TITLE',
        'PRE_NAME_ADJUNCT': 'PRE_NAME_ADJUNCT',
        'SUFFIX': 'SUFFIX',
        'KNOWN_AS': 'KNOWN_AS',
        'PREVIOUS_LAST_NAME': 'PREVIOUS_LAST_NAME',
        'HONORS': 'HONORS',
        'MILITARY_RANK': 'MILITARY_RANK',
        'CHAR_SET_CONTEXT': 'CHAR_SET_CONTEXT',
        'ACADEMIC_TITLE': 'ACADEMIC_TITLE',
        'BIRTHNAME_PREFIX': 'BIRTHNAME_PREFIX',
        'BIRTHNAME': 'BIRTHNAME',
        'BIRTHNAME_SUFFIX': 'BIRTHNAME_SUFFIX',
        'STD_MANNER_OF_ADDRESS': 'STD_MANNER_OF_ADDRESS',
        'EXTEND_MANNER_ADDRESS': 'EXTEND_MANNER_ADDRESS',
        'REPORT_FIRST_NAME': 'REPORT_FIRST_NAME',
        'REPORT_LAST_NAME': 'REPORT_LAST_NAME',
        'MAIDEN_NAME': 'MAIDEN_NAME',
        'ATTRIBUTE_CATEGORY': 'ATTRIBUTE_CATEGORY',
        'ATTRIBUTE1': 'ATTRIBUTE1',
    },
    ('HCM_PERSON_NAME_INTF', 'DOE'): {
        'COUNTRY_CODE': 'COUNTRY_CODE',
        'SWIFT_PERSON_TYPE': 'SWIFT_PERSON_TYPE',
        'PERSON_NUMBER': 'PERSON_NUMBER',
        'NAME_TYPE': 'NAME_TYPE',
        'EFFECTIVE_START_DATE': 'EFFECTIVE_START_DATE',
        'LAST_NAME': 'LAST_NAME',
        'FIRST_NAME': 'FIRST_NAME',
        'ATTRIBUTE_CATEGORY': 'ATTRIBUTE_CATEGORY',
        'ATTRIBUTE1': 'ATTRIBUTE1',
        'X': 'X',
    },
    ('HCM_PERSON_NAME_INTF', 'ADPPOLICIA'): {
        'COUNTRY_CODE': 'COUNTRY_CODE',
        'SWIFT_PERSON_TYPE': 'SWIFT_PERSON_TYPE',
        'PERSON_NUMBER': 'PERSON_NUMBER',
        'NAME_TYPE': 'NAME_TYPE',
        'EFFECTIVE_START_DATE': 'EFFECTIVE_START_DATE',
        'EFFECTIVE_END_DATE': 'EFFECTIVE_END_DATE',
        'LAST_NAME': 'LAST_NAME',
        'FIRST_NAME': 'FIRST_NAME',
        'MIDDLE_NAME': 'MIDDLE_NAME',
        'SUFFIX': 'SUFFIX',
        'PREVIOUS_LAST_NAME': 'PREVIOUS_LAST_NAME',
        'SOURCE_SYSTEM_OWNER': 'SOURCE_SYSTEM_OWNER',
    },

    # =========================================================================
    # HCM_PERSON_NID_INTF - National ID file
    # =========================================================================
    ('HCM_PERSON_NID_INTF', 'RHUM'): {
        'COUNTRY': 'COUNTRY_CODE',
        'EMPLID': 'PERSON_NUMBER',
        'NATIONAL_ID_TYPE': 'NATIONAL_IDENTIFIER_TYPE',
        'NATIONAL_ID': 'NATIONAL_IDENTIFIER_NUMBER',
        'NID_COUNTRY': 'PRIMARY_FLAG',
        'US_WORK_ELIGIBILTY': 'US_WORK_ELIGIBILITY',
        'PAYGROUP': 'PAYGROUP',
    },
    ('HCM_PERSON_NID_INTF', 'FIMAS'): {
        'COUNTRY': 'COUNTRY_CODE',
        'EMPLID': 'PERSON_NUMBER',
        'NATIONAL_ID_TYPE': 'NATIONAL_IDENTIFIER_TYPE',
        'NATIONAL_ID': 'NATIONAL_IDENTIFIER_NUMBER',
        'NID_COUNTRY': 'PRIMARY_FLAG',
        'US_WORK_ELIGIBILTY': 'US_WORK_ELIGIBILITY',
        'PAYGROUP': 'PAYGROUP',
    },
    ('HCM_PERSON_NID_INTF', 'HACIENDA'): {
        'COUNTRY': 'COUNTRY_CODE',
        'EMPLID': 'PERSON_NUMBER',
        'NATIONAL_ID_TYPE': 'NATIONAL_IDENTIFIER_TYPE',
        'NATIONAL_ID': 'NATIONAL_IDENTIFIER_NUMBER',
        'NID_COUNTRY': 'PRIMARY_FLAG',
        'US_WORK_ELIGIBILTY': 'US_WORK_ELIGIBILITY',
        'PAYGROUP': 'PAYGROUP',
    },
    ('HCM_PERSON_NID_INTF', 'KRONOSPOL'): {
        'Country Code': 'COUNTRY_CODE',
        'Person Number': 'PERSON_NUMBER',
        'Identifier Number': 'NATIONAL_IDENTIFIER_NUMBER',
    },
    ('HCM_PERSON_NID_INTF', 'KRONOSDE'): {
        'Country Code': 'COUNTRY_CODE',
        'Person Number': 'PERSON_NUMBER',
        'Identifier Number': 'NATIONAL_IDENTIFIER_NUMBER',
    },
    ('HCM_PERSON_NID_INTF', 'DOE'): {
        'COUNTRY_CODE': 'COUNTRY_CODE',
        'SWIFT_PERSON_TYPE': 'SWIFT_PERSON_TYPE',
        'PERSON_NUMBER': 'PERSON_NUMBER',
        'LEGISLATION_CODE': 'LEGISLATION_CODE',
        'NATIONAL_IDENTIFIER_TYPE': 'NATIONAL_IDENTIFIER_TYPE',
        'NATIONAL_IDENTIFIER_NUMBER': 'NATIONAL_IDENTIFIER_NUMBER',
        'PRIMARY_FLAG': 'PRIMARY_FLAG',
    },
    ('HCM_PERSON_NID_INTF', 'ADPPOLICIA'): {
        'COUNTRY_CODE': 'COUNTRY_CODE',
        'SWIFT_PERSON_TYPE': 'SWIFT_PERSON_TYPE',
        'PERSON_NUMBER': 'PERSON_NUMBER',
        'LEGISLATION_CODE': 'LEGISLATION_CODE',
        'NATIONAL_IDENTIFIER_TYPE': 'NATIONAL_IDENTIFIER_TYPE',
        'NATIONAL_IDENTIFIER_NUMBER': 'NATIONAL_IDENTIFIER_NUMBER',
        'PRIMARY_FLAG': 'PRIMARY_FLAG',
        'SOURCE_SYSTEM_OWNER': 'SOURCE_SYSTEM_OWNER',
    },

    # =========================================================================
    # HCM_PERSON_SUPERVISOR_INTF - Supervisor file
    # =========================================================================
    ('HCM_PERSON_SUPERVISOR_INTF', 'RHUM'): {
        'COUNTRY': 'COUNTRY_CODE',
        'PERSON_NUMBER': 'PERSON_NUMBER',
        'ACTION': 'ACTION',
        'ACTION_REASON': 'ACTION_REASON',
        'MANAGER_PERSON_NUMBER': 'MANAGER_PERSON_NUMBER',
        'MANAGER_TYPE': 'MANAGER_TYPE',
    },
    ('HCM_PERSON_SUPERVISOR_INTF', 'FIMAS'): {
        'COUNTRY': 'COUNTRY_CODE',
        'EMPLID': 'PERSON_NUMBER',
        'EFFDT': 'EFFECTIVE_START_DATE',
        'ACTION': 'ACTION',
        'ACTION_REASON': 'ACTION_REASON',
        'EMPLID2': 'MANAGER_PERSON_NUMBER',
        'DESCR': 'DESCR',
        'REPORTS_TO': 'REPORTS_TO',
        'TAX_LOCATION_CD': 'TAX_LOCATION_CD',
        'PAYGROUP': 'PAYGROUP',
        'COMPANY': 'COMPANY',
    },
    ('HCM_PERSON_SUPERVISOR_INTF', 'HACIENDA'): {
        'COUNTRY': 'COUNTRY_CODE',
        'EMPLID': 'PERSON_NUMBER',
        'EFFDT': 'EFFECTIVE_START_DATE',
        'ACTION': 'ACTION',
        'ACTION_REASON': 'ACTION_REASON',
        'EMPLID2': 'MANAGER_PERSON_NUMBER',
        'DESCR': 'DESCR',
        'REPORTS_TO': 'REPORTS_TO',
        'TAX_LOCATION_CD': 'TAX_LOCATION_CD',
        'PAYGROUP': 'PAYGROUP',
        'COMPANY': 'COMPANY',
    },
    ('HCM_PERSON_SUPERVISOR_INTF', '911'): {
        'PERSON_NUMBER': 'PERSON_NUMBER',
        'SupervisorPersonNumber': 'MANAGER_PERSON_NUMBER',
    },
    ('HCM_PERSON_SUPERVISOR_INTF', 'KRONOSPOL'): {
        'COUNTRY_CODE': 'COUNTRY_CODE',
        'SWIFT_PERSON_TYPE': 'SWIFT_PERSON_TYPE',
        'PERSON_NUMBER': 'PERSON_NUMBER',
        'ASSIGNMENT_NUMBER': 'ASSIGNMENT_NUMBER',
        'LEGAL_ENTITY': 'LEGAL_ENTITY',
        'MANAGER_TYPE': 'MANAGER_TYPE',
        'EFFECTIVE_START_DATE': 'EFFECTIVE_START_DATE',
        'EFFECTIVE_END_DATE': 'EFFECTIVE_END_DATE',
        'ACTION_CODE': 'ACTION_CODE',
        'ACTION_REASON': 'ACTION_REASON',
        'MANAGER_PERSON_NUMBER': 'MANAGER_PERSON_NUMBER',
        'MANAGER_ASSIGNMENT_NUMBER': 'MANAGER_ASSIGNMENT_NUMBER',
        'MANAGER_LEGAL_ENTITY': 'MANAGER_LEGAL_ENTITY',
        'PRIMARY_FLAG': 'PRIMARY_FLAG',
        'ATTRIBUTE_CATEGORY': 'ATTRIBUTE_CATEGORY',
        'ATTRIBUTE1': 'ATTRIBUTE1',
    },

    # =========================================================================
    # HCM_PERSON_EMAIL_INTF - Email file
    # =========================================================================
    ('HCM_PERSON_EMAIL_INTF', 'RHUM'): {
        'COUNTRY': 'COUNTRY_CODE',
        'EMPLID': 'PERSON_NUMBER',
        'E_ADDR_TYPE': 'EMAIL_TYPE',
        'Email_ADDR': 'EMAIL_ADDRESS',
        'EFFDT': 'EFFECTIVE_START_DATE',
    },
    ('HCM_PERSON_EMAIL_INTF', 'FIMAS'): {
        'COUNTRY': 'COUNTRY_CODE',
        'EMPLID': 'PERSON_NUMBER',
        'E_ADDR_TYPE': 'EMAIL_TYPE',
        'EmailADDR': 'EMAIL_ADDRESS',
        'EFFDT': 'EFFECTIVE_START_DATE',
    },
    ('HCM_PERSON_EMAIL_INTF', 'HACIENDA'): {
        'COUNTRY': 'COUNTRY_CODE',
        'EMPLID': 'PERSON_NUMBER',
        'E_ADDR_TYPE': 'EMAIL_TYPE',
        'EmailADDR': 'EMAIL_ADDRESS',
        'EFFDT': 'EFFECTIVE_START_DATE',
    },
    ('HCM_PERSON_EMAIL_INTF', 'DOE'): {
        'COUNTRY_CODE': 'COUNTRY_CODE',
        'SWIFT_PERSON_TYPE': 'SWIFT_PERSON_TYPE',
        'PERSON_NUMBER': 'PERSON_NUMBER',
        'EMAIL_TYPE': 'EMAIL_TYPE',
        'EMAIL_ADDRESS': 'EMAIL_ADDRESS',
        'DATE_FROM': 'EFFECTIVE_START_DATE',
        'PRIMARY_FLAG': 'PRIMARY_FLAG',
        'ATTRIBUTE_CATEGORY': 'ATTRIBUTE_CATEGORY',
        'ATTRIBUTE1': 'ATTRIBUTE1',
        'X': 'X',
    },
    ('HCM_PERSON_EMAIL_INTF', 'KRONOSPOL'): {
        'COUNTRY_CODE': 'COUNTRY_CODE',
        'SWIFT_PERSON_TYPE': 'SWIFT_PERSON_TYPE',
        'PERSON_NUMBER': 'PERSON_NUMBER',
        'EMAIL_TYPE': 'EMAIL_TYPE',
        'EMAIL_ADDRESS': 'EMAIL_ADDRESS',
        'EFFECTIVE_START_DATE': 'EFFECTIVE_START_DATE',
        'PRIMARY_FLAG': 'PRIMARY_FLAG',
        'ATTRIBUTE_CATEGORY': 'ATTRIBUTE_CATEGORY',
        'ATTRIBUTE1': 'ATTRIBUTE1',
    },
    ('HCM_PERSON_EMAIL_INTF', 'ADPPOLICIA'): {
        'COUNTRY_CODE': 'COUNTRY_CODE',
        'SWIFT_PERSON_TYPE': 'SWIFT_PERSON_TYPE',
        'PERSON_NUMBER': 'PERSON_NUMBER',
        'EMAIL_TYPE': 'EMAIL_TYPE',
        'DATE_FROM': 'DATE_FROM',
        'DATE_TO': 'DATE_TO',
        'EMAIL_ADDRESS': 'EMAIL_ADDRESS',
        'PRIMARY_FLAG': 'PRIMARY_FLAG',
        'SOURCE_SYSTEM_OWNER': 'SOURCE_SYSTEM_OWNER',
    },
}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_aws_secret(secret_name: str, region: str = 'us-east-1') -> str:
    """Retrieve a secret from AWS Secrets Manager."""
    client = boto3.client('secretsmanager', region_name=region)
    response = client.get_secret_value(SecretId=secret_name)

    if 'SecretString' in response:
        return response['SecretString']
    else:
        return response['SecretBinary'].decode('utf-8')


def extract_source_from_filename(filename: str) -> Optional[str]:
    """
    Extract the source system from a filename.

    File naming convention: HCM_{ENTITY}_INTF_{SOURCE}_{DATE}.csv

    Examples:
        HCM_PERSON_ADDRESS_INTF_FIMAS_20251209.csv -> FIMAS
        HCM_PERSON_INTF_RHUM_20251209.csv -> RHUM
        Worker_Assignment_HAC88_20251205.csv -> HACIENDA (via alias)
    """
    try:
        fname_nopath = filename.split('/')[-1].split('\\')[-1]
        parts = fname_nopath.replace('.csv', '').replace('.CSV', '').split('_')

        # Try different positions for source name
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


def extract_entity_from_filename(filename: str) -> Optional[str]:
    """
    Extract the entity type from a filename.

    Examples:
        HCM_PERSON_ADDRESS_INTF_FIMAS_20251209.csv -> HCM_PERSON_ADDRESS_INTF
        HCM_PERSON_INTF_RHUM_20251209.csv -> HCM_PERSON_INTF
    """
    fname_nopath = filename.split('/')[-1].split('\\')[-1].upper()

    # Known entity patterns
    entities = [
        'HCM_PERSON_ASSIGNMENT_INTF',
        'HCM_PERSON_ADDRESS_INTF',
        'HCM_PERSON_SUPERVISOR_INTF',
        'HCM_PERSON_EMAIL_INTF',
        'HCM_PERSON_NAME_INTF',
        'HCM_PERSON_NID_INTF',
        'HCM_PERSON_INTF',
        'HCM_SENIORITY_INTF',
        'HCM_EXTERNAL_IDENTIFIER_INTF',
        'HCM_DEPARTMENT_INTF',
        'HCM_JOBS_INTF',
        'HCM_LOCATION_INTF',
        'HCM_EMPLOYEES_TO_SYNCH',
    ]

    for entity in entities:
        if entity in fname_nopath:
            return entity

    return None


def extract_table_name_from_filename(filename: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract the table name and date portion from a filename.

    Returns:
        Tuple of (table_name, date_portion) or (None, None) if parsing fails
    """
    # Remove extension
    name = filename.rsplit('.', 1)[0] if '.' in filename else filename

    # Pattern: everything before the last underscore followed by digits
    match = re.match(r'^(.+?)_(\d{8,14})$', name)

    if match:
        table_name = match.group(1).upper()
        date_portion = match.group(2)
        return table_name, date_portion

    return None, None


def sanitize_column_name(name: str) -> str:
    """Sanitize a column name for SQL Server."""
    sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    if sanitized and sanitized[0].isdigit():
        sanitized = '_' + sanitized
    return sanitized[:128] if sanitized else 'COLUMN'


def get_column_mapping(entity: str, source: str) -> Optional[Dict[str, str]]:
    """
    Get the column mapping for a specific entity and source combination.

    Args:
        entity: Entity type (e.g., 'HCM_PERSON_INTF')
        source: Source system (e.g., 'FIMAS')

    Returns:
        Dict mapping CSV column names to DB column names, or None if not found
    """
    key = (entity, source)
    return COLUMN_MAPPINGS.get(key)


def parse_connection_string(conn_str: str) -> Dict:
    """Parse ODBC connection string into components for pymssql."""
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


def read_csv_data(s3_client, bucket: str, key: str) -> Tuple[List[str], List[List[str]]]:
    """Read CSV data from S3."""
    response = s3_client.get_object(Bucket=bucket, Key=key)
    content = response['Body'].read().decode('utf-8', errors='ignore')

    reader = csv.reader(io.StringIO(content))
    rows = list(reader)

    if not rows:
        return [], []

    headers = [h.strip().strip('"').strip() for h in rows[0]]
    data_rows = []

    for row in rows[1:]:
        if len(row) < len(headers):
            row = row + [''] * (len(headers) - len(row))
        elif len(row) > len(headers):
            row = row[:len(headers)]

        cleaned_row = [str(val).strip() if val else '' for val in row]
        data_rows.append(cleaned_row)

    return headers, data_rows


def get_table_columns(cursor, table_name: str) -> List[str]:
    """Get the list of column names from a database table."""
    safe_table_name = sanitize_column_name(table_name)
    cursor.execute(f"""
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = '{safe_table_name}' AND TABLE_SCHEMA = 'dbo'
        ORDER BY ORDINAL_POSITION
    """)
    return [row[0].upper() for row in cursor.fetchall()]


# =============================================================================
# MAIN LOADING FUNCTION
# =============================================================================

def load_file_to_sql(
    s3_client,
    connection_string: str,
    bucket: str,
    s3_key: str,
    table_name: str,
    filename: str,
    clear_existing: bool = True
) -> Dict:
    """
    Load a single CSV file from S3 into an existing SQL Server table.
    Uses proper column mapping based on entity type and source system.
    """
    import pymssql

    safe_table_name = sanitize_column_name(table_name)

    result = {
        'table_name': table_name,
        's3_key': s3_key,
        'filename': filename,
        'success': False,
        'rows_loaded': 0,
        'error': None,
        'error_type': None,
        'columns_matched': 0,
        'columns_skipped': [],
        'csv_columns': [],
        'db_columns': [],
    }

    try:
        # Extract entity and source from filename
        entity = extract_entity_from_filename(filename)
        source = extract_source_from_filename(filename)

        result['entity'] = entity
        result['source'] = source

        if not entity:
            result['error'] = f'Could not determine entity type from filename: {filename}'
            result['error_type'] = 'INVALID_FILENAME'
            return result

        if not source:
            result['error'] = f'Could not determine source from filename: {filename}'
            result['error_type'] = 'INVALID_FILENAME'
            return result

        # Get column mapping for this entity/source combination
        column_mapping = get_column_mapping(entity, source)

        if not column_mapping:
            result['error'] = f'No column mapping defined for entity={entity}, source={source}'
            result['error_type'] = 'NO_MAPPING'
            return result

        # Read CSV data
        headers, rows = read_csv_data(s3_client, bucket, s3_key)
        result['csv_columns'] = headers
        result['csv_row_count'] = len(rows)

        if not headers:
            result['error'] = 'No headers found in CSV'
            result['error_type'] = 'NO_HEADERS'
            return result

        # Parse connection string and connect
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

            # Set session options for proper date handling
            cursor.execute("SET LANGUAGE us_english")
            cursor.execute("SET DATEFORMAT mdy")

            # Get actual database columns for this table
            db_columns = get_table_columns(cursor, table_name)
            result['db_columns'] = db_columns

            if not db_columns:
                result['error'] = f'Table {table_name} not found in database or has no columns'
                result['error_type'] = 'TABLE_NOT_FOUND'
                return result

            # Build column index mapping: csv_index -> (csv_col, db_col)
            db_cols_upper = {c.upper(): c for c in db_columns}
            col_indices = []  # List of (csv_index, db_column_name)
            skipped_cols = []
            mapping_details = []  # Debug: show exactly what was mapped

            for i, csv_col in enumerate(headers):
                csv_col_clean = csv_col.strip()

                # Check if this CSV column has a mapping defined
                if csv_col_clean in column_mapping:
                    db_col = column_mapping[csv_col_clean]
                    # Verify the DB column exists
                    if db_col.upper() in db_cols_upper:
                        col_indices.append((i, db_cols_upper[db_col.upper()]))
                        mapping_details.append(f"{csv_col_clean} -> {db_col} (mapped)")
                    else:
                        skipped_cols.append(f"{csv_col_clean} (mapped to {db_col} but not in DB)")
                elif csv_col_clean.upper() in db_cols_upper:
                    # Direct match (no mapping needed)
                    col_indices.append((i, db_cols_upper[csv_col_clean.upper()]))
                    mapping_details.append(f"{csv_col_clean} -> {csv_col_clean} (direct)")
                else:
                    skipped_cols.append(csv_col_clean)

            result['columns_matched'] = len(col_indices)
            result['columns_skipped'] = skipped_cols
            result['mapping_details'] = mapping_details
            result['column_mapping_used'] = column_mapping
            result['db_cols_upper'] = list(db_cols_upper.keys())

            if not col_indices:
                result['error'] = f'No CSV columns could be mapped to database columns'
                result['error_type'] = 'COLUMN_MISMATCH'
                return result

            # Clear existing data if requested (but not for RHUM - append mode)
            if clear_existing and source != 'RHUM':
                cursor.execute(f"DELETE FROM dbo.[{safe_table_name}]")
                conn.commit()

            # Insert data
            if rows:
                matched_db_cols = [col_info[1] for col_info in col_indices]
                column_names = ", ".join(f"[{col}]" for col in matched_db_cols)
                placeholders = ", ".join(["%s"] * len(matched_db_cols))
                insert_sql = f"INSERT INTO dbo.[{safe_table_name}] ({column_names}) VALUES ({placeholders})"

                batch_size = 1000
                rows_inserted = 0
                failed_rows = []

                for i in range(0, len(rows), batch_size):
                    batch = rows[i:i + batch_size]
                    filtered_batch = [
                        tuple(row[idx] if idx < len(row) else '' for idx, _ in col_indices)
                        for row in batch
                    ]
                    try:
                        cursor.executemany(insert_sql, filtered_batch)
                        rows_inserted += len(filtered_batch)
                    except Exception as batch_error:
                        # Try one by one to identify problem rows
                        for row_idx, row_data in enumerate(filtered_batch):
                            try:
                                cursor.execute(insert_sql, row_data)
                                rows_inserted += 1
                            except Exception as row_error:
                                actual_row_num = i + row_idx + 2
                                failed_rows.append({
                                    'row_number': actual_row_num,
                                    'error': str(row_error),
                                })
                                if len(failed_rows) >= 10:
                                    break
                        if len(failed_rows) >= 10:
                            break

                conn.commit()
                result['rows_loaded'] = rows_inserted

                if failed_rows:
                    result['failed_rows'] = failed_rows
                    result['error'] = f'Some rows failed to insert: {len(failed_rows)} errors'
                    result['error_type'] = 'DATA_ERROR'
                    result['success'] = rows_inserted > 0
                    return result

            result['success'] = True

    except Exception as e:
        error_str = str(e)
        result['error'] = error_str

        if 'truncat' in error_str.lower():
            result['error_type'] = 'DATA_TRUNCATION'
        elif 'conversion' in error_str.lower() or 'convert' in error_str.lower():
            result['error_type'] = 'DATA_CONVERSION'
        elif 'constraint' in error_str.lower():
            result['error_type'] = 'CONSTRAINT_VIOLATION'
        elif 'permission' in error_str.lower() or 'denied' in error_str.lower():
            result['error_type'] = 'PERMISSION_ERROR'
        else:
            result['error_type'] = 'UNKNOWN_ERROR'

    return result


def identify_files_to_load(files: List[Dict]) -> Dict[str, Dict]:
    """
    Identify the newest version of each file type to load.
    """
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

    result = {}
    for table_name, files_list in table_files.items():
        sorted_files = sorted(files_list, key=lambda x: x['date_portion'], reverse=True)
        result[table_name] = sorted_files[0]

    return result


# =============================================================================
# SQLSERVERLOADER CLASS (Used by full_pipeline.py)
# =============================================================================

class SqlServerLoader:
    """
    SQL Server loader class for use by the full pipeline orchestrator.
    """

    def __init__(
        self,
        bucket: str = None,
        secret_name: str = 'Hacienda_ERP_Test_MSSQL_text',
        database_override: Optional[str] = None,
        region: str = 'us-east-1'
    ):
        import os
        self.bucket = bucket or os.environ.get('S3_BUCKET', 'hacienda-sftp-downloads')
        self.secret_name = secret_name
        self.database_override = database_override
        self.region = region
        self.s3_client = boto3.client('s3', region_name=region)

    def load_files(
        self,
        files: List[Dict],
        drop_existing: bool = False
    ) -> Dict:
        """
        Load multiple files to SQL Server.

        Args:
            files: List of dicts with 'filename' and 's3_key'
            drop_existing: Whether to clear existing data (True = clear)

        Returns:
            Dict with load results
        """
        # Get connection string
        connection_string = get_aws_secret(self.secret_name, self.region)

        # Apply database override if specified
        if self.database_override:
            # Modify connection string to use different database
            parts = connection_string.split(';')
            new_parts = []
            for part in parts:
                if part.strip().upper().startswith('DATABASE='):
                    new_parts.append(f'Database={self.database_override}')
                else:
                    new_parts.append(part)
            connection_string = ';'.join(new_parts)

        # Identify files to load
        files_to_load = identify_files_to_load(files)

        results = {
            'total_tables': len(files_to_load),
            'loaded_tables': 0,
            'failed_tables': 0,
            'total_rows': 0,
            'table_results': [],
            'detailed_failures': []
        }

        for table_name, file_info in files_to_load.items():
            s3_key = file_info.get('s3_key') or file_info.get('key', '')
            filename = file_info.get('filename', '')

            load_result = load_file_to_sql(
                s3_client=self.s3_client,
                connection_string=connection_string,
                bucket=self.bucket,
                s3_key=s3_key,
                table_name=table_name,
                filename=filename,
                clear_existing=drop_existing
            )

            load_result['source_file'] = filename
            results['table_results'].append(load_result)

            if load_result['success']:
                results['loaded_tables'] += 1
                results['total_rows'] += load_result.get('rows_loaded', 0)
            else:
                results['failed_tables'] += 1
                results['detailed_failures'].append(load_result)

        return results


# =============================================================================
# LAMBDA HANDLERS
# =============================================================================

def load_to_sql_handler(event, context):
    """Lambda handler for loading validated files to SQL Server."""
    import os
    import json

    try:
        if 'body' in event and event['body']:
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        else:
            body = event

        files = body.get('files', [])
        bucket = body.get('bucket', os.environ.get('S3_BUCKET', 'hacienda-sftp-downloads'))
        clear_existing = body.get('clear_existing', True)
        database_override = body.get('database_override')

        if not files:
            return {
                'statusCode': 400,
                'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
                'body': json.dumps({'error': 'No files provided'})
            }

        loader = SqlServerLoader(
            bucket=bucket,
            database_override=database_override
        )

        results = loader.load_files(files=files, drop_existing=clear_existing)

        return {
            'statusCode': 200,
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'body': json.dumps(results, default=str)
        }

    except Exception as e:
        return {
            'statusCode': 500,
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'error': str(e)})
        }
