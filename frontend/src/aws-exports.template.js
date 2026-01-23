// AWS Amplify Configuration Template
// Copy this file to aws-exports.js and fill in your values after deploying the backend

const awsconfig = {
  Auth: {
    Cognito: {
      userPoolId: 'YOUR_USER_POOL_ID',           // e.g., us-east-1_XXXXXXXXX
      userPoolClientId: 'YOUR_USER_POOL_CLIENT_ID', // e.g., 1234567890abcdef
      region: 'us-east-1'
    }
  },
  API: {
    REST: {
      haciendaApi: {
        endpoint: 'YOUR_API_ENDPOINT',           // e.g., https://xxxxx.execute-api.us-east-1.amazonaws.com/prod
        region: 'us-east-1'
      }
    }
  }
};

export default awsconfig;
