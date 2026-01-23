import React from 'react';

function StatusMessage({ status, onDismiss, onRetry }) {
  const isSuccess = status.status === 'completed';
  const isFailed = status.status === 'failed';

  const parseErrorDetails = () => {
    if (!status.errorDetails) return null;

    try {
      const errorData = typeof status.errorDetails === 'string'
        ? JSON.parse(status.errorDetails)
        : status.errorDetails;

      return errorData;
    } catch {
      return { message: status.errorDetails };
    }
  };

  const errorDetails = parseErrorDetails();

  if (isSuccess) {
    return (
      <div style={styles.successContainer}>
        <div style={styles.iconContainer}>
          <span style={styles.successIcon}>&#10004;</span>
        </div>
        <h3 style={styles.successTitle}>Download Complete!</h3>
        <p style={styles.successMessage}>
          Successfully downloaded {status.filesDownloaded} files.
        </p>
        <p style={styles.details}>
          Files have been stored in your S3 bucket and verified for integrity.
        </p>
        <button onClick={onDismiss} style={styles.dismissButton}>
          Done
        </button>
      </div>
    );
  }

  if (isFailed) {
    return (
      <div style={styles.errorContainer}>
        <div style={styles.iconContainer}>
          <span style={styles.errorIcon}>&#10006;</span>
        </div>
        <h3 style={styles.errorTitle}>Download Failed</h3>
        <p style={styles.errorMessage}>
          {status.message || 'An error occurred during download.'}
        </p>

        {errorDetails && (
          <div style={styles.errorDetailsBox}>
            <h4 style={styles.detailsTitle}>Error Details:</h4>

            {errorDetails.failed_downloads && errorDetails.failed_downloads.length > 0 && (
              <div style={styles.errorSection}>
                <p style={styles.sectionTitle}>Failed Downloads:</p>
                <ul style={styles.errorList}>
                  {errorDetails.failed_downloads.map((item, index) => (
                    <li key={index}>
                      {item.filename}: {item.error}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {errorDetails.verification_errors && errorDetails.verification_errors.length > 0 && (
              <div style={styles.errorSection}>
                <p style={styles.sectionTitle}>Verification Errors:</p>
                <ul style={styles.errorList}>
                  {errorDetails.verification_errors.map((error, index) => (
                    <li key={index}>{error}</li>
                  ))}
                </ul>
              </div>
            )}

            {errorDetails.message && (
              <p style={styles.generalError}>{errorDetails.message}</p>
            )}
          </div>
        )}

        <div style={styles.buttonGroup}>
          <button onClick={onRetry} style={styles.retryButton}>
            Try Again
          </button>
          <button onClick={onDismiss} style={styles.cancelButton}>
            Cancel
          </button>
        </div>
      </div>
    );
  }

  return null;
}

const styles = {
  successContainer: {
    marginTop: '24px',
    padding: '24px',
    backgroundColor: '#e8f5e9',
    borderRadius: '12px',
    textAlign: 'center',
    width: '100%'
  },
  errorContainer: {
    marginTop: '24px',
    padding: '24px',
    backgroundColor: '#ffebee',
    borderRadius: '12px',
    textAlign: 'center',
    width: '100%'
  },
  iconContainer: {
    marginBottom: '16px'
  },
  successIcon: {
    fontSize: '48px',
    color: '#4caf50'
  },
  errorIcon: {
    fontSize: '48px',
    color: '#f44336'
  },
  successTitle: {
    color: '#2e7d32',
    margin: '0 0 8px 0'
  },
  errorTitle: {
    color: '#c62828',
    margin: '0 0 8px 0'
  },
  successMessage: {
    color: '#388e3c',
    margin: '0 0 8px 0'
  },
  errorMessage: {
    color: '#d32f2f',
    margin: '0 0 16px 0'
  },
  details: {
    color: '#666',
    fontSize: '14px',
    margin: '0 0 16px 0'
  },
  errorDetailsBox: {
    backgroundColor: 'rgba(255,255,255,0.7)',
    padding: '16px',
    borderRadius: '8px',
    textAlign: 'left',
    marginBottom: '16px'
  },
  detailsTitle: {
    margin: '0 0 8px 0',
    color: '#c62828',
    fontSize: '14px'
  },
  errorSection: {
    marginBottom: '12px'
  },
  sectionTitle: {
    margin: '0 0 4px 0',
    fontWeight: 'bold',
    fontSize: '13px',
    color: '#333'
  },
  errorList: {
    margin: '0',
    paddingLeft: '20px',
    fontSize: '12px',
    color: '#666'
  },
  generalError: {
    margin: '0',
    fontSize: '13px',
    color: '#666'
  },
  dismissButton: {
    padding: '12px 32px',
    backgroundColor: '#4caf50',
    color: 'white',
    border: 'none',
    borderRadius: '6px',
    fontSize: '16px',
    cursor: 'pointer'
  },
  buttonGroup: {
    display: 'flex',
    justifyContent: 'center',
    gap: '12px'
  },
  retryButton: {
    padding: '12px 32px',
    backgroundColor: '#f44336',
    color: 'white',
    border: 'none',
    borderRadius: '6px',
    fontSize: '16px',
    cursor: 'pointer'
  },
  cancelButton: {
    padding: '12px 32px',
    backgroundColor: '#9e9e9e',
    color: 'white',
    border: 'none',
    borderRadius: '6px',
    fontSize: '16px',
    cursor: 'pointer'
  }
};

export default StatusMessage;
