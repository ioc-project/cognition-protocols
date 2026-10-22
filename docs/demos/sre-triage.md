# SRE Triage

<p class="eyebrow">Four subsystem owners, one remediation</p>

Role-specialised agents — telemetry, network, database and the region owner — must
converge on a single remediation for a region-wide incident. Each agent owns one
subsystem and can see only its own, so each opening proposal is correct from where it
stands and unacceptable from somewhere else.

This is the application the protocol benchmark is built on, and the one the measured
results report from.

!!! abstract "SRE Triage — converging on a remediation across four subsystem owners"
    [:octicons-download-24: Watch the recording](https://github.com/cisco-eti/IoC-BHAG/releases/download/demos-2026-10/sre-triage.mp4)
    &nbsp;&middot;&nbsp; 720p &middot; short &middot; 36 MB

## What to watch for

- The satisfaction floor rising round by round rather than a vote being taken.
- The committed plan being authored mid-run by the agent that was worst off.

[:octicons-arrow-right-24: The measured run](../protocols/concord/design.md#worked-example)
