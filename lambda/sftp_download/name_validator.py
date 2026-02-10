"""
File Naming Validator for Hacienda SFTP Downloads
Validates file names against the official naming conventions.
"""

import re
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass
class ValidationResult:
    """Result of file name validation."""
    is_valid: bool
    file_name: str
    entity: Optional[str] = None
    source: Optional[str] = None
    date_str: Optional[str] = None
    error_message: Optional[str] = None
    suggested_correction: Optional[str] = None
    similarity_score: float = 0.0


# Official naming patterns from NameingOfFiles.docx
# Format: HCM_{SOURCE}_INTF_{ENTITY}_{DATE}.csv
# Where DATE is YYYYMMDD or YYYYMMDD_HHMM or YYYYMMDDHHMM or YYYYMMDDHHMMSS

VALID_SOURCES = [
    "PERSON",
    "PERSON_NAME",
    "PERSON_ASSIGNMENT",
    "PERSON_ADDRESS",
    "PERSON_NID",
    "PERSON_SUPERVISOR",
    "PERSON_EMAIL",
    "SENIORITY"
]

VALID_ENTITIES = [
    "911",
    "RHUM",
    "HACIENDA",
    "FIMAS",
    "DOE",
    "KRONOSPOL",
    "KRONOSDE",
    "SEPI",
    "ADPPOLICIA"
]

# Alias mappings for common variations
SOURCE_ALIASES = {
    "ASSIGN": "PERSON_ASSIGNMENT",
    "ASSIGNMENT": "PERSON_ASSIGNMENT",
    "PERSON_ASSIGN": "PERSON_ASSIGNMENT",
    "PERSON_ASSIGN_INTF": "PERSON_ASSIGNMENT",
    "NAME": "PERSON_NAME",
    "PERSON_NAME_INTF": "PERSON_NAME",
    "ADDRESS": "PERSON_ADDRESS",
    "PERSON_ADDRESS_INTF": "PERSON_ADDRESS",
    "NID": "PERSON_NID",
    "PERSON_NID_INTF": "PERSON_NID",
    "SUPERVISOR": "PERSON_SUPERVISOR",
    "SUPV": "PERSON_SUPERVISOR",
    "PERSON_SUPV": "PERSON_SUPERVISOR",
    "PERSON_SUPV_INTF": "PERSON_SUPERVISOR",
    "PERSON_SUPERVISOR_INTF": "PERSON_SUPERVISOR",
    "EMAIL": "PERSON_EMAIL",
    "PERSON_EMAIL_INTF": "PERSON_EMAIL",
    "PERSON_INTF": "PERSON",
    "SENIOR": "SENIORITY",
    "SENIORITY_INTF": "SENIORITY",
}

ENTITY_ALIASES = {
    "HAC88": "HACIENDA",
    "HAC": "HACIENDA",
    "RHUM75": "RHUM",
    "POLICIA": "ADPPOLICIA",
    "ADP": "ADPPOLICIA",
    "KRONOS_POL": "KRONOSPOL",
    "KRONOS_DE": "KRONOSDE",
}

# Base pattern for HCM files
# Matches: HCM_{SOURCE}_INTF_{ENTITY}_{DATE}.csv
# Where SOURCE can be multi-part (PERSON_ASSIGNMENT) and DATE varies
FILE_PATTERN = re.compile(
    r'^HCM_(.+?)_INTF_(.+?)_(\d{8,14})\.csv$',
    re.IGNORECASE
)

# Alternative pattern for files without INTF
ALT_PATTERN = re.compile(
    r'^HCM_(.+?)_(.+?)_(\d{8,14})\.csv$',
    re.IGNORECASE
)

# Date patterns
DATE_PATTERNS = [
    (re.compile(r'^\d{8}$'), "YYYYMMDD"),
    (re.compile(r'^\d{8}_\d{4}$'), "YYYYMMDD_HHMM"),
    (re.compile(r'^\d{12}$'), "YYYYMMDDHHMM"),
    (re.compile(r'^\d{14}$'), "YYYYMMDDHHMMSS"),
]


def normalize_source(source: str) -> Tuple[str, bool]:
    """
    Normalize a source name to its canonical form.
    Returns (normalized_name, is_exact_match).
    """
    upper_source = source.upper().replace("_INTF", "")

    # Check exact match first
    if upper_source in VALID_SOURCES:
        return upper_source, True

    # Check aliases
    if upper_source in SOURCE_ALIASES:
        return SOURCE_ALIASES[upper_source], False

    # Try fuzzy matching
    best_match = None
    best_score = 0.0
    for valid_source in VALID_SOURCES:
        score = SequenceMatcher(None, upper_source, valid_source).ratio()
        if score > best_score and score > 0.6:
            best_score = score
            best_match = valid_source

    if best_match:
        return best_match, False

    return source.upper(), False


def normalize_entity(entity: str) -> Tuple[str, bool]:
    """
    Normalize an entity name to its canonical form.
    Returns (normalized_name, is_exact_match).
    """
    upper_entity = entity.upper()

    # Check exact match first
    if upper_entity in VALID_ENTITIES:
        return upper_entity, True

    # Check aliases
    if upper_entity in ENTITY_ALIASES:
        return ENTITY_ALIASES[upper_entity], False

    # Try fuzzy matching
    best_match = None
    best_score = 0.0
    for valid_entity in VALID_ENTITIES:
        score = SequenceMatcher(None, upper_entity, valid_entity).ratio()
        if score > best_score and score > 0.6:
            best_score = score
            best_match = valid_entity

    if best_match:
        return best_match, False

    return entity.upper(), False


def validate_date_format(date_str: str) -> Tuple[bool, str]:
    """
    Validate the date portion of the filename.
    Returns (is_valid, format_description).
    """
    # Remove underscore if present
    clean_date = date_str.replace("_", "")

    for pattern, description in DATE_PATTERNS:
        if pattern.match(date_str) or pattern.match(clean_date):
            return True, description

    return False, "Invalid date format"


def extract_components(file_name: str) -> Optional[Dict]:
    """
    Extract source, entity, and date from a file name.
    Returns None if the file doesn't match expected patterns.

    For files like 'hcm_person_address_rhum75_20260109.csv':
    - Source should be 'PERSON_ADDRESS' (known source)
    - Entity should be 'rhum75' (which normalizes to RHUM)
    """
    # Try primary pattern first (with _INTF_)
    match = FILE_PATTERN.match(file_name)
    if match:
        return {
            "source": match.group(1),
            "entity": match.group(2),
            "date": match.group(3),
            "pattern": "primary"
        }

    # For alternative pattern (no _INTF_), we need smarter parsing
    # Try to match known sources from longest to shortest
    match = ALT_PATTERN.match(file_name)
    if match:
        # Get the full middle portion (everything between HCM_ and _DATE.csv)
        full_middle = match.group(1) + "_" + match.group(2)
        date_str = match.group(3)

        # Known sources (sorted by length, longest first to match greedily)
        known_sources = sorted(VALID_SOURCES + list(SOURCE_ALIASES.keys()), key=len, reverse=True)

        upper_middle = full_middle.upper()

        # Try to find a known source at the start
        for source in known_sources:
            # Check if middle starts with this source followed by underscore
            if upper_middle.startswith(source + "_"):
                # Found the source, the rest is the entity
                entity = full_middle[len(source) + 1:]  # Skip source and underscore
                return {
                    "source": source,
                    "entity": entity,
                    "date": date_str,
                    "pattern": "alternative_smart"
                }

        # If no known source found, fall back to original behavior
        # (first part is source, rest is entity)
        return {
            "source": match.group(1),
            "entity": match.group(2),
            "date": date_str,
            "pattern": "alternative"
        }

    return None


def build_corrected_filename(source: str, entity: str, date_str: str) -> str:
    """Build a correctly formatted filename from components."""
    return f"HCM_{source}_INTF_{entity}_{date_str}.csv"


def validate_file_name(file_name: str) -> ValidationResult:
    """
    Validate a single file name against naming conventions.

    Args:
        file_name: The file name to validate (not full path)

    Returns:
        ValidationResult with validation status and suggestions
    """
    # Skip non-CSV files
    if not file_name.lower().endswith('.csv'):
        return ValidationResult(
            is_valid=False,
            file_name=file_name,
            error_message="Not a CSV file",
            similarity_score=0.0
        )

    # Skip files that don't start with HCM
    if not file_name.upper().startswith('HCM_'):
        return ValidationResult(
            is_valid=False,
            file_name=file_name,
            error_message="File name must start with 'HCM_'",
            similarity_score=0.0
        )

    # Extract components
    components = extract_components(file_name)
    if not components:
        return ValidationResult(
            is_valid=False,
            file_name=file_name,
            error_message="File name does not match expected pattern: HCM_{SOURCE}_INTF_{ENTITY}_{DATE}.csv",
            similarity_score=0.0
        )

    # Normalize and validate source
    raw_source = components["source"]
    normalized_source, source_exact = normalize_source(raw_source)
    source_valid = normalized_source in VALID_SOURCES

    # Normalize and validate entity
    raw_entity = components["entity"]
    normalized_entity, entity_exact = normalize_entity(raw_entity)
    entity_valid = normalized_entity in VALID_ENTITIES

    # Validate date
    date_str = components["date"]
    date_valid, date_format = validate_date_format(date_str)

    # Build validation result
    errors = []
    if not source_valid:
        errors.append(f"Invalid source '{raw_source}'. Valid sources: {', '.join(VALID_SOURCES)}")
    if not entity_valid:
        errors.append(f"Invalid entity '{raw_entity}'. Valid entities: {', '.join(VALID_ENTITIES)}")
    if not date_valid:
        errors.append(f"Invalid date format '{date_str}'. Expected: YYYYMMDD, YYYYMMDD_HHMM, YYYYMMDDHHMM, or YYYYMMDDHHMMSS")

    # File is valid if source, entity, and date are all valid
    # Note: We accept aliases (HAC88->HACIENDA, RHUM75->RHUM, etc.) as valid
    # The file doesn't need to have the exact canonical name, just a recognized alias
    is_valid = source_valid and entity_valid and date_valid

    # Calculate similarity score
    if source_valid and entity_valid and date_valid:
        similarity_score = 1.0 if (source_exact and entity_exact) else 0.95  # Aliases get 0.95
    elif source_valid or entity_valid:
        similarity_score = 0.5
    else:
        similarity_score = 0.2

    # Generate suggested correction only for INVALID files
    # Don't suggest corrections for files that are valid but use aliases
    suggested_correction = None
    if not is_valid and (source_valid or entity_valid):
        corrected_source = normalized_source if source_valid else "PERSON"
        corrected_entity = normalized_entity if entity_valid else "HACIENDA"
        suggested_correction = build_corrected_filename(corrected_source, corrected_entity, date_str)

    return ValidationResult(
        is_valid=is_valid,
        file_name=file_name,
        entity=normalized_entity if entity_valid else raw_entity,
        source=normalized_source if source_valid else raw_source,
        date_str=date_str,
        error_message="; ".join(errors) if errors else None,
        suggested_correction=suggested_correction,
        similarity_score=similarity_score
    )


def validate_file_list(file_names: List[str]) -> Dict:
    """
    Validate a list of file names.

    Args:
        file_names: List of file names to validate

    Returns:
        Dictionary with validation results and summary
    """
    results = []
    valid_count = 0
    invalid_count = 0
    correctable_count = 0

    for file_name in file_names:
        result = validate_file_name(file_name)
        results.append(result)

        if result.is_valid:
            valid_count += 1
        else:
            invalid_count += 1
            if result.suggested_correction:
                correctable_count += 1

    return {
        "total_files": len(file_names),
        "valid_count": valid_count,
        "invalid_count": invalid_count,
        "correctable_count": correctable_count,
        "results": results,
        "valid_sources": VALID_SOURCES,
        "valid_entities": VALID_ENTITIES
    }


def get_file_metadata(file_name: str) -> Optional[Dict]:
    """
    Extract metadata from a valid file name.

    Args:
        file_name: The file name to parse

    Returns:
        Dictionary with source, entity, date, or None if invalid
    """
    result = validate_file_name(file_name)
    if result.source and result.entity and result.date_str:
        return {
            "source": result.source,
            "entity": result.entity,
            "date": result.date_str,
            "is_valid": result.is_valid
        }
    return None


# Lambda handler for AWS Lambda integration
def lambda_handler(event, context):
    """
    AWS Lambda handler for file name validation.

    Expected event format:
    {
        "action": "validate" | "validate_batch",
        "file_name": "single file name" (for validate),
        "file_names": ["list", "of", "files"] (for validate_batch)
    }
    """
    action = event.get("action", "validate")

    if action == "validate":
        file_name = event.get("file_name", "")
        result = validate_file_name(file_name)
        return {
            "statusCode": 200,
            "body": {
                "is_valid": result.is_valid,
                "file_name": result.file_name,
                "entity": result.entity,
                "source": result.source,
                "date": result.date_str,
                "error_message": result.error_message,
                "suggested_correction": result.suggested_correction,
                "similarity_score": result.similarity_score
            }
        }

    elif action == "validate_batch":
        file_names = event.get("file_names", [])
        validation_results = validate_file_list(file_names)

        # Convert ValidationResult objects to dictionaries
        results_dict = []
        for r in validation_results["results"]:
            results_dict.append({
                "is_valid": r.is_valid,
                "file_name": r.file_name,
                "entity": r.entity,
                "source": r.source,
                "date": r.date_str,
                "error_message": r.error_message,
                "suggested_correction": r.suggested_correction,
                "similarity_score": r.similarity_score
            })

        return {
            "statusCode": 200,
            "body": {
                "total_files": validation_results["total_files"],
                "valid_count": validation_results["valid_count"],
                "invalid_count": validation_results["invalid_count"],
                "correctable_count": validation_results["correctable_count"],
                "results": results_dict,
                "valid_sources": validation_results["valid_sources"],
                "valid_entities": validation_results["valid_entities"]
            }
        }

    elif action == "get_valid_patterns":
        return {
            "statusCode": 200,
            "body": {
                "valid_sources": VALID_SOURCES,
                "valid_entities": VALID_ENTITIES,
                "source_aliases": SOURCE_ALIASES,
                "entity_aliases": ENTITY_ALIASES,
                "date_formats": ["YYYYMMDD", "YYYYMMDD_HHMM", "YYYYMMDDHHMM", "YYYYMMDDHHMMSS"]
            }
        }

    return {
        "statusCode": 400,
        "body": {"error": f"Unknown action: {action}"}
    }


if __name__ == "__main__":
    # Test validation with sample files
    test_files = [
        "HCM_PERSON_ADDRESS_INTF_FIMAS_20251126160556.csv",
        "HCM_PERSON_ASSIGNMENT_INTF_HAC88_20251205.csv",
        "hcm_person_address_rhum75_20260109.csv",
        "hcm_person_assign_intf_rhum75_20260116.csv",
        "HCM_SENIORITY_INTF_HACIENDA_20251205.csv",
        "invalid_file_name.csv",
        "HCM_INVALID_SOURCE_INTF_FIMAS_20251126.csv",
    ]

    print("=" * 80)
    print("FILE NAME VALIDATION TEST")
    print("=" * 80)

    for file_name in test_files:
        result = validate_file_name(file_name)
        print(f"\nFile: {file_name}")
        print(f"  Valid: {result.is_valid}")
        print(f"  Source: {result.source}")
        print(f"  Entity: {result.entity}")
        print(f"  Date: {result.date_str}")
        if result.error_message:
            print(f"  Error: {result.error_message}")
        if result.suggested_correction:
            print(f"  Suggested: {result.suggested_correction}")
        print(f"  Similarity: {result.similarity_score:.2f}")
