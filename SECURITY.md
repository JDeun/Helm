# Security Policy

Helm is a local operations tool. It is designed to reduce risk around repeated agent work, but it should not be treated as a sandbox or security boundary.

## Supported Versions

Security fixes target the latest released version.

## Reporting a Vulnerability

Please report security issues through GitHub private vulnerability reporting if available, or open a minimal public issue that avoids sensitive details and asks for a private follow-up.

Useful reports include:

- command guard bypasses for destructive or out-of-profile commands
- accidental credential capture or logging
- unsafe default behavior in checkpoints, reports, or workspace adoption
- packaging behavior that exposes private local files

Do not include secrets, private memory, local credentials, or proprietary task history in a public report.

## Scope

In scope:

- Helm CLI behavior
- bundled reference policies
- workspace initialization and adoption logic
- checkpoint, report, guard, and memory-capture behavior

Out of scope:

- vulnerabilities in external agent runtimes
- malicious local users with filesystem access
- commands intentionally approved by the operator outside Helm policy
