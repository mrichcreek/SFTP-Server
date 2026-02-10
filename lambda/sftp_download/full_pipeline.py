"""
Full Pipeline Orchestrator for HCM Data Processing

This module coordinates the complete data processing pipeline:
1. VPN Connectivity Check
2. SFTP Download
3. Duplicate Check (auto-move duplicates)
4. Name Validation
5. Column Schema Validation
6. Completeness Check
7. Load to SQL Server
8. Run HCM_MAIN_INTF stored procedure
9. (Future) Export delta files
10. (Future) Upload to Sterling

All files are organized in timestamped S3 folders:
    YYYYMMDD_HHMM/
        1_Initial_Files/
        2_Validation_Reports/
        3_Load_Reports/
        4_Post_Load_Reports/
        5_Export_Files/
        6_Upload_Reports/
"""

import boto3
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field, asdict
from enum import Enum

# Puerto Rico timezone (UTC-4, no daylight saving time)
PR_TIMEZONE = timezone(timedelta(hours=-4))

# Import existing modules
try:
    from .duplicate_detector import detect_and_move_duplicates
    from .completeness_checker import (
        check_completeness as check_file_completeness,
        get_missing_files_report as generate_completeness_report,
        get_files_for_complete_entities
    )
    from .name_validator import validate_file_list as validate_file_names
    from .column_schema import (
        validate_files_schema,
        generate_schema_validation_report
    )
    from .sql_loader import SqlServerLoader
    from .stored_procedure_runner import execute_hcm_main_intf
    from .delta_exporter import DeltaExporter
    from .sftp_uploader import SftpUploader
    from .sftp_downloader import SftpDownloader, download_from_sftp
    from .report_generator import get_report_url_handler
except ImportError:
    from sftp_download.duplicate_detector import detect_and_move_duplicates
    from sftp_download.completeness_checker import (
        check_completeness as check_file_completeness,
        get_missing_files_report as generate_completeness_report,
        get_files_for_complete_entities
    )
    from sftp_download.name_validator import validate_file_list as validate_file_names
    from sftp_download.column_schema import (
        validate_files_schema,
        generate_schema_validation_report
    )
    from sftp_download.sql_loader import SqlServerLoader
    from sftp_download.stored_procedure_runner import execute_hcm_main_intf
    from sftp_download.delta_exporter import DeltaExporter
    from sftp_download.sftp_uploader import SftpUploader
    from sftp_download.sftp_downloader import SftpDownloader, download_from_sftp
    from sftp_download.report_generator import get_report_url_handler


class PipelineStep(Enum):
    """Pipeline step identifiers."""
    VPN_CHECK = "vpn_check"
    SFTP_DOWNLOAD = "sftp_download"
    CREATE_FOLDERS = "create_folders"
    DUPLICATE_CHECK = "duplicate_check"
    NAME_VALIDATION = "name_validation"
    SCHEMA_VALIDATION = "schema_validation"
    COMPLETENESS_CHECK = "completeness_check"
    SQL_LOAD = "sql_load"
    RUN_PROCEDURE = "run_procedure"
    EXPORT_FILES = "export_files"
    UPLOAD_STERLING = "upload_sterling"
    COMPLETE = "complete"


@dataclass
class StepResult:
    """Result of a single pipeline step."""
    step: str
    success: bool
    message: str
    details: Dict = field(default_factory=dict)
    report_key: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


@dataclass
class PipelineResult:
    """Complete pipeline execution result."""
    pipeline_id: str
    folder_name: str
    status: str  # 'success', 'failed', 'partial'
    current_step: str
    total_steps: int
    completed_steps: int
    steps: List[StepResult] = field(default_factory=list)
    error: Optional[str] = None
    report_url: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            'pipeline_id': self.pipeline_id,
            'folder_name': self.folder_name,
            'status': self.status,
            'current_step': self.current_step,
            'total_steps': self.total_steps,
            'completed_steps': self.completed_steps,
            'steps': [asdict(s) for s in self.steps],
            'error': self.error,
            'report_url': self.report_url,
            'started_at': self.started_at,
            'completed_at': self.completed_at
        }


class FullPipelineOrchestrator:
    """
    Orchestrates the complete data processing pipeline.

    Usage:
        orchestrator = FullPipelineOrchestrator(
            bucket='hacienda-sftp-downloads',
            secret_name='Hacienda_ERP_MSSQL_Production'
        )
        result = orchestrator.run_pipeline(
            environment='test',
            test_mode=True,
            on_progress=lambda step, pct, msg: print(f"{pct}% - {msg}")
        )
    """

    # S3 subfolder structure
    FOLDERS = {
        'initial': '1_Initial_Files',
        'validation': '2_Validation_Reports',
        'load': '3_Load_Reports',
        'invalid': '4_InvalidFiles',
        'invalid_schema': '5_InvalidSchema',
        'delta': '6_Delta_Files',
        'export': '7_Export_Files',
        'upload': '8_Upload_Reports'
    }

    # Steps in execution order with their weights for progress calculation
    STEP_WEIGHTS = {
        PipelineStep.VPN_CHECK: 5,
        PipelineStep.SFTP_DOWNLOAD: 15,
        PipelineStep.CREATE_FOLDERS: 5,
        PipelineStep.DUPLICATE_CHECK: 10,
        PipelineStep.NAME_VALIDATION: 10,
        PipelineStep.SCHEMA_VALIDATION: 10,
        PipelineStep.COMPLETENESS_CHECK: 10,
        PipelineStep.SQL_LOAD: 15,
        PipelineStep.RUN_PROCEDURE: 15,
        PipelineStep.EXPORT_FILES: 3,
        PipelineStep.UPLOAD_STERLING: 2,
    }

    def __init__(
        self,
        bucket: str = None,
        secret_name: str = 'Hacienda_ERP_MSSQL_Production',
        region: str = 'us-east-1'
    ):
        self.bucket = bucket or os.environ.get('S3_BUCKET', 'hacienda-sftp-downloads')
        self.secret_name = secret_name
        self.region = region
        self.s3_client = boto3.client('s3', region_name=region)

    def _create_timestamped_folder(self) -> str:
        """Create a timestamped folder name using Puerto Rico time (UTC-4)."""
        pr_time = datetime.now(PR_TIMEZONE)
        return pr_time.strftime('%Y%m%d_%H%M')

    def create_timestamped_folder(self, source_prefix: str = 'downloads/') -> Dict:
        """
        Create a timestamped folder and move files from source to it.
        This is the public method called by Step Functions.

        Args:
            source_prefix: S3 prefix where source files are located (default: 'downloads/')

        Returns:
            Dict with folder_name, files_moved, success, and folder structure
        """
        try:
            # Create timestamped folder name
            folder_name = self._create_timestamped_folder()

            # Create S3 folder structure
            folders = self._create_s3_folders(folder_name)

            # Copy files from source to initial files folder
            initial_folder = folders['initial']
            files_copied = self._copy_files_to_folder(source_prefix, initial_folder)

            # Delete original files from downloads folder after successful copy
            for file_info in files_copied:
                source_key = file_info.get('source_key')
                if source_key:
                    try:
                        self.s3_client.delete_object(Bucket=self.bucket, Key=source_key)
                    except Exception:
                        pass  # Continue even if delete fails

            return {
                'success': True,
                'folder_name': folder_name,
                'files_moved': len(files_copied),
                'folders': folders,
                'files': files_copied
            }

        except Exception as e:
            import traceback
            return {
                'success': False,
                'error': str(e),
                'traceback': traceback.format_exc()
            }

    def run_validation_steps(self, folder: str) -> Dict:
        """
        Run all validation steps for Step Functions.
        Steps: duplicate check, name validation, schema validation, completeness check.

        Args:
            folder: Timestamped folder name (e.g., '20260206_1530')

        Returns:
            Dict with validation results
        """
        try:
            folders = self._create_s3_folders(folder)
            initial_folder = folders['initial']

            # Get list of files to validate
            files = self._list_initial_files(initial_folder)

            if not files:
                return {
                    'all_passed': False,
                    'has_critical_errors': True,
                    'error': 'No CSV files found in initial folder',
                    'valid_files': []
                }

            results = {
                'all_passed': True,
                'has_critical_errors': False,
                'duplicate_check': {},
                'name_validation': {},
                'schema_validation': {},
                'completeness_check': {},
                'valid_files': [],
                'invalid_files': []
            }

            # Step 1: Duplicate Check
            try:
                dup_result = detect_and_move_duplicates(
                    prefix=initial_folder,
                    bucket=self.bucket,
                    auto_move=True,
                    destination_folder=folders['invalid']  # Move duplicates to invalid folder
                )
                results['duplicate_check'] = {
                    'success': True,
                    'duplicates_found': dup_result.get('duplicates_moved', 0),
                    'files_remaining': dup_result.get('files_remaining', len(files))
                }

                # Re-list files after duplicate removal
                files = self._list_initial_files(initial_folder)

            except Exception as e:
                results['duplicate_check'] = {
                    'success': False,
                    'error': str(e)
                }

            # Step 2: Name Validation
            try:
                file_list = [f['filename'] for f in files]
                name_result = validate_file_names(file_list)

                # name_result is a dict with 'results' list of ValidationResult dataclass objects
                validation_results = name_result.get('results', [])
                # ValidationResult has is_valid attribute, not 'valid' key
                invalid_names = [r for r in validation_results if not r.is_valid]
                valid_names = [r for r in validation_results if r.is_valid]

                results['name_validation'] = {
                    'success': len(invalid_names) == 0,
                    'files_checked': len(file_list),
                    'invalid_count': len(invalid_names),
                    'valid_count': len(valid_names),
                    'invalid_files': [{'file_name': r.file_name, 'error': r.error_message} for r in invalid_names[:10]]
                }

                # Move invalid name files to invalid folder
                if invalid_names:
                    invalid_filenames = {r.file_name for r in invalid_names}
                    files_by_name = {f['filename']: f for f in files}

                    for invalid_result in invalid_names:
                        file_info = files_by_name.get(invalid_result.file_name)
                        source_key = file_info.get('s3_key') if file_info else None
                        if source_key:
                            filename = source_key.split('/')[-1]
                            dest_key = f"{folders['invalid']}/{filename}"
                            try:
                                self.s3_client.copy_object(
                                    Bucket=self.bucket,
                                    CopySource={'Bucket': self.bucket, 'Key': source_key},
                                    Key=dest_key
                                )
                                self.s3_client.delete_object(Bucket=self.bucket, Key=source_key)
                                results['invalid_files'].append({
                                    'filename': filename,
                                    'reason': 'name_invalid',
                                    'error': invalid_result.error_message
                                })
                            except Exception:
                                pass

                    # Re-list files after moving invalid ones
                    files = self._list_initial_files(initial_folder)
                    results['all_passed'] = False

                    # Only critical if ALL files had invalid names
                    if len(valid_names) == 0:
                        results['has_critical_errors'] = True

            except Exception as e:
                results['name_validation'] = {
                    'success': False,
                    'error': str(e)
                }

            # Step 3: Schema Validation
            try:
                # validate_files_schema expects (files_list, s3_client, bucket) and returns SchemaValidationReport
                schema_report = validate_files_schema(
                    files,  # List of dicts with 'filename' and 's3_key'
                    self.s3_client,
                    self.bucket
                )

                # SchemaValidationReport has .results (list of ColumnValidationResult dataclasses)
                invalid_schema = [r for r in schema_report.results if not r.is_valid]
                results['schema_validation'] = {
                    'success': len(invalid_schema) == 0,
                    'files_checked': schema_report.total_files,
                    'invalid_count': len(invalid_schema),
                    'invalid_files': [{'file_name': r.file_name, 'error': r.error_message} for r in invalid_schema[:10]]
                }

                # Move invalid schema files - find their s3_keys from the original files list
                files_by_name = {f['filename']: f for f in files}
                for invalid in invalid_schema:
                    file_info = files_by_name.get(invalid.file_name)
                    source_key = file_info.get('s3_key') if file_info else None
                    if source_key:
                        filename = source_key.split('/')[-1]
                        dest_key = f"{folders['invalid_schema']}{filename}"
                        try:
                            self.s3_client.copy_object(
                                Bucket=self.bucket,
                                CopySource={'Bucket': self.bucket, 'Key': source_key},
                                Key=dest_key
                            )
                            self.s3_client.delete_object(Bucket=self.bucket, Key=source_key)
                            results['invalid_files'].append({
                                'filename': filename,
                                'reason': 'schema_invalid',
                                'errors': invalid.get('errors', [])
                            })
                        except Exception:
                            pass

                if invalid_schema:
                    results['all_passed'] = False
                    # Schema errors are critical only if all files fail
                    if len(invalid_schema) == len(files):
                        results['has_critical_errors'] = True

            except Exception as e:
                results['schema_validation'] = {
                    'success': False,
                    'error': str(e)
                }

            # Step 4: Completeness Check
            try:
                # Re-list files after schema validation
                files = self._list_initial_files(initial_folder)
                file_list = [f['filename'] for f in files]

                completeness = check_file_completeness(file_list)

                # CompletenessResult is a dataclass, access attributes directly
                # We have valid data if at least one entity set is complete
                has_complete_sets = completeness.complete_sets > 0
                is_fully_complete = has_complete_sets and completeness.incomplete_sets == 0

                results['completeness_check'] = {
                    'success': is_fully_complete,
                    'has_complete_sets': has_complete_sets,
                    'entities_complete': completeness.complete_entities,
                    'entities_incomplete': completeness.incomplete_entities,
                    'complete_sets': completeness.complete_sets,
                    'incomplete_sets': completeness.incomplete_sets,
                    'total_files': completeness.total_files
                }

                # Move files from incomplete entity sets to invalid folder
                if completeness.incomplete_entities:
                    incomplete_entities = set(e.upper() for e in completeness.incomplete_entities)
                    files_by_name = {f['filename']: f for f in files}

                    for f in files:
                        filename = f['filename']
                        # Extract entity from filename (e.g., RHUM from hcm_person_intf_rhum_20260116.csv)
                        name_result = validate_file_names([filename])
                        if name_result.get('results'):
                            file_entity = name_result['results'][0].entity
                            if file_entity and file_entity.upper() in incomplete_entities:
                                source_key = f.get('s3_key')
                                if source_key:
                                    dest_key = f"{folders['invalid']}/{filename}"
                                    try:
                                        self.s3_client.copy_object(
                                            Bucket=self.bucket,
                                            CopySource={'Bucket': self.bucket, 'Key': source_key},
                                            Key=dest_key
                                        )
                                        self.s3_client.delete_object(Bucket=self.bucket, Key=source_key)
                                        results['invalid_files'].append({
                                            'filename': filename,
                                            'reason': 'incomplete_entity_set',
                                            'entity': file_entity
                                        })
                                    except Exception:
                                        pass

                    # Re-list files after moving incomplete sets
                    files = self._list_initial_files(initial_folder)

                # Only critical if NO complete entity sets remain
                if not has_complete_sets:
                    results['has_critical_errors'] = True
                    results['all_passed'] = False
                elif not is_fully_complete:
                    # Some incomplete sets were removed, but we have complete sets to process
                    results['all_passed'] = False  # Not perfect, but we can continue

            except Exception as e:
                results['completeness_check'] = {
                    'success': False,
                    'error': str(e)
                }

            # Final list of valid files
            files = self._list_initial_files(initial_folder)
            results['valid_files'] = [f['s3_key'] for f in files]

            # Generate validation reports
            pipeline_id = datetime.now().strftime('%Y%m%d_%H%M%S')

            # Generate name validation report
            if results['name_validation'].get('invalid_count', 0) > 0:
                name_report_lines = [
                    "=" * 80,
                    "FILE NAME VALIDATION REPORT",
                    "=" * 80,
                    "",
                    "SUMMARY",
                    "-" * 40,
                    f"Total files checked: {results['name_validation'].get('files_checked', 0)}",
                    f"Valid files: {results['name_validation'].get('valid_count', 0)}",
                    f"Invalid files: {results['name_validation'].get('invalid_count', 0)}",
                    "",
                    "NOTE: Pipeline will continue processing VALID files.",
                    "Invalid files have been moved to the InvalidFiles folder.",
                    "",
                    "EXPECTED FILE NAME FORMAT",
                    "-" * 40,
                    "Pattern: HCM_{SOURCE}_INTF_{ENTITY}_{DATE}.csv",
                    "",
                    "Valid SOURCES: PERSON, PERSON_NAME, PERSON_ASSIGNMENT, PERSON_ADDRESS, PERSON_NID, PERSON_SUPERVISOR, PERSON_EMAIL, SENIORITY",
                    "Valid ENTITIES: 911, RHUM, HACIENDA, FIMAS, DOE, KRONOSPOL, KRONOSDE, SEPI, ADPPOLICIA",
                    "Valid DATE formats: YYYYMMDD, YYYYMMDDHHMM, YYYYMMDDHHMMSS",
                    "",
                    "=" * 80,
                    "INVALID FILES - ACTION REQUIRED",
                    "=" * 80,
                    ""
                ]
                for i, inv_file in enumerate(results['name_validation'].get('invalid_files', []), 1):
                    name_report_lines.append(f"[{i}] FILE: {inv_file.get('file_name', 'Unknown')}")
                    name_report_lines.append(f"    ERROR: {inv_file.get('error', 'Unknown error')}")
                    name_report_lines.append("")

                name_report_lines.append("=" * 80)
                name_report_lines.append("END OF VALIDATION REPORT")
                name_report_lines.append("=" * 80)

                self._upload_report(
                    folders['validation'],
                    f'name_validation_{pipeline_id}.txt',
                    "\n".join(name_report_lines)
                )

            # Generate completeness report
            comp_check = results.get('completeness_check', {})
            comp_report_lines = [
                "=" * 80,
                "FILE COMPLETENESS REPORT",
                "=" * 80,
                "",
                "SUMMARY",
                "-" * 40,
                f"Total files: {comp_check.get('total_files', 0)}",
                f"Complete entity sets: {comp_check.get('complete_sets', 0)}",
                f"Incomplete entity sets: {comp_check.get('incomplete_sets', 0)}",
                "",
                "COMPLETE ENTITIES (will be processed):",
            ]
            for entity in comp_check.get('entities_complete', []):
                comp_report_lines.append(f"  - {entity}")
            if not comp_check.get('entities_complete'):
                comp_report_lines.append("  (none)")

            comp_report_lines.append("")
            comp_report_lines.append("INCOMPLETE ENTITIES (moved to InvalidFiles):")
            for entity in comp_check.get('entities_incomplete', []):
                comp_report_lines.append(f"  - {entity}")
            if not comp_check.get('entities_incomplete'):
                comp_report_lines.append("  (none)")

            comp_report_lines.append("")
            comp_report_lines.append("=" * 80)

            self._upload_report(
                folders['validation'],
                f'completeness_{pipeline_id}.txt',
                "\n".join(comp_report_lines)
            )

            # Generate pipeline summary report
            summary_lines = [
                "=" * 80,
                "PIPELINE VALIDATION SUMMARY",
                "=" * 80,
                "",
                f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"Folder: {folder}",
                "",
                "STEP RESULTS:",
                "-" * 40,
                f"1. Duplicate Check: {'PASS' if results['duplicate_check'].get('success') else 'FAIL'}",
                f"   Duplicates found: {results['duplicate_check'].get('duplicates_found', 0)}",
                "",
                f"2. Name Validation: {'PASS' if results['name_validation'].get('success') else 'PARTIAL' if results['name_validation'].get('valid_count', 0) > 0 else 'FAIL'}",
                f"   Valid files: {results['name_validation'].get('valid_count', 0)}",
                f"   Invalid files: {results['name_validation'].get('invalid_count', 0)}",
                "",
                f"3. Schema Validation: {'PASS' if results['schema_validation'].get('success') else 'FAIL'}",
                f"   Files checked: {results['schema_validation'].get('files_checked', 0)}",
                f"   Invalid count: {results['schema_validation'].get('invalid_count', 0)}",
                "",
                f"4. Completeness Check: {'PASS' if comp_check.get('success') else 'PARTIAL' if comp_check.get('has_complete_sets') else 'FAIL'}",
                f"   Complete sets: {comp_check.get('complete_sets', 0)}",
                f"   Incomplete sets: {comp_check.get('incomplete_sets', 0)}",
                "",
                "-" * 40,
                f"OVERALL: {'PASSED' if results['all_passed'] else 'PASSED WITH WARNINGS' if not results['has_critical_errors'] else 'FAILED'}",
                f"Files to process: {len(results['valid_files'])}",
                "",
                "=" * 80,
            ]

            self._upload_report(
                folders['validation'],
                f'pipeline_summary_{pipeline_id}.txt',
                "\n".join(summary_lines)
            )

            return results

        except Exception as e:
            import traceback
            return {
                'all_passed': False,
                'has_critical_errors': True,
                'error': str(e),
                'traceback': traceback.format_exc(),
                'valid_files': []
            }

    def load_files_to_sql(self, folder: str, database: str = 'Hacienda_ERP') -> Dict:
        """
        Load validated files to SQL Server.

        Args:
            folder: Timestamped folder name (e.g., '20260206_1530')
            database: Target database name

        Returns:
            Dict with load results
        """
        try:
            folders = self._create_s3_folders(folder)
            initial_folder = folders['initial']

            # Get list of files to load
            files = self._list_initial_files(initial_folder)

            if not files:
                return {
                    'success': False,
                    'error': 'No files to load',
                    'tables_created': 0,
                    'rows_loaded': 0
                }

            # Create SQL loader
            loader = SqlServerLoader(
                bucket=self.bucket,
                secret_name=self.secret_name
            )

            # Load each file
            load_results = []
            total_rows = 0
            tables_created = 0

            for file_info in files:
                s3_key = file_info['s3_key']
                filename = file_info['filename']

                try:
                    result = loader.load_file(
                        s3_key=s3_key,
                        database=database,
                        drop_existing=True
                    )

                    load_results.append({
                        'filename': filename,
                        'success': result.get('success', False),
                        'table_name': result.get('table_name'),
                        'rows_loaded': result.get('rows_loaded', 0)
                    })

                    if result.get('success'):
                        tables_created += 1
                        total_rows += result.get('rows_loaded', 0)

                except Exception as e:
                    load_results.append({
                        'filename': filename,
                        'success': False,
                        'error': str(e)
                    })

            # Generate load report
            report_content = json.dumps({
                'timestamp': datetime.now().isoformat(),
                'folder': folder,
                'database': database,
                'tables_created': tables_created,
                'total_rows': total_rows,
                'files': load_results
            }, indent=2)

            report_key = self._upload_report(
                folders['load'],
                f"sql_load_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                report_content
            )

            return {
                'success': tables_created > 0,
                'tables_created': tables_created,
                'total_rows': total_rows,
                'files_processed': len(files),
                'files_loaded': tables_created,
                'load_results': load_results[:20],  # Limit response size
                'report_key': report_key
            }

        except Exception as e:
            import traceback
            return {
                'success': False,
                'error': str(e),
                'traceback': traceback.format_exc(),
                'tables_created': 0,
                'rows_loaded': 0
            }

    def _get_folder_path(self, base_folder: str, subfolder: str) -> str:
        """Get the full S3 prefix for a subfolder."""
        return f"{base_folder}/{self.FOLDERS.get(subfolder, subfolder)}/"

    def _create_s3_folders(self, base_folder: str) -> Dict[str, str]:
        """
        Create S3 folder structure by putting empty markers.
        Returns dict of folder type -> S3 prefix.
        """
        folders = {}
        for folder_type, folder_name in self.FOLDERS.items():
            prefix = f"{base_folder}/{folder_name}/"
            # S3 doesn't need explicit folder creation, but we track the paths
            folders[folder_type] = prefix
        return folders

    def _upload_report(
        self,
        folder_path: str,
        report_name: str,
        content: str
    ) -> str:
        """Upload a report to S3 and return the key."""
        key = f"{folder_path}{report_name}"
        self.s3_client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=content.encode('utf-8'),
            ContentType='text/plain'
        )
        return key

    def _generate_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        """Generate a presigned URL for downloading a file."""
        return self.s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': self.bucket, 'Key': key},
            ExpiresIn=expires_in
        )

    def _list_initial_files(self, folder_path: str) -> List[Dict]:
        """List all CSV files in the initial files folder."""
        files = []
        paginator = self.s3_client.get_paginator('list_objects_v2')

        for page in paginator.paginate(Bucket=self.bucket, Prefix=folder_path):
            for obj in page.get('Contents', []):
                key = obj['Key']
                if key.lower().endswith('.csv'):
                    filename = key.split('/')[-1]
                    files.append({
                        'filename': filename,
                        's3_key': key,
                        'size': obj['Size'],
                        'last_modified': obj['LastModified'].isoformat()
                    })

        return files

    def _copy_files_to_folder(
        self,
        source_prefix: str,
        dest_folder: str
    ) -> List[Dict]:
        """Copy files from source prefix to destination folder."""
        files_copied = []
        paginator = self.s3_client.get_paginator('list_objects_v2')

        for page in paginator.paginate(Bucket=self.bucket, Prefix=source_prefix):
            for obj in page.get('Contents', []):
                source_key = obj['Key']
                filename = source_key.split('/')[-1]

                if not filename or filename.startswith('.'):
                    continue

                dest_key = f"{dest_folder}{filename}"

                self.s3_client.copy_object(
                    Bucket=self.bucket,
                    CopySource={'Bucket': self.bucket, 'Key': source_key},
                    Key=dest_key
                )

                files_copied.append({
                    'filename': filename,
                    's3_key': dest_key,
                    'source_key': source_key
                })

        return files_copied

    def check_vpn_connectivity(self) -> StepResult:
        """
        Check VPN connectivity by attempting to reach the SFTP server.

        In AWS Lambda, this checks if the Lambda can reach the SFTP endpoint
        through the VPC configuration.
        """
        started = datetime.now().isoformat()

        try:
            import socket

            # SFTP server details from environment
            sftp_host = os.environ.get('SFTP_HOST', 'filetransfer.gm.com')
            sftp_port = int(os.environ.get('SFTP_PORT', 22))

            # Attempt to connect with a short timeout
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)

            try:
                sock.connect((sftp_host, sftp_port))
                sock.close()

                return StepResult(
                    step=PipelineStep.VPN_CHECK.value,
                    success=True,
                    message=f"VPN connectivity verified - {sftp_host}:{sftp_port} reachable",
                    details={'host': sftp_host, 'port': sftp_port},
                    started_at=started,
                    completed_at=datetime.now().isoformat()
                )
            except socket.timeout:
                return StepResult(
                    step=PipelineStep.VPN_CHECK.value,
                    success=False,
                    message=f"VPN connectivity failed - connection timeout to {sftp_host}:{sftp_port}",
                    details={'host': sftp_host, 'port': sftp_port, 'error': 'timeout'},
                    started_at=started,
                    completed_at=datetime.now().isoformat()
                )
            except socket.error as e:
                return StepResult(
                    step=PipelineStep.VPN_CHECK.value,
                    success=False,
                    message=f"VPN connectivity failed - cannot reach {sftp_host}:{sftp_port}",
                    details={'host': sftp_host, 'port': sftp_port, 'error': str(e)},
                    started_at=started,
                    completed_at=datetime.now().isoformat()
                )

        except Exception as e:
            return StepResult(
                step=PipelineStep.VPN_CHECK.value,
                success=False,
                message=f"VPN check failed: {str(e)}",
                details={'error': str(e)},
                started_at=started,
                completed_at=datetime.now().isoformat()
            )

    def run_pipeline(
        self,
        environment: str = 'test',
        test_mode: bool = True,
        source_prefix: str = 'downloads/',
        skip_download: bool = True,  # Skip SFTP download for now - use existing files
        skip_procedure: bool = False,  # Skip stored procedure (run locally from desktop app)
        skip_sftp_upload: bool = False,  # Skip SFTP upload (run locally from desktop app with VPN)
        on_progress: Optional[Callable[[str, int, str], None]] = None,
        sftp_download_config: Optional[Dict] = None  # SFTP download credentials from desktop app
    ) -> PipelineResult:
        """
        Run the complete data processing pipeline.

        Args:
            environment: 'test' or 'production' - determines target database
            test_mode: If True, run stored procedure with test SSN filter
            source_prefix: S3 prefix where source files are located
            skip_download: If True, skip SFTP download (use existing S3 files)
            skip_procedure: If True, skip stored procedure step (to run locally)
            skip_sftp_upload: If True, skip SFTP upload step (to run locally with VPN access)
            on_progress: Callback function(step_name, percent, message)
            sftp_download_config: Dict with SFTP credentials {host, port, user, password, folder}

        Returns:
            PipelineResult with all execution details
        """
        pipeline_id = datetime.now().strftime('%Y%m%d_%H%M%S')
        folder_name = self._create_timestamped_folder()

        result = PipelineResult(
            pipeline_id=pipeline_id,
            folder_name=folder_name,
            status='running',
            current_step=PipelineStep.VPN_CHECK.value,
            total_steps=len(self.STEP_WEIGHTS),
            completed_steps=0,
            started_at=datetime.now().isoformat()
        )

        # Calculate total weight for progress
        total_weight = sum(self.STEP_WEIGHTS.values())
        completed_weight = 0

        def update_progress(step: PipelineStep, message: str):
            nonlocal completed_weight
            result.current_step = step.value
            if on_progress:
                pct = int((completed_weight / total_weight) * 100)
                on_progress(step.value, pct, message)

        def complete_step(step: PipelineStep, step_result: StepResult):
            nonlocal completed_weight
            result.steps.append(step_result)
            if step_result.success:
                result.completed_steps += 1
                completed_weight += self.STEP_WEIGHTS.get(step, 0)

        # Determine database based on environment
        database_override = None
        if environment == 'production':
            database_override = 'Hacienda_ERP'
        elif environment == 'intf':
            database_override = 'Hacienda_ERP_INTF'
        # else: uses default from connection string (Hacienda_ERP_Test)

        folders = {}

        try:
            # Step 1: VPN Check - Skipped (running on VPC)
            # VPN check is not needed when running on VPC - network connectivity
            # to SFTP server is handled by VPC peering/routes
            update_progress(PipelineStep.VPN_CHECK, "VPC environment - skipping VPN check...")
            complete_step(PipelineStep.VPN_CHECK, StepResult(
                step=PipelineStep.VPN_CHECK.value,
                success=True,
                message="Running on VPC - network connectivity assumed",
                started_at=datetime.now().isoformat(),
                completed_at=datetime.now().isoformat()
            ))

            # Step 2: SFTP Download
            update_progress(PipelineStep.SFTP_DOWNLOAD, "Downloading files from SFTP...")
            started = datetime.now().isoformat()

            if skip_download:
                complete_step(PipelineStep.SFTP_DOWNLOAD, StepResult(
                    step=PipelineStep.SFTP_DOWNLOAD.value,
                    success=True,
                    message="SFTP download skipped - using existing S3 files",
                    started_at=started,
                    completed_at=datetime.now().isoformat()
                ))
            else:
                # Perform actual SFTP download from Sterling server
                try:
                    # Get SFTP credentials from config (passed from desktop app) or environment
                    sftp_config = sftp_download_config or {}
                    sftp_host = sftp_config.get('host') or os.environ.get('SFTP_DOWNLOAD_HOST', '10.3.3.146')
                    sftp_port = int(sftp_config.get('port') or os.environ.get('SFTP_DOWNLOAD_PORT', 22))
                    sftp_user = sftp_config.get('user') or os.environ.get('SFTP_DOWNLOAD_USER', 'gprerpusr')
                    sftp_password = sftp_config.get('password') or os.environ.get('SFTP_DOWNLOAD_PASSWORD')
                    sftp_secret = os.environ.get('SFTP_DOWNLOAD_SECRET')  # AWS secret (fallback)
                    remote_folder = sftp_config.get('folder') or os.environ.get('SFTP_DOWNLOAD_FOLDER', '/OCI/HCM/OUTPUT/')

                    downloader = SftpDownloader(
                        bucket=self.bucket,
                        sftp_secret_name=sftp_secret if not sftp_password else None,
                        sftp_host=sftp_host,
                        sftp_port=sftp_port,
                        sftp_user=sftp_user,
                        sftp_password=sftp_password,
                        remote_folder=remote_folder
                    )

                    # Download all files to the source prefix
                    download_result = downloader.download_all(
                        s3_prefix=source_prefix,
                        progress_callback=lambda done, total, fname:
                            update_progress(PipelineStep.SFTP_DOWNLOAD,
                                f"Downloading {done+1}/{total}: {fname}")
                    )

                    if not download_result.get('success'):
                        complete_step(PipelineStep.SFTP_DOWNLOAD, StepResult(
                            step=PipelineStep.SFTP_DOWNLOAD.value,
                            success=False,
                            message=f"SFTP download failed: {download_result.get('error', 'Unknown error')}",
                            details=download_result,
                            started_at=started,
                            completed_at=datetime.now().isoformat()
                        ))

                        result.status = 'failed'
                        result.error = f"SFTP download failed: {download_result.get('error', 'Unknown error')}"
                        result.completed_at = datetime.now().isoformat()
                        return result

                    complete_step(PipelineStep.SFTP_DOWNLOAD, StepResult(
                        step=PipelineStep.SFTP_DOWNLOAD.value,
                        success=True,
                        message=f"Downloaded {download_result.get('files_downloaded', 0)} files from SFTP ({download_result.get('total_bytes', 0):,} bytes)",
                        details=download_result,
                        started_at=started,
                        completed_at=datetime.now().isoformat()
                    ))

                except Exception as download_error:
                    complete_step(PipelineStep.SFTP_DOWNLOAD, StepResult(
                        step=PipelineStep.SFTP_DOWNLOAD.value,
                        success=False,
                        message=f"SFTP download failed: {str(download_error)}",
                        details={'error': str(download_error)},
                        started_at=started,
                        completed_at=datetime.now().isoformat()
                    ))

                    result.status = 'failed'
                    result.error = f"SFTP download failed: {str(download_error)}"
                    result.completed_at = datetime.now().isoformat()
                    return result

            # Step 3: Create folder structure
            update_progress(PipelineStep.CREATE_FOLDERS, "Creating folder structure...")
            folders = self._create_s3_folders(folder_name)

            # Copy source files to initial folder
            files_copied = self._copy_files_to_folder(
                source_prefix,
                folders['initial']
            )

            complete_step(PipelineStep.CREATE_FOLDERS, StepResult(
                step=PipelineStep.CREATE_FOLDERS.value,
                success=True,
                message=f"Created folder structure: {folder_name}",
                details={
                    'folder_name': folder_name,
                    'files_copied': len(files_copied),
                    'folders': folders
                },
                started_at=datetime.now().isoformat(),
                completed_at=datetime.now().isoformat()
            ))

            # Get list of files to process
            files = self._list_initial_files(folders['initial'])

            if not files:
                result.status = 'failed'
                result.error = "No CSV files found in source location"
                result.completed_at = datetime.now().isoformat()
                return result

            # Step 4: Duplicate Check
            # Note: RHUM files are excluded from duplicate check because they need
            # to process older files first, then run again with new files to update the database
            update_progress(PipelineStep.DUPLICATE_CHECK, "Checking for duplicate files...")
            started = datetime.now().isoformat()

            dup_result = detect_and_move_duplicates(
                prefix=folders['initial'],
                bucket=self.bucket,
                auto_move=True,
                exclude_entities=['RHUM']  # RHUM files process older files first
            )

            # Refresh file list after duplicates moved
            files = self._list_initial_files(folders['initial'])

            # Calculate totals for message
            exact_dups = dup_result.get('total_exact_duplicates', 0)
            superseded = dup_result.get('total_superseded', 0)
            total_moved = dup_result.get('total_moved', exact_dups + superseded)

            complete_step(PipelineStep.DUPLICATE_CHECK, StepResult(
                step=PipelineStep.DUPLICATE_CHECK.value,
                success=True,
                message=f"Duplicate check complete - {total_moved} files moved ({exact_dups} exact duplicates, {superseded} superseded)",
                details=dup_result,
                started_at=started,
                completed_at=datetime.now().isoformat()
            ))

            # Step 5: Name Validation
            update_progress(PipelineStep.NAME_VALIDATION, "Validating file names...")
            started = datetime.now().isoformat()

            # validate_file_names returns dict with 'results' list of ValidationResult objects
            file_names_list = [f['filename'] for f in files]
            name_validation = validate_file_names(file_names_list)
            name_results = name_validation.get('results', [])

            invalid_names = [r for r in name_results if not r.is_valid]

            # Separate valid and invalid files
            valid_names = [r for r in name_results if r.is_valid]
            invalid_file_details = []
            invalid_filenames = set()
            report_key = None

            if invalid_names:
                # Generate detailed report content for person who needs to fix files
                report_lines = [
                    "=" * 80,
                    "FILE NAME VALIDATION REPORT",
                    "=" * 80,
                    "",
                    "SUMMARY",
                    "-" * 40,
                    f"Total files checked: {name_validation.get('total_files', 0)}",
                    f"Valid files: {name_validation.get('valid_count', 0)}",
                    f"Invalid files: {name_validation.get('invalid_count', 0)}",
                    f"Auto-correctable: {name_validation.get('correctable_count', 0)}",
                    "",
                    "NOTE: Pipeline will continue processing VALID files.",
                    "Invalid files have been moved to the InvalidFiles folder.",
                    "",
                    "EXPECTED FILE NAME FORMAT",
                    "-" * 40,
                    "Pattern: HCM_{SOURCE}_INTF_{ENTITY}_{DATE}.csv",
                    "",
                    f"Valid SOURCES: {', '.join(name_validation.get('valid_sources', []))}",
                    f"Valid ENTITIES: {', '.join(name_validation.get('valid_entities', []))}",
                    "Valid DATE formats: YYYYMMDD, YYYYMMDDHHMM, YYYYMMDDHHMMSS",
                    "",
                    "=" * 80,
                    "INVALID FILES - ACTION REQUIRED",
                    "=" * 80,
                    ""
                ]

                # Build detailed list of invalid files for report
                for i, r in enumerate(invalid_names, 1):
                    report_lines.append(f"[{i}] FILE: {r.file_name}")
                    report_lines.append(f"    ERROR: {r.error_message or 'Unknown error'}")
                    if r.source:
                        report_lines.append(f"    Detected Source: {r.source}")
                    if r.entity:
                        report_lines.append(f"    Detected Entity: {r.entity}")
                    if r.date_str:
                        report_lines.append(f"    Detected Date: {r.date_str}")
                    if r.suggested_correction:
                        report_lines.append(f"    SUGGESTED FIX: Rename to -> {r.suggested_correction}")
                    report_lines.append("")

                    # Build dict for JSON serialization
                    invalid_file_details.append({
                        'file_name': r.file_name,
                        'error_message': r.error_message,
                        'detected_source': r.source,
                        'detected_entity': r.entity,
                        'detected_date': r.date_str,
                        'suggested_correction': r.suggested_correction,
                        'similarity_score': r.similarity_score
                    })
                    invalid_filenames.add(r.file_name)

                report_lines.append("=" * 80)
                report_lines.append("END OF VALIDATION REPORT")
                report_lines.append("=" * 80)
                report_content = "\n".join(report_lines)

                report_key = self._upload_report(
                    folders['validation'],
                    f'name_validation_{pipeline_id}.txt',
                    report_content
                )

                # Move invalid files to a separate folder (don't stop the pipeline)
                invalid_folder = f"{folder_name}/4_InvalidFiles/"
                moved_invalid = 0
                for f in files:
                    if f['filename'] in invalid_filenames:
                        try:
                            # Move to invalid folder
                            new_key = f"{invalid_folder}{f['filename']}"
                            self.s3_client.copy_object(
                                Bucket=self.bucket,
                                CopySource={'Bucket': self.bucket, 'Key': f['s3_key']},
                                Key=new_key
                            )
                            self.s3_client.delete_object(Bucket=self.bucket, Key=f['s3_key'])
                            moved_invalid += 1
                        except Exception as move_err:
                            pass  # Continue even if move fails

                # Filter out invalid files from the processing list
                files = [f for f in files if f['filename'] not in invalid_filenames]

            # Determine success status - partial success if some files are invalid but we have valid files
            if invalid_names and valid_names:
                # Partial success - some invalid, some valid
                complete_step(PipelineStep.NAME_VALIDATION, StepResult(
                    step=PipelineStep.NAME_VALIDATION.value,
                    success=True,  # Mark as success so pipeline continues
                    message=f"Name validation: {len(valid_names)} valid, {len(invalid_names)} invalid (moved aside, continuing with valid files)",
                    details={
                        'total_files': len(name_results),
                        'valid_files': len(valid_names),
                        'invalid_files': len(invalid_names),
                        'correctable_files': name_validation.get('correctable_count', 0),
                        'valid_sources': name_validation.get('valid_sources', []),
                        'valid_entities': name_validation.get('valid_entities', []),
                        'invalid_file_details': invalid_file_details,
                        'invalid_files_moved': moved_invalid if invalid_names else 0,
                        'continuing_with_valid': True
                    },
                    report_key=report_key,
                    started_at=started,
                    completed_at=datetime.now().isoformat()
                ))
            elif invalid_names and not valid_names:
                # All files invalid - must stop
                complete_step(PipelineStep.NAME_VALIDATION, StepResult(
                    step=PipelineStep.NAME_VALIDATION.value,
                    success=False,
                    message=f"Name validation failed - ALL {len(invalid_names)} files have invalid names",
                    details={
                        'total_files': len(name_results),
                        'valid_files': 0,
                        'invalid_files': len(invalid_names),
                        'correctable_files': name_validation.get('correctable_count', 0),
                        'valid_sources': name_validation.get('valid_sources', []),
                        'valid_entities': name_validation.get('valid_entities', []),
                        'invalid_file_details': invalid_file_details
                    },
                    report_key=report_key,
                    started_at=started,
                    completed_at=datetime.now().isoformat()
                ))

                # Stop pipeline only if ALL files are invalid
                result.status = 'failed'
                result.error = f"Name validation failed - ALL files have invalid names"
                result.report_url = self._generate_presigned_url(report_key) if report_key else None
                result.completed_at = datetime.now().isoformat()
                return result
            else:
                # All files valid
                complete_step(PipelineStep.NAME_VALIDATION, StepResult(
                    step=PipelineStep.NAME_VALIDATION.value,
                    success=True,
                    message=f"All {len(name_results)} file names are valid",
                    details={'total_files': len(name_results)},
                    started_at=started,
                    completed_at=datetime.now().isoformat()
                ))

            # Step 6: Column Schema Validation
            update_progress(PipelineStep.SCHEMA_VALIDATION, "Validating column schemas...")
            started = datetime.now().isoformat()

            schema_report = validate_files_schema(
                files=[{'filename': f['filename'], 's3_key': f['s3_key']} for f in files],
                s3_client=self.s3_client,
                bucket=self.bucket
            )

            schema_report_key = None
            if schema_report.has_errors:
                # Generate and upload report
                report_content = generate_schema_validation_report(schema_report)
                schema_report_key = self._upload_report(
                    folders['validation'],
                    f'schema_validation_{pipeline_id}.txt',
                    report_content
                )

                # Get list of invalid file names from schema report
                invalid_schema_files = set()
                if hasattr(schema_report, 'file_results'):
                    for fr in schema_report.file_results:
                        if hasattr(fr, 'is_valid') and not fr.is_valid:
                            invalid_schema_files.add(fr.filename if hasattr(fr, 'filename') else '')

                # Check if we have valid files to continue with
                if schema_report.valid_files > 0:
                    # Move invalid schema files aside, continue with valid ones
                    invalid_folder = f"{folder_name}/5_InvalidSchema/"
                    moved_invalid = 0
                    for f in files:
                        if f['filename'] in invalid_schema_files:
                            try:
                                new_key = f"{invalid_folder}{f['filename']}"
                                self.s3_client.copy_object(
                                    Bucket=self.bucket,
                                    CopySource={'Bucket': self.bucket, 'Key': f['s3_key']},
                                    Key=new_key
                                )
                                self.s3_client.delete_object(Bucket=self.bucket, Key=f['s3_key'])
                                moved_invalid += 1
                            except Exception:
                                pass

                    # Filter out invalid files
                    files = [f for f in files if f['filename'] not in invalid_schema_files]

                    complete_step(PipelineStep.SCHEMA_VALIDATION, StepResult(
                        step=PipelineStep.SCHEMA_VALIDATION.value,
                        success=True,  # Mark success to continue pipeline
                        message=f"Schema validation: {schema_report.valid_files} valid, {schema_report.invalid_files} invalid (moved aside, continuing)",
                        details={
                            'total_files': schema_report.total_files,
                            'valid_files': schema_report.valid_files,
                            'invalid_files': schema_report.invalid_files,
                            'continuing_with_valid': True
                        },
                        report_key=schema_report_key,
                        started_at=started,
                        completed_at=datetime.now().isoformat()
                    ))
                else:
                    # All files have schema errors - must stop
                    complete_step(PipelineStep.SCHEMA_VALIDATION, StepResult(
                        step=PipelineStep.SCHEMA_VALIDATION.value,
                        success=False,
                        message=f"Schema validation failed - ALL {schema_report.invalid_files} files have invalid columns",
                        details={
                            'total_files': schema_report.total_files,
                            'valid_files': schema_report.valid_files,
                            'invalid_files': schema_report.invalid_files
                        },
                        report_key=schema_report_key,
                        started_at=started,
                        completed_at=datetime.now().isoformat()
                    ))

                    result.status = 'failed'
                    result.error = f"Schema validation failed - ALL files have invalid columns"
                    result.report_url = self._generate_presigned_url(schema_report_key)
                    result.completed_at = datetime.now().isoformat()
                    return result
            else:
                complete_step(PipelineStep.SCHEMA_VALIDATION, StepResult(
                    step=PipelineStep.SCHEMA_VALIDATION.value,
                    success=True,
                    message=f"All {schema_report.total_files} file schemas are valid",
                    details={
                        'total_files': schema_report.total_files,
                        'valid_files': schema_report.valid_files
                    },
                    started_at=started,
                    completed_at=datetime.now().isoformat()
                ))

            # Step 7: Completeness Check
            # PARTIAL PROCESSING: Complete entities continue, incomplete entities are reported
            update_progress(PipelineStep.COMPLETENESS_CHECK, "Checking file completeness...")
            started = datetime.now().isoformat()

            completeness_result = check_file_completeness(
                [f['filename'] for f in files]
            )

            # Track which files to process (only from complete entities)
            files_to_process = files  # Default: all files
            incomplete_entity_report = None

            if completeness_result.incomplete_sets > 0:
                # Generate report for incomplete entities
                report_content = generate_completeness_report(completeness_result)
                report_key = self._upload_report(
                    folders['validation'],
                    f'completeness_{pipeline_id}.txt',
                    report_content
                )
                incomplete_entity_report = report_key

                # Check if we have any complete entities to process
                if completeness_result.complete_entities:
                    # PARTIAL PROCESSING: Process only complete entities
                    complete_filenames, incomplete_filenames = get_files_for_complete_entities(
                        [f['filename'] for f in files],
                        completeness_result
                    )

                    # Filter files to only include those from complete entities
                    complete_filename_set = set(complete_filenames)
                    files_to_process = [f for f in files if f['filename'] in complete_filename_set]

                    complete_step(PipelineStep.COMPLETENESS_CHECK, StepResult(
                        step=PipelineStep.COMPLETENESS_CHECK.value,
                        success=True,  # Partial success - some entities complete
                        message=f"Partial completeness: {len(completeness_result.complete_entities)} complete entities ({', '.join(completeness_result.complete_entities)}), "
                                f"{len(completeness_result.incomplete_entities)} incomplete ({', '.join(completeness_result.incomplete_entities)})",
                        details={
                            'complete_sets': completeness_result.complete_sets,
                            'incomplete_sets': completeness_result.incomplete_sets,
                            'complete_entities': completeness_result.complete_entities,
                            'incomplete_entities': completeness_result.incomplete_entities,
                            'files_to_process': len(files_to_process),
                            'files_skipped': len(files) - len(files_to_process),
                            'partial_processing': True
                        },
                        report_key=report_key,
                        started_at=started,
                        completed_at=datetime.now().isoformat()
                    ))
                else:
                    # No complete entities - fail the pipeline
                    complete_step(PipelineStep.COMPLETENESS_CHECK, StepResult(
                        step=PipelineStep.COMPLETENESS_CHECK.value,
                        success=False,
                        message=f"Completeness check failed - no complete entity sets found. "
                                f"Incomplete entities: {', '.join(completeness_result.incomplete_entities)}",
                        details={
                            'incomplete_sets': completeness_result.incomplete_sets,
                            'complete_sets': 0,
                            'incomplete_entities': completeness_result.incomplete_entities
                        },
                        report_key=report_key,
                        started_at=started,
                        completed_at=datetime.now().isoformat()
                    ))

                    result.status = 'failed'
                    result.error = f"Completeness check failed - no complete entity sets"
                    result.report_url = self._generate_presigned_url(report_key)
                    result.completed_at = datetime.now().isoformat()
                    return result
            else:
                # All entities complete
                complete_step(PipelineStep.COMPLETENESS_CHECK, StepResult(
                    step=PipelineStep.COMPLETENESS_CHECK.value,
                    success=True,
                    message=f"All file sets complete - {completeness_result.complete_sets} sets for {len(completeness_result.complete_entities)} entities",
                    details={
                        'complete_sets': completeness_result.complete_sets,
                        'total_files': completeness_result.total_files,
                        'complete_entities': completeness_result.complete_entities
                    },
                    started_at=started,
                    completed_at=datetime.now().isoformat()
                ))

            # Update files list to only include files from complete entities
            files = files_to_process

            # Step 8: SQL Load
            update_progress(PipelineStep.SQL_LOAD, "Loading data to SQL Server...")
            started = datetime.now().isoformat()

            try:
                loader = SqlServerLoader(
                    bucket=self.bucket,
                    secret_name=self.secret_name,
                    database_override=database_override
                )

                load_results = loader.load_files(
                    files=[{'filename': f['filename'], 's3_key': f['s3_key']} for f in files],
                    drop_existing=True  # Clear existing data before loading new data
                )

                # Generate load report
                load_report_content = self._generate_load_report(load_results)
                report_key = self._upload_report(
                    folders['load'],
                    f'sql_load_{pipeline_id}.txt',
                    load_report_content
                )

                if load_results.get('failed_tables', 0) > 0:
                    complete_step(PipelineStep.SQL_LOAD, StepResult(
                        step=PipelineStep.SQL_LOAD.value,
                        success=False,
                        message=f"SQL load partially failed - {load_results.get('failed_tables', 0)} tables failed",
                        details=load_results,
                        report_key=report_key,
                        started_at=started,
                        completed_at=datetime.now().isoformat()
                    ))

                    result.status = 'failed'
                    result.error = f"SQL load failed for some tables"
                    result.report_url = self._generate_presigned_url(report_key)
                    result.completed_at = datetime.now().isoformat()
                    return result

                complete_step(PipelineStep.SQL_LOAD, StepResult(
                    step=PipelineStep.SQL_LOAD.value,
                    success=True,
                    message=f"Data loaded to SQL Server - {load_results.get('loaded_tables', 0)} tables, {load_results.get('total_rows', 0)} rows",
                    details=load_results,
                    report_key=report_key,
                    started_at=started,
                    completed_at=datetime.now().isoformat()
                ))

            except Exception as load_error:
                complete_step(PipelineStep.SQL_LOAD, StepResult(
                    step=PipelineStep.SQL_LOAD.value,
                    success=False,
                    message=f"SQL load failed: {str(load_error)}",
                    details={'error': str(load_error)},
                    started_at=started,
                    completed_at=datetime.now().isoformat()
                ))

                result.status = 'failed'
                result.error = f"SQL load failed: {str(load_error)}"
                result.completed_at = datetime.now().isoformat()
                return result

            # Step 9: Run Stored Procedure
            update_progress(PipelineStep.RUN_PROCEDURE, "Running HCM_MAIN_INTF stored procedure...")
            started = datetime.now().isoformat()

            if skip_procedure:
                # Skip stored procedure - will be run locally from desktop app
                complete_step(PipelineStep.RUN_PROCEDURE, StepResult(
                    step=PipelineStep.RUN_PROCEDURE.value,
                    success=True,
                    message="Stored procedure skipped - run locally from desktop app",
                    details={'skipped': True, 'reason': 'skip_procedure=True'},
                    started_at=started,
                    completed_at=datetime.now().isoformat()
                ))
            else:
                proc_result = execute_hcm_main_intf(
                    test_execution=test_mode,
                    secret_name=self.secret_name,
                    database_override=database_override
                )

                # Generate procedure report
                proc_report_content = self._generate_procedure_report(proc_result)
                report_key = self._upload_report(
                    folders['post_load'],
                    f'procedure_{pipeline_id}.txt',
                    proc_report_content
                )

                if proc_result.get('status') == 'error':
                    complete_step(PipelineStep.RUN_PROCEDURE, StepResult(
                        step=PipelineStep.RUN_PROCEDURE.value,
                        success=False,
                        message=f"Stored procedure failed: {proc_result.get('error', {}).get('message', 'Unknown error')}",
                        details=proc_result,
                        report_key=report_key,
                        started_at=started,
                        completed_at=datetime.now().isoformat()
                    ))

                    result.status = 'failed'
                    result.error = "Stored procedure execution failed"
                    result.report_url = self._generate_presigned_url(report_key)
                    result.completed_at = datetime.now().isoformat()
                    return result

                complete_step(PipelineStep.RUN_PROCEDURE, StepResult(
                    step=PipelineStep.RUN_PROCEDURE.value,
                    success=True,
                    message=f"Stored procedure completed - {sum(proc_result.get('delta_counts', {}).values())} delta records created",
                    details=proc_result,
                    report_key=report_key,
                    started_at=started,
                    completed_at=datetime.now().isoformat()
                ))

            # Step 10: Export Delta Files
            update_progress(PipelineStep.EXPORT_FILES, "Exporting delta files...")
            started = datetime.now().isoformat()

            # If procedure was skipped, also skip export (both will be done locally)
            if skip_procedure:
                complete_step(PipelineStep.EXPORT_FILES, StepResult(
                    step=PipelineStep.EXPORT_FILES.value,
                    success=True,
                    message="Export skipped - will run locally after stored procedure",
                    details={'skipped': True, 'reason': 'skip_procedure=True', 'export_folder': folders['export']},
                    started_at=started,
                    completed_at=datetime.now().isoformat()
                ))
            else:
                try:
                    exporter = DeltaExporter(
                        bucket=self.bucket,
                        secret_name=self.secret_name,
                        database_override=database_override,
                        output_prefix=folders['export']
                    )

                    export_result = exporter.export_all(update_status=True)

                    # Generate export report
                    export_report_content = self._generate_export_report(export_result)
                    report_key = self._upload_report(
                        folders['export'],
                        f'export_{pipeline_id}.txt',
                        export_report_content
                    )

                    if not export_result.get('success'):
                        complete_step(PipelineStep.EXPORT_FILES, StepResult(
                            step=PipelineStep.EXPORT_FILES.value,
                            success=False,
                            message=f"Export failed: {export_result.get('error', 'Unknown error')}",
                            details=export_result,
                            report_key=report_key,
                            started_at=started,
                            completed_at=datetime.now().isoformat()
                        ))

                        result.status = 'failed'
                        result.error = f"Export failed: {export_result.get('error', 'Unknown error')}"
                        result.report_url = self._generate_presigned_url(report_key)
                        result.completed_at = datetime.now().isoformat()
                        return result

                    complete_step(PipelineStep.EXPORT_FILES, StepResult(
                        step=PipelineStep.EXPORT_FILES.value,
                        success=True,
                        message=f"Exported {export_result.get('total_files', 0)} files with {export_result.get('total_rows', 0)} total rows",
                        details=export_result,
                        report_key=report_key,
                        started_at=started,
                        completed_at=datetime.now().isoformat()
                    ))

                except Exception as export_error:
                    complete_step(PipelineStep.EXPORT_FILES, StepResult(
                        step=PipelineStep.EXPORT_FILES.value,
                        success=False,
                        message=f"Export failed: {str(export_error)}",
                        details={'error': str(export_error)},
                        started_at=started,
                        completed_at=datetime.now().isoformat()
                    ))

                    result.status = 'failed'
                    result.error = f"Export failed: {str(export_error)}"
                    result.completed_at = datetime.now().isoformat()
                    return result

            # Step 11: Upload to Sterling/SFTP
            update_progress(PipelineStep.UPLOAD_STERLING, "Uploading export files to SFTP...")
            started = datetime.now().isoformat()

            # Check if SFTP upload should be skipped (to run locally with VPN)
            if skip_sftp_upload:
                # List export files so desktop app can do the upload locally
                export_files = []
                try:
                    paginator = self.s3_client.get_paginator('list_objects_v2')
                    for page in paginator.paginate(Bucket=self.bucket, Prefix=folders['export']):
                        for obj in page.get('Contents', []):
                            if obj['Key'].lower().endswith('.csv'):
                                export_files.append({
                                    's3_key': obj['Key'],
                                    'filename': obj['Key'].split('/')[-1],
                                    'size': obj['Size']
                                })
                except Exception as e:
                    export_files = []

                complete_step(PipelineStep.UPLOAD_STERLING, StepResult(
                    step=PipelineStep.UPLOAD_STERLING.value,
                    success=True,
                    message=f"SFTP upload skipped - {len(export_files)} files ready for local upload",
                    details={
                        'skipped': True,
                        'reason': 'Local VPN required',
                        'export_folder': folders['export'],
                        'export_files': export_files,
                        'files_pending_upload': len(export_files)
                    },
                    started_at=started,
                    completed_at=datetime.now().isoformat()
                ))
            else:
                try:
                    # Get SFTP credentials from environment or secret
                    sftp_host = os.environ.get('SFTP_UPLOAD_HOST', '10.3.3.146')
                    sftp_port = int(os.environ.get('SFTP_UPLOAD_PORT', 22))
                    sftp_user = os.environ.get('SFTP_UPLOAD_USER', 'gprerpusr')
                    sftp_password = os.environ.get('SFTP_UPLOAD_PASSWORD')
                    sftp_secret = os.environ.get('SFTP_UPLOAD_SECRET')
                    remote_folder = os.environ.get('SFTP_UPLOAD_FOLDER', '/GPR/HCM/INPUT')

                    uploader = SftpUploader(
                        bucket=self.bucket,
                        sftp_secret_name=sftp_secret,
                        sftp_host=sftp_host,
                        sftp_port=sftp_port,
                        sftp_user=sftp_user,
                        sftp_password=sftp_password,
                        remote_folder=remote_folder
                    )

                    # Upload all files from export folder
                    upload_result = uploader.upload_from_prefix(
                        prefix=folders['export'],
                        file_extension='.csv'
                    )

                    # Generate upload report
                    upload_report_content = self._generate_upload_report(upload_result)
                    report_key = self._upload_report(
                        folders['upload'],
                        f'upload_{pipeline_id}.txt',
                        upload_report_content
                    )

                    if not upload_result.get('success'):
                        complete_step(PipelineStep.UPLOAD_STERLING, StepResult(
                            step=PipelineStep.UPLOAD_STERLING.value,
                            success=False,
                            message=f"SFTP upload failed: {upload_result.get('error', 'Unknown error')}",
                            details=upload_result,
                            report_key=report_key,
                            started_at=started,
                            completed_at=datetime.now().isoformat()
                        ))

                        result.status = 'partial'  # Pipeline still partially successful
                        result.error = f"SFTP upload failed: {upload_result.get('error', 'Unknown error')}"
                        result.report_url = self._generate_presigned_url(report_key)
                        result.completed_at = datetime.now().isoformat()
                        return result

                    # Move uploaded files to sent folder in S3
                    if upload_result.get('file_results'):
                        uploaded_keys = [
                            fr['s3_key']
                            for fr in upload_result['file_results']
                            if fr.get('success')
                        ]
                        if uploaded_keys:
                            sent_prefix = f"{folder_name}/7_Sent_Files/"
                            move_result = uploader.move_uploaded_to_sent(uploaded_keys, sent_prefix)
                            upload_result['move_result'] = move_result

                    complete_step(PipelineStep.UPLOAD_STERLING, StepResult(
                        step=PipelineStep.UPLOAD_STERLING.value,
                        success=True,
                        message=f"Uploaded {upload_result.get('files_uploaded', 0)} files to SFTP ({upload_result.get('total_bytes', 0):,} bytes)",
                        details=upload_result,
                        report_key=report_key,
                        started_at=started,
                        completed_at=datetime.now().isoformat()
                    ))

                except Exception as upload_error:
                    complete_step(PipelineStep.UPLOAD_STERLING, StepResult(
                        step=PipelineStep.UPLOAD_STERLING.value,
                        success=False,
                        message=f"SFTP upload failed: {str(upload_error)}",
                        details={'error': str(upload_error)},
                        started_at=started,
                        completed_at=datetime.now().isoformat()
                    ))

                    # Mark as partial success since data processing completed
                    result.status = 'partial'
                    result.error = f"SFTP upload failed: {str(upload_error)}"
                    result.completed_at = datetime.now().isoformat()
                    return result

            # Pipeline complete!
            result.status = 'success'
            result.current_step = PipelineStep.COMPLETE.value
            result.completed_at = datetime.now().isoformat()

            # Generate final summary report
            summary_report = self._generate_summary_report(result)
            summary_key = self._upload_report(
                folders['validation'],
                f'pipeline_summary_{pipeline_id}.txt',
                summary_report
            )
            result.report_url = self._generate_presigned_url(summary_key)

            if on_progress:
                on_progress(PipelineStep.COMPLETE.value, 100, "Pipeline completed successfully!")

            return result

        except Exception as e:
            result.status = 'failed'
            result.error = str(e)
            result.completed_at = datetime.now().isoformat()

            # Try to upload error report
            try:
                error_report = self._generate_error_report(result, str(e))
                if folders.get('validation'):
                    report_key = self._upload_report(
                        folders['validation'],
                        f'pipeline_error_{pipeline_id}.txt',
                        error_report
                    )
                    result.report_url = self._generate_presigned_url(report_key)
            except Exception:
                pass

            return result

    def _generate_load_report(self, load_results: Dict) -> str:
        """Generate a detailed text report for SQL load results."""
        lines = ["=" * 80, "SQL SERVER LOAD REPORT", f"Code Version: {CODE_VERSION}", "=" * 80, ""]

        lines.append(f"Loaded Tables: {load_results.get('loaded_tables', 0)}")
        lines.append(f"Failed Tables: {load_results.get('failed_tables', 0)}")
        lines.append(f"Total Rows: {load_results.get('total_rows', 0):,}")
        lines.append("")

        # Successful tables section
        successful_tables = [t for t in load_results.get('table_results', []) if t.get('success')]
        if successful_tables:
            lines.append("-" * 40)
            lines.append("SUCCESSFUL LOADS:")
            lines.append("-" * 40)
            for table_result in successful_tables:
                lines.append(f"  [OK] {table_result.get('table_name', 'Unknown')}")
                lines.append(f"       Source: {table_result.get('filename', 'Unknown')}")
                lines.append(f"       Rows: {table_result.get('rows_loaded', 0):,}")
                if table_result.get('columns_matched'):
                    lines.append(f"       Columns Matched: {table_result.get('columns_matched', 0)}")
                if table_result.get('columns_skipped'):
                    lines.append(f"       Columns Skipped: {', '.join(table_result.get('columns_skipped', []))}")
                lines.append("")

        # Failed tables section with detailed info
        detailed_failures = load_results.get('detailed_failures', [])
        if detailed_failures:
            lines.append("")
            lines.append("=" * 80)
            lines.append("DETAILED FAILURE ANALYSIS")
            lines.append("=" * 80)

            for failure in detailed_failures:
                lines.append("")
                lines.append(f"TABLE: {failure.get('table_name', 'Unknown')}")
                lines.append(f"FILE:  {failure.get('filename', 'Unknown')}")
                lines.append(f"ERROR TYPE: {failure.get('error_type', 'UNKNOWN')}")
                lines.append("-" * 60)

                error_type = failure.get('error_type', '')

                if error_type == 'TABLE_NOT_FOUND':
                    lines.append("CAUSE: The target table does not exist in the database.")
                    lines.append("")
                    lines.append("RESOLUTION: Create the table in the database before loading.")
                    if failure.get('csv_columns'):
                        lines.append("")
                        lines.append("CSV COLUMNS FOUND:")
                        for col in failure.get('csv_columns', []):
                            lines.append(f"  - {col}")

                elif error_type == 'COLUMN_MISMATCH':
                    lines.append("CAUSE: CSV columns do not match database table columns.")
                    lines.append("")
                    lines.append("COLUMN COMPARISON:")
                    lines.append(f"  Columns Matched: {failure.get('columns_matched', 0)}")
                    lines.append("")

                    if failure.get('missing_in_db'):
                        lines.append("  COLUMNS IN CSV BUT NOT IN DATABASE TABLE:")
                        for col in failure.get('missing_in_db', []):
                            lines.append(f"    - {col}")
                        lines.append("")

                    if failure.get('missing_in_csv'):
                        lines.append("  COLUMNS IN DATABASE TABLE BUT NOT IN CSV:")
                        for col in failure.get('missing_in_csv', []):
                            lines.append(f"    - {col}")
                        lines.append("")

                    lines.append("  CSV COLUMNS (first 20):")
                    csv_cols = failure.get('csv_columns', [])[:20]
                    for col in csv_cols:
                        lines.append(f"    - {col}")
                    if len(failure.get('csv_columns', [])) > 20:
                        lines.append(f"    ... and {len(failure.get('csv_columns', [])) - 20} more")
                    lines.append("")

                    lines.append("  DATABASE COLUMNS (first 20):")
                    db_cols = failure.get('db_columns', [])[:20]
                    for col in db_cols:
                        lines.append(f"    - {col}")
                    if len(failure.get('db_columns', [])) > 20:
                        lines.append(f"    ... and {len(failure.get('db_columns', [])) - 20} more")

                elif error_type == 'DATA_TRUNCATION':
                    lines.append("CAUSE: Data in one or more columns is too long for the database field.")
                    lines.append("")
                    lines.append(f"ERROR DETAILS: {failure.get('error', 'No details available')}")

                elif error_type == 'DATA_CONVERSION':
                    lines.append("CAUSE: Data type conversion failed (e.g., text to number).")
                    lines.append("")
                    lines.append(f"ERROR DETAILS: {failure.get('error', 'No details available')}")

                elif error_type == 'DATA_ERROR':
                    lines.append("CAUSE: Some rows failed to insert due to data issues.")
                    lines.append(f"  Rows in CSV: {failure.get('csv_row_count', 0):,}")
                    lines.append(f"  Rows Loaded: {failure.get('rows_loaded', 0):,}")
                    lines.append("")
                    failed_rows = failure.get('failed_rows', [])
                    if failed_rows:
                        lines.append("  SAMPLE FAILED ROWS:")
                        for fr in failed_rows[:5]:
                            lines.append(f"    Row {fr.get('row_number', '?')}: {fr.get('error', 'Unknown error')}")
                            if fr.get('sample_values'):
                                lines.append(f"      Sample values: {fr.get('sample_values')}")

                elif error_type == 'INVALID_FILENAME':
                    lines.append("CAUSE: Could not determine table name from filename.")
                    lines.append("")
                    lines.append("EXPECTED FORMAT: HCM_*_INTF_ENTITY_YYYYMMDD.csv")
                    lines.append(f"ACTUAL FILENAME: {failure.get('filename', 'Unknown')}")

                else:
                    lines.append(f"ERROR: {failure.get('error', 'No error details available')}")

                lines.append("")

        lines.append("=" * 80)
        return "\n".join(lines)

    def _generate_procedure_report(self, proc_result: Dict) -> str:
        """Generate a text report for stored procedure results."""
        lines = ["=" * 80, "STORED PROCEDURE EXECUTION REPORT", "=" * 80, ""]

        lines.append(f"Status: {proc_result.get('status', 'Unknown')}")
        lines.append(f"Test Mode: {'Yes' if proc_result.get('test_mode') else 'No'}")
        lines.append(f"Database: {proc_result.get('database', 'Unknown')}")
        lines.append(f"Started: {proc_result.get('started_at', 'N/A')}")
        lines.append(f"Completed: {proc_result.get('completed_at', 'N/A')}")
        lines.append("")

        if proc_result.get('error'):
            error = proc_result['error']
            lines.append("-" * 40)
            lines.append("ERROR:")
            lines.append(f"  {error.get('message', str(error))}")
            lines.append("")

        delta_counts = proc_result.get('delta_counts', {})
        if delta_counts:
            lines.append("-" * 40)
            lines.append("DELTA TABLE COUNTS:")
            total = 0
            for table, count in sorted(delta_counts.items()):
                if count >= 0:
                    lines.append(f"  {table}: {count:,}")
                    total += count
            lines.append(f"  TOTAL: {total:,}")
            lines.append("")

        steps = proc_result.get('steps_completed', [])
        if steps:
            lines.append("-" * 40)
            lines.append("EXECUTION STEPS:")
            for step in reversed(steps):  # Chronological order
                lines.append(f"  - {step.get('step', 'Unknown')}")

        lines.append("")
        lines.append("=" * 80)
        return "\n".join(lines)

    def _generate_summary_report(self, result: PipelineResult) -> str:
        """Generate a summary report for the entire pipeline."""
        lines = ["=" * 80, "PIPELINE EXECUTION SUMMARY", "=" * 80, ""]

        lines.append(f"Pipeline ID: {result.pipeline_id}")
        lines.append(f"Folder: {result.folder_name}")
        lines.append(f"Status: {result.status.upper()}")
        lines.append(f"Started: {result.started_at}")
        lines.append(f"Completed: {result.completed_at}")
        lines.append(f"Steps Completed: {result.completed_steps}/{result.total_steps}")
        lines.append("")

        lines.append("-" * 40)
        lines.append("STEP DETAILS:")
        lines.append("-" * 40)

        for step in result.steps:
            status_icon = "OK" if step.success else "FAILED"
            lines.append(f"\n[{status_icon}] {step.step}")
            lines.append(f"    {step.message}")
            if step.report_key:
                lines.append(f"    Report: {step.report_key}")

        if result.error:
            lines.append("")
            lines.append("-" * 40)
            lines.append("ERROR:")
            lines.append(f"  {result.error}")

        lines.append("")
        lines.append("=" * 80)
        return "\n".join(lines)

    def _generate_error_report(self, result: PipelineResult, error_message: str) -> str:
        """Generate an error report when pipeline fails unexpectedly."""
        lines = ["=" * 80, "PIPELINE ERROR REPORT", "=" * 80, ""]

        lines.append(f"Pipeline ID: {result.pipeline_id}")
        lines.append(f"Failed at step: {result.current_step}")
        lines.append(f"Error: {error_message}")
        lines.append("")

        lines.append("-" * 40)
        lines.append("COMPLETED STEPS:")
        lines.append("-" * 40)

        for step in result.steps:
            status_icon = "OK" if step.success else "FAILED"
            lines.append(f"[{status_icon}] {step.step}: {step.message}")

        lines.append("")
        lines.append("=" * 80)
        return "\n".join(lines)

    def _generate_export_report(self, export_result: Dict) -> str:
        """Generate a text report for delta export results."""
        lines = ["=" * 80, "DELTA EXPORT REPORT", "=" * 80, ""]

        lines.append(f"Status: {'SUCCESS' if export_result.get('success') else 'FAILED'}")
        lines.append(f"Instance: {export_result.get('instance', 'N/A')}")
        lines.append(f"Total Files: {export_result.get('total_files', 0)}")
        lines.append(f"Total Rows: {export_result.get('total_rows', 0)}")
        lines.append("")

        if export_result.get('error'):
            lines.append("-" * 40)
            lines.append("ERROR:")
            lines.append(f"  {export_result['error']}")
            lines.append("")

        files_exported = export_result.get('files_exported', [])
        if files_exported:
            lines.append("-" * 40)
            lines.append("EXPORTED FILES:")
            lines.append("-" * 40)
            for file_info in files_exported:
                lines.append(f"  {file_info.get('filename', 'Unknown')}")
                lines.append(f"    Category: {file_info.get('category', 'N/A')}")
                lines.append(f"    Rows: {file_info.get('row_count', 0):,}")
                lines.append(f"    S3 Key: {file_info.get('s3_key', 'N/A')}")
                lines.append("")

        if export_result.get('errors'):
            lines.append("-" * 40)
            lines.append("ERRORS:")
            for error in export_result['errors']:
                lines.append(f"  - {error}")

        lines.append("")
        lines.append("=" * 80)
        return "\n".join(lines)

    def _generate_upload_report(self, upload_result: Dict) -> str:
        """Generate a text report for SFTP upload results."""
        lines = ["=" * 80, "SFTP UPLOAD REPORT", "=" * 80, ""]

        lines.append(f"Status: {'SUCCESS' if upload_result.get('success') else 'FAILED'}")
        lines.append(f"Files Uploaded: {upload_result.get('files_uploaded', 0)}")
        lines.append(f"Files Failed: {upload_result.get('files_failed', 0)}")
        lines.append(f"Total Bytes: {upload_result.get('total_bytes', 0):,}")
        lines.append("")

        if upload_result.get('error'):
            lines.append("-" * 40)
            lines.append("ERROR:")
            lines.append(f"  {upload_result['error']}")
            lines.append("")

        file_results = upload_result.get('file_results', [])
        if file_results:
            lines.append("-" * 40)
            lines.append("FILE UPLOADS:")
            lines.append("-" * 40)
            for fr in file_results:
                status = "OK" if fr.get('success') else "FAILED"
                lines.append(f"  [{status}] {fr.get('filename', 'Unknown')}")
                if fr.get('remote_path'):
                    lines.append(f"        Remote: {fr['remote_path']}")
                if fr.get('size'):
                    lines.append(f"        Size: {fr['size']:,} bytes")
                if fr.get('error'):
                    lines.append(f"        Error: {fr['error']}")
                lines.append("")

        if upload_result.get('move_result'):
            move = upload_result['move_result']
            lines.append("-" * 40)
            lines.append("S3 FILE MOVE (to sent folder):")
            lines.append(f"  Moved: {move.get('moved', 0)}")
            lines.append(f"  Failed: {move.get('failed', 0)}")
            if move.get('errors'):
                for error in move['errors']:
                    lines.append(f"    - {error}")

        if upload_result.get('errors'):
            lines.append("-" * 40)
            lines.append("ERRORS:")
            for error in upload_result['errors']:
                lines.append(f"  - {error}")

        lines.append("")
        lines.append("=" * 80)
        return "\n".join(lines)


# Version identifier for deployment verification
CODE_VERSION = "2.3.0-header-validation"


def _run_diagnostics(body):
    """
    Run diagnostics to check column mappings, CSV structure, and DB columns.
    """
    import pymssql
    from .sql_table_loader import (
        COLUMN_MAPPINGS, EXPECTED_CSV_HEADERS, get_column_mapping, get_expected_headers,
        validate_csv_headers, get_aws_secret, parse_connection_string,
        extract_entity_from_filename, extract_source_from_filename, read_csv_data,
        get_table_columns, SQL_TABLE_LOADER_VERSION
    )

    results = {
        'code_version': CODE_VERSION,
        'sql_table_loader_version': SQL_TABLE_LOADER_VERSION,
        'diagnostics': {}
    }

    entity = body.get('entity', 'HCM_PERSON_ASSIGNMENT_INTF')
    source = body.get('source', 'FIMAS')
    environment = body.get('environment', 'intf')

    # Check expected headers for this entity/source
    expected_headers = get_expected_headers(entity, source)
    results['diagnostics']['expected_headers_defined'] = expected_headers is not None
    results['diagnostics']['expected_headers'] = expected_headers if expected_headers else "NO EXPECTED HEADERS DEFINED"

    # 1. Check what mapping is defined
    mapping_key = (entity, source)
    mapping = get_column_mapping(entity, source)

    results['diagnostics']['mapping_key'] = str(mapping_key)
    results['diagnostics']['mapping_exists'] = mapping is not None
    results['diagnostics']['mapping_content'] = mapping if mapping else "NO MAPPING FOUND"

    # Check if ASSIGNMENT_STATUS_TYPE is in the mapping
    if mapping:
        results['diagnostics']['has_assignment_status_type_mapping'] = 'ASSIGNMENT_STATUS_TYPE' in mapping
        if 'ASSIGNMENT_STATUS_TYPE' in mapping:
            results['diagnostics']['assignment_status_type_maps_to'] = mapping['ASSIGNMENT_STATUS_TYPE']

    # 2. List all available mappings for this entity
    entity_mappings = [k for k in COLUMN_MAPPINGS.keys() if k[0] == entity]
    results['diagnostics']['available_mappings_for_entity'] = [str(k) for k in entity_mappings]

    # 3. Try to connect to database and get table columns
    try:
        secret_name = os.environ.get('SQL_SECRET_NAME', 'Hacienda_ERP_MSSQL_Production')
        connection_string = get_aws_secret(secret_name)
        conn_params = parse_connection_string(connection_string)

        # Override database based on environment
        if environment == 'production':
            conn_params['database'] = 'Hacienda_ERP'
        elif environment == 'intf':
            conn_params['database'] = 'Hacienda_ERP_INTF'

        results['diagnostics']['database'] = conn_params['database']
        results['diagnostics']['server'] = conn_params['server']

        table_name = f"{entity}_{source}"

        with pymssql.connect(
            server=conn_params['server'],
            port=conn_params.get('port', 1433),
            user=conn_params['user'],
            password=conn_params['password'],
            database=conn_params['database'],
            tds_version='7.3'
        ) as conn:
            cursor = conn.cursor()

            # Get table columns
            db_columns = get_table_columns(cursor, table_name)
            results['diagnostics']['db_table'] = table_name
            results['diagnostics']['db_columns'] = db_columns
            results['diagnostics']['db_has_assignment_status_type'] = 'ASSIGNMENT_STATUS_TYPE' in [c.upper() for c in db_columns]

            # Also check if table exists and what schemas have it
            cursor.execute(f"""
                SELECT TABLE_SCHEMA, TABLE_NAME
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_NAME LIKE '%{table_name}%'
            """)
            results['diagnostics']['matching_tables'] = [{'schema': r[0], 'table': r[1]} for r in cursor.fetchall()]

            # Check all schemas for this table
            cursor.execute(f"""
                SELECT TABLE_SCHEMA, COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_NAME = '{table_name}'
                ORDER BY ORDINAL_POSITION
            """)
            results['diagnostics']['columns_all_schemas'] = [{'schema': r[0], 'column': r[1]} for r in cursor.fetchall()]

            # List ALL tables in the database (first 50)
            cursor.execute("""
                SELECT TOP 50 TABLE_SCHEMA, TABLE_NAME
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_TYPE = 'BASE TABLE'
                ORDER BY TABLE_NAME
            """)
            results['diagnostics']['all_tables_sample'] = [{'schema': r[0], 'table': r[1]} for r in cursor.fetchall()]

    except Exception as db_error:
        results['diagnostics']['db_error'] = str(db_error)

    # 4. Try to read CSV headers from S3
    try:
        bucket = os.environ.get('S3_BUCKET', 'hacienda-sftp-downloads')
        s3_client = boto3.client('s3')

        # Find a matching file
        prefix = 'downloads/'
        response = s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix)

        matching_files = []
        for obj in response.get('Contents', []):
            key = obj['Key']
            filename = key.split('/')[-1]
            if entity in filename.upper() and source in filename.upper():
                matching_files.append(key)

        results['diagnostics']['matching_s3_files'] = matching_files[:5]  # First 5

        if matching_files:
            # Read first matching file's headers
            s3_key = matching_files[0]
            headers, rows = read_csv_data(s3_client, bucket, s3_key)
            results['diagnostics']['csv_file'] = s3_key
            results['diagnostics']['csv_headers'] = headers
            results['diagnostics']['csv_row_count'] = len(rows)
            results['diagnostics']['csv_has_assignment_status_type'] = 'ASSIGNMENT_STATUS_TYPE' in [h.upper() for h in headers]

            # Validate CSV headers against expected
            header_validation = validate_csv_headers(headers, entity, source, strict=False)
            results['diagnostics']['header_validation'] = {
                'valid': header_validation['valid'],
                'missing_headers': header_validation.get('missing_headers', []),
                'extra_headers': header_validation.get('extra_headers', []),
                'error': header_validation.get('error')
            }

            # 5. Simulate mapping process
            if mapping and db_columns:
                db_cols_upper = {c.upper(): c for c in db_columns}
                col_indices = []
                skipped_cols = []
                duplicate_check = {}

                for i, csv_col in enumerate(headers):
                    csv_col_clean = csv_col.strip()

                    # Check if this CSV column has a mapping defined
                    if csv_col_clean in mapping:
                        db_col = mapping[csv_col_clean]
                        if db_col.upper() in db_cols_upper:
                            actual_db_col = db_cols_upper[db_col.upper()]
                            if actual_db_col in duplicate_check:
                                results['diagnostics']['DUPLICATE_FOUND'] = {
                                    'db_column': actual_db_col,
                                    'first_csv_col': duplicate_check[actual_db_col],
                                    'second_csv_col': csv_col_clean,
                                    'mapping_type': 'mapped'
                                }
                            else:
                                duplicate_check[actual_db_col] = csv_col_clean
                                col_indices.append({'csv': csv_col_clean, 'db': actual_db_col, 'type': 'mapped'})
                        else:
                            skipped_cols.append(f"{csv_col_clean} (mapped to {db_col} but not in DB)")
                    elif csv_col_clean.upper() in db_cols_upper:
                        # Direct match
                        actual_db_col = db_cols_upper[csv_col_clean.upper()]
                        if actual_db_col in duplicate_check:
                            results['diagnostics']['DUPLICATE_FOUND'] = {
                                'db_column': actual_db_col,
                                'first_csv_col': duplicate_check[actual_db_col],
                                'second_csv_col': csv_col_clean,
                                'mapping_type': 'direct'
                            }
                        else:
                            duplicate_check[actual_db_col] = csv_col_clean
                            col_indices.append({'csv': csv_col_clean, 'db': actual_db_col, 'type': 'direct'})
                    else:
                        skipped_cols.append(csv_col_clean)

                results['diagnostics']['simulated_mapping'] = col_indices
                results['diagnostics']['simulated_skipped'] = skipped_cols
                results['diagnostics']['columns_to_insert'] = len(col_indices)

    except Exception as s3_error:
        results['diagnostics']['s3_error'] = str(s3_error)

    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*'
        },
        'body': json.dumps(results, default=str, indent=2)
    }


def run_full_pipeline_handler(event, context):
    """
    Lambda handler for running the full pipeline.

    POST /full-pipeline
    Body: {
        "environment": "test" or "production",
        "test_mode": true/false,
        "source_prefix": "downloads/" (optional),
        "skip_sftp": true/false (optional, default true),
        "skip_procedure": true/false (optional, default false - run locally from desktop),
        "skip_sftp_upload": true/false (optional, default true - SFTP upload requires local VPN)
    }

    Special diagnostic actions:
    - "action": "diagnose" - Run diagnostics on column mappings and CSV/DB structure
    """
    try:
        # Parse request
        if event.get('body'):
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        else:
            body = event  # Allow direct JSON in event for CLI testing

        # Handle diagnostic action
        if body.get('action') == 'diagnose':
            return _run_diagnostics(body)

        environment = body.get('environment', 'test')
        test_mode = body.get('test_mode', True)
        source_prefix = body.get('source_prefix', 'downloads/')
        skip_sftp = body.get('skip_sftp', True)  # Default to skip SFTP download
        skip_procedure = body.get('skip_procedure', False)  # Default to run procedure
        skip_sftp_upload = body.get('skip_sftp_upload', True)  # Default to skip - requires local VPN

        # SFTP download credentials (passed from desktop app)
        sftp_download_config = {
            'host': body.get('sftp_download_host', os.environ.get('SFTP_DOWNLOAD_HOST', '10.3.3.146')),
            'port': int(body.get('sftp_download_port', os.environ.get('SFTP_DOWNLOAD_PORT', 22))),
            'user': body.get('sftp_download_user', os.environ.get('SFTP_DOWNLOAD_USER', 'gprerpusr')),
            'password': body.get('sftp_download_password', os.environ.get('SFTP_DOWNLOAD_PASSWORD')),
            'folder': body.get('sftp_download_folder', os.environ.get('SFTP_DOWNLOAD_FOLDER', '/OCI/HCM/OUTPUT/'))
        }

        # Get bucket from environment variable
        bucket = os.environ.get('S3_BUCKET', 'hacienda-sftp-downloads')
        secret_name = os.environ.get('SQL_SECRET_NAME', 'Hacienda_ERP_MSSQL_Production')

        # Test S3 connectivity first
        try:
            s3_test = boto3.client('s3')
            s3_test.head_bucket(Bucket=bucket)
        except Exception as s3_error:
            return {
                'statusCode': 500,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({
                    'error': f'S3 connectivity failed: {str(s3_error)}',
                    'bucket': bucket,
                    'hint': 'Lambda may need VPC endpoint for S3 or NAT Gateway'
                })
            }

        # Run pipeline
        orchestrator = FullPipelineOrchestrator(
            bucket=bucket,
            secret_name=secret_name
        )
        result = orchestrator.run_pipeline(
            environment=environment,
            test_mode=test_mode,
            source_prefix=source_prefix,
            skip_download=skip_sftp,
            skip_procedure=skip_procedure,
            skip_sftp_upload=skip_sftp_upload,
            sftp_download_config=sftp_download_config
        )

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps(result.to_dict(), default=str)
        }

    except Exception as e:
        import traceback
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'error': str(e),
                'traceback': traceback.format_exc()
            })
        }


def pipeline_step_handler(event, context):
    """
    Lambda handler for Step Functions pipeline steps.
    This is a multi-purpose handler that routes based on the 'action' parameter.

    Called by Step Functions state machine for various pipeline steps:
    - download: SFTP download from Sterling
    - create_folders: Create timestamped folder structure
    - full_validation: Run duplicate check, name validation, schema validation, completeness
    - load_files: Load validated files to SQL Server
    - generate_report: Generate validation error or success report

    Input format (from Step Functions):
    {
        "action": "download|create_folders|full_validation|load_files|generate_report",
        "s3_bucket": "hacienda-sftp-downloads",
        "folder": "20240115_1030" (optional, for steps after create_folders),
        "sftp_secret": "Sterling_SFTP_Direct_Production",
        "sql_secret": "Hacienda_ERP_MSSQL_Production",
        ...other action-specific params
    }
    """
    try:
        action = event.get('action', 'unknown')
        bucket = event.get('s3_bucket', os.environ.get('S3_BUCKET', 'hacienda-sftp-downloads'))

        result = {
            'action': action,
            'timestamp': datetime.now().isoformat(),
            's3_bucket': bucket
        }

        if action == 'test_connectivity':
            # Test network connectivity
            import socket
            import urllib.request

            tests = {}

            # Test DNS resolution
            try:
                tests['google_dns'] = socket.gethostbyname('google.com')
            except Exception as e:
                tests['google_dns'] = f'Error: {e}'

            # Test HTTPS connection
            try:
                response = urllib.request.urlopen('https://httpbin.org/ip', timeout=10)
                tests['httpbin_ip'] = response.read().decode()
            except Exception as e:
                tests['httpbin_ip'] = f'Error: {e}'

            # Test Sterling SFTP port
            sftp_host = event.get('sftp_host', '64.185.194.33')
            sftp_port = event.get('sftp_port', 22)
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(15)
                result_code = sock.connect_ex((sftp_host, sftp_port))
                if result_code == 0:
                    tests['sterling_sftp'] = f'Port {sftp_port} is OPEN on {sftp_host}'
                else:
                    tests['sterling_sftp'] = f'Port {sftp_port} connection to {sftp_host} failed with error code: {result_code}'
                sock.close()
            except Exception as e:
                tests['sterling_sftp'] = f'Error: {e}'

            result['connectivity_tests'] = tests
            result['success'] = True

        elif action == 'download':
            # SFTP Download from Sterling
            sftp_secret = event.get('sftp_secret', 'Sterling_SFTP_Direct_Production')
            remote_folder = event.get('remote_folder', '/GPR/HCM')

            # Clean up downloads folder before starting new download
            # This prevents old files from previous runs being included
            s3_client = boto3.client('s3')
            try:
                paginator = s3_client.get_paginator('list_objects_v2')
                objects_to_delete = []
                for page in paginator.paginate(Bucket=bucket, Prefix='downloads/'):
                    for obj in page.get('Contents', []):
                        # Only delete files, not folders
                        if not obj['Key'].endswith('/'):
                            objects_to_delete.append({'Key': obj['Key']})

                # Delete in batches of 1000 (S3 limit)
                if objects_to_delete:
                    for i in range(0, len(objects_to_delete), 1000):
                        batch = objects_to_delete[i:i+1000]
                        s3_client.delete_objects(
                            Bucket=bucket,
                            Delete={'Objects': batch}
                        )
                    result['cleaned_files'] = len(objects_to_delete)
            except Exception as e:
                # Log but don't fail if cleanup fails
                result['cleanup_warning'] = f'Failed to clean downloads folder: {str(e)}'

            try:
                from .sftp_downloader import download_from_sftp
            except ImportError:
                from sftp_download.sftp_downloader import download_from_sftp

            download_result = download_from_sftp(
                bucket=bucket,
                sftp_secret_name=sftp_secret,
                remote_folder=remote_folder,
                s3_prefix='downloads/'
            )
            result.update(download_result)

        elif action == 'create_folders':
            # Create timestamped folder and organize files
            source_prefix = event.get('source_prefix', 'downloads/')

            orchestrator = FullPipelineOrchestrator(bucket=bucket)
            folder_result = orchestrator.create_timestamped_folder(source_prefix)

            result['folder_name'] = folder_result.get('folder_name')
            result['files_moved'] = folder_result.get('files_moved', 0)
            result['success'] = folder_result.get('success', False)

        elif action == 'full_validation':
            # Run all validation steps
            folder = event.get('folder')
            if not folder:
                raise ValueError("folder parameter required for full_validation")

            orchestrator = FullPipelineOrchestrator(bucket=bucket)

            # Run validation steps
            validation_result = orchestrator.run_validation_steps(folder)

            result['validation_passed'] = validation_result.get('all_passed', False)
            result['has_critical_errors'] = validation_result.get('has_critical_errors', False)
            result['duplicate_check'] = validation_result.get('duplicate_check', {})
            result['name_validation'] = validation_result.get('name_validation', {})
            result['schema_validation'] = validation_result.get('schema_validation', {})
            result['completeness_check'] = validation_result.get('completeness_check', {})
            result['valid_files'] = validation_result.get('valid_files', [])

        elif action == 'load_files':
            # Load validated files to SQL Server
            folder = event.get('folder')
            sql_secret = event.get('sql_secret', 'Hacienda_ERP_MSSQL_Production')
            database = event.get('database', 'Hacienda_ERP')

            if not folder:
                raise ValueError("folder parameter required for load_files")

            orchestrator = FullPipelineOrchestrator(
                bucket=bucket,
                secret_name=sql_secret
            )

            load_result = orchestrator.load_files_to_sql(folder, database)
            result.update(load_result)

        elif action == 'start_procedure':
            # Start the stored procedure asynchronously
            sql_secret = event.get('sql_secret', 'Hacienda_ERP_MSSQL_Production')
            database = event.get('database', 'Hacienda_ERP')
            procedure = event.get('procedure', 'HCM_MAIN_INTF')
            test_execution = event.get('test_execution', False)

            try:
                # Import stored procedure runner
                try:
                    from .stored_procedure_runner import start_procedure_async, clear_run_status
                except ImportError:
                    from sftp_download.stored_procedure_runner import start_procedure_async, clear_run_status

                # Clear any stuck run status
                clear_run_status(sql_secret, database)

                # Start procedure
                proc_result = start_procedure_async(
                    secret_name=sql_secret,
                    database=database,
                    procedure=procedure,
                    test_mode=test_execution
                )

                result['procedure_started'] = True
                result['procedure'] = procedure
                result['test_execution'] = test_execution
                result['success'] = True
                result.update(proc_result)

            except Exception as e:
                result['error'] = str(e)
                result['success'] = False
                result['procedure_started'] = False

        elif action == 'check_procedure_status':
            # Check stored procedure completion status
            sql_secret = event.get('sql_secret', 'Hacienda_ERP_MSSQL_Production')
            database = event.get('database', 'Hacienda_ERP')

            try:
                # Import stored procedure runner
                try:
                    from .stored_procedure_runner import check_procedure_status
                except ImportError:
                    from sftp_download.stored_procedure_runner import check_procedure_status

                status_result = check_procedure_status(
                    secret_name=sql_secret,
                    database=database
                )

                result['is_completed'] = status_result.get('is_completed', False)
                result['status'] = status_result.get('status', 'unknown')
                result['current_step'] = status_result.get('current_step')
                result['steps_completed'] = status_result.get('steps_completed', [])
                result['delta_counts'] = status_result.get('delta_counts', {})
                result['success'] = True

            except Exception as e:
                result['error'] = str(e)
                result['success'] = False
                result['is_completed'] = False
                result['status'] = 'error'

        elif action == 'export_delta':
            # Export delta files from SQL Server to S3
            sql_secret = event.get('sql_secret', 'Hacienda_ERP_MSSQL_Production')
            database = event.get('database', 'Hacienda_ERP')
            folder = event.get('folder')

            try:
                # Import delta exporter
                try:
                    from .delta_exporter import DeltaExporter
                except ImportError:
                    from sftp_download.delta_exporter import DeltaExporter

                # Set output prefix to folder's delta directory if provided
                output_prefix = f"{folder}/6_Delta_Files/" if folder else 'exports/'

                exporter = DeltaExporter(
                    bucket=bucket,
                    secret_name=sql_secret,
                    database_override=database,
                    output_prefix=output_prefix
                )

                # Export delta files
                export_result = exporter.export_all(update_status=True)

                result.update(export_result)
                result['folder'] = folder
                result['success'] = export_result.get('success', False)

            except Exception as e:
                import traceback
                result['error'] = str(e)
                result['traceback'] = traceback.format_exc()
                result['success'] = False

        elif action == 'generate_report':
            # Generate report (validation error, procedure error, or success)
            report_type = event.get('report_type', 'unknown')
            folder = event.get('folder')
            s3_bucket = event.get('s3_bucket', os.environ.get('S3_BUCKET', 'hacienda-sftp-downloads'))

            result['report_type'] = report_type
            result['folder'] = folder
            result['success'] = True
            result['message'] = f"Report type: {report_type}"

            # Generate and write the report to S3
            s3_client = boto3.client('s3')
            report_lines = []
            report_lines.append(f"{'='*60}")
            report_lines.append(f"Pipeline Report - {report_type.upper().replace('_', ' ')}")
            report_lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            report_lines.append(f"Folder: {folder}")
            report_lines.append(f"{'='*60}")
            report_lines.append("")

            # Include relevant data based on report type
            if report_type == 'validation_error':
                validation_result = event.get('validation_result', {})
                result['validation_result'] = validation_result

                report_lines.append("VALIDATION FAILED")
                report_lines.append("-" * 40)

                # Duplicate check
                dup = validation_result.get('duplicate_check', {})
                report_lines.append(f"\n1. Duplicate Check: {'PASS' if dup.get('success') else 'FAIL'}")
                report_lines.append(f"   Duplicates found: {dup.get('duplicates_found', 0)}")
                report_lines.append(f"   Files remaining: {dup.get('files_remaining', 0)}")

                # Name validation
                name = validation_result.get('name_validation', {})
                report_lines.append(f"\n2. Name Validation: {'PASS' if name.get('success') else 'FAIL'}")
                report_lines.append(f"   Files checked: {name.get('files_checked', 0)}")
                report_lines.append(f"   Invalid count: {name.get('invalid_count', 0)}")
                if name.get('invalid_files'):
                    report_lines.append("   Invalid files:")
                    for f in name.get('invalid_files', []):
                        report_lines.append(f"     - {f.get('file_name')}: {f.get('error')}")

                # Schema validation
                schema = validation_result.get('schema_validation', {})
                report_lines.append(f"\n3. Schema Validation: {'PASS' if schema.get('success') else 'FAIL'}")
                report_lines.append(f"   Files checked: {schema.get('files_checked', 0)}")
                report_lines.append(f"   Invalid count: {schema.get('invalid_count', 0)}")
                if schema.get('invalid_files'):
                    report_lines.append("   Invalid files:")
                    for f in schema.get('invalid_files', []):
                        report_lines.append(f"     - {f.get('file_name')}: {f.get('error')}")

                # Completeness check
                comp = validation_result.get('completeness_check', {})
                report_lines.append(f"\n4. Completeness Check: {'PASS' if comp.get('success') else 'FAIL'}")
                report_lines.append(f"   Complete sets: {comp.get('complete_sets', 0)}")
                report_lines.append(f"   Incomplete sets: {comp.get('incomplete_sets', 0)}")
                report_lines.append(f"   Complete entities: {', '.join(comp.get('entities_complete', []))}")
                report_lines.append(f"   Incomplete entities: {', '.join(comp.get('entities_incomplete', []))}")

            elif report_type == 'procedure_error':
                proc_status = event.get('proc_status', {})
                result['proc_status'] = proc_status

                report_lines.append("STORED PROCEDURE FAILED")
                report_lines.append("-" * 40)
                report_lines.append(f"Status: {proc_status.get('status', 'Unknown')}")
                report_lines.append(f"Error: {proc_status.get('error', 'Unknown error')}")
                if proc_status.get('completed_steps'):
                    report_lines.append(f"Completed steps: {', '.join(proc_status.get('completed_steps', []))}")

            elif report_type == 'pipeline_success':
                result['sftp_download'] = event.get('sftp_download', {})
                result['validation'] = event.get('validation', {})
                result['sql_load'] = event.get('sql_load', {})
                result['proc_status'] = event.get('proc_status', {})
                result['export'] = event.get('export', {})

                report_lines.append("PIPELINE COMPLETED SUCCESSFULLY")
                report_lines.append("-" * 40)

            # Write report to S3 - use the standard folder structure
            report_content = "\n".join(report_lines)
            report_key = f"{folder}/2_Validation_Reports/Validation Report.txt"

            try:
                s3_client.put_object(
                    Bucket=s3_bucket,
                    Key=report_key,
                    Body=report_content.encode('utf-8'),
                    ContentType='text/plain'
                )
                result['report_key'] = report_key
                result['report_written'] = True
            except Exception as e:
                result['report_write_error'] = str(e)
                result['report_written'] = False

        elif action in ['validation_error', 'procedure_error', 'pipeline_success']:
            # Legacy report action handling
            result['report_type'] = action
            result['success'] = True
            result['message'] = f"Report type: {action}"

        else:
            result['error'] = f"Unknown action: {action}"
            result['success'] = False

        return result

    except Exception as e:
        import traceback
        return {
            'action': event.get('action', 'unknown'),
            'error': str(e),
            'error_type': type(e).__name__,
            'traceback': traceback.format_exc(),
            'success': False
        }


def get_pipeline_folders_handler(event, context):
    """
    Lambda handler to list available pipeline folders.

    GET /pipeline-folders
    """
    try:
        bucket = os.environ.get('S3_BUCKET', 'hacienda-sftp-downloads')
        s3_client = boto3.client('s3')

        # List top-level folders that match timestamped pattern
        folders = []
        paginator = s3_client.get_paginator('list_objects_v2')

        for page in paginator.paginate(Bucket=bucket, Delimiter='/'):
            for prefix in page.get('CommonPrefixes', []):
                folder = prefix['Prefix'].rstrip('/')
                # Check if it's a timestamped folder (YYYYMMDD_HHMM format)
                if len(folder) == 13 and folder[8] == '_':
                    try:
                        datetime.strptime(folder, '%Y%m%d_%H%M')
                        folders.append(folder)
                    except ValueError:
                        pass

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({'folders': sorted(folders, reverse=True)})
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


# ============================================================================
# Step Functions API Handlers
# ============================================================================

STATE_MACHINE_ARN = os.environ.get(
    'STATE_MACHINE_ARN',
    'arn:aws:states:us-east-1:087243890715:stateMachine:hacienda-hcm-pipeline-prod'
)


def start_pipeline_handler(event, context):
    """
    API Gateway handler to start the Step Functions pipeline.

    POST /start-pipeline
    Body: {
        "test_execution": true/false,
        "s3_bucket": "hacienda-sftp-downloads"
    }
    """
    try:
        # Parse request body
        body = {}
        if event.get('body'):
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']

        # Build Step Functions input
        sf_input = {
            's3_bucket': body.get('s3_bucket', 'hacienda-sftp-downloads'),
            'test_execution': body.get('test_execution', False)
        }

        # Start execution
        sfn_client = boto3.client('stepfunctions')

        # Generate execution name with timestamp
        execution_name = f"pipeline-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

        response = sfn_client.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            name=execution_name,
            input=json.dumps(sf_input)
        )

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type,Authorization',
                'Access-Control-Allow-Methods': 'POST,OPTIONS'
            },
            'body': json.dumps({
                'success': True,
                'execution_id': response['executionArn'],
                'execution_name': execution_name,
                'started_at': response['startDate'].isoformat()
            })
        }

    except Exception as e:
        import traceback
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'success': False,
                'error': str(e),
                'traceback': traceback.format_exc()
            })
        }


def get_pipeline_status_handler(event, context):
    """
    API Gateway handler to get Step Functions execution status.

    GET /pipeline-status/{executionId}

    Returns step_details with accumulated state containing all results
    (sftpDownloadResult, validationResult, sqlLoadResult, procStatus, etc.)
    """
    try:
        # Get execution ID from path parameters
        execution_id = None
        if event.get('pathParameters'):
            execution_id = event['pathParameters'].get('executionId')

        if not execution_id:
            return {
                'statusCode': 400,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({'error': 'executionId required'})
            }

        # URL decode if needed
        import urllib.parse
        execution_id = urllib.parse.unquote(execution_id)

        # Convert execution name to full ARN if needed
        if not execution_id.startswith('arn:'):
            # Extract state machine name from STATE_MACHINE_ARN
            sm_arn_parts = STATE_MACHINE_ARN.split(':')
            # Build execution ARN: arn:aws:states:region:account:execution:stateMachineName:executionName
            execution_arn = f"arn:aws:states:{sm_arn_parts[3]}:{sm_arn_parts[4]}:execution:{sm_arn_parts[6]}:{execution_id}"
        else:
            execution_arn = execution_id

        sfn_client = boto3.client('stepfunctions')

        # Get execution details
        execution = sfn_client.describe_execution(executionArn=execution_arn)

        # Get execution history to determine current step
        history = sfn_client.get_execution_history(
            executionArn=execution_arn,
            maxResults=200,
            reverseOrder=False  # Chronological order to get accumulated state
        )

        # Parse current state and step details
        current_step = None
        completed_states = []
        latest_state_data = {}  # Will contain the most recent accumulated state

        for event_item in history['events']:
            event_type = event_item['type']

            if event_type == 'TaskStateEntered':
                state_name = event_item.get('stateEnteredEventDetails', {}).get('name')
                if state_name:
                    current_step = state_name

            elif event_type == 'TaskStateExited':
                state_details = event_item.get('stateExitedEventDetails', {})
                state_name = state_details.get('name')
                output = state_details.get('output')
                if state_name and state_name not in completed_states:
                    completed_states.append(state_name)
                    # Parse the accumulated state output
                    if output:
                        try:
                            latest_state_data = json.loads(output)
                        except:
                            pass

            elif event_type == 'WaitStateEntered':
                state_name = event_item.get('stateEnteredEventDetails', {}).get('name')
                if state_name:
                    current_step = state_name

            elif event_type == 'WaitStateExited':
                state_details = event_item.get('stateExitedEventDetails', {})
                output = state_details.get('output')
                if output:
                    try:
                        latest_state_data = json.loads(output)
                    except:
                        pass

            elif event_type == 'ChoiceStateEntered':
                state_details = event_item.get('stateEnteredEventDetails', {})
                state_name = state_details.get('name')
                if state_name:
                    current_step = state_name
                    # Get input data which contains accumulated state
                    input_str = state_details.get('input')
                    if input_str:
                        try:
                            latest_state_data = json.loads(input_str)
                        except:
                            pass

            elif event_type == 'ExecutionFailed':
                error = event_item.get('executionFailedEventDetails', {})
                latest_state_data['_error'] = error

        # Build response with step_details containing the accumulated state
        # This includes sftpDownloadResult, folderResult, validationResult, etc.
        result = {
            'execution_id': execution_id,
            'status': execution['status'],
            'current_step': current_step,
            'completed_states': completed_states,
            'step_details': latest_state_data,  # Contains all accumulated results
            'started_at': execution['startDate'].isoformat() if execution.get('startDate') else None,
            'stopped_at': execution.get('stopDate').isoformat() if execution.get('stopDate') else None
        }

        # Include output if execution completed
        if execution['status'] in ['SUCCEEDED', 'FAILED', 'ABORTED']:
            if execution.get('output'):
                try:
                    output_data = json.loads(execution['output'])
                    result['output'] = output_data
                    # Use final output as step_details for completed executions
                    result['step_details'] = output_data
                except:
                    result['output'] = execution['output']
            if execution.get('error'):
                result['error'] = execution.get('error')
            if execution.get('cause'):
                result['cause'] = execution.get('cause')

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type,Authorization',
                'Access-Control-Allow-Methods': 'GET,OPTIONS'
            },
            'body': json.dumps(result)
        }

    except Exception as e:
        error_msg = str(e)
        if 'ExecutionDoesNotExist' in error_msg or 'does not exist' in error_msg.lower():
            return {
                'statusCode': 404,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({'error': 'Execution not found'})
            }
        import traceback
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'error': str(e),
                'traceback': traceback.format_exc()
            })
        }


def list_pipeline_executions_handler(event, context):
    """
    API Gateway handler to list recent Step Functions executions.

    GET /pipeline-executions?max_results=10
    """
    try:
        # Get max_results from query parameters
        max_results = 10
        if event.get('queryStringParameters'):
            max_results = int(event['queryStringParameters'].get('max_results', 10))

        sfn_client = boto3.client('stepfunctions')

        # List executions
        response = sfn_client.list_executions(
            stateMachineArn=STATE_MACHINE_ARN,
            maxResults=min(max_results, 100)
        )

        executions = []
        for execution in response['executions']:
            executions.append({
                'execution_id': execution['executionArn'],
                'name': execution['name'],
                'status': execution['status'],
                'started_at': execution['startDate'].isoformat(),
                'stopped_at': execution.get('stopDate').isoformat() if execution.get('stopDate') else None
            })

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type,Authorization',
                'Access-Control-Allow-Methods': 'GET,OPTIONS'
            },
            'body': json.dumps({
                'executions': executions,
                'count': len(executions)
            })
        }

    except Exception as e:
        import traceback
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'error': str(e),
                'traceback': traceback.format_exc()
            })
        }


def stop_pipeline_handler(event, context):
    """
    API Gateway handler to stop a running Step Functions execution.

    POST /stop-pipeline
    Body: { "execution_id": "arn:aws:states:..." }
    """
    try:
        body = {}
        if event.get('body'):
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']

        execution_id = body.get('execution_id')
        if not execution_id:
            return {
                'statusCode': 400,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({'error': 'execution_id required'})
            }

        sfn_client = boto3.client('stepfunctions')

        sfn_client.stop_execution(
            executionArn=execution_id,
            cause='Stopped by user'
        )

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type,Authorization',
                'Access-Control-Allow-Methods': 'POST,OPTIONS'
            },
            'body': json.dumps({
                'success': True,
                'message': 'Execution stopped',
                'execution_id': execution_id
            })
        }

    except Exception as e:
        import traceback
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'error': str(e),
                'traceback': traceback.format_exc()
            })
        }


# ============================================================================
# Unified API Gateway Router
# ============================================================================

def api_router_handler(event, context):
    """
    Unified API Gateway handler that routes requests to the appropriate handler
    based on the request path.

    This is the main entry point for API Gateway requests.
    """
    path = event.get('path', '')
    http_method = event.get('httpMethod', 'GET')

    # Handle CORS preflight
    if http_method == 'OPTIONS':
        return {
            'statusCode': 200,
            'headers': {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type,Authorization',
                'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS'
            },
            'body': ''
        }

    # Route based on path
    if path == '/start-pipeline':
        return start_pipeline_handler(event, context)
    elif path.startswith('/pipeline-status/'):
        return get_pipeline_status_handler(event, context)
    elif path == '/pipeline-executions':
        return list_pipeline_executions_handler(event, context)
    elif path == '/stop-pipeline':
        return stop_pipeline_handler(event, context)
    elif path == '/pipeline-folders':
        return get_pipeline_folders_handler(event, context)
    elif path == '/get-report-url':
        return get_report_url_handler(event, context)
    else:
        # Fall back to the step handler for Step Functions calls
        return pipeline_step_handler(event, context)
