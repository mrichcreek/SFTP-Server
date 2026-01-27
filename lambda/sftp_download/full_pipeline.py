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
from datetime import datetime
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field, asdict
from enum import Enum

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
            secret_name='Hacienda_ERP_Test_MSSQL_text'
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
        'post_load': '4_Post_Load_Reports',
        'export': '5_Export_Files',
        'upload': '6_Upload_Reports'
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
        secret_name: str = 'Hacienda_ERP_Test_MSSQL_text',
        region: str = 'us-east-1'
    ):
        self.bucket = bucket or os.environ.get('S3_BUCKET', 'hacienda-sftp-downloads')
        self.secret_name = secret_name
        self.region = region
        self.s3_client = boto3.client('s3', region_name=region)

    def _create_timestamped_folder(self) -> str:
        """Create a timestamped folder name."""
        return datetime.now().strftime('%Y%m%d_%H%M')

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
        on_progress: Optional[Callable[[str, int, str], None]] = None
    ) -> PipelineResult:
        """
        Run the complete data processing pipeline.

        Args:
            environment: 'test' or 'production' - determines target database
            test_mode: If True, run stored procedure with test SSN filter
            source_prefix: S3 prefix where source files are located
            skip_download: If True, skip SFTP download (use existing S3 files)
            skip_procedure: If True, skip stored procedure step (to run locally)
            on_progress: Callback function(step_name, percent, message)

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
            database_override = 'Hacienda ERP'

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

            # Step 2: SFTP Download (skip for now)
            update_progress(PipelineStep.SFTP_DOWNLOAD, "Downloading files from SFTP...")
            if skip_download:
                complete_step(PipelineStep.SFTP_DOWNLOAD, StepResult(
                    step=PipelineStep.SFTP_DOWNLOAD.value,
                    success=True,
                    message="SFTP download skipped - using existing S3 files",
                    started_at=datetime.now().isoformat(),
                    completed_at=datetime.now().isoformat()
                ))
            else:
                # TODO: Implement actual SFTP download
                complete_step(PipelineStep.SFTP_DOWNLOAD, StepResult(
                    step=PipelineStep.SFTP_DOWNLOAD.value,
                    success=True,
                    message="SFTP download not yet implemented",
                    started_at=datetime.now().isoformat(),
                    completed_at=datetime.now().isoformat()
                ))

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

            complete_step(PipelineStep.DUPLICATE_CHECK, StepResult(
                step=PipelineStep.DUPLICATE_CHECK.value,
                success=True,
                message=f"Duplicate check complete - {dup_result.get('total_exact_duplicates', 0)} duplicates moved",
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

            if invalid_names:
                # Generate report content
                report_lines = ["Name Validation Report", "=" * 50, ""]
                report_lines.append(f"Total files: {name_validation.get('total_files', 0)}")
                report_lines.append(f"Valid: {name_validation.get('valid_count', 0)}")
                report_lines.append(f"Invalid: {name_validation.get('invalid_count', 0)}")
                report_lines.append("")
                report_lines.append("Invalid Files:")
                for r in invalid_names:
                    report_lines.append(f"  - {r.file_name}")
                    if r.error_message:
                        report_lines.append(f"      Error: {r.error_message}")
                    if r.suggested_correction:
                        report_lines.append(f"      Suggested: {r.suggested_correction}")
                report_content = "\n".join(report_lines)

                report_key = self._upload_report(
                    folders['validation'],
                    f'name_validation_{pipeline_id}.txt',
                    report_content
                )

                complete_step(PipelineStep.NAME_VALIDATION, StepResult(
                    step=PipelineStep.NAME_VALIDATION.value,
                    success=False,
                    message=f"Name validation failed - {len(invalid_names)} invalid file names",
                    details={
                        'total_files': len(name_results),
                        'invalid_files': len(invalid_names),
                        'invalid_names': [r.file_name for r in invalid_names]
                    },
                    report_key=report_key,
                    started_at=started,
                    completed_at=datetime.now().isoformat()
                ))

                # Stop pipeline on validation error
                result.status = 'failed'
                result.error = f"Name validation failed - {len(invalid_names)} invalid files"
                result.report_url = self._generate_presigned_url(report_key)
                result.completed_at = datetime.now().isoformat()
                return result

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

            if schema_report.has_errors:
                # Generate and upload report
                report_content = generate_schema_validation_report(schema_report)
                report_key = self._upload_report(
                    folders['validation'],
                    f'schema_validation_{pipeline_id}.txt',
                    report_content
                )

                complete_step(PipelineStep.SCHEMA_VALIDATION, StepResult(
                    step=PipelineStep.SCHEMA_VALIDATION.value,
                    success=False,
                    message=f"Schema validation failed - {schema_report.invalid_files} files have invalid columns",
                    details={
                        'total_files': schema_report.total_files,
                        'valid_files': schema_report.valid_files,
                        'invalid_files': schema_report.invalid_files
                    },
                    report_key=report_key,
                    started_at=started,
                    completed_at=datetime.now().isoformat()
                ))

                # Stop pipeline on validation error
                result.status = 'failed'
                result.error = f"Schema validation failed - {schema_report.invalid_files} files have invalid columns"
                result.report_url = self._generate_presigned_url(report_key)
                result.completed_at = datetime.now().isoformat()
                return result

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
                    drop_existing=False  # Use existing tables
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
        lines = ["=" * 80, "SQL SERVER LOAD REPORT", "=" * 80, ""]

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


def run_full_pipeline_handler(event, context):
    """
    Lambda handler for running the full pipeline.

    POST /full-pipeline
    Body: {
        "environment": "test" or "production",
        "test_mode": true/false,
        "source_prefix": "downloads/" (optional),
        "skip_sftp": true/false (optional, default true),
        "skip_procedure": true/false (optional, default false - run locally from desktop)
    }
    """
    try:
        # Parse request
        if event.get('body'):
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        else:
            body = {}

        environment = body.get('environment', 'test')
        test_mode = body.get('test_mode', True)
        source_prefix = body.get('source_prefix', 'downloads/')
        skip_sftp = body.get('skip_sftp', True)  # Default to skip SFTP download
        skip_procedure = body.get('skip_procedure', False)  # Default to run procedure

        # Get bucket from environment variable
        bucket = os.environ.get('S3_BUCKET', 'hacienda-sftp-downloads')
        secret_name = os.environ.get('SQL_SECRET_NAME', 'Hacienda_ERP_Test_MSSQL_text')

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
            skip_procedure=skip_procedure
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
