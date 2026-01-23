# AWS Site-to-Site VPN Setup Guide

This document provides instructions for setting up a Site-to-Site VPN connection between AWS and your corporate network. This is the long-term solution that will allow the Amplify web app to connect directly to the SFTP server.

## Architecture

```
                        AWS Cloud                           Corporate Network
┌─────────────────────────────────────────┐     ┌─────────────────────────────────┐
│                                         │     │                                 │
│  ┌─────────┐   ┌──────────────────┐    │     │    ┌────────────┐              │
│  │ Amplify │──▶│   API Gateway    │    │     │    │  Fortinet  │              │
│  │   App   │   └────────┬─────────┘    │     │    │  Firewall  │              │
│  └─────────┘            │              │     │    └─────┬──────┘              │
│                         ▼              │     │          │                     │
│              ┌──────────────────┐      │     │          │                     │
│              │  Lambda (in VPC) │      │     │    ┌─────▼──────┐              │
│              └────────┬─────────┘      │     │    │   SFTP     │              │
│                       │                │     │    │  Server    │              │
│              ┌────────▼─────────┐      │     │    │10.3.3.146  │              │
│              │  Virtual Private │◀════════════▶   └────────────┘              │
│              │    Gateway       │  IPsec      │                               │
│              └──────────────────┘  Tunnel     │                               │
│                                         │     │                                 │
└─────────────────────────────────────────┘     └─────────────────────────────────┘
```

## Prerequisites

### Information Needed from IT/Network Team

1. **Customer Gateway IP Address**: Public IP of your Fortinet firewall
2. **Internal Network CIDR**: The IP range of your corporate network (e.g., `10.0.0.0/16` or `10.3.0.0/16`)
3. **BGP ASN** (if using BGP): Your network's AS number, or confirm static routing

### AWS Requirements

1. AWS account with networking permissions
2. A VPC for the Lambda functions (can create new or use existing)

---

## Step 1: Create or Identify Your AWS VPC

If you need a new VPC:

### Using AWS Console:
1. Go to **VPC** > **Your VPCs** > **Create VPC**
2. Settings:
   - **Name**: `hacienda-sftp-vpc`
   - **IPv4 CIDR**: `10.100.0.0/16` (choose a range that doesn't conflict with corporate network)
3. Create at least 2 private subnets in different AZs:
   - `hacienda-private-subnet-1a`: `10.100.1.0/24` (us-east-1a)
   - `hacienda-private-subnet-1b`: `10.100.2.0/24` (us-east-1b)

### Using AWS CLI:
```bash
# Create VPC
aws ec2 create-vpc --cidr-block 10.100.0.0/16 --tag-specifications 'ResourceType=vpc,Tags=[{Key=Name,Value=hacienda-sftp-vpc}]'

# Note the VPC ID from output, then create subnets
aws ec2 create-subnet --vpc-id vpc-XXXXX --cidr-block 10.100.1.0/24 --availability-zone us-east-1a
aws ec2 create-subnet --vpc-id vpc-XXXXX --cidr-block 10.100.2.0/24 --availability-zone us-east-1b
```

---

## Step 2: Create Virtual Private Gateway

### Using AWS Console:
1. Go to **VPC** > **Virtual private gateways** > **Create virtual private gateway**
2. Settings:
   - **Name**: `hacienda-vpn-gateway`
   - **ASN**: Amazon default ASN (or specify custom if needed)
3. Click **Create**
4. Select the gateway > **Actions** > **Attach to VPC** > Select your VPC

### Using AWS CLI:
```bash
# Create Virtual Private Gateway
aws ec2 create-vpn-gateway --type ipsec.1 --tag-specifications 'ResourceType=vpn-gateway,Tags=[{Key=Name,Value=hacienda-vpn-gateway}]'

# Attach to VPC (replace IDs)
aws ec2 attach-vpn-gateway --vpn-gateway-id vgw-XXXXX --vpc-id vpc-XXXXX
```

---

## Step 3: Create Customer Gateway

This represents your Fortinet firewall in AWS.

### Using AWS Console:
1. Go to **VPC** > **Customer gateways** > **Create customer gateway**
2. Settings:
   - **Name**: `hacienda-corporate-gateway`
   - **BGP ASN**: `65000` (or your actual ASN if using BGP)
   - **IP Address**: Your Fortinet firewall's public IP address
3. Click **Create**

### Using AWS CLI:
```bash
# Replace FORTINET_PUBLIC_IP with your firewall's public IP
aws ec2 create-customer-gateway \
    --type ipsec.1 \
    --public-ip FORTINET_PUBLIC_IP \
    --bgp-asn 65000 \
    --tag-specifications 'ResourceType=customer-gateway,Tags=[{Key=Name,Value=hacienda-corporate-gateway}]'
```

---

## Step 4: Create Site-to-Site VPN Connection

### Using AWS Console:
1. Go to **VPC** > **Site-to-Site VPN connections** > **Create VPN connection**
2. Settings:
   - **Name**: `hacienda-vpn-connection`
   - **Target gateway type**: Virtual private gateway
   - **Virtual private gateway**: Select `hacienda-vpn-gateway`
   - **Customer gateway**: Select `hacienda-corporate-gateway`
   - **Routing options**: Static (unless using BGP)
   - **Static IP prefixes**: Enter your corporate network CIDR (e.g., `10.0.0.0/16` or `10.3.0.0/24`)
3. Click **Create**

### Using AWS CLI:
```bash
aws ec2 create-vpn-connection \
    --type ipsec.1 \
    --customer-gateway-id cgw-XXXXX \
    --vpn-gateway-id vgw-XXXXX \
    --options '{"StaticRoutesOnly":true}' \
    --tag-specifications 'ResourceType=vpn-connection,Tags=[{Key=Name,Value=hacienda-vpn-connection}]'
```

---

## Step 5: Download VPN Configuration for Fortinet

### Using AWS Console:
1. Go to **VPC** > **Site-to-Site VPN connections**
2. Select your VPN connection
3. Click **Download configuration**
4. Select:
   - **Vendor**: Fortinet
   - **Platform**: Fortigate 40+ Series
   - **Software**: FortiOS 6.4+ (or your version)
5. Download the configuration file

**Give this file to your IT/Network team** - it contains the IPsec configuration for the Fortinet firewall.

---

## Step 6: Update Route Tables

Add routes to direct traffic to corporate network through the VPN.

### Using AWS Console:
1. Go to **VPC** > **Route tables**
2. Select the route table for your private subnets
3. **Edit routes** > **Add route**:
   - **Destination**: `10.3.0.0/16` (or your corporate CIDR containing 10.3.3.146)
   - **Target**: Select your Virtual Private Gateway
4. Save

### Enable Route Propagation:
1. In route table, go to **Route propagation** tab
2. Click **Edit route propagation**
3. Enable propagation for your Virtual Private Gateway

---

## Step 7: Create Security Group for Lambda

### Using AWS Console:
1. Go to **VPC** > **Security groups** > **Create security group**
2. Settings:
   - **Name**: `hacienda-lambda-sg`
   - **Description**: Security group for SFTP Lambda functions
   - **VPC**: Select your VPC
3. **Outbound rules**:
   - Type: Custom TCP
   - Port: 22
   - Destination: 10.3.3.146/32 (SFTP server)
   - Description: SFTP access
4. Click **Create**

### Using AWS CLI:
```bash
# Create security group
aws ec2 create-security-group \
    --group-name hacienda-lambda-sg \
    --description "Security group for SFTP Lambda" \
    --vpc-id vpc-XXXXX

# Add outbound rule for SFTP
aws ec2 authorize-security-group-egress \
    --group-id sg-XXXXX \
    --protocol tcp \
    --port 22 \
    --cidr 10.3.3.146/32
```

---

## Step 8: Information for IT Team

Send the following to your IT/Network team:

### VPN Configuration Request

**Subject**: AWS Site-to-Site VPN Configuration Request

Hello,

We need to establish a Site-to-Site VPN connection between AWS and our corporate network to allow secure access to the SFTP server at 10.3.3.146.

**AWS Side Information:**
- Virtual Private Gateway ID: [vgw-XXXXX]
- AWS VPC CIDR: 10.100.0.0/16 (or your chosen CIDR)

**Attached:**
- Fortinet configuration file downloaded from AWS (contains IPsec parameters, pre-shared keys, tunnel IPs)

**Access Required:**
- From AWS Lambda (10.100.0.0/16) to SFTP server (10.3.3.146) on port 22

**Please configure:**
1. IPsec tunnels per the attached configuration
2. Firewall rules to allow traffic from AWS CIDR to 10.3.3.146:22
3. Routing to send traffic for 10.100.0.0/16 through the VPN tunnels

Let me know if you need any additional information.

---

## Step 9: Verify VPN Connection

Once IT configures the Fortinet side:

### Check Tunnel Status:
1. Go to **VPC** > **Site-to-Site VPN connections**
2. Select your connection
3. Check **Tunnel details** tab
4. Both tunnels should show **UP** status

### Test Connectivity:
Deploy a test Lambda in the VPC and try to connect to the SFTP server.

---

## Step 10: Deploy the Amplify Backend

Once VPN is up, update `infrastructure/samconfig.toml` with:

```toml
parameter_overrides = """
Environment=prod
VpcId=vpc-XXXXX                    # Your VPC ID
SubnetIds=subnet-XXXXX,subnet-YYYYY  # Your private subnet IDs
SecurityGroupId=sg-XXXXX            # Lambda security group ID
SftpHost=10.3.3.146
SftpPort=22
SftpUsername=gprerpusr
SftpPassword=YOUR_PASSWORD
"""
```

Then deploy:
```bash
cd infrastructure
sam build
sam deploy
```

---

## Troubleshooting

### VPN Tunnel Won't Come Up
- Verify the Customer Gateway IP is correct
- Check Fortinet firewall logs
- Ensure IKE/IPsec settings match on both sides

### Lambda Can't Reach SFTP
- Verify route tables have route to corporate CIDR via VGW
- Check security group allows outbound port 22
- Verify Fortinet allows traffic from AWS CIDR

### Intermittent Connectivity
- Both tunnels should be active for redundancy
- Check for overlapping CIDR ranges
- Verify MTU settings (may need to enable TCP MSS clamping)

---

## Cost Estimate

| Resource | Approximate Cost |
|----------|-----------------|
| Site-to-Site VPN | ~$0.05/hour (~$36/month) |
| Data Transfer (out) | ~$0.09/GB |
| NAT Gateway (if needed) | ~$0.045/hour + data |

Total estimated cost: **$40-60/month** depending on data transfer
