import { fetchAuthSession } from 'aws-amplify/auth';
import awsconfig from '../aws-exports';

const API_ENDPOINT = awsconfig.API.REST.haciendaApi.endpoint;

async function getAuthHeaders() {
  try {
    const session = await fetchAuthSession();
    const token = session.tokens?.idToken?.toString();
    return {
      'Content-Type': 'application/json',
      'Authorization': token || ''
    };
  } catch (error) {
    console.error('Error getting auth session:', error);
    throw new Error('Authentication required');
  }
}

export async function startDownload() {
  const headers = await getAuthHeaders();

  const response = await fetch(`${API_ENDPOINT}/download`, {
    method: 'POST',
    headers
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.message || 'Failed to start download');
  }

  return response.json();
}

export async function getDownloadStatus(jobId) {
  const headers = await getAuthHeaders();

  const response = await fetch(`${API_ENDPOINT}/download/status/${jobId}`, {
    method: 'GET',
    headers
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.message || 'Failed to get status');
  }

  return response.json();
}

export async function listFiles(jobId = null) {
  const headers = await getAuthHeaders();

  let url = `${API_ENDPOINT}/files`;
  if (jobId) {
    url += `?jobId=${jobId}`;
  }

  const response = await fetch(url, {
    method: 'GET',
    headers
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.message || 'Failed to list files');
  }

  return response.json();
}

// ============================================================================
// NEW: Validation and Workflow API Functions
// ============================================================================

/**
 * Validate file names in S3 bucket
 * @param {string} prefix - S3 prefix to validate
 * @returns {Promise} Validation results
 */
export async function validateFiles(prefix = '') {
  const headers = await getAuthHeaders();

  const response = await fetch(`${API_ENDPOINT}/validate`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ prefix })
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.message || 'Failed to validate files');
  }

  return response.json();
}

/**
 * Check file completeness
 * @param {string} prefix - S3 prefix to check
 * @param {boolean} includeReport - Include human-readable report
 * @returns {Promise} Completeness check results
 */
export async function checkCompleteness(prefix = '', includeReport = false) {
  const headers = await getAuthHeaders();

  const response = await fetch(`${API_ENDPOINT}/completeness`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ prefix, include_report: includeReport })
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.message || 'Failed to check completeness');
  }

  return response.json();
}

/**
 * Check for duplicate files
 * @param {string} prefix - S3 prefix to check
 * @returns {Promise} Duplicate detection results
 */
export async function checkDuplicates(prefix = '') {
  const headers = await getAuthHeaders();

  const response = await fetch(`${API_ENDPOINT}/duplicates`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ prefix })
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.message || 'Failed to check duplicates');
  }

  return response.json();
}

/**
 * Load files to database
 * @param {string} prefix - S3 prefix containing files to load
 * @param {string[]} s3Keys - Optional specific keys to load
 * @returns {Promise} Load results
 */
export async function loadToDatabase(prefix = '', s3Keys = null) {
  const headers = await getAuthHeaders();

  const body = { prefix };
  if (s3Keys) {
    body.s3_keys = s3Keys;
  }

  const response = await fetch(`${API_ENDPOINT}/load`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body)
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.message || 'Failed to load to database');
  }

  return response.json();
}

/**
 * Run complete workflow
 * @param {object} options - Workflow options
 * @returns {Promise} Workflow results
 */
export async function runWorkflow(options = {}) {
  const headers = await getAuthHeaders();

  const response = await fetch(`${API_ENDPOINT}/workflow`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ options })
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.message || 'Failed to run workflow');
  }

  return response.json();
}

/**
 * Get workflow status
 * @param {string} jobId - Workflow job ID
 * @returns {Promise} Workflow status
 */
export async function getWorkflowStatus(jobId) {
  const headers = await getAuthHeaders();

  const response = await fetch(`${API_ENDPOINT}/workflow/status/${jobId}`, {
    method: 'GET',
    headers
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.message || 'Failed to get workflow status');
  }

  return response.json();
}

// ============================================================================
// SQL Server Loading API Functions
// ============================================================================

/**
 * Preview what tables would be created from the files
 * @param {Array} files - Array of file objects with filename and s3_key
 * @returns {Promise} Preview of tables to be created
 */
export async function previewSqlLoad(files) {
  const headers = await getAuthHeaders();

  const response = await fetch(`${API_ENDPOINT}/preview-load`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ files })
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.message || 'Failed to preview SQL load');
  }

  return response.json();
}

/**
 * Load files to SQL Server
 * @param {Array} files - Array of file objects with filename and s3_key
 * @param {boolean} dropExisting - Whether to drop existing tables (default: true)
 * @returns {Promise} Load results
 */
export async function loadToSql(files, dropExisting = true) {
  const headers = await getAuthHeaders();

  const response = await fetch(`${API_ENDPOINT}/load-to-sql`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ files, drop_existing: dropExisting })
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.message || 'Failed to load to SQL Server');
  }

  return response.json();
}

// ============================================================================
// Integrated Validation Workflow
// ============================================================================

/**
 * Run the integrated validation workflow
 * Steps: 1. Check duplicates (auto-move) 2. Validate names 3. Check completeness
 *        4. Generate report if errors 5. Load to SQL if no errors
 * @param {boolean} loadToSqlFlag - Whether to load to SQL if all checks pass
 * @returns {Promise} Workflow results with step details and report URL if errors
 */
export async function runValidationWorkflow(loadToSqlFlag = false) {
  const headers = await getAuthHeaders();

  const response = await fetch(`${API_ENDPOINT}/run-workflow`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ load_to_sql: loadToSqlFlag })
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.message || 'Failed to run validation workflow');
  }

  return response.json();
}

// ============================================================================
// Stored Procedure Execution API Functions
// ============================================================================

/**
 * Run the HCM_MAIN_INTF stored procedure
 * This processes the loaded CSV data and prepares delta records for Oracle
 * @param {boolean} testMode - If true, runs in test mode (filters to specific test SSNs)
 * @returns {Promise} Execution results including status, steps completed, delta counts
 */
export async function runStoredProcedure(testMode = true) {
  const headers = await getAuthHeaders();

  const response = await fetch(`${API_ENDPOINT}/run-procedure`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ test_mode: testMode })
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.message || 'Failed to run stored procedure');
  }

  return response.json();
}

/**
 * Get the current status of stored procedure execution
 * Can be used to poll for progress during a long-running execution
 * @returns {Promise} Current run status, completed steps, and delta counts
 */
export async function getProcedureStatus() {
  const headers = await getAuthHeaders();

  const response = await fetch(`${API_ENDPOINT}/procedure-status`, {
    method: 'GET',
    headers
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.message || 'Failed to get procedure status');
  }

  return response.json();
}
