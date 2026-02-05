import { fetchAuthSession } from 'aws-amplify/auth';
import awsconfig from '../aws-exports';

const API_ENDPOINT = awsconfig.API.REST.haciendaApi.endpoint;
// Direct Lambda function URL for full-pipeline (bypasses API Gateway)
const FULL_PIPELINE_URL = 'https://5253fdqsppqvdveoyaeq6dl7ty0pmjep.lambda-url.us-east-1.on.aws';

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
 * @param {string} environment - 'test' for Hacienda ERP Test, 'production' for Hacienda ERP
 * @returns {Promise} Execution results including status, steps completed, delta counts
 */
export async function runStoredProcedure(testMode = true, environment = 'test') {
  const headers = await getAuthHeaders();

  const response = await fetch(`${API_ENDPOINT}/run-procedure`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ test_mode: testMode, environment })
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
 * @param {string} environment - 'test' for Hacienda ERP Test, 'production' for Hacienda ERP
 * @returns {Promise} Current run status, completed steps, and delta counts
 */
export async function getProcedureStatus(environment = 'test') {
  const headers = await getAuthHeaders();

  const response = await fetch(`${API_ENDPOINT}/procedure-status?environment=${environment}`, {
    method: 'GET',
    headers
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.message || 'Failed to get procedure status');
  }

  return response.json();
}

// ============================================================================
// Full Pipeline API Functions
// ============================================================================

/**
 * Run the complete data processing pipeline
 * Steps: VPN Check -> SFTP Download -> Duplicate Check -> Name Validation ->
 *        Schema Validation -> Completeness Check -> SQL Load -> Run Procedure
 * @param {string} environment - 'test' or 'production'
 * @param {boolean} testMode - If true, run stored procedure in test mode
 * @param {string} sourcePrefix - S3 prefix where source files are located
 * @returns {Promise} Pipeline execution results with step details
 */
export async function runFullPipeline(environment = 'test', testMode = true, sourcePrefix = 'downloads/', skipSftp = false) {
  // Full pipeline uses direct Lambda URL (no API Gateway auth required)
  const headers = {
    'Content-Type': 'application/json'
  };

  const response = await fetch(FULL_PIPELINE_URL, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      environment,
      test_mode: testMode,
      source_prefix: sourcePrefix,
      skip_sftp: skipSftp
    })
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.message || 'Failed to run full pipeline');
  }

  return response.json();
}

/**
 * List available pipeline folders (timestamped)
 * @returns {Promise} List of folder names
 */
export async function listPipelineFolders() {
  const headers = await getAuthHeaders();

  const response = await fetch(`${API_ENDPOINT}/pipeline-folders`, {
    method: 'GET',
    headers
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.message || 'Failed to list pipeline folders');
  }

  return response.json();
}

// ============================================================================
// Step Functions Pipeline API
// ============================================================================

/**
 * Start the full pipeline via Step Functions
 * This initiates the AWS Step Functions state machine execution
 * @param {object} options - Pipeline options
 * @param {boolean} options.test_execution - If true, run stored procedure in test mode
 * @returns {Promise} Execution details including execution_id
 */
export async function startPipeline(options = {}) {
  const headers = await getAuthHeaders();

  const response = await fetch(`${API_ENDPOINT}/start-pipeline`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      test_execution: options.test_execution || false,
      s3_bucket: options.s3_bucket || 'hacienda-sftp-downloads'
    })
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.message || 'Failed to start pipeline');
  }

  return response.json();
}

/**
 * Get pipeline execution status from Step Functions
 * @param {string} executionId - The Step Functions execution ID/ARN
 * @returns {Promise} Execution status including current state, history, output
 */
export async function getPipelineStatus(executionId) {
  const headers = await getAuthHeaders();

  // URL-encode the execution ID in case it contains special characters
  const encodedId = encodeURIComponent(executionId);

  const response = await fetch(`${API_ENDPOINT}/pipeline-status/${encodedId}`, {
    method: 'GET',
    headers
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.message || 'Failed to get pipeline status');
  }

  return response.json();
}

/**
 * Stop a running pipeline execution
 * @param {string} executionId - The Step Functions execution ID/ARN
 * @returns {Promise} Stop result
 */
export async function stopPipeline(executionId) {
  const headers = await getAuthHeaders();

  const response = await fetch(`${API_ENDPOINT}/stop-pipeline`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ execution_id: executionId })
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.message || 'Failed to stop pipeline');
  }

  return response.json();
}

/**
 * List recent pipeline executions
 * @param {number} maxResults - Maximum number of results (default 10)
 * @returns {Promise} List of recent executions
 */
export async function listPipelineExecutions(maxResults = 10) {
  const headers = await getAuthHeaders();

  const response = await fetch(`${API_ENDPOINT}/pipeline-executions?max_results=${maxResults}`, {
    method: 'GET',
    headers
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.message || 'Failed to list pipeline executions');
  }

  return response.json();
}
