import React, { useState } from 'react';
import { validateFiles, checkCompleteness, checkDuplicates, runWorkflow, getWorkflowStatus } from '../services/api';

const ValidationPanel = () => {
  const [activeTab, setActiveTab] = useState('validate');
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState(null);
  const [error, setError] = useState(null);
  const [workflowJobId, setWorkflowJobId] = useState(null);
  const [workflowStatus, setWorkflowStatus] = useState(null);

  // Validate file names
  const handleValidate = async () => {
    setLoading(true);
    setError(null);
    setResults(null);
    try {
      const result = await validateFiles();
      setResults({ type: 'validation', data: result });
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  // Check completeness
  const handleCheckCompleteness = async () => {
    setLoading(true);
    setError(null);
    setResults(null);
    try {
      const result = await checkCompleteness('', true);
      setResults({ type: 'completeness', data: result });
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  // Check duplicates
  const handleCheckDuplicates = async () => {
    setLoading(true);
    setError(null);
    setResults(null);
    try {
      const result = await checkDuplicates();
      setResults({ type: 'duplicates', data: result });
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  // Run complete workflow
  const handleRunWorkflow = async () => {
    setLoading(true);
    setError(null);
    setResults(null);
    setWorkflowStatus(null);
    try {
      const result = await runWorkflow({
        continue_on_validation_errors: false,
        continue_on_incomplete_files: true,
        skip_database_load: false,
        skip_interface_execution: false
      });
      setWorkflowJobId(result.job_id);
      setResults({ type: 'workflow', data: result });
      // Poll for status
      pollWorkflowStatus(result.job_id);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  // Poll workflow status
  const pollWorkflowStatus = async (jobId) => {
    try {
      const status = await getWorkflowStatus(jobId);
      setWorkflowStatus(status);
      if (status.status !== 'COMPLETED' && status.status !== 'FAILED') {
        setTimeout(() => pollWorkflowStatus(jobId), 2000);
      }
    } catch (err) {
      console.error('Error polling status:', err);
    }
  };

  const renderValidationResults = (data) => (
    <div style={styles.resultSection}>
      <h4 style={styles.resultTitle}>File Name Validation Results</h4>
      <div style={styles.statsGrid}>
        <div style={styles.statBox}>
          <span style={styles.statNumber}>{data.total_files}</span>
          <span style={styles.statLabel}>Total Files</span>
        </div>
        <div style={{ ...styles.statBox, backgroundColor: '#d4edda' }}>
          <span style={styles.statNumber}>{data.valid_count}</span>
          <span style={styles.statLabel}>Valid</span>
        </div>
        <div style={{ ...styles.statBox, backgroundColor: data.invalid_count > 0 ? '#f8d7da' : '#d4edda' }}>
          <span style={styles.statNumber}>{data.invalid_count}</span>
          <span style={styles.statLabel}>Invalid</span>
        </div>
        <div style={{ ...styles.statBox, backgroundColor: '#fff3cd' }}>
          <span style={styles.statNumber}>{data.correctable_count}</span>
          <span style={styles.statLabel}>Correctable</span>
        </div>
      </div>

      {data.invalid_files && data.invalid_files.length > 0 && (
        <div style={styles.errorList}>
          <h5>Invalid Files:</h5>
          {data.invalid_files.slice(0, 10).map((file, idx) => (
            <div key={idx} style={styles.errorItem}>
              <div style={styles.fileName}>{file.file}</div>
              <div style={styles.errorMsg}>{file.error}</div>
              {file.suggestion && (
                <div style={styles.suggestion}>
                  Suggested: {file.suggestion}
                </div>
              )}
            </div>
          ))}
          {data.invalid_files.length > 10 && (
            <div style={styles.moreItems}>...and {data.invalid_files.length - 10} more</div>
          )}
        </div>
      )}

      <div style={styles.validPatterns}>
        <strong>Valid Sources:</strong> {data.valid_sources?.join(', ')}
        <br />
        <strong>Valid Entities:</strong> {data.valid_entities?.join(', ')}
      </div>
    </div>
  );

  const renderCompletenessResults = (data) => (
    <div style={styles.resultSection}>
      <h4 style={styles.resultTitle}>File Completeness Check</h4>
      <div style={styles.statsGrid}>
        <div style={styles.statBox}>
          <span style={styles.statNumber}>{data.entities_found}</span>
          <span style={styles.statLabel}>Entities Found</span>
        </div>
        <div style={{ ...styles.statBox, backgroundColor: '#d4edda' }}>
          <span style={styles.statNumber}>{data.complete_sets}</span>
          <span style={styles.statLabel}>Complete Sets</span>
        </div>
        <div style={{ ...styles.statBox, backgroundColor: data.incomplete_sets > 0 ? '#fff3cd' : '#d4edda' }}>
          <span style={styles.statNumber}>{data.incomplete_sets}</span>
          <span style={styles.statLabel}>Incomplete Sets</span>
        </div>
        <div style={styles.statBox}>
          <span style={styles.statNumber}>{data.completeness_percentage?.toFixed(1)}%</span>
          <span style={styles.statLabel}>Complete</span>
        </div>
      </div>

      {data.summary?.entities_found && (
        <div style={styles.entitiesList}>
          <strong>Entities Found:</strong> {data.summary.entities_found.join(', ')}
        </div>
      )}

      {data.file_sets && data.file_sets.filter(fs => !fs.is_complete).length > 0 && (
        <div style={styles.incompleteList}>
          <h5>Incomplete File Sets:</h5>
          {data.file_sets.filter(fs => !fs.is_complete).slice(0, 5).map((fs, idx) => (
            <div key={idx} style={styles.incompleteItem}>
              <strong>{fs.entity} - {fs.date}</strong>
              <div style={styles.missingList}>
                Missing: {fs.missing_sources.join(', ')}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );

  const renderDuplicatesResults = (data) => (
    <div style={styles.resultSection}>
      <h4 style={styles.resultTitle}>Duplicate File Detection</h4>
      <div style={styles.statsGrid}>
        <div style={styles.statBox}>
          <span style={styles.statNumber}>{data.total_files}</span>
          <span style={styles.statLabel}>Total Files</span>
        </div>
        <div style={{ ...styles.statBox, backgroundColor: '#d4edda' }}>
          <span style={styles.statNumber}>{data.unique_files}</span>
          <span style={styles.statLabel}>Unique Files</span>
        </div>
        <div style={{ ...styles.statBox, backgroundColor: data.total_exact_duplicates > 0 ? '#f8d7da' : '#d4edda' }}>
          <span style={styles.statNumber}>{data.total_exact_duplicates || 0}</span>
          <span style={styles.statLabel}>Exact Duplicates</span>
        </div>
        <div style={{ ...styles.statBox, backgroundColor: data.total_superseded > 0 ? '#fff3cd' : '#d4edda' }}>
          <span style={styles.statNumber}>{data.total_superseded || 0}</span>
          <span style={styles.statLabel}>Older Versions</span>
        </div>
      </div>

      <div style={styles.storageInfo}>
        <strong>Potential Storage Savings:</strong> {data.storage_waste_mb} MB
      </div>

      {/* Exact Duplicates Section */}
      {data.exact_duplicates && data.exact_duplicates.length > 0 && (
        <div style={styles.duplicateSection}>
          <h5 style={styles.sectionHeader}>
            <span style={styles.headerIcon}>⚠️</span>
            Exact Duplicates (Same Content)
          </h5>
          <p style={styles.sectionDescription}>
            These files have identical content. Only one copy is needed.
          </p>
          {data.exact_duplicates.slice(0, 5).map((group, idx) => (
            <div key={idx} style={styles.duplicateGroup}>
              <div style={styles.duplicateHeader}>
                Group {idx + 1} ({group.files.length} identical files)
              </div>
              {group.files.map((file, fidx) => (
                <div key={fidx} style={{
                  ...styles.duplicateFile,
                  backgroundColor: file.s3_key === group.recommended_keep ? '#d4edda' : '#fff3cd'
                }}>
                  <span>{file.filename}</span>
                  {file.s3_key === group.recommended_keep ?
                    <span style={styles.keepBadge}>KEEP</span> :
                    <span style={styles.removeBadge}>REMOVE</span>
                  }
                </div>
              ))}
            </div>
          ))}
          {data.exact_duplicates.length > 5 && (
            <div style={styles.moreItems}>...and {data.exact_duplicates.length - 5} more groups</div>
          )}
        </div>
      )}

      {/* Superseded Files Section */}
      {data.superseded && data.superseded.length > 0 && (
        <div style={styles.duplicateSection}>
          <h5 style={styles.sectionHeader}>
            <span style={styles.headerIcon}>📅</span>
            Superseded Files (Older Versions)
          </h5>
          <p style={styles.sectionDescription}>
            These are older versions of the same file type. The newest version is recommended.
          </p>
          {data.superseded.slice(0, 5).map((group, idx) => (
            <div key={idx} style={styles.duplicateGroup}>
              <div style={styles.duplicateHeader}>
                {group.file_type} - {group.entity}
              </div>
              {group.files.map((file, fidx) => (
                <div key={fidx} style={{
                  ...styles.duplicateFile,
                  backgroundColor: file.s3_key === group.recommended_keep ? '#d4edda' : '#e9ecef'
                }}>
                  <div style={styles.fileInfo}>
                    <span>{file.filename}</span>
                    <span style={styles.fileDate}>Date: {file.date}</span>
                  </div>
                  {file.s3_key === group.recommended_keep ?
                    <span style={styles.keepBadge}>NEWEST - KEEP</span> :
                    <span style={styles.oldBadge}>OLDER</span>
                  }
                </div>
              ))}
            </div>
          ))}
          {data.superseded.length > 5 && (
            <div style={styles.moreItems}>...and {data.superseded.length - 5} more groups</div>
          )}
        </div>
      )}

      {(!data.exact_duplicates || data.exact_duplicates.length === 0) &&
       (!data.superseded || data.superseded.length === 0) && (
        <div style={styles.noIssues}>
          <span style={styles.checkmark}>✓</span>
          No duplicate or superseded files found.
        </div>
      )}
    </div>
  );

  const renderWorkflowResults = (data) => (
    <div style={styles.resultSection}>
      <h4 style={styles.resultTitle}>Workflow Results</h4>
      <div style={styles.workflowInfo}>
        <strong>Job ID:</strong> {data.job_id}
      </div>

      {workflowStatus && (
        <div style={styles.workflowProgress}>
          <div style={styles.progressBar}>
            <div style={{
              ...styles.progressFill,
              width: `${workflowStatus.progress || 0}%`
            }} />
          </div>
          <div style={styles.progressText}>
            {workflowStatus.status}: {workflowStatus.message}
          </div>
        </div>
      )}

      {data.steps && Object.entries(data.steps).map(([stepName, stepData]) => (
        <div key={stepName} style={{
          ...styles.stepResult,
          borderLeftColor: stepData.status === 'completed' ? '#28a745' :
            stepData.status === 'failed' ? '#dc3545' :
              stepData.status === 'skipped' ? '#6c757d' : '#ffc107'
        }}>
          <strong>{stepName.replace(/_/g, ' ').toUpperCase()}</strong>
          <div style={styles.stepStatus}>Status: {stepData.status || 'pending'}</div>
          {stepData.error && <div style={styles.stepError}>{stepData.error}</div>}
          {stepData.total_files !== undefined && <div>Files: {stepData.total_files}</div>}
          {stepData.valid_count !== undefined && <div>Valid: {stepData.valid_count}</div>}
          {stepData.complete_sets !== undefined && <div>Complete Sets: {stepData.complete_sets}</div>}
          {stepData.total_rows !== undefined && <div>Rows Loaded: {stepData.total_rows}</div>}
        </div>
      ))}
    </div>
  );

  const renderResults = () => {
    if (!results) return null;

    switch (results.type) {
      case 'validation':
        return renderValidationResults(results.data);
      case 'completeness':
        return renderCompletenessResults(results.data);
      case 'duplicates':
        return renderDuplicatesResults(results.data);
      case 'workflow':
        return renderWorkflowResults(results.data);
      default:
        return <pre>{JSON.stringify(results.data, null, 2)}</pre>;
    }
  };

  return (
    <div style={styles.container}>
      <div style={styles.tabs}>
        <button
          style={activeTab === 'validate' ? styles.activeTab : styles.tab}
          onClick={() => setActiveTab('validate')}
        >
          Validate Names
        </button>
        <button
          style={activeTab === 'completeness' ? styles.activeTab : styles.tab}
          onClick={() => setActiveTab('completeness')}
        >
          Check Completeness
        </button>
        <button
          style={activeTab === 'duplicates' ? styles.activeTab : styles.tab}
          onClick={() => setActiveTab('duplicates')}
        >
          Find Duplicates
        </button>
        <button
          style={activeTab === 'workflow' ? styles.activeTab : styles.tab}
          onClick={() => setActiveTab('workflow')}
        >
          Full Workflow
        </button>
      </div>

      <div style={styles.content}>
        {activeTab === 'validate' && (
          <div style={styles.section}>
            <p style={styles.description}>
              Validate file names against the official naming conventions.
              Files must match the pattern: HCM_{'{SOURCE}'}_INTF_{'{ENTITY}'}_{'{DATE}'}.csv
            </p>
            <button
              style={styles.actionButton}
              onClick={handleValidate}
              disabled={loading}
            >
              {loading ? 'Validating...' : 'Validate File Names'}
            </button>
          </div>
        )}

        {activeTab === 'completeness' && (
          <div style={styles.section}>
            <p style={styles.description}>
              Check if all required files are present for each entity/date combination.
              A complete set includes: PERSON, PERSON_NAME, PERSON_ASSIGNMENT, PERSON_ADDRESS,
              PERSON_NID, PERSON_SUPERVISOR, PERSON_EMAIL, and SENIORITY files.
            </p>
            <button
              style={styles.actionButton}
              onClick={handleCheckCompleteness}
              disabled={loading}
            >
              {loading ? 'Checking...' : 'Check Completeness'}
            </button>
          </div>
        )}

        {activeTab === 'duplicates' && (
          <div style={styles.section}>
            <p style={styles.description}>
              Detect duplicate files in the S3 bucket. Identifies files with identical content
              and recommends which ones to keep.
            </p>
            <button
              style={styles.actionButton}
              onClick={handleCheckDuplicates}
              disabled={loading}
            >
              {loading ? 'Checking...' : 'Find Duplicates'}
            </button>
          </div>
        )}

        {activeTab === 'workflow' && (
          <div style={styles.section}>
            <p style={styles.description}>
              Run the complete validation and loading workflow:
              1. Check for duplicates
              2. Validate file names
              3. Check completeness
              4. Load to database
              5. Execute HCM interface
            </p>
            <button
              style={{ ...styles.actionButton, backgroundColor: '#28a745' }}
              onClick={handleRunWorkflow}
              disabled={loading}
            >
              {loading ? 'Running Workflow...' : 'Run Complete Workflow'}
            </button>
          </div>
        )}

        {error && (
          <div style={styles.error}>
            Error: {error}
          </div>
        )}

        {renderResults()}
      </div>
    </div>
  );
};

const styles = {
  container: {
    width: '100%'
  },
  tabs: {
    display: 'flex',
    borderBottom: '2px solid #dee2e6',
    marginBottom: '20px'
  },
  tab: {
    padding: '12px 20px',
    border: 'none',
    backgroundColor: 'transparent',
    cursor: 'pointer',
    fontSize: '14px',
    color: '#666',
    borderBottom: '2px solid transparent',
    marginBottom: '-2px'
  },
  activeTab: {
    padding: '12px 20px',
    border: 'none',
    backgroundColor: 'transparent',
    cursor: 'pointer',
    fontSize: '14px',
    color: '#232f3e',
    fontWeight: 'bold',
    borderBottom: '2px solid #232f3e',
    marginBottom: '-2px'
  },
  content: {
    padding: '0'
  },
  section: {
    marginBottom: '20px'
  },
  description: {
    color: '#666',
    marginBottom: '16px',
    lineHeight: '1.5'
  },
  actionButton: {
    padding: '12px 24px',
    backgroundColor: '#232f3e',
    color: 'white',
    border: 'none',
    borderRadius: '6px',
    cursor: 'pointer',
    fontSize: '14px',
    fontWeight: 'bold'
  },
  error: {
    padding: '16px',
    backgroundColor: '#f8d7da',
    color: '#721c24',
    borderRadius: '6px',
    marginTop: '16px'
  },
  resultSection: {
    marginTop: '24px',
    padding: '20px',
    backgroundColor: '#f8f9fa',
    borderRadius: '8px'
  },
  resultTitle: {
    marginTop: 0,
    marginBottom: '16px',
    color: '#232f3e'
  },
  statsGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(4, 1fr)',
    gap: '12px',
    marginBottom: '20px'
  },
  statBox: {
    backgroundColor: '#fff',
    padding: '16px',
    borderRadius: '8px',
    textAlign: 'center',
    boxShadow: '0 1px 3px rgba(0,0,0,0.1)'
  },
  statNumber: {
    display: 'block',
    fontSize: '24px',
    fontWeight: 'bold',
    color: '#232f3e'
  },
  statLabel: {
    fontSize: '12px',
    color: '#666'
  },
  errorList: {
    backgroundColor: '#fff',
    padding: '16px',
    borderRadius: '8px',
    marginTop: '16px'
  },
  errorItem: {
    padding: '12px',
    borderBottom: '1px solid #eee',
    marginBottom: '8px'
  },
  fileName: {
    fontWeight: 'bold',
    color: '#333'
  },
  errorMsg: {
    color: '#dc3545',
    fontSize: '13px',
    marginTop: '4px'
  },
  suggestion: {
    color: '#28a745',
    fontSize: '13px',
    marginTop: '4px',
    fontStyle: 'italic'
  },
  moreItems: {
    color: '#666',
    fontStyle: 'italic',
    padding: '8px'
  },
  validPatterns: {
    padding: '12px',
    backgroundColor: '#e9ecef',
    borderRadius: '6px',
    fontSize: '13px',
    lineHeight: '1.6'
  },
  entitiesList: {
    padding: '12px',
    backgroundColor: '#fff',
    borderRadius: '6px',
    marginBottom: '16px'
  },
  incompleteList: {
    backgroundColor: '#fff',
    padding: '16px',
    borderRadius: '8px'
  },
  incompleteItem: {
    padding: '12px',
    borderBottom: '1px solid #eee'
  },
  missingList: {
    color: '#856404',
    fontSize: '13px',
    marginTop: '4px'
  },
  duplicateGroups: {
    backgroundColor: '#fff',
    padding: '16px',
    borderRadius: '8px'
  },
  duplicateGroup: {
    marginBottom: '16px',
    borderBottom: '1px solid #eee',
    paddingBottom: '12px'
  },
  duplicateHeader: {
    fontWeight: 'bold',
    marginBottom: '8px'
  },
  duplicateFile: {
    padding: '8px 12px',
    borderRadius: '4px',
    marginBottom: '4px',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center'
  },
  keepBadge: {
    backgroundColor: '#28a745',
    color: 'white',
    padding: '2px 8px',
    borderRadius: '4px',
    fontSize: '11px',
    fontWeight: 'bold'
  },
  removeBadge: {
    backgroundColor: '#dc3545',
    color: 'white',
    padding: '2px 8px',
    borderRadius: '4px',
    fontSize: '11px',
    fontWeight: 'bold'
  },
  oldBadge: {
    backgroundColor: '#6c757d',
    color: 'white',
    padding: '2px 8px',
    borderRadius: '4px',
    fontSize: '11px',
    fontWeight: 'bold'
  },
  duplicateSection: {
    backgroundColor: '#fff',
    padding: '16px',
    borderRadius: '8px',
    marginBottom: '16px'
  },
  sectionHeader: {
    margin: '0 0 8px 0',
    fontSize: '16px',
    display: 'flex',
    alignItems: 'center',
    gap: '8px'
  },
  headerIcon: {
    fontSize: '18px'
  },
  sectionDescription: {
    fontSize: '13px',
    color: '#666',
    marginBottom: '12px'
  },
  storageInfo: {
    padding: '12px',
    backgroundColor: '#fff',
    borderRadius: '6px',
    marginBottom: '16px',
    textAlign: 'center'
  },
  fileInfo: {
    display: 'flex',
    flexDirection: 'column',
    gap: '2px'
  },
  fileDate: {
    fontSize: '11px',
    color: '#666'
  },
  noIssues: {
    padding: '24px',
    backgroundColor: '#d4edda',
    borderRadius: '8px',
    textAlign: 'center',
    color: '#155724'
  },
  checkmark: {
    fontSize: '24px',
    marginRight: '8px'
  },
  workflowInfo: {
    padding: '12px',
    backgroundColor: '#e9ecef',
    borderRadius: '6px',
    marginBottom: '16px'
  },
  workflowProgress: {
    marginBottom: '20px'
  },
  progressBar: {
    height: '8px',
    backgroundColor: '#e9ecef',
    borderRadius: '4px',
    overflow: 'hidden'
  },
  progressFill: {
    height: '100%',
    backgroundColor: '#28a745',
    transition: 'width 0.3s ease'
  },
  progressText: {
    marginTop: '8px',
    fontSize: '13px',
    color: '#666'
  },
  stepResult: {
    padding: '12px',
    marginBottom: '12px',
    borderLeft: '4px solid #ddd',
    backgroundColor: '#fff',
    borderRadius: '0 6px 6px 0'
  },
  stepStatus: {
    fontSize: '13px',
    color: '#666',
    marginTop: '4px'
  },
  stepError: {
    color: '#dc3545',
    fontSize: '13px',
    marginTop: '4px'
  }
};

export default ValidationPanel;
