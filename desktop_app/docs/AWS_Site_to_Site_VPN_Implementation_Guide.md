# AWS Site-to-Site VPN Implementation Guide
## Hacienda SFTP Integration - Production Architecture

**Version:** 1.0.0
**Date:** January 2026
**Classification:** Internal Technical Documentation

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Architecture Overview](#2-architecture-overview)
3. [Prerequisites and Requirements](#3-prerequisites-and-requirements)
4. [AWS Components and Permissions](#4-aws-components-and-permissions)
5. [Fortinet/Corporate Network Requirements](#5-fortinetcorporate-network-requirements)
6. [Step-by-Step Implementation](#6-step-by-step-implementation)
7. [Security Configuration](#7-security-configuration)
8. [Testing and Validation](#8-testing-and-validation)
9. [Monitoring and Maintenance](#9-monitoring-and-maintenance)
10. [Cost Analysis](#10-cost-analysis)
11. [Troubleshooting Guide](#11-troubleshooting-guide)
12. [Appendices](#12-appendices)

---

## 1. Executive Summary

### 1.1 Purpose

This document provides comprehensive guidance for implementing an AWS Site-to-Site VPN connection between AWS cloud infrastructure and the corporate network. This VPN tunnel will enable AWS Lambda functions to securely access the internal SFTP server (10.3.3.146), eliminating the need for users to run a desktop application on VPN-connected machines.

### 1.2 Current State vs. Future State

| Aspect | Current (Desktop App) | Future (Site-to-Site VPN) |
|--------|----------------------|---------------------------|
| User Experience | Must be on VPN, run desktop app | Web browser access from anywhere |
| Network Path | User PC → VPN → SFTP | Web App → AWS → VPN → SFTP |
| Dependencies | FortiClient on user machine | VPN managed by AWS/IT |
| Scalability | One user at a time | Concurrent users supported |
| Maintenance | App updates to each user | Centralized updates |

### 1.3 Target Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              End Users                                       │
│                        (Any location, any device)                           │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │ HTTPS
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              AWS Cloud                                       │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────────────────┐ │
│  │   Amplify   │───►│ API Gateway │───►│         VPC (10.100.0.0/16)     │ │
│  │   Web App   │    │  + Cognito  │    │  ┌─────────────────────────┐   │ │
│  └─────────────┘    └─────────────┘    │  │   Lambda Function       │   │ │
│                                         │  │   (Private Subnet)      │   │ │
│                                         │  └───────────┬─────────────┘   │ │
│                                         │              │                  │ │
│                                         │  ┌───────────▼─────────────┐   │ │
│                                         │  │  Virtual Private Gateway │   │ │
│                                         │  │       (VPN Endpoint)     │   │ │
│                                         │  └───────────┬─────────────┘   │ │
│                                         └──────────────┼────────────────┘ │
└────────────────────────────────────────────────────────┼────────────────────┘
                                                         │
                                              IPsec VPN Tunnels
                                             (Encrypted, Redundant)
                                                         │
┌────────────────────────────────────────────────────────┼────────────────────┐
│                         Corporate Network              │                     │
│  ┌─────────────────────────────────────────────────────▼───────────────────┐│
│  │                     Fortinet Firewall                                   ││
│  │                   (Customer Gateway)                                    ││
│  └─────────────────────────────────────────────────────┬───────────────────┘│
│                                                        │                     │
│  ┌─────────────────────────────────────────────────────▼───────────────────┐│
│  │                     SFTP Server                                         ││
│  │                     10.3.3.146:22                                       ││
│  └─────────────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Architecture Overview

### 2.1 AWS Components

| Component | Purpose | AWS Service |
|-----------|---------|-------------|
| Web Application | User interface | AWS Amplify |
| Authentication | User login | Amazon Cognito |
| API Layer | Request routing | API Gateway |
| Business Logic | SFTP operations | AWS Lambda |
| Network Isolation | Private networking | Amazon VPC |
| VPN Endpoint | AWS side of tunnel | Virtual Private Gateway |
| File Storage | Downloaded files | Amazon S3 |

### 2.2 Corporate Network Components

| Component | Purpose | Technology |
|-----------|---------|------------|
| VPN Endpoint | Corporate side of tunnel | Fortinet FortiGate |
| Firewall | Traffic filtering | FortiGate Firewall |
| SFTP Server | File source | Linux SFTP (10.3.3.146) |

### 2.3 Network Design

#### CIDR Allocation

| Network | CIDR | Purpose |
|---------|------|---------|
| AWS VPC | 10.100.0.0/16 | AWS resources |
| Private Subnet 1 | 10.100.1.0/24 | Lambda (us-east-1a) |
| Private Subnet 2 | 10.100.2.0/24 | Lambda (us-east-1b) |
| Corporate Network | 10.0.0.0/8 (or specific range) | On-premises resources |
| SFTP Server | 10.3.3.146/32 | Target server |

**Important**: AWS VPC CIDR must not overlap with corporate network CIDR.

---

## 3. Prerequisites and Requirements

### 3.1 AWS Account Requirements

| Requirement | Details |
|-------------|---------|
| AWS Account | Active account with billing enabled |
| IAM Permissions | Admin or VPC/VPN management permissions |
| Region | us-east-1 (N. Virginia) |
| Service Quotas | Default VPN quotas sufficient |

### 3.2 Corporate Network Requirements

| Requirement | Details |
|-------------|---------|
| Firewall | Fortinet FortiGate with IPsec VPN capability |
| Public IP | Static public IP for Customer Gateway |
| BGP Support | Optional (static routing alternative) |
| Network Admin | Access to configure firewall rules |

### 3.3 Information Gathering Checklist

Before beginning implementation, collect:

#### From AWS Administrator
- [ ] AWS Account ID
- [ ] Preferred VPC CIDR (e.g., 10.100.0.0/16)
- [ ] IAM user/role for deployment
- [ ] Preferred region (us-east-1 recommended)

#### From Corporate Network/IT Team
- [ ] Public IP address of Fortinet firewall
- [ ] Corporate network CIDR (IP range)
- [ ] BGP ASN (if using BGP) or confirm static routing
- [ ] SFTP server IP and port (10.3.3.146:22)
- [ ] Firewall administrator contact

### 3.4 Technical Prerequisites

| Prerequisite | Verification Command |
|--------------|---------------------|
| AWS CLI installed | `aws --version` |
| AWS CLI configured | `aws sts get-caller-identity` |
| Sufficient IAM permissions | See Section 4.2 |

---

## 4. AWS Components and Permissions

### 4.1 Required AWS Services

| Service | Purpose | Pricing Model |
|---------|---------|---------------|
| Amazon VPC | Private network | Free (data transfer charges) |
| Virtual Private Gateway | VPN endpoint | Free |
| Site-to-Site VPN | VPN connection | $0.05/hour (~$36/month) |
| AWS Lambda | Compute | Pay per request |
| Amazon S3 | Storage | Pay per GB stored |
| API Gateway | API management | Pay per request |
| Amazon Cognito | Authentication | Pay per MAU |

### 4.2 Required IAM Permissions

#### VPN Setup Permissions

The user/role creating VPN resources needs these permissions:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "VPCFullAccess",
            "Effect": "Allow",
            "Action": [
                "ec2:CreateVpc",
                "ec2:DeleteVpc",
                "ec2:DescribeVpcs",
                "ec2:ModifyVpcAttribute",
                "ec2:CreateSubnet",
                "ec2:DeleteSubnet",
                "ec2:DescribeSubnets",
                "ec2:CreateRouteTable",
                "ec2:DeleteRouteTable",
                "ec2:DescribeRouteTables",
                "ec2:CreateRoute",
                "ec2:DeleteRoute",
                "ec2:AssociateRouteTable",
                "ec2:DisassociateRouteTable",
                "ec2:CreateInternetGateway",
                "ec2:DeleteInternetGateway",
                "ec2:AttachInternetGateway",
                "ec2:DetachInternetGateway",
                "ec2:DescribeInternetGateways"
            ],
            "Resource": "*"
        },
        {
            "Sid": "VPNAccess",
            "Effect": "Allow",
            "Action": [
                "ec2:CreateVpnGateway",
                "ec2:DeleteVpnGateway",
                "ec2:AttachVpnGateway",
                "ec2:DetachVpnGateway",
                "ec2:DescribeVpnGateways",
                "ec2:CreateCustomerGateway",
                "ec2:DeleteCustomerGateway",
                "ec2:DescribeCustomerGateways",
                "ec2:CreateVpnConnection",
                "ec2:DeleteVpnConnection",
                "ec2:DescribeVpnConnections",
                "ec2:CreateVpnConnectionRoute",
                "ec2:DeleteVpnConnectionRoute",
                "ec2:EnableVgwRoutePropagation",
                "ec2:DisableVgwRoutePropagation"
            ],
            "Resource": "*"
        },
        {
            "Sid": "SecurityGroupAccess",
            "Effect": "Allow",
            "Action": [
                "ec2:CreateSecurityGroup",
                "ec2:DeleteSecurityGroup",
                "ec2:DescribeSecurityGroups",
                "ec2:AuthorizeSecurityGroupIngress",
                "ec2:AuthorizeSecurityGroupEgress",
                "ec2:RevokeSecurityGroupIngress",
                "ec2:RevokeSecurityGroupEgress"
            ],
            "Resource": "*"
        },
        {
            "Sid": "TaggingAccess",
            "Effect": "Allow",
            "Action": [
                "ec2:CreateTags",
                "ec2:DeleteTags",
                "ec2:DescribeTags"
            ],
            "Resource": "*"
        }
    ]
}
```

#### Lambda Execution Role Permissions

The Lambda function needs:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "VPCAccess",
            "Effect": "Allow",
            "Action": [
                "ec2:CreateNetworkInterface",
                "ec2:DescribeNetworkInterfaces",
                "ec2:DeleteNetworkInterface",
                "ec2:AssignPrivateIpAddresses",
                "ec2:UnassignPrivateIpAddresses"
            ],
            "Resource": "*"
        },
        {
            "Sid": "S3Access",
            "Effect": "Allow",
            "Action": [
                "s3:PutObject",
                "s3:GetObject",
                "s3:ListBucket"
            ],
            "Resource": [
                "arn:aws:s3:::hacienda-sftp-downloads",
                "arn:aws:s3:::hacienda-sftp-downloads/*"
            ]
        },
        {
            "Sid": "SecretsAccess",
            "Effect": "Allow",
            "Action": [
                "secretsmanager:GetSecretValue"
            ],
            "Resource": "arn:aws:secretsmanager:us-east-1:*:secret:hacienda-sftp-credentials*"
        },
        {
            "Sid": "LogsAccess",
            "Effect": "Allow",
            "Action": [
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents"
            ],
            "Resource": "*"
        }
    ]
}
```

### 4.3 AWS Resource Naming Convention

| Resource | Name |
|----------|------|
| VPC | `hacienda-sftp-vpc` |
| Subnet (AZ-a) | `hacienda-private-subnet-1a` |
| Subnet (AZ-b) | `hacienda-private-subnet-1b` |
| Virtual Private Gateway | `hacienda-vpn-gateway` |
| Customer Gateway | `hacienda-corporate-gateway` |
| VPN Connection | `hacienda-vpn-connection` |
| Route Table | `hacienda-private-rt` |
| Security Group | `hacienda-lambda-sg` |

---

## 5. Fortinet/Corporate Network Requirements

### 5.1 FortiGate Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| FortiOS Version | 6.0+ | 7.0+ |
| IPsec VPN License | Included | Included |
| Public IP | 1 static IP | 1 static IP |
| Throughput | 100 Mbps | 500+ Mbps |

### 5.2 IPsec VPN Configuration Parameters

AWS will provide these parameters when you download the VPN configuration:

#### IKE (Phase 1) Parameters

| Parameter | Value |
|-----------|-------|
| IKE Version | IKEv1 or IKEv2 |
| Authentication | Pre-Shared Key |
| Encryption | AES-256 |
| Integrity | SHA-256 |
| DH Group | 14 (2048-bit MODP) |
| Lifetime | 28800 seconds (8 hours) |

#### IPsec (Phase 2) Parameters

| Parameter | Value |
|-----------|-------|
| Encryption | AES-256 |
| Integrity | SHA-256 |
| PFS DH Group | 14 (2048-bit MODP) |
| Lifetime | 3600 seconds (1 hour) |

### 5.3 Firewall Rules Required

#### Inbound Rules (Internet to FortiGate)

| Source | Destination | Protocol | Port | Purpose |
|--------|-------------|----------|------|---------|
| AWS VPN Endpoints* | FortiGate Public IP | UDP | 500 | IKE negotiation |
| AWS VPN Endpoints* | FortiGate Public IP | UDP | 4500 | IPsec NAT-T |
| AWS VPN Endpoints* | FortiGate Public IP | IP Protocol 50 | N/A | ESP (IPsec) |

*AWS VPN endpoint IPs provided in VPN configuration download

#### Internal Rules (VPN Tunnel to Corporate Network)

| Source | Destination | Protocol | Port | Purpose |
|--------|-------------|----------|------|---------|
| 10.100.0.0/16 (AWS VPC) | 10.3.3.146 | TCP | 22 | SFTP access |

### 5.4 Routing Requirements

The FortiGate must be configured to:

1. **Accept VPN traffic** from AWS VPN endpoints
2. **Route traffic** from AWS CIDR (10.100.0.0/16) through VPN tunnel
3. **Allow forwarding** from VPN tunnel to SFTP server (10.3.3.146)
4. **Advertise routes** for corporate network to AWS (if using BGP)

### 5.5 Network Administrator Checklist

Provide this checklist to your network/IT team:

```
AWS Site-to-Site VPN Configuration Request
==========================================

1. INFORMATION NEEDED FROM YOU:
   [ ] Public IP address of FortiGate firewall: _______________
   [ ] Corporate network CIDR(s) to advertise: _______________
   [ ] BGP ASN (if using BGP): _______________
       OR confirm static routing: [ ]
   [ ] Contact for VPN configuration: _______________

2. CONFIGURATION FILE:
   We will provide a FortiGate-specific configuration file
   downloaded from AWS after creating the VPN connection.

3. FIREWALL RULES TO CREATE:
   - Allow UDP 500, 4500 from AWS VPN endpoint IPs
   - Allow ESP (IP protocol 50) from AWS VPN endpoint IPs
   - Allow TCP 22 from 10.100.0.0/16 to 10.3.3.146

4. ROUTING:
   - Route 10.100.0.0/16 through VPN tunnel
   - (BGP will handle this automatically if configured)

5. TESTING:
   Once configured, we will test by:
   - Verifying tunnel status shows UP
   - Testing connectivity from AWS Lambda to 10.3.3.146:22
```

---

## 6. Step-by-Step Implementation

### 6.1 Phase 1: Create AWS VPC

#### Step 1.1: Create VPC

**AWS Console:**
1. Navigate to VPC Dashboard
2. Click "Create VPC"
3. Configure:
   - Name: `hacienda-sftp-vpc`
   - IPv4 CIDR: `10.100.0.0/16`
   - IPv6 CIDR: No
   - Tenancy: Default

**AWS CLI:**
```bash
aws ec2 create-vpc \
    --cidr-block 10.100.0.0/16 \
    --tag-specifications 'ResourceType=vpc,Tags=[{Key=Name,Value=hacienda-sftp-vpc}]'
```

#### Step 1.2: Enable DNS Support

```bash
aws ec2 modify-vpc-attribute \
    --vpc-id vpc-XXXXXXXXX \
    --enable-dns-support '{"Value":true}'

aws ec2 modify-vpc-attribute \
    --vpc-id vpc-XXXXXXXXX \
    --enable-dns-hostnames '{"Value":true}'
```

#### Step 1.3: Create Private Subnets

```bash
# Subnet in us-east-1a
aws ec2 create-subnet \
    --vpc-id vpc-XXXXXXXXX \
    --cidr-block 10.100.1.0/24 \
    --availability-zone us-east-1a \
    --tag-specifications 'ResourceType=subnet,Tags=[{Key=Name,Value=hacienda-private-subnet-1a}]'

# Subnet in us-east-1b
aws ec2 create-subnet \
    --vpc-id vpc-XXXXXXXXX \
    --cidr-block 10.100.2.0/24 \
    --availability-zone us-east-1b \
    --tag-specifications 'ResourceType=subnet,Tags=[{Key=Name,Value=hacienda-private-subnet-1b}]'
```

### 6.2 Phase 2: Create VPN Components

#### Step 2.1: Create Virtual Private Gateway

```bash
aws ec2 create-vpn-gateway \
    --type ipsec.1 \
    --amazon-side-asn 64512 \
    --tag-specifications 'ResourceType=vpn-gateway,Tags=[{Key=Name,Value=hacienda-vpn-gateway}]'
```

#### Step 2.2: Attach VPN Gateway to VPC

```bash
aws ec2 attach-vpn-gateway \
    --vpn-gateway-id vgw-XXXXXXXXX \
    --vpc-id vpc-XXXXXXXXX
```

#### Step 2.3: Create Customer Gateway

Replace `FORTINET_PUBLIC_IP` with the actual public IP of the FortiGate firewall.

```bash
aws ec2 create-customer-gateway \
    --type ipsec.1 \
    --public-ip FORTINET_PUBLIC_IP \
    --bgp-asn 65000 \
    --tag-specifications 'ResourceType=customer-gateway,Tags=[{Key=Name,Value=hacienda-corporate-gateway}]'
```

Note: If using static routing instead of BGP, the BGP ASN is still required but won't be used.

#### Step 2.4: Create VPN Connection

**For Static Routing:**
```bash
aws ec2 create-vpn-connection \
    --type ipsec.1 \
    --customer-gateway-id cgw-XXXXXXXXX \
    --vpn-gateway-id vgw-XXXXXXXXX \
    --options '{"StaticRoutesOnly":true}' \
    --tag-specifications 'ResourceType=vpn-connection,Tags=[{Key=Name,Value=hacienda-vpn-connection}]'
```

**For BGP (Dynamic Routing):**
```bash
aws ec2 create-vpn-connection \
    --type ipsec.1 \
    --customer-gateway-id cgw-XXXXXXXXX \
    --vpn-gateway-id vgw-XXXXXXXXX \
    --tag-specifications 'ResourceType=vpn-connection,Tags=[{Key=Name,Value=hacienda-vpn-connection}]'
```

#### Step 2.5: Add Static Routes (if using static routing)

```bash
aws ec2 create-vpn-connection-route \
    --vpn-connection-id vpn-XXXXXXXXX \
    --destination-cidr-block 10.0.0.0/8
```

Adjust the CIDR to match your corporate network range.

### 6.3 Phase 3: Download and Share VPN Configuration

#### Step 3.1: Download Configuration

**AWS Console:**
1. Go to VPC → Site-to-Site VPN Connections
2. Select `hacienda-vpn-connection`
3. Click "Download configuration"
4. Select:
   - Vendor: **Fortinet**
   - Platform: **Fortigate 40+ Series**
   - Software: **FortiOS 6.4+** (or your version)
5. Download the file

#### Step 3.2: Share with IT Team

Send the downloaded configuration file to your network administrator along with:
- The checklist from Section 5.5
- AWS VPN endpoint IP addresses (from configuration file)
- Expected AWS CIDR (10.100.0.0/16)

### 6.4 Phase 4: Configure Route Tables

#### Step 4.1: Create Route Table

```bash
aws ec2 create-route-table \
    --vpc-id vpc-XXXXXXXXX \
    --tag-specifications 'ResourceType=route-table,Tags=[{Key=Name,Value=hacienda-private-rt}]'
```

#### Step 4.2: Add Route to Corporate Network

```bash
aws ec2 create-route \
    --route-table-id rtb-XXXXXXXXX \
    --destination-cidr-block 10.0.0.0/8 \
    --gateway-id vgw-XXXXXXXXX
```

#### Step 4.3: Associate Subnets with Route Table

```bash
aws ec2 associate-route-table \
    --route-table-id rtb-XXXXXXXXX \
    --subnet-id subnet-XXXXXXXXX

aws ec2 associate-route-table \
    --route-table-id rtb-XXXXXXXXX \
    --subnet-id subnet-YYYYYYYYY
```

#### Step 4.4: Enable Route Propagation (for BGP)

```bash
aws ec2 enable-vgw-route-propagation \
    --route-table-id rtb-XXXXXXXXX \
    --gateway-id vgw-XXXXXXXXX
```

### 6.5 Phase 5: Create Security Group

```bash
# Create security group
aws ec2 create-security-group \
    --group-name hacienda-lambda-sg \
    --description "Security group for Hacienda SFTP Lambda" \
    --vpc-id vpc-XXXXXXXXX

# Add outbound rule for SFTP
aws ec2 authorize-security-group-egress \
    --group-id sg-XXXXXXXXX \
    --protocol tcp \
    --port 22 \
    --cidr 10.3.3.146/32

# Add outbound rule for HTTPS (AWS services)
aws ec2 authorize-security-group-egress \
    --group-id sg-XXXXXXXXX \
    --protocol tcp \
    --port 443 \
    --cidr 0.0.0.0/0
```

### 6.6 Phase 6: Deploy Lambda and Application

Once the VPN is active, deploy the application stack:

```bash
cd C:\SFTPProgram\infrastructure

# Update samconfig.toml with:
# - VpcId
# - SubnetIds
# - SecurityGroupId

sam build
sam deploy
```

---

## 7. Security Configuration

### 7.1 Encryption in Transit

| Path | Encryption |
|------|------------|
| User → Amplify | HTTPS (TLS 1.2+) |
| Amplify → API Gateway | HTTPS (TLS 1.2+) |
| API Gateway → Lambda | Internal AWS |
| Lambda → S3 | HTTPS (TLS 1.2+) |
| Lambda → VPN → SFTP | IPsec (AES-256) + SSH |

### 7.2 Encryption at Rest

| Data | Encryption |
|------|------------|
| S3 Objects | SSE-S3 (AES-256) |
| Secrets Manager | AWS KMS |
| CloudWatch Logs | AWS KMS (optional) |

### 7.3 Network Security Layers

```
┌────────────────────────────────────────┐
│ Layer 1: AWS Security Groups           │
│ - Only allow outbound to SFTP IP:22    │
│ - Only allow outbound HTTPS for AWS    │
├────────────────────────────────────────┤
│ Layer 2: VPC Network ACLs              │
│ - Stateless firewall rules             │
│ - Subnet-level protection              │
├────────────────────────────────────────┤
│ Layer 3: IPsec VPN Encryption          │
│ - AES-256 encryption                   │
│ - Perfect Forward Secrecy              │
├────────────────────────────────────────┤
│ Layer 4: FortiGate Firewall            │
│ - Intrusion Prevention                 │
│ - Application Control                  │
├────────────────────────────────────────┤
│ Layer 5: SFTP (SSH)                    │
│ - SSH-2 protocol encryption            │
│ - Password authentication              │
└────────────────────────────────────────┘
```

### 7.4 IAM Best Practices

1. **Principle of Least Privilege**: Lambda role only has permissions for required resources
2. **Resource-Based Policies**: S3 bucket policy restricts access
3. **No Hardcoded Credentials**: Use Secrets Manager for SFTP credentials
4. **Separate Roles**: Different IAM roles for deployment vs. runtime

### 7.5 Secrets Management

Store SFTP credentials in AWS Secrets Manager:

```bash
aws secretsmanager create-secret \
    --name hacienda-sftp-credentials \
    --secret-string '{
        "host": "10.3.3.146",
        "port": "22",
        "username": "gprerpusr",
        "password": "REDACTED"
    }'
```

---

## 8. Testing and Validation

### 8.1 VPN Tunnel Verification

#### Check Tunnel Status (AWS Console)

1. Go to VPC → Site-to-Site VPN Connections
2. Select your VPN connection
3. Click "Tunnel details" tab
4. Both tunnels should show "UP"

#### Check Tunnel Status (AWS CLI)

```bash
aws ec2 describe-vpn-connections \
    --vpn-connection-ids vpn-XXXXXXXXX \
    --query 'VpnConnections[0].VgwTelemetry'
```

Expected output for healthy tunnels:
```json
[
    {
        "Status": "UP",
        "OutsideIpAddress": "x.x.x.x",
        "AcceptedRouteCount": 1
    },
    {
        "Status": "UP",
        "OutsideIpAddress": "y.y.y.y",
        "AcceptedRouteCount": 1
    }
]
```

### 8.2 Connectivity Testing

#### Deploy Test Lambda

Create a simple Lambda to test SFTP connectivity:

```python
import socket

def lambda_handler(event, context):
    host = "10.3.3.146"
    port = 22
    timeout = 10

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()

        if result == 0:
            return {"status": "SUCCESS", "message": f"Port {port} is reachable"}
        else:
            return {"status": "FAILED", "message": f"Port {port} is not reachable"}
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}
```

### 8.3 End-to-End Testing

1. **User Authentication**: Log in via Cognito
2. **API Call**: Trigger download via API Gateway
3. **Lambda Execution**: Verify Lambda runs in VPC
4. **VPN Transit**: Confirm traffic routes through VPN
5. **SFTP Connection**: Verify SFTP server accepts connection
6. **File Transfer**: Confirm files transfer successfully
7. **S3 Upload**: Verify files appear in S3 bucket

### 8.4 Test Checklist

```
VPN Tunnel Tests
[ ] Tunnel 1 status: UP
[ ] Tunnel 2 status: UP
[ ] Routes propagated correctly

Network Tests
[ ] Lambda can resolve DNS
[ ] Lambda can reach 10.3.3.146
[ ] Port 22 is accessible

Application Tests
[ ] SFTP authentication succeeds
[ ] File listing works
[ ] File download works
[ ] S3 upload works
[ ] File verification passes

Security Tests
[ ] Lambda cannot reach unauthorized IPs
[ ] VPN traffic is encrypted
[ ] Logs do not contain credentials
```

---

## 9. Monitoring and Maintenance

### 9.1 CloudWatch Metrics

#### VPN Metrics

| Metric | Description | Alarm Threshold |
|--------|-------------|-----------------|
| `TunnelState` | Tunnel up/down | < 1 (down) |
| `TunnelDataIn` | Bytes received | Baseline comparison |
| `TunnelDataOut` | Bytes sent | Baseline comparison |

#### Create Tunnel Down Alarm

```bash
aws cloudwatch put-metric-alarm \
    --alarm-name hacienda-vpn-tunnel-down \
    --metric-name TunnelState \
    --namespace AWS/VPN \
    --statistic Average \
    --period 300 \
    --threshold 1 \
    --comparison-operator LessThanThreshold \
    --evaluation-periods 2 \
    --dimensions Name=VpnId,Value=vpn-XXXXXXXXX \
    --alarm-actions arn:aws:sns:us-east-1:ACCOUNT:alerts
```

### 9.2 VPN Maintenance

| Task | Frequency | Procedure |
|------|-----------|-----------|
| Tunnel health check | Daily (automated) | CloudWatch alarm |
| Key rotation | Annually | Recreate VPN connection |
| Security review | Quarterly | Review firewall rules |
| Performance review | Monthly | Check throughput metrics |

### 9.3 Troubleshooting Runbook

#### Tunnel Down

1. Check FortiGate VPN status
2. Verify internet connectivity to FortiGate
3. Check IKE/IPsec logs on both sides
4. Verify pre-shared keys match
5. Contact network team if unresolved

#### Slow Performance

1. Check VPN throughput metrics
2. Verify no bandwidth throttling
3. Check Lambda timeout settings
4. Review file sizes being transferred

---

## 10. Cost Analysis

### 10.1 AWS Costs

| Service | Unit Cost | Estimated Monthly |
|---------|-----------|-------------------|
| Site-to-Site VPN | $0.05/hour | $36.00 |
| Data Transfer (VPN out) | $0.09/GB | Variable |
| Lambda | $0.20/million requests | < $1.00 |
| S3 Storage | $0.023/GB | Variable |
| API Gateway | $3.50/million requests | < $5.00 |
| Cognito | Free tier (50k MAU) | $0.00 |

**Estimated Base Cost**: ~$40-50/month (excluding data transfer)

### 10.2 Data Transfer Costs

| Data Volume | VPN Data Out Cost |
|-------------|-------------------|
| 1 GB/month | $0.09 |
| 10 GB/month | $0.90 |
| 100 GB/month | $9.00 |
| 1 TB/month | $92.16 |

### 10.3 Cost Optimization

1. **Use single tunnel** if redundancy not required (saves ~50% VPN cost)
2. **Compress data** before transfer if possible
3. **Schedule transfers** during off-peak hours
4. **Use S3 lifecycle policies** to archive old files

---

## 11. Troubleshooting Guide

### 11.1 VPN Issues

#### Tunnel Won't Establish

| Symptom | Possible Cause | Solution |
|---------|---------------|----------|
| Phase 1 fails | Mismatched IKE settings | Verify encryption/hash/DH group |
| Phase 1 fails | Wrong pre-shared key | Re-download config from AWS |
| Phase 1 fails | Firewall blocking UDP 500/4500 | Open firewall ports |
| Phase 2 fails | Mismatched IPsec settings | Verify encryption/hash/PFS |
| Tunnel flapping | MTU issues | Enable TCP MSS clamping |

#### Tunnel Up But No Traffic

| Symptom | Possible Cause | Solution |
|---------|---------------|----------|
| No connectivity | Missing routes | Add route to VPN gateway |
| No connectivity | Security group blocking | Check outbound rules |
| No connectivity | FortiGate policy missing | Add allow policy |
| Partial connectivity | Asymmetric routing | Check all route tables |

### 11.2 Lambda Connectivity Issues

#### Lambda Cannot Reach SFTP

1. **Verify VPN tunnel is UP**
   ```bash
   aws ec2 describe-vpn-connections --query 'VpnConnections[0].VgwTelemetry'
   ```

2. **Verify Lambda is in correct VPC**
   - Check Lambda configuration in console
   - Verify VPC ID matches hacienda-sftp-vpc

3. **Verify Security Group allows outbound**
   ```bash
   aws ec2 describe-security-groups --group-ids sg-XXXXXXXXX
   ```

4. **Verify Route Table has route to corporate network**
   ```bash
   aws ec2 describe-route-tables --route-table-ids rtb-XXXXXXXXX
   ```

5. **Test with simple connectivity Lambda** (see Section 8.2)

### 11.3 Common Error Messages

| Error | Cause | Solution |
|-------|-------|----------|
| `Connection timed out` | VPN down or routing issue | Check VPN and routes |
| `Connection refused` | SFTP server not running | Contact server admin |
| `Authentication failed` | Wrong credentials | Verify Secrets Manager |
| `Network unreachable` | Lambda not in VPC | Check Lambda VPC config |

---

## 12. Appendices

### Appendix A: AWS CLI Commands Reference

```bash
# VPC Commands
aws ec2 describe-vpcs --filters "Name=tag:Name,Values=hacienda-sftp-vpc"
aws ec2 describe-subnets --filters "Name=vpc-id,Values=vpc-XXXXXXXXX"
aws ec2 describe-route-tables --filters "Name=vpc-id,Values=vpc-XXXXXXXXX"

# VPN Commands
aws ec2 describe-vpn-gateways
aws ec2 describe-customer-gateways
aws ec2 describe-vpn-connections
aws ec2 describe-vpn-connections --query 'VpnConnections[0].VgwTelemetry'

# Security Group Commands
aws ec2 describe-security-groups --group-ids sg-XXXXXXXXX
```

### Appendix B: FortiGate CLI Commands

```
# Show VPN status
get vpn ipsec tunnel summary
diagnose vpn tunnel list

# Show routing
get router info routing-table all

# Debug VPN
diagnose debug application ike -1
diagnose debug enable
```

### Appendix C: Resource IDs Template

Fill in after creating resources:

```
AWS Account ID:       _______________
Region:               us-east-1

VPC ID:               vpc-_______________
Subnet 1 ID (1a):     subnet-_______________
Subnet 2 ID (1b):     subnet-_______________
Route Table ID:       rtb-_______________

VPN Gateway ID:       vgw-_______________
Customer Gateway ID:  cgw-_______________
VPN Connection ID:    vpn-_______________

Security Group ID:    sg-_______________

FortiGate Public IP:  _______________
Corporate CIDR:       _______________
```

### Appendix D: Contact Information Template

```
AWS Administrator:
  Name:  _______________
  Email: _______________
  Phone: _______________

Network Administrator:
  Name:  _______________
  Email: _______________
  Phone: _______________

SFTP Server Administrator:
  Name:  _______________
  Email: _______________
  Phone: _______________
```

---

## Document Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0.0 | January 2026 | System | Initial release |

---

*This document is confidential and intended for internal use only.*
