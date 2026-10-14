# Security policy

This repository publishes `billkit-eu`, the BillKit Python SDK.

## Reporting a vulnerability

Email **security@billkit.eu**. Please do not open a public issue, pull request or discussion for a
vulnerability.

Include what is affected (the package version, and the runtime or operating system), the steps to
reproduce it, and what an attacker gains. If it involves the API, include the `X-Request-Id` of the
requests involved.

You will get an acknowledgement from a person within 3 business days, an initial assessment within
7 business days, and an update at least every 7 days until it is resolved. Disclosure is
coordinated with you: unless agreed otherwise, you are free to publish 90 days after your report
or once a fix has been released, whichever comes first.

The full policy, including scope and safe harbour for good-faith research, is at
https://docs.billkit.eu/docs/security. There is no bug bounty programme.

## Supported versions

| Version | Security fixes |
|---------|----------------|
| Latest major release | Yes |
| Previous major release | For 12 months after the next major release ships |
| Anything older | No |

Before 1.0.0, only the latest release receives security fixes.

Fixes are released as a new patch version and recorded in `CHANGELOG.md`. The versioning and
deprecation rules are at https://docs.billkit.eu/docs/deprecation.
