# Third-Party Notices

This file lists the third-party open-source dependencies used by ClassHub OSS,
along with their licenses and source URLs.

ClassHub OSS is licensed under the MIT License, but each dependency retains its
own license. This file does not override or replace any upstream license terms.

---

## Python Dependencies (Backend)

| Package | Version | License | Project URL |
|---------|---------|---------|-------------|
| fastapi | >=0.115.0 | MIT | https://github.com/tiangolo/fastapi |
| uvicorn[standard] | >=0.32.0 | BSD-3-Clause | https://github.com/encode/uvicorn |
| pydantic | >=2.9.0 | MIT | https://github.com/pydantic/pydantic |
| pydantic-settings | >=2.5.0 | MIT | https://github.com/pydantic/pydantic-settings |
| python-multipart | >=0.0.12 | Apache-2.0 | https://github.com/Kludex/python-multipart |
| bcrypt | >=4.0.0 | Apache-2.0 | https://github.com/pyca/bcrypt |
| cryptography | >=43.0.0 | Apache-2.0 OR BSD-3-Clause | https://github.com/pyca/cryptography |
| httpx | >=0.27.0 | BSD-3-Clause | https://github.com/encode/httpx |
| pytest | >=8.0 (dev only) | MIT | https://github.com/pytest-dev/pytest |

### Apache-2.0 NOTICE
The following packages are licensed under Apache License 2.0, which may include
additional NOTICE file requirements:
- **python-multipart** — Copyright (c) Marcelo Trylesinski
- **bcrypt** — Copyright (c) The Python Cryptographic Authority
- **cryptography** — Copyright (c) The Python Cryptographic Authority

---

## JavaScript Dependencies (Frontend)

### Runtime

| Package | Version | License | Project URL |
|---------|---------|---------|-------------|
| react | 18.3.1 | MIT | https://github.com/facebook/react |
| react-dom | 18.3.1 | MIT | https://github.com/facebook/react |
| qrcode | 1.5.4 | MIT | https://github.com/soldair/node-qrcode |

### Build-time / Dev only (not shipped to end users)

| Package | Version | License | Project URL |
|---------|---------|---------|-------------|
| vite | 8.2.1 | MIT | https://github.com/vitejs/vite |
| @vitejs/plugin-react | 6.0.5 | MIT | https://github.com/vitejs/vite-plugin-react |

### Transitive dependencies
Vite, React, and their transitive dependencies are pulled via npm. For the full
dependency tree with resolved versions, see `web/package-lock.json`. All known
transitive dependencies in the current lock file are MIT or BSD licensed.

---

## Summary

- **No GPL/AGPL/LGPL dependencies** are used in this project.
- **Apache-2.0 packages** (3): python-multipart, bcrypt, cryptography — standard
  for Python web/security stacks, no special NOTICE files required beyond this
  attribution.
- **All frontend runtime dependencies** are MIT licensed.
- If a future dependency introduces a copyleft license (GPL/AGPL), this file
  must be updated and the distribution model must be re-evaluated.

---

*Generated for ClassHub OSS initial release. Update this file whenever
dependencies are added, removed, or their licenses change.*
