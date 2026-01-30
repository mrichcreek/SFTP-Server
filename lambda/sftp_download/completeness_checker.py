"""
Completeness Checker for Hacienda SFTP Downloads
Verifies that all required files are present for each entity/source combination.
"""

from typing import Dict, List, Set, Optional
from dataclasses import dataclass, field
from collections import defaultdict
from datetime import datetime
import re

try:
    # Try relative import first (for package usage)
    from .file_naming_validator import (
        validate_file_name,
        get_file_metadata,
        VALID_SOURCES,
        VALID_ENTITIES,
        ENTITY_ALIASES
    )
except ImportError:
    try:
        # Fall back to absolute import (for Lambda with sftp_download package)
        from sftp_download.name_validator import (
            validate_file_name,
            get_file_metadata,
            VALID_SOURCES,
            VALID_ENTITIES,
            ENTITY_ALIASES
        )
    except ImportError:
        # Fall back to file_validation import
        from file_validation.file_naming_validator import (
            validate_file_name,
            get_file_metadata,
            VALID_SOURCES,
            VALID_ENTITIES,
            ENTITY_ALIASES
        )


@dataclass
class FileSet:
    """Represents a set of files for a specific entity and date."""
    entity: str
    date: str
    files: Dict[str, str] = field(default_factory=dict)  # source -> filename
    missing_sources: List[str] = field(default_factory=list)
    is_complete: bool = False


@dataclass
class CompletenessResult:
    """Result of completeness check."""
    total_files: int
    total_entities_found: int
    complete_sets: int
    incomplete_sets: int
    file_sets: List[FileSet]
    orphan_files: List[str]  # Files that couldn't be grouped
    duplicate_files: Dict[str, List[str]]  # source -> list of duplicate files
    summary: Dict
    # New: per-entity status for partial processing
    complete_entities: List[str] = field(default_factory=list)
    incomplete_entities: List[str] = field(default_factory=list)


# Required sources for a complete file set
# Each entity should have all these source types for a complete upload
REQUIRED_SOURCES = [
    "PERSON",
    "PERSON_NAME",
    "PERSON_ASSIGNMENT",
    "PERSON_ADDRESS",
    "PERSON_NID",
    "PERSON_SUPERVISOR",
    "PERSON_EMAIL",
    "SENIORITY"
]

# Some entities may have different requirements (configurable)
ENTITY_REQUIRED_SOURCES = {
    # Default: all sources required
    "DEFAULT": REQUIRED_SOURCES,
    # Specific entity overrides can be added here
    # "911": ["PERSON", "PERSON_NAME", "PERSON_ASSIGNMENT"],
}


def normalize_entity_name(entity: str) -> str:
    """Normalize entity name using aliases."""
    upper_entity = entity.upper()
    return ENTITY_ALIASES.get(upper_entity, upper_entity)


def extract_date_from_filename(file_name: str) -> Optional[str]:
    """Extract the date portion from a filename (just YYYYMMDD)."""
    # Match various date formats and extract just the date part
    match = re.search(r'(\d{8})', file_name)
    if match:
        return match.group(1)
    return None


def get_required_sources_for_entity(entity: str) -> List[str]:
    """Get the required sources for a specific entity."""
    normalized = normalize_entity_name(entity)
    return ENTITY_REQUIRED_SOURCES.get(normalized, ENTITY_REQUIRED_SOURCES["DEFAULT"])


def group_files_by_entity_and_date(file_names: List[str]) -> Dict[str, Dict[str, Dict[str, str]]]:
    """
    Group files by entity and date.

    Returns:
        {entity: {date: {source: filename}}}
    """
    groups = defaultdict(lambda: defaultdict(dict))
    orphans = []

    for file_name in file_names:
        metadata = get_file_metadata(file_name)
        if metadata:
            entity = normalize_entity_name(metadata["entity"])
            date = extract_date_from_filename(file_name) or metadata["date"][:8]
            source = metadata["source"]

            # Check for duplicates
            if source in groups[entity][date]:
                # Keep the more recent file (longer timestamp = more recent usually)
                existing = groups[entity][date][source]
                if len(file_name) > len(existing):
                    groups[entity][date][source] = file_name
            else:
                groups[entity][date][source] = file_name
        else:
            orphans.append(file_name)

    return dict(groups), orphans


def check_completeness(file_names: List[str]) -> CompletenessResult:
    """
    Check if all required files are present for each entity/date combination.

    Args:
        file_names: List of file names to check

    Returns:
        CompletenessResult with detailed analysis
    """
    groups, orphan_files = group_files_by_entity_and_date(file_names)

    file_sets = []
    complete_sets = 0
    incomplete_sets = 0
    duplicates = defaultdict(list)
    all_entities = set()

    for entity, dates in groups.items():
        all_entities.add(entity)
        required_sources = get_required_sources_for_entity(entity)

        for date, sources in dates.items():
            file_set = FileSet(
                entity=entity,
                date=date,
                files=dict(sources)
            )

            # Check for missing sources
            present_sources = set(sources.keys())
            required_set = set(required_sources)
            missing = required_set - present_sources

            file_set.missing_sources = sorted(list(missing))
            file_set.is_complete = len(missing) == 0

            if file_set.is_complete:
                complete_sets += 1
            else:
                incomplete_sets += 1

            file_sets.append(file_set)

    # Check for duplicate files across different dates for same entity/source
    for entity, dates in groups.items():
        source_files = defaultdict(list)
        for date, sources in dates.items():
            for source, filename in sources.items():
                source_files[f"{entity}_{source}"].append(filename)

        for key, files in source_files.items():
            if len(files) > 1:
                duplicates[key] = files

    # Build summary
    summary = {
        "entities_found": sorted(list(all_entities)),
        "entities_expected": VALID_ENTITIES,
        "missing_entities": sorted(list(set(VALID_ENTITIES) - all_entities)),
        "complete_percentage": (complete_sets / len(file_sets) * 100) if file_sets else 0,
        "by_entity": {}
    }

    for entity in all_entities:
        entity_sets = [fs for fs in file_sets if fs.entity == entity]
        complete_count = sum(1 for fs in entity_sets if fs.is_complete)
        summary["by_entity"][entity] = {
            "total_date_sets": len(entity_sets),
            "complete_sets": complete_count,
            "incomplete_sets": len(entity_sets) - complete_count,
            "dates": sorted([fs.date for fs in entity_sets])
        }

    # Determine complete and incomplete entities
    complete_entities = []
    incomplete_entities = []
    for entity in all_entities:
        entity_sets = [fs for fs in file_sets if fs.entity == entity]
        all_complete = all(fs.is_complete for fs in entity_sets)
        if all_complete and entity_sets:
            complete_entities.append(entity)
        else:
            incomplete_entities.append(entity)

    return CompletenessResult(
        total_files=len(file_names),
        total_entities_found=len(all_entities),
        complete_sets=complete_sets,
        incomplete_sets=incomplete_sets,
        file_sets=file_sets,
        orphan_files=orphan_files,
        duplicate_files=dict(duplicates),
        summary=summary,
        complete_entities=sorted(complete_entities),
        incomplete_entities=sorted(incomplete_entities)
    )


def get_missing_files_report(result: CompletenessResult) -> str:
    """Generate a human-readable report of missing files."""
    lines = []
    lines.append("=" * 80)
    lines.append("FILE COMPLETENESS REPORT")
    lines.append("=" * 80)
    lines.append("")
    lines.append(f"Total Files Analyzed: {result.total_files}")
    lines.append(f"Entities Found: {result.total_entities_found}")
    lines.append(f"Complete Sets: {result.complete_sets}")
    lines.append(f"Incomplete Sets: {result.incomplete_sets}")
    lines.append(f"Completeness: {result.summary['complete_percentage']:.1f}%")
    lines.append("")

    if result.summary['missing_entities']:
        lines.append("MISSING ENTITIES (no files found):")
        for entity in result.summary['missing_entities']:
            lines.append(f"  - {entity}")
        lines.append("")

    if result.incomplete_sets > 0:
        lines.append("INCOMPLETE FILE SETS:")
        lines.append("-" * 40)
        for file_set in result.file_sets:
            if not file_set.is_complete:
                lines.append(f"\n{file_set.entity} - {file_set.date}:")
                lines.append(f"  Present: {len(file_set.files)}/{len(REQUIRED_SOURCES)} sources")
                lines.append(f"  Missing: {', '.join(file_set.missing_sources)}")
                lines.append(f"  Files present:")
                for source, filename in sorted(file_set.files.items()):
                    lines.append(f"    - [{source}] {filename}")

    if result.duplicate_files:
        lines.append("")
        lines.append("DUPLICATE FILES DETECTED:")
        lines.append("-" * 40)
        for key, files in result.duplicate_files.items():
            lines.append(f"\n{key}:")
            for f in files:
                lines.append(f"    - {f}")

    if result.orphan_files:
        lines.append("")
        lines.append("ORPHAN FILES (could not be parsed):")
        lines.append("-" * 40)
        for f in result.orphan_files:
            lines.append(f"  - {f}")

    lines.append("")
    lines.append("=" * 80)

    return "\n".join(lines)


def get_files_for_complete_entities(
    file_names: List[str],
    completeness_result: CompletenessResult
) -> tuple:
    """
    Separate files into those belonging to complete entities and incomplete entities.

    Args:
        file_names: List of all file names
        completeness_result: Result from check_completeness

    Returns:
        Tuple of (complete_entity_files, incomplete_entity_files)
        where each is a list of filenames
    """
    complete_entities = set(completeness_result.complete_entities)
    incomplete_entities = set(completeness_result.incomplete_entities)

    complete_files = []
    incomplete_files = []

    for file_name in file_names:
        metadata = get_file_metadata(file_name)
        if metadata:
            entity = normalize_entity_name(metadata["entity"])
            if entity in complete_entities:
                complete_files.append(file_name)
            elif entity in incomplete_entities:
                incomplete_files.append(file_name)
            else:
                # Unknown entity goes to incomplete
                incomplete_files.append(file_name)
        else:
            # Orphan files go to incomplete
            incomplete_files.append(file_name)

    return complete_files, incomplete_files


def suggest_missing_files(result: CompletenessResult) -> List[Dict]:
    """
    Suggest which files need to be uploaded to complete the sets.

    Returns:
        List of dictionaries with entity, date, and missing sources
    """
    suggestions = []

    for file_set in result.file_sets:
        if not file_set.is_complete:
            suggestions.append({
                "entity": file_set.entity,
                "date": file_set.date,
                "missing_sources": file_set.missing_sources,
                "expected_filenames": [
                    f"HCM_{source}_INTF_{file_set.entity}_{file_set.date}.csv"
                    for source in file_set.missing_sources
                ]
            })

    return suggestions


# Lambda handler
def lambda_handler(event, context):
    """
    AWS Lambda handler for completeness checking.

    Expected event format:
    {
        "action": "check" | "report" | "suggest",
        "file_names": ["list", "of", "files"]
    }
    """
    action = event.get("action", "check")
    file_names = event.get("file_names", [])

    if not file_names:
        return {
            "statusCode": 400,
            "body": {"error": "No file names provided"}
        }

    result = check_completeness(file_names)

    if action == "check":
        # Return structured data
        file_sets_data = []
        for fs in result.file_sets:
            file_sets_data.append({
                "entity": fs.entity,
                "date": fs.date,
                "files": fs.files,
                "missing_sources": fs.missing_sources,
                "is_complete": fs.is_complete
            })

        return {
            "statusCode": 200,
            "body": {
                "total_files": result.total_files,
                "total_entities_found": result.total_entities_found,
                "complete_sets": result.complete_sets,
                "incomplete_sets": result.incomplete_sets,
                "file_sets": file_sets_data,
                "orphan_files": result.orphan_files,
                "duplicate_files": result.duplicate_files,
                "summary": result.summary
            }
        }

    elif action == "report":
        # Return human-readable report
        report = get_missing_files_report(result)
        return {
            "statusCode": 200,
            "body": {
                "report": report,
                "complete_sets": result.complete_sets,
                "incomplete_sets": result.incomplete_sets
            }
        }

    elif action == "suggest":
        # Return suggestions for missing files
        suggestions = suggest_missing_files(result)
        return {
            "statusCode": 200,
            "body": {
                "suggestions": suggestions,
                "total_missing_files": sum(len(s["missing_sources"]) for s in suggestions)
            }
        }

    return {
        "statusCode": 400,
        "body": {"error": f"Unknown action: {action}"}
    }


if __name__ == "__main__":
    # Test with sample files from the downloaded_files_list.txt
    test_files = [
        "HCM_PERSON_ADDRESS_INTF_FIMAS_20251126160556.csv",
        "HCM_PERSON_ADDRESS_INTF_FIMAS_20251209121226.csv",
        "HCM_PERSON_ADDRESS_INTF_HAC88_20251205.csv",
        "HCM_PERSON_ASSIGNMENT_INTF_FIMAS_20251126160540.csv",
        "HCM_PERSON_ASSIGNMENT_INTF_FIMAS_20251209121210.csv",
        "HCM_PERSON_ASSIGNMENT_INTF_HAC88_20251205.csv",
        "HCM_PERSON_EMAIL_INTF_FIMAS_20251126160642.csv",
        "HCM_PERSON_EMAIL_INTF_FIMAS_20251209121312.csv",
        "HCM_PERSON_EMAIL_INTF_HAC88_20251205.csv",
        "HCM_PERSON_INTF_FIMAS_20251126160540.csv",
        "HCM_PERSON_INTF_FIMAS_20251209121210.csv",
        "HCM_PERSON_INTF_HAC88_20251205.csv",
        "HCM_PERSON_NAME_INTF_FIMAS_20251126160540.csv",
        "HCM_PERSON_NAME_INTF_FIMAS_20251209121210.csv",
        "HCM_PERSON_NAME_INTF_HAC88_20251205.csv",
        "HCM_PERSON_NID_INTF_FIMAS_20251126160540.csv",
        "HCM_PERSON_NID_INTF_FIMAS_20251209121210.csv",
        "HCM_PERSON_NID_INTF_HAC88_20251205.csv",
        "HCM_PERSON_SUPERVISOR_INTF_FIMAS_20251126160540.csv",
        "HCM_PERSON_SUPERVISOR_INTF_FIMAS_20251209121210.csv",
        "HCM_PERSON_SUPERVISOR_INTF_HAC88_20251205.csv",
        "HCM_SENIORITY_INTF_FIMAS_20251126160540.csv",
        "HCM_SENIORITY_INTF_FIMAS_20251209121210.csv",
        "HCM_SENIORITY_INTF_HAC88_20251205.csv",
        "hcm_person_address_rhum75_20260109.csv",
        "hcm_person_address_rhum75_20260116.csv",
        "hcm_person_assign_intf_rhum75_20260116.csv",
        "hcm_person_email_intf_rhum75_20260116.csv",
        "hcm_person_intf_rhum75_20260116.csv",
        "hcm_person_name_intf_rhum75_20260116.csv",
        "hcm_person_nid_intf_rhum75_20260116.csv",
        "hcm_person_supv_intf_rhum75_20260116.csv",
        "hcm_seniority_intf_rhum75_20260116.csv",
    ]

    result = check_completeness(test_files)
    report = get_missing_files_report(result)
    print(report)

    print("\n\nSUGGESTED MISSING FILES:")
    print("-" * 40)
    suggestions = suggest_missing_files(result)
    for s in suggestions:
        print(f"\n{s['entity']} - {s['date']}:")
        for fn in s['expected_filenames']:
            print(f"  - {fn}")
