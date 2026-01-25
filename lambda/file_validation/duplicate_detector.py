"""
Duplicate File Detector for Hacienda SFTP Downloads
Detects and manages duplicate files in S3 bucket.
"""

import hashlib
import boto3
from typing import Dict, List, Set, Tuple, Optional
from dataclasses import dataclass
from collections import defaultdict
from datetime import datetime
import re


@dataclass
class DuplicateGroup:
    """Represents a group of duplicate files."""
    key: str  # Unique identifier for the file content
    files: List[Dict]  # List of {filename, s3_key, size, last_modified, hash}
    recommended_keep: Optional[str] = None  # S3 key of file to keep


@dataclass
class SupersededGroup:
    """Represents files of the same type with different dates."""
    file_type: str  # e.g., "HCM_PERSON_ADDRESS_INTF_FIMAS"
    entity: str  # e.g., "FIMAS"
    files: List[Dict]  # List of files sorted by date (newest first)
    recommended_keep: str  # S3 key of newest file to keep
    superseded_files: List[str]  # S3 keys of older files that could be removed


@dataclass
class DuplicateCheckResult:
    """Result of duplicate detection."""
    total_files: int
    unique_files: int
    duplicate_groups: int
    total_duplicates: int
    storage_waste_bytes: int
    groups: List[DuplicateGroup]
    # New: Superseded files (same type, different dates)
    superseded_groups: List[SupersededGroup] = None
    total_superseded: int = 0


def compute_file_hash(content: bytes) -> str:
    """Compute SHA-256 hash of file content."""
    return hashlib.sha256(content).hexdigest()


def extract_filename_key(filename: str) -> Tuple[str, str]:
    """
    Extract a normalized key from filename (without date/time portion).
    Returns (key, date_portion).
    """
    # Pattern to match HCM files and extract date
    match = re.match(
        r'^(HCM_.+?_INTF_.+?)_(\d{8,14})\.csv$',
        filename,
        re.IGNORECASE
    )
    if match:
        return match.group(1).upper(), match.group(2)

    # Alternative pattern without INTF
    match = re.match(
        r'^(hcm_.+?)_(\d{8,14})\.csv$',
        filename,
        re.IGNORECASE
    )
    if match:
        return match.group(1).upper(), match.group(2)

    return filename.upper(), ""


def find_name_based_duplicates(file_list: List[Dict]) -> Dict[str, List[Dict]]:
    """
    Find duplicates based on file name pattern (same entity/source, different dates).
    Useful for identifying files that might contain the same data from different runs.

    Args:
        file_list: List of dicts with at minimum {filename, s3_key}

    Returns:
        Dictionary mapping filename key to list of matching files
    """
    groups = defaultdict(list)

    for file_info in file_list:
        filename = file_info.get("filename", "")
        key, date = extract_filename_key(filename)
        if key:
            file_info["_date_portion"] = date
            groups[key].append(file_info)

    # Filter to only groups with more than one file
    return {k: v for k, v in groups.items() if len(v) > 1}


def find_size_based_duplicates(file_list: List[Dict]) -> Dict[int, List[Dict]]:
    """
    Find potential duplicates based on file size.
    Files with identical sizes might be duplicates.

    Args:
        file_list: List of dicts with {filename, s3_key, size}

    Returns:
        Dictionary mapping size to list of files with that size
    """
    size_groups = defaultdict(list)

    for file_info in file_list:
        size = file_info.get("size", 0)
        if size > 0:
            size_groups[size].append(file_info)

    # Filter to only sizes with multiple files
    return {k: v for k, v in size_groups.items() if len(v) > 1}


def find_superseded_files(file_list: List[Dict]) -> List[SupersededGroup]:
    """
    Find files that are superseded (same entity/source type but older dates).
    The newest file in each group is recommended to keep.

    Args:
        file_list: List of dicts with {filename, s3_key, size, last_modified}

    Returns:
        List of SupersededGroup objects
    """
    # Group files by their type (entity + source pattern)
    groups = defaultdict(list)

    for file_info in file_list:
        filename = file_info.get("filename", "")
        key, date = extract_filename_key(filename)
        if key and date:
            file_info["_file_type"] = key
            file_info["_date_portion"] = date
            # Extract entity from the key (last part before date)
            parts = key.split('_')
            entity = parts[-1] if len(parts) > 3 else "UNKNOWN"
            file_info["_entity"] = entity
            groups[key].append(file_info)

    # Build superseded groups (only where we have multiple dates)
    superseded_groups = []
    for file_type, files in groups.items():
        if len(files) > 1:
            # Sort by date portion descending (newest first)
            sorted_files = sorted(files, key=lambda x: x.get("_date_portion", ""), reverse=True)

            # The newest file is recommended to keep
            newest = sorted_files[0]
            superseded = [f["s3_key"] for f in sorted_files[1:]]

            superseded_groups.append(SupersededGroup(
                file_type=file_type,
                entity=newest.get("_entity", "UNKNOWN"),
                files=sorted_files,
                recommended_keep=newest["s3_key"],
                superseded_files=superseded
            ))

    return superseded_groups


def find_exact_duplicates_s3(
    bucket_name: str,
    prefix: str = "",
    s3_client=None,
    include_superseded: bool = True
) -> DuplicateCheckResult:
    """
    Find exact duplicates in S3 bucket by computing file hashes.

    Args:
        bucket_name: Name of S3 bucket
        prefix: S3 key prefix to search
        s3_client: Optional boto3 S3 client

    Returns:
        DuplicateCheckResult with detailed duplicate information
    """
    if s3_client is None:
        s3_client = boto3.client('s3')

    # List all files
    files = []
    paginator = s3_client.get_paginator('list_objects_v2')

    for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
        for obj in page.get('Contents', []):
            key = obj['Key']
            if key.endswith('/'):
                continue  # Skip directories

            files.append({
                'filename': key.split('/')[-1],
                's3_key': key,
                'size': obj['Size'],
                'last_modified': obj['LastModified'].isoformat(),
                'etag': obj['ETag'].strip('"')  # ETag is often MD5 hash
            })

    # Group by ETag first (faster than downloading)
    etag_groups = defaultdict(list)
    for file_info in files:
        etag_groups[file_info['etag']].append(file_info)

    # Build duplicate groups
    groups = []
    total_duplicates = 0
    storage_waste = 0

    for etag, group_files in etag_groups.items():
        if len(group_files) > 1:
            # Sort by last_modified, keep the oldest
            sorted_files = sorted(group_files, key=lambda x: x['last_modified'])
            recommended = sorted_files[0]['s3_key']

            groups.append(DuplicateGroup(
                key=etag,
                files=group_files,
                recommended_keep=recommended
            ))

            # Calculate waste (all but one file is duplicate)
            total_duplicates += len(group_files) - 1
            file_size = group_files[0]['size']
            storage_waste += file_size * (len(group_files) - 1)

    # Find superseded files (same type, different dates)
    superseded_groups = []
    total_superseded = 0
    if include_superseded:
        superseded_groups = find_superseded_files(files)
        total_superseded = sum(len(g.superseded_files) for g in superseded_groups)

    return DuplicateCheckResult(
        total_files=len(files),
        unique_files=len(files) - total_duplicates,
        duplicate_groups=len(groups),
        total_duplicates=total_duplicates,
        storage_waste_bytes=storage_waste,
        groups=groups,
        superseded_groups=superseded_groups,
        total_superseded=total_superseded
    )


def check_file_exists_in_s3(
    bucket_name: str,
    filename: str,
    file_size: int,
    prefix: str = "",
    s3_client=None
) -> Optional[str]:
    """
    Check if a file with the same name and size already exists in S3.

    Args:
        bucket_name: Name of S3 bucket
        filename: Name of file to check
        file_size: Size of file in bytes
        prefix: S3 key prefix
        s3_client: Optional boto3 S3 client

    Returns:
        S3 key of existing file if found, None otherwise
    """
    if s3_client is None:
        s3_client = boto3.client('s3')

    # Search for files with the same name
    paginator = s3_client.get_paginator('list_objects_v2')

    for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
        for obj in page.get('Contents', []):
            existing_filename = obj['Key'].split('/')[-1]
            if existing_filename == filename and obj['Size'] == file_size:
                return obj['Key']

    return None


def generate_duplicate_report(result: DuplicateCheckResult) -> str:
    """Generate a human-readable duplicate report."""
    lines = []
    lines.append("=" * 80)
    lines.append("DUPLICATE FILE DETECTION REPORT")
    lines.append("=" * 80)
    lines.append("")
    lines.append(f"Total Files Scanned: {result.total_files}")
    lines.append(f"Unique Files: {result.unique_files}")
    lines.append(f"Duplicate Groups: {result.duplicate_groups}")
    lines.append(f"Total Duplicate Files: {result.total_duplicates}")
    lines.append(f"Storage Waste: {result.storage_waste_bytes:,} bytes ({result.storage_waste_bytes / 1024 / 1024:.2f} MB)")
    lines.append("")

    if result.groups:
        lines.append("DUPLICATE GROUPS:")
        lines.append("-" * 40)

        for i, group in enumerate(result.groups, 1):
            lines.append(f"\nGroup {i} (Hash: {group.key[:16]}...):")
            for file_info in group.files:
                keep_marker = " [KEEP]" if file_info['s3_key'] == group.recommended_keep else " [DUPLICATE]"
                lines.append(f"  - {file_info['filename']}{keep_marker}")
                lines.append(f"    Size: {file_info['size']:,} bytes")
                lines.append(f"    Modified: {file_info['last_modified']}")
                lines.append(f"    S3 Key: {file_info['s3_key']}")

    lines.append("")
    lines.append("=" * 80)

    return "\n".join(lines)


def move_files_to_folder(
    bucket_name: str,
    files_to_move: List[str],
    destination_folder: str = "DuplicateCheck",
    s3_client=None
) -> Dict:
    """
    Move files to a different folder within the same S3 bucket.

    Args:
        bucket_name: Name of S3 bucket
        files_to_move: List of S3 keys to move
        destination_folder: Target folder (default: DuplicateCheck)
        s3_client: Optional boto3 S3 client

    Returns:
        Dictionary with move results
    """
    if s3_client is None:
        s3_client = boto3.client('s3')

    moved = []
    errors = []

    for source_key in files_to_move:
        try:
            # Get just the filename from the key
            filename = source_key.split('/')[-1]
            dest_key = f"{destination_folder}/{filename}"

            # Copy to new location
            s3_client.copy_object(
                Bucket=bucket_name,
                CopySource={'Bucket': bucket_name, 'Key': source_key},
                Key=dest_key
            )

            # Delete original
            s3_client.delete_object(Bucket=bucket_name, Key=source_key)

            moved.append({
                'source': source_key,
                'destination': dest_key
            })
        except Exception as e:
            errors.append({
                'file': source_key,
                'error': str(e)
            })

    return {
        'moved_count': len(moved),
        'moved_files': moved,
        'errors': errors
    }


def move_duplicates_and_superseded(
    bucket_name: str,
    result: DuplicateCheckResult,
    destination_folder: str = "DuplicateCheck",
    s3_client=None
) -> Dict:
    """
    Move duplicate and superseded files to a separate folder.

    Args:
        bucket_name: Name of S3 bucket
        result: DuplicateCheckResult from find_exact_duplicates_s3
        destination_folder: Target folder (default: DuplicateCheck)
        s3_client: Optional boto3 S3 client

    Returns:
        Dictionary with move results and files kept
    """
    if s3_client is None:
        s3_client = boto3.client('s3')

    files_to_move = []
    files_kept = []

    # Collect exact duplicates to move (keep recommended, move others)
    for group in result.groups:
        for file_info in group.files:
            if file_info['s3_key'] == group.recommended_keep:
                files_kept.append(file_info['s3_key'])
            else:
                files_to_move.append(file_info['s3_key'])

    # Collect superseded files to move (keep newest, move older)
    if result.superseded_groups:
        for group in result.superseded_groups:
            files_kept.append(group.recommended_keep)
            files_to_move.extend(group.superseded_files)

    # Remove duplicates from files_to_move (a file might be in both groups)
    files_to_move = list(set(files_to_move))

    # Also remove any files_kept from files_to_move
    files_kept_set = set(files_kept)
    files_to_move = [f for f in files_to_move if f not in files_kept_set]

    # Move the files
    move_result = move_files_to_folder(
        bucket_name,
        files_to_move,
        destination_folder,
        s3_client
    )

    return {
        'exact_duplicates_moved': sum(1 for g in result.groups for f in g.files if f['s3_key'] != g.recommended_keep),
        'superseded_moved': result.total_superseded,
        'total_moved': move_result['moved_count'],
        'files_moved': move_result['moved_files'],
        'files_kept': list(files_kept_set),
        'errors': move_result['errors']
    }


def delete_duplicates(
    bucket_name: str,
    result: DuplicateCheckResult,
    dry_run: bool = True,
    s3_client=None
) -> Dict:
    """
    Delete duplicate files from S3, keeping the recommended file in each group.

    Args:
        bucket_name: Name of S3 bucket
        result: DuplicateCheckResult from find_exact_duplicates_s3
        dry_run: If True, only report what would be deleted
        s3_client: Optional boto3 S3 client

    Returns:
        Dictionary with deletion results
    """
    if s3_client is None:
        s3_client = boto3.client('s3')

    to_delete = []
    for group in result.groups:
        for file_info in group.files:
            if file_info['s3_key'] != group.recommended_keep:
                to_delete.append({
                    'Key': file_info['s3_key']
                })

    if dry_run:
        return {
            "dry_run": True,
            "would_delete": len(to_delete),
            "files": [d['Key'] for d in to_delete],
            "storage_freed_bytes": result.storage_waste_bytes
        }

    # Actually delete
    deleted = []
    errors = []

    if to_delete:
        # S3 delete_objects allows up to 1000 at a time
        for i in range(0, len(to_delete), 1000):
            batch = to_delete[i:i+1000]
            try:
                response = s3_client.delete_objects(
                    Bucket=bucket_name,
                    Delete={'Objects': batch}
                )
                deleted.extend([d['Key'] for d in response.get('Deleted', [])])
                errors.extend(response.get('Errors', []))
            except Exception as e:
                errors.append({"Error": str(e)})

    return {
        "dry_run": False,
        "deleted_count": len(deleted),
        "deleted_files": deleted,
        "errors": errors,
        "storage_freed_bytes": result.storage_waste_bytes
    }


# Lambda handler
def lambda_handler(event, context):
    """
    AWS Lambda handler for duplicate detection.

    Expected event format:
    {
        "action": "check" | "report" | "delete",
        "bucket_name": "s3-bucket-name",
        "prefix": "optional/prefix/",
        "dry_run": true (for delete action)
    }
    """
    action = event.get("action", "check")
    bucket_name = event.get("bucket_name")
    prefix = event.get("prefix", "")

    if not bucket_name:
        return {
            "statusCode": 400,
            "body": {"error": "bucket_name is required"}
        }

    try:
        result = find_exact_duplicates_s3(bucket_name, prefix)

        if action == "check":
            # Return structured data for exact duplicates
            groups_data = []
            for group in result.groups:
                groups_data.append({
                    "key": group.key,
                    "files": group.files,
                    "recommended_keep": group.recommended_keep,
                    "type": "exact_duplicate"
                })

            # Return structured data for superseded files
            superseded_data = []
            if result.superseded_groups:
                for group in result.superseded_groups:
                    # Clean up internal fields before returning
                    clean_files = []
                    for f in group.files:
                        clean_file = {k: v for k, v in f.items() if not k.startswith('_')}
                        clean_file['date'] = f.get('_date_portion', '')
                        clean_files.append(clean_file)

                    superseded_data.append({
                        "file_type": group.file_type,
                        "entity": group.entity,
                        "files": clean_files,
                        "recommended_keep": group.recommended_keep,
                        "superseded_files": group.superseded_files,
                        "type": "superseded"
                    })

            return {
                "statusCode": 200,
                "body": {
                    "total_files": result.total_files,
                    "unique_files": result.unique_files,
                    "duplicate_groups": result.duplicate_groups,
                    "total_duplicates": result.total_duplicates,
                    "storage_waste_bytes": result.storage_waste_bytes,
                    "storage_waste_mb": round(result.storage_waste_bytes / 1024 / 1024, 2),
                    "exact_duplicate_groups": groups_data,
                    "superseded_groups": superseded_data,
                    "total_superseded": result.total_superseded
                }
            }

        elif action == "report":
            report = generate_duplicate_report(result)
            return {
                "statusCode": 200,
                "body": {
                    "report": report,
                    "total_duplicates": result.total_duplicates,
                    "storage_waste_bytes": result.storage_waste_bytes
                }
            }

        elif action == "delete":
            dry_run = event.get("dry_run", True)
            delete_result = delete_duplicates(bucket_name, result, dry_run)
            return {
                "statusCode": 200,
                "body": delete_result
            }

    except Exception as e:
        return {
            "statusCode": 500,
            "body": {"error": str(e)}
        }

    return {
        "statusCode": 400,
        "body": {"error": f"Unknown action: {action}"}
    }


if __name__ == "__main__":
    # Test name-based duplicate detection (doesn't require S3)
    test_files = [
        {"filename": "HCM_PERSON_ADDRESS_INTF_FIMAS_20251126160556.csv", "s3_key": "job1/HCM_PERSON_ADDRESS_INTF_FIMAS_20251126160556.csv", "size": 576150},
        {"filename": "HCM_PERSON_ADDRESS_INTF_FIMAS_20251209121226.csv", "s3_key": "job2/HCM_PERSON_ADDRESS_INTF_FIMAS_20251209121226.csv", "size": 576150},
        {"filename": "HCM_PERSON_ADDRESS_INTF_HAC88_20251205.csv", "s3_key": "job1/HCM_PERSON_ADDRESS_INTF_HAC88_20251205.csv", "size": 3753492},
        {"filename": "HCM_PERSON_ASSIGNMENT_INTF_FIMAS_20251126160540.csv", "s3_key": "job1/HCM_PERSON_ASSIGNMENT_INTF_FIMAS_20251126160540.csv", "size": 3979944},
        {"filename": "HCM_PERSON_ASSIGNMENT_INTF_FIMAS_20251209121210.csv", "s3_key": "job2/HCM_PERSON_ASSIGNMENT_INTF_FIMAS_20251209121210.csv", "size": 3979944},
    ]

    print("=" * 80)
    print("NAME-BASED DUPLICATE DETECTION TEST")
    print("=" * 80)

    name_duplicates = find_name_based_duplicates(test_files)
    for key, files in name_duplicates.items():
        print(f"\n{key}:")
        for f in files:
            print(f"  - {f['filename']} (date: {f.get('_date_portion', 'N/A')})")

    print("\n")
    print("=" * 80)
    print("SIZE-BASED DUPLICATE DETECTION TEST")
    print("=" * 80)

    size_duplicates = find_size_based_duplicates(test_files)
    for size, files in size_duplicates.items():
        print(f"\nSize {size:,} bytes:")
        for f in files:
            print(f"  - {f['filename']}")
