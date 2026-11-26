# Public Hydra privacy boundaries

Public Hydra is a standalone agent. It does not discover or use a private BACKS
provider. The private deployment's integration belongs in its own repository.

## Default sources and requests

- Skill discovery uses the catalogs shipped with this package. It does not
  automatically import a shared Codex skill directory. An explicit skill root
  remains an operator choice; imported content can enter model requests.
- Shared Claude project memory is not imported by default. Setting
  `HYDRA_IMPORT_CLAUDE_MEMORY=1` explicitly permits that import, including during
  migration. Imported memory can enter model requests; enable it only when the
  selected memory is intended for this public agent.
- Skill, capability, declarative and workspace context files must resolve inside
  their selected root. A link cannot silently add a file from elsewhere on the
  machine. These checks validate the selected paths; they do not provide atomic
  protection against another process changing links concurrently.
- Environment sessions refuse source links that escape the selected project.
  Internal project links are relocated into the copied workspace. A failed copy
  does not leave a partially initialized session behind.
- The agent's HTTP fetch tool uses public addresses by default. Private and
  loopback services require an explicit `HYDRA_HTTP_FETCH_ALLOWED_HOSTS` entry.
  Redirects pass the same address checks. An explicit entry never permits
  link-local metadata services or other always-refused address classes.

An explicitly configured model provider is separate from the HTTP fetch tool.
For example, a model server inside a dedicated public-agent VM remains usable.
Do not supply private deployment endpoints or credentials to the public agent.

## Host permissions still matter

Approval for a shell command grants that command the documented host capability.
The shell sandbox restricts writes outside the workspace; it does not promise
that approved commands cannot read the rest of the host or use its network.
Read-only access is still access to private data. A configured approval policy
is not a replacement for a separate operating-system trust boundary.

For strict separation, run public Hydra in a dedicated VM or separately confined
account with its own home, configuration, workspace and model credentials. Keep
private repositories, SSH keys, credential stores, host mounts and deployment
configuration outside that environment. Restrict its network so private services
are unreachable, and run any local model service inside the same boundary.

## Publication checks

A clean current checkout does not establish that Git history, old branches,
tags, pull-request references, logs or downloadable artifacts are clean.
Review those surfaces separately. Preserve public contributor and upstream
license credit while using a public GitHub noreply address for commit metadata.

Do not include suspected secret values, private addresses or personal contact
details in a public issue, pull request, test fixture or scanner log. Use synthetic
canaries for regression tests and keep any incident evidence access-restricted.
