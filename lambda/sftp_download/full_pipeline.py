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
        check_file_completeness,
        generate_completeness_report
    )
    from .name_validator import (
        validate_files as validate_file_names,
        generate_validation_report as generate_name_validation_report
    )
    from .column_schema import (
        validate_files_schema,
        generate_schema_validation_report
    )
    from .sql_loader import SqlServerLoader
    from .stored_procedure_runner import execute_hcm_main_intf
except ImportError:
    from sftp_download.duplicate_detector import detect_and_move_duplicates
    from sftp_download.completeness_checker import (
        check_file_completeness,
        generate_completeness_report
    )
    from sftp_download.name_validator import (
        validate_files as validate_file_names,
        generate_validation_report as generate_name_validation_report
    )
    from sftp_download.column_schema import (
        validate_files_schema,
        generate_schema_validation_report
    )
    from sftp_download.sql_loader import SqlServerLoader
    from sftp_download.stored_procedure_runner import execute_hcm_main_intf


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
        on_progress: Optional[Callable[[str, int, str], None]] = None
    ) -> PipelineResult:
        """
        Run the complete data processing pipeline.

        Args:
            environment: 'test' or 'production' - determines target database
            test_mode: If True, run stored procedure with test SSN filter
            source_prefix: S3 prefix where source files are located
            skip_download: If True, skip SFTP download (use existing S3 files)
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
            # Step 1: VPN Check (optional - can be skipped if using existing files)
            update_progress(PipelineStep.VPN_CHECK, "Checking VPN connectivity...")
            if not skip_download:
                vpn_result = self.check_vpn_connectivity()
                complete_step(PipelineStep.VPN_CHECK, vpn_result)
                if not vpn_result.success:
                    result.status = 'failed'
                    result.error = vpn_result.message
                    result.completed_at = datetime.now().isoformat()
                    return result
            else:
                complete_step(PipelineStep.VPN_CHECK, StepResult(
                    step=PipelineStep.VPN_CHECK.value,
                    success=True,
                    message="VPN check skipped - using existing S3 files",
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
            update_progress(PipelineStep.DUPLICATE_CHECK, "Checking for duplicate files...")
            started = datetime.now().isoformat()

            dup_result = detect_and_move_duplicates(
                prefix=folders['initial'],
                bucket=self.bucket,
                auto_move=True
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

            name_results = validate_file_names(
                files=[{'filename': f['filename'], 's3_key': f['s3_key']} for f in files]
            )

            invalid_names = [r for r in name_results if not r.is_valid]

            if invalid_names:
                # Generate and upload report
                report_content = generate_name_validation_report(name_results)
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
                        'invalid_names': [r.filename for r in invalid_names]
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
            update_progress(PipelineStep.COMPLETENESS_CHECK, "Checking file completeness...")
            started = datetime.now().isoformat()

            completeness_result = check_file_completeness(
                files=[{'filename': f['filename']} for f in files]
            )

            if completeness_result.get('incomplete_count', 0) > 0:
                # Generate and upload report
                report_content = generate_completeness_report(completeness_result)
                report_key = self._upload_report(
                    folders['validation'],
                    f'completeness_{pipeline_id}.txt',
                    report_content
                )

                complete_step(PipelineStep.COMPLETENESS_CHECK, StepResult(
                    step=PipelineStep.COMPLETENESS_CHECK.value,
                    success=False,
                    message=f"Completeness check failed - missing files for {completeness_result.get('incomplete_count', 0)} dates",
                    details=completeness_result,
                    report_key=report_key,
                    started_at=started,
                    completed_at=datetime.now().isoformat()
                ))

                # Stop pipeline on completeness error
                result.status = 'failed'
                result.error = f"Completeness check failed - missing files for some dates"
                result.report_url = self._generate_presigned_url(report_key)
                result.completed_at = datetime.now().isoformat()
                return result

            complete_step(PipelineStep.COMPLETENESS_CHECK, StepResult(
                step=PipelineStep.COMPLETENESS_CHECK.value,
                success=True,
                message=f"File completeness verified - {completeness_result.get('complete_count', 0)} complete sets",
                details=completeness_result,
                started_at=started,
                completed_at=datetime.now().isoformat()
            ))

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

            # Step 10: Export Files (future)
            update_progress(PipelineStep.EXPORT_FILES, "Exporting delta files...")
            complete_step(PipelineStep.EXPORT_FILES, StepResult(
                step=PipelineStep.EXPORT_FILES.value,
                success=True,
                message="Export step not yet implemented",
                started_at=datetime.now().isoformat(),
                completed_at=datetime.now().isoformat()
            ))

            # Step 11: Upload to Sterling (future)
            update_progress(PipelineStep.UPLOAD_STERLING, "Uploading to Sterling...")
            complete_step(PipelineStep.UPLOAD_STERLING, StepResult(
                step=PipelineStep.UPLOAD_STERLING.value,
                success=True,
                message="Sterling upload not yet implemented",
                started_at=datetime.now().isoformat(),
                completed_at=datetime.now().isoformat()
            ))

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
        """Generate a text report for SQL load results."""
        lines = ["=" * 80, "SQL SERVER LOAD REPORT", "=" * 80, ""]

        lines.append(f"Loaded Tables: {load_results.get('loaded_tables', 0)}")
        lines.append(f"Failed Tables: {load_results.get('failed_tables', 0)}")
        lines.append(f"Total Rows: {load_results.get('total_rows', 0)}")
        lines.append("")

        for table_result in load_results.get('tables', []):
            status = "SUCCESS" if table_result.get('success') else "FAILED"
            lines.append(f"{status}: {table_result.get('table_name', 'Unknown')}")
            lines.append(f"  Rows: {table_result.get('row_count', 0)}")
            if table_result.get('error'):
                lines.append(f"  Error: {table_result.get('error')}")
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


def run_full_pipeline_handler(event, context):
    """
    Lambda handler for running the full pipeline.

    POST /full-pipeline
    Body: {
        "environment": "test" or "production",
        "test_mode": true/false,
        "source_prefix": "downloads/" (optional)
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

        # Run pipeline
        orchestrator = FullPipelineOrchestrator()
        result = orchestrator.run_pipeline(
            environment=environment,
            test_mode=test_mode,
            source_prefix=source_prefix
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
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({'error': str(e)})
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
