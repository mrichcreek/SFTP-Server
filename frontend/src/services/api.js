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
