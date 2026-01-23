import React, { useState, useEffect, useCallback } from 'react';
import { startDownload, getDownloadStatus } from '../services/api';
import ProgressBar from './ProgressBar';
import StatusMessage from './StatusMessage';

const POLL_INTERVAL = 2000; // 2 seconds

function DownloadButton() {
  const [isDownloading, setIsDownloading] = useState(false);
  const [jobId, setJobId] = useState(null);
  const [status, setStatus] = useState(null);
  const [showResult, setShowResult] = useState(false);
  const [error, setError] = useState(null);

  const pollStatus = useCallback(async (id) => {
    try {
      const statusData = await getDownloadStatus(id);
      setStatus(statusData);

      if (statusData.status === 'completed' || statusData.status === 'failed') {
        setIsDownloading(false);
        setShowResult(true);
      }
    } catch (err) {
      console.error('Error polling status:', err);
      setError(err.message);
      setIsDownloading(false);
    }
  }, []);

  useEffect(() => {
    let intervalId;

    if (isDownloading && jobId) {
      intervalId = setInterval(() => {
        pollStatus(jobId);
      }, POLL_INTERVAL);
    }

    return () => {
      if (intervalId) {
        clearInterval(intervalId);
      }
    };
  }, [isDownloading, jobId, pollStatus]);

  const handleDownload = async () => {
    setIsDownloading(true);
    setError(null);
    setShowResult(false);
    setStatus(null);

    try {
      const response = await startDownload();
      setJobId(response.jobId);

      // Initial status check
      await pollStatus(response.jobId);
    } catch (err) {
      console.error('Error starting download:', err);
      setError(err.message);
      setIsDownloading(false);
    }
  };

  const handleDismiss = () => {
    setShowResult(false);
    setStatus(null);
    setJobId(null);
    setError(null);
  };

  const handleRetry = () => {
    handleDismiss();
    handleDownload();
  };

  return (
    <div style={styles.container}>
      <button
        onClick={handleDownload}
        disabled={isDownloading}
        style={{
          ...styles.button,
          ...(isDownloading ? styles.buttonDisabled : {})
        }}
      >
        {isDownloading ? 'Downloading...' : 'Download Files'}
      </button>

      {(isDownloading || showResult) && status && (
        <ProgressBar
          progress={status.progress || 0}
          status={status.status}
          message={status.message}
        />
      )}

      {showResult && status && (
        <StatusMessage
          status={status}
          onDismiss={handleDismiss}
          onRetry={handleRetry}
        />
      )}

      {error && !showResult && (
        <div style={styles.errorBanner}>
          <p style={styles.errorText}>{error}</p>
          <button onClick={handleRetry} style={styles.retryButton}>
            Try Again
          </button>
        </div>
      )}
    </div>
  );
}

const styles = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    padding: '20px',
    maxWidth: '500px',
    margin: '0 auto'
  },
  button: {
    padding: '16px 48px',
    fontSize: '18px',
    fontWeight: 'bold',
    color: 'white',
    backgroundColor: '#232f3e',
    border: 'none',
    borderRadius: '8px',
    cursor: 'pointer',
    transition: 'background-color 0.2s ease'
  },
  buttonDisabled: {
    backgroundColor: '#666',
    cursor: 'not-allowed'
  },
  errorBanner: {
    marginTop: '20px',
    padding: '16px',
    backgroundColor: '#ffebee',
    borderRadius: '8px',
    textAlign: 'center',
    width: '100%'
  },
  errorText: {
    color: '#c62828',
    margin: '0 0 12px 0'
  },
  retryButton: {
    padding: '8px 24px',
    backgroundColor: '#f44336',
    color: 'white',
    border: 'none',
    borderRadius: '4px',
    cursor: 'pointer'
  }
};

export default DownloadButton;
