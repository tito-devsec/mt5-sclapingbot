# Security Policy

## Supported versions

No versions have been released yet. Until a first release, only the latest commit on the default branch is supported.

| Version | Supported |
|---|---|
| Default branch (unreleased) | ✅ |

## What counts as a vulnerability

Examples include:

- exposure of credentials, tokens, or account data through code, logs, or configuration
- ways to bypass account validation, demo-only enforcement, or risk limits
- unintended order placement or account modification
- SQL injection or unsafe deserialization, for example of model files
- insecure GitHub Actions workflows, such as secret exposure or untrusted code execution
- vulnerable or malicious dependencies

Trading losses caused by strategy behaviour are **not** security vulnerabilities. Report them as bugs.

## How to report

**Do not open a public issue for security problems.**

Report privately using GitHub's **"Report a vulnerability"** button on the repository's **Security** tab.

> [!IMPORTANT]
> The maintainer must turn on private vulnerability reporting in the repository's security settings before this channel is available. If the button is not shown, no private channel has been set up yet. In that case, open a public issue that asks for a private contact and contains **no vulnerability details**.

### Include

- a description of the issue and its impact
- the affected files or commit
- steps to reproduce, using demo or placeholder data
- a suggested fix, if you have one

### Do not include

- live or demo account credentials, passwords, or tokens
- account numbers, balances, broker server names linked to you, or personal data
- exploits run against real accounts or third-party systems

## Response process

The maintainer will acknowledge the report, investigate it, and work on a fix. The maintainer will coordinate disclosure with you after the issue is addressed. This is a single-maintainer project, so no specific response time is guaranteed.

## If you find a leaked secret

If you notice a credential in this repository or its history, report it privately. Do not use it. The affected credential must be revoked or rotated. Removing it from the files alone is not enough.
