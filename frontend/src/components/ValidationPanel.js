import React, { useState } from 'react';
import { validateFiles, checkCompleteness, checkDuplicates, runWorkflow, getWorkflowStatus, listFiles, previewSqlLoad, loadToSql, runValidationWorkflow } from '../services/api';

const ValidationPanel = () => {
  const [activeTab, setActiveTab] = useState('duplicates');
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState(null);
  const [error, setError] = useState(null);
  const [workflowJobId, setWorkflowJobId] = useState(null);
  const [workflowStatus, setWorkflowStatus] = useState(null);
  const [sqlPreview, setSqlPreview] = useState(null);
  const [sqlLoading, setSqlLoading] = useState(false);

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

  // Run integrated validation workflow
  const handleIntegratedWorkflow = async (loadToSql = false) => {
    setLoading(true);
    setError(null);
    setResults(null);
    try {
      const result = await runValidationWorkflow(loadToSql);
      setResults({ type: 'integratedWorkflow', data: result });
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  // Preview SQL load
  const handlePreviewSqlLoad = async () => {
    setLoading(true);
    setError(null);
    setSqlPreview(null);
    setResults(null);
    try {
      // First get all files from S3
      const filesResult = await listFiles();
      const files = filesResult.files || [];

      // Transform to expected format
      const fileList = files.map(f => ({
        filename: f.filename || f.key?.split('/').pop(),
        s3_key: f.key || f.s3_key
      }));

      // Get preview
      const preview = await previewSqlLoad(fileList);
      setSqlPreview(preview);
      setResults({ type: 'sqlpreview', data: preview });
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  // Load to SQL Server
  const handleLoadToSql = async () => {
    if (!sqlPreview || !sqlPreview.tables) {
      setError('Please preview the load first');
      return;
    }

    setSqlLoading(true);
    setError(null);
    try {
      // Get the files from the preview
      const files = sqlPreview.tables.map(t => ({
        filename: t.source_file,
        s3_key: t.s3_key
      }));

      const result = await loadToSql(files, true);
      setResults({ type: 'sqlload', data: result });
    } catch (err) {
      setError(err.message);
    } finally {
      setSqlLoading(false);
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

  const renderSqlPreviewResults = (data) => (
    <div style={styles.resultSection}>
      <h4 style={styles.resultTitle}>SQL Load Preview</h4>
      <div style={styles.statsGrid}>
        <div style={styles.statBox}>
          <span style={styles.statNumber}>{data.total_tables}</span>
          <span style={styles.statLabel}>Tables to Create</span>
        </div>
      </div>

      <p style={styles.sectionDescription}>
        The following tables will be created in SQL Server. Each table contains the newest version of the file.
      </p>

      <div style={styles.tableList}>
        {data.tables && data.tables.map((table, idx) => (
          <div key={idx} style={styles.tableItem}>
            <div style={styles.tableName}>{table.table_name}</div>
            <div style={styles.tableSource}>
              Source: {table.source_file}
            </div>
            <div style={styles.tableDate}>
              Date: {table.date_portion}
            </div>
          </div>
        ))}
      </div>

      <div style={styles.loadButtonContainer}>
        <button
          style={{ ...styles.actionButton, backgroundColor: '#28a745' }}
          onClick={handleLoadToSql}
          disabled={sqlLoading}
        >
          {sqlLoading ? 'Loading to SQL Server...' : 'Load to SQL Server'}
        </button>
      </div>
    </div>
  );

  const renderSqlLoadResults = (data) => (
    <div style={styles.resultSection}>
      <h4 style={styles.resultTitle}>SQL Load Results</h4>
      <div style={styles.statsGrid}>
        <div style={styles.statBox}>
          <span style={styles.statNumber}>{data.total_tables}</span>
          <span style={styles.statLabel}>Total Tables</span>
        </div>
        <div style={{ ...styles.statBox, backgroundColor: '#d4edda' }}>
          <span style={styles.statNumber}>{data.successful}</span>
          <span style={styles.statLabel}>Successful</span>
        </div>
        <div style={{ ...styles.statBox, backgroundColor: data.failed > 0 ? '#f8d7da' : '#d4edda' }}>
          <span style={styles.statNumber}>{data.failed}</span>
          <span style={styles.statLabel}>Failed</span>
        </div>
      </div>

      <div style={styles.tableList}>
        {data.tables && data.tables.map((table, idx) => (
          <div key={idx} style={{
            ...styles.tableItem,
            borderLeft: `4px solid ${table.success ? '#28a745' : '#dc3545'}`
          }}>
            <div style={styles.tableName}>
              {table.success ? '✓' : '✗'} {table.table_name}
            </div>
            <div style={styles.tableSource}>
              Source: {table.source_file}
            </div>
            {table.success ? (
              <div style={styles.rowsLoaded}>
                Rows loaded: {table.rows_loaded} | Columns: {table.columns}
              </div>
            ) : (
              <div style={styles.loadError}>
                Error: {table.error}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );

  const renderIntegratedWorkflowResults = (data) => (
    <div style={styles.resultSection}>
      <h4 style={styles.resultTitle}>Validation Workflow Results</h4>

      {/* Overall Status */}
      <div style={{
        ...styles.statusBanner,
        backgroundColor: data.has_errors ? '#f8d7da' : data.status === 'completed' ? '#d4edda' : '#fff3cd'
      }}>
        <strong>Status: </strong>
        {data.has_errors ? 'Errors Found - Review Report' :
          data.status === 'completed' ? 'All Checks Passed' : data.status}
      </div>

      {/* Step Results */}
      <div style={styles.stepsList}>
        {/* Step 1: Initial Files */}
        {data.steps?.initial_files && (
          <div style={styles.stepBox}>
            <div style={styles.stepHeader}>Step 1: List Files</div>
            <div style={styles.stepDetail}>
              Total files found: {data.steps.initial_files.total_files}
            </div>
          </div>
        )}

        {/* Step 2: Duplicates */}
        {data.steps?.duplicates && (
          <div style={{
            ...styles.stepBox,
            borderLeft: `4px solid ${data.steps.duplicates.total_moved > 0 ? '#ffc107' : '#28a745'}`
          }}>
            <div style={styles.stepHeader}>Step 2: Duplicate Check</div>
            <div style={styles.stepDetail}>
              Exact duplicates moved: {data.steps.duplicates.exact_duplicates_moved}
            </div>
            <div style={styles.stepDetail}>
              Older versions moved: {data.steps.duplicates.superseded_moved}
            </div>
            <div style={styles.stepDetail}>
              Files remaining: {data.steps.duplicates.files_remaining}
            </div>
            {data.steps.duplicates.total_moved > 0 && (
              <div style={styles.stepNote}>
                Files moved to DuplicateCheck/ folder
              </div>
            )}
          </div>
        )}

        {/* Step 3: Validation */}
        {data.steps?.validation && (
          <div style={{
            ...styles.stepBox,
            borderLeft: `4px solid ${data.steps.validation.status === 'passed' ? '#28a745' : '#dc3545'}`
          }}>
            <div style={styles.stepHeader}>Step 3: File Name Validation</div>
            <div style={styles.stepDetail}>
              Valid: {data.steps.validation.valid_count} | Invalid: {data.steps.validation.invalid_count}
            </div>
            {data.steps.validation.invalid_files?.length > 0 && (
              <div style={styles.invalidList}>
                <strong>Invalid files:</strong>
                {data.steps.validation.invalid_files.slice(0, 5).map((f, i) => (
                  <div key={i} style={styles.invalidItem}>
                    {f.file_name}: {f.error_message}
                  </div>
                ))}
                {data.steps.validation.invalid_files.length > 5 && (
                  <div style={styles.moreItems}>...and {data.steps.validation.invalid_files.length - 5} more</div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Step 4: Completeness */}
        {data.steps?.completeness && (
          <div style={{
            ...styles.stepBox,
            borderLeft: `4px solid ${data.steps.completeness.status === 'passed' ? '#28a745' : '#dc3545'}`
          }}>
            <div style={styles.stepHeader}>Step 4: Completeness Check</div>
            <div style={styles.stepDetail}>
              Complete sets: {data.steps.completeness.complete_sets} |
              Incomplete: {data.steps.completeness.incomplete_sets} |
              {data.steps.completeness.completeness_percentage}%
            </div>
          </div>
        )}

        {/* Step 5: Report (if errors) */}
        {data.steps?.report && (
          <div style={{ ...styles.stepBox, borderLeft: '4px solid #17a2b8' }}>
            <div style={styles.stepHeader}>Step 5: Error Report Generated</div>
            <a
              href={data.report_url}
              target="_blank"
              rel="noopener noreferrer"
              style={styles.downloadLink}
            >
              Download Report ({data.report_name})
            </a>
          </div>
        )}

        {/* Step 6: SQL Load (if no errors) */}
        {data.steps?.sql_load && data.steps.sql_load.status !== 'skipped' && (
          <div style={{
            ...styles.stepBox,
            borderLeft: `4px solid ${data.steps.sql_load.status === 'completed' ? '#28a745' : '#dc3545'}`
          }}>
            <div style={styles.stepHeader}>Step 6: SQL Server Load</div>
            <div style={styles.stepDetail}>
              Tables: {data.steps.sql_load.total_tables} |
              Successful: {data.steps.sql_load.successful} |
              Failed: {data.steps.sql_load.failed}
            </div>
          </div>
        )}
      </div>

      {/* Download Report Button */}
      {data.report_url && (
        <div style={styles.reportSection}>
          <button
            style={styles.downloadButton}
            onClick={() => {
              const link = document.createElement('a');
              link.href = data.report_url;
              link.download = data.report_name || 'validation_report.csv';
              document.body.appendChild(link);
              link.click();
              document.body.removeChild(link);
            }}
          >
            <span style={styles.downloadIcon}>&#8681;</span>
            Download Error Report (CSV)
          </button>
          <div style={styles.reportFileName}>
            {data.report_name}
          </div>
        </div>
      )}

      {/* Load to SQL Button (if no errors and not yet loaded) */}
      {!data.has_errors && data.steps?.sql_load?.status === 'skipped' && (
        <div style={styles.loadButtonContainer}>
          <button
            style={{ ...styles.actionButton, backgroundColor: '#28a745' }}
            onClick={() => handleIntegratedWorkflow(true)}
            disabled={loading}
          >
            All Checks Passed - Load to SQL Server
          </button>
        </div>
      )}
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
      case 'integratedWorkflow':
        return renderIntegratedWorkflowResults(results.data);
      case 'sqlpreview':
        return renderSqlPreviewResults(results.data);
      case 'sqlload':
        return renderSqlLoadResults(results.data);
      default:
        return <pre>{JSON.stringify(results.data, null, 2)}</pre>;
    }
  };

  return (
    <div style={styles.container}>
      <div style={styles.tabs}>
        <button
          style={activeTab === 'duplicates' ? styles.activeTab : styles.tab}
          onClick={() => setActiveTab('duplicates')}
        >
          Find Duplicates
        </button>
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
          style={activeTab === 'workflow' ? styles.activeTab : styles.tab}
          onClick={() => setActiveTab('workflow')}
        >
          Full Workflow
        </button>
        <button
          style={activeTab === 'sqlload' ? styles.activeTab : styles.tab}
          onClick={() => setActiveTab('sqlload')}
        >
          Load to SQL
        </button>
      </div>

      <div style={styles.content}>
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

        {activeTab === 'workflow' && (
          <div style={styles.section}>
            <p style={styles.description}>
              Run the integrated validation workflow:
            </p>
            <ol style={styles.workflowSteps}>
              <li>Check for duplicates and older versions - automatically move to DuplicateCheck/ folder</li>
              <li>Validate file names against naming conventions</li>
              <li>Check completeness - each source must have all 8 entity types</li>
              <li>If errors found: generate downloadable report</li>
              <li>If all checks pass: option to load to SQL Server</li>
            </ol>
            <button
              style={{ ...styles.actionButton, backgroundColor: '#28a745' }}
              onClick={() => handleIntegratedWorkflow(false)}
              disabled={loading}
            >
              {loading ? 'Running Validation...' : 'Run Validation Workflow'}
            </button>
          </div>
        )}

        {activeTab === 'sqlload' && (
          <div style={styles.section}>
            <p style={styles.description}>
              Load the newest version of each file type to SQL Server tables.
              Tables will be created with column names matching the CSV headers.
              Only the newest file for each type (e.g., HCM_PERSON_ADDRESS_INTF_FIMAS) will be loaded.
            </p>
            <button
              style={styles.actionButton}
              onClick={handlePreviewSqlLoad}
              disabled={loading}
            >
              {loading ? 'Analyzing Files...' : 'Preview SQL Load'}
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
  },
  tableList: {
    marginTop: '16px'
  },
  tableItem: {
    padding: '12px',
    backgroundColor: '#fff',
    borderRadius: '6px',
    marginBottom: '8px',
    borderLeft: '4px solid #007bff'
  },
  tableName: {
    fontWeight: 'bold',
    fontSize: '14px',
    color: '#232f3e'
  },
  tableSource: {
    fontSize: '12px',
    color: '#666',
    marginTop: '4px'
  },
  tableDate: {
    fontSize: '12px',
    color: '#888',
    marginTop: '2px'
  },
  rowsLoaded: {
    fontSize: '12px',
    color: '#28a745',
    marginTop: '4px'
  },
  loadError: {
    fontSize: '12px',
    color: '#dc3545',
    marginTop: '4px'
  },
  loadButtonContainer: {
    marginTop: '20px',
    textAlign: 'center'
  },
  workflowSteps: {
    marginBottom: '16px',
    paddingLeft: '20px',
    lineHeight: '1.8'
  },
  statusBanner: {
    padding: '16px',
    borderRadius: '8px',
    marginBottom: '20px',
    textAlign: 'center',
    fontSize: '16px'
  },
  stepsList: {
    marginTop: '16px'
  },
  stepBox: {
    padding: '16px',
    backgroundColor: '#fff',
    borderRadius: '8px',
    marginBottom: '12px',
    borderLeft: '4px solid #28a745'
  },
  stepHeader: {
    fontWeight: 'bold',
    marginBottom: '8px',
    color: '#232f3e'
  },
  stepDetail: {
    fontSize: '13px',
    color: '#666',
    marginBottom: '4px'
  },
  stepNote: {
    fontSize: '12px',
    color: '#856404',
    fontStyle: 'italic',
    marginTop: '8px'
  },
  invalidList: {
    marginTop: '8px',
    fontSize: '12px'
  },
  invalidItem: {
    color: '#dc3545',
    marginLeft: '12px',
    marginTop: '4px'
  },
  downloadLink: {
    color: '#007bff',
    textDecoration: 'underline'
  },
  reportSection: {
    marginTop: '20px',
    textAlign: 'center'
  },
  reportButton: {
    display: 'inline-block',
    padding: '12px 24px',
    backgroundColor: '#17a2b8',
    color: 'white',
    textDecoration: 'none',
    borderRadius: '6px',
    fontWeight: 'bold'
  },
  downloadButton: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '10px',
    padding: '14px 28px',
    backgroundColor: '#dc3545',
    color: 'white',
    border: 'none',
    borderRadius: '8px',
    fontSize: '16px',
    fontWeight: 'bold',
    cursor: 'pointer',
    boxShadow: '0 2px 4px rgba(0,0,0,0.2)'
  },
  downloadIcon: {
    fontSize: '20px',
    fontWeight: 'bold'
  },
  reportFileName: {
    marginTop: '8px',
    fontSize: '12px',
    color: '#666'
  }
};

export default ValidationPanel;
