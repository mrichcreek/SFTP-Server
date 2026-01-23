import React, { useState } from 'react';
import { Amplify } from 'aws-amplify';
import { Authenticator } from '@aws-amplify/ui-react';
import '@aws-amplify/ui-react/styles.css';
import awsconfig from './aws-exports';
import DownloadButton from './components/DownloadButton';
import ValidationPanel from './components/ValidationPanel';

Amplify.configure(awsconfig);

function App() {
  const [activeSection, setActiveSection] = useState('download');

  return (
    <div style={styles.appContainer}>
      <Authenticator>
        {({ signOut, user }) => (
          <div style={styles.mainContainer}>
            <header style={styles.header}>
              <h1 style={styles.title}>Hacienda SFTP Portal</h1>
              <div style={styles.userInfo}>
                <span style={styles.userName}>
                  {user?.signInDetails?.loginId || 'User'}
                </span>
                <button onClick={signOut} style={styles.signOutButton}>
                  Sign Out
                </button>
              </div>
            </header>

            <nav style={styles.nav}>
              <button
                style={activeSection === 'download' ? styles.navButtonActive : styles.navButton}
                onClick={() => setActiveSection('download')}
              >
                File Download
              </button>
              <button
                style={activeSection === 'validation' ? styles.navButtonActive : styles.navButton}
                onClick={() => setActiveSection('validation')}
              >
                Validation & Processing
              </button>
            </nav>

            <main style={styles.main}>
              {activeSection === 'download' && (
                <div style={styles.card}>
                  <h2 style={styles.cardTitle}>File Download</h2>
                  <p style={styles.cardDescription}>
                    Click the button below to download files from the SFTP server.
                    Files will be securely transferred to S3 storage.
                  </p>
                  <DownloadButton />
                </div>
              )}

              {activeSection === 'validation' && (
                <div style={styles.wideCard}>
                  <h2 style={styles.cardTitle}>File Validation & Processing</h2>
                  <p style={styles.cardDescription}>
                    Validate downloaded files, check for completeness, detect duplicates,
                    and process files through the complete workflow.
                  </p>
                  <ValidationPanel />
                </div>
              )}
            </main>

            <footer style={styles.footer}>
              <p>Hacienda ERP Integration System</p>
            </footer>
          </div>
        )}
      </Authenticator>
    </div>
  );
}

const styles = {
  appContainer: {
    minHeight: '100vh',
    backgroundColor: '#f5f5f5'
  },
  mainContainer: {
    minHeight: '100vh',
    display: 'flex',
    flexDirection: 'column'
  },
  header: {
    backgroundColor: '#232f3e',
    color: 'white',
    padding: '16px 24px',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center'
  },
  title: {
    margin: 0,
    fontSize: '24px'
  },
  userInfo: {
    display: 'flex',
    alignItems: 'center',
    gap: '16px'
  },
  userName: {
    fontSize: '14px'
  },
  signOutButton: {
    padding: '8px 16px',
    backgroundColor: 'transparent',
    color: 'white',
    border: '1px solid white',
    borderRadius: '4px',
    cursor: 'pointer',
    fontSize: '14px'
  },
  nav: {
    backgroundColor: '#37475a',
    padding: '0 24px',
    display: 'flex',
    gap: '4px'
  },
  navButton: {
    padding: '14px 20px',
    backgroundColor: 'transparent',
    color: '#adb5bd',
    border: 'none',
    cursor: 'pointer',
    fontSize: '14px',
    borderBottom: '3px solid transparent'
  },
  navButtonActive: {
    padding: '14px 20px',
    backgroundColor: 'transparent',
    color: 'white',
    border: 'none',
    cursor: 'pointer',
    fontSize: '14px',
    fontWeight: 'bold',
    borderBottom: '3px solid #ff9900'
  },
  main: {
    flex: 1,
    padding: '40px 24px',
    display: 'flex',
    justifyContent: 'center',
    alignItems: 'flex-start'
  },
  card: {
    backgroundColor: 'white',
    borderRadius: '12px',
    padding: '32px',
    boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
    maxWidth: '600px',
    width: '100%',
    textAlign: 'center'
  },
  wideCard: {
    backgroundColor: 'white',
    borderRadius: '12px',
    padding: '32px',
    boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
    maxWidth: '1000px',
    width: '100%',
    textAlign: 'left'
  },
  cardTitle: {
    margin: '0 0 16px 0',
    color: '#232f3e'
  },
  cardDescription: {
    color: '#666',
    marginBottom: '24px',
    lineHeight: '1.5'
  },
  footer: {
    backgroundColor: '#232f3e',
    color: '#999',
    textAlign: 'center',
    padding: '16px',
    fontSize: '12px'
  }
};

export default App;
