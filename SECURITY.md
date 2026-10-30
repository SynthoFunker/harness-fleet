# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.2.x   | :white_check_mark: |
| < 0.2.0 | :x:                |

## Reporting a Vulnerability

We take the security of `hmux` seriously. Because `hmux` manages local process execution and Unix domain sockets, security vulnerabilities should be handled responsibly.

### How to Report
- **Preferred Method:** Use GitHub's private vulnerability reporting by clicking **"Report a vulnerability"** under the repository's **Security** tab.
- **Alternative:** Email security disclosures to `hema.boss50@gmail.com`.
- **Please DO NOT** file public GitHub issues for security vulnerabilities.

### What to Include
- Description of the issue and attack vector
- Steps to reproduce or proof-of-concept (PoC)
- Impact assessment (e.g., socket hijacking, arbitrary process execution)

### Response Timeline
- **Initial acknowledgment:** Within 48 hours
- **Assessment & triage:** Within 5 business days
- **Fix & disclosure:** Coordinated release once a patch is tested
