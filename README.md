# Eniris SmartgridOne for Home Assistant

Custom Home Assistant integration for Eniris SmartgridOne controllers.

## HACS Installation

This repository is structured for HACS as a custom integration:

- `hacs.json` is present at the repository root.
- The integration lives in `custom_components/eniris_smartgridone`.
- The integration manifest contains a semantic version.

HACS should install this integration from a GitHub release tag, not directly
from a commit hash. Publish releases with semantic version tags that match the
integration version, for example:

```text
v0.1.0
```

Installing from a raw commit such as `5cd82b5` can trigger HACS version
validation errors because a commit hash is not a valid integration version.
