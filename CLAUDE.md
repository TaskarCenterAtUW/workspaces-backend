# CLAUDE.md

Guidance for working in this repo. Focused on the test infrastructure and
conventions established for it; see `README.md` for app setup.

## Permission Structure

Project Group Admin ("POC")
* Superuser for the whole project group
* Implied by "poc" role in TDEI

Lead/Owner/Workspace Admin
* Admin-level access for a workspace
* Configures workspace settings and quest definitions
* Assigns users to workspace teams
* Ability to merge changes from other workspace
* Exports data to TDEI (with appropriate TDEI core roles)
* Granted by Workspaces setting.

Contributor/Data Generator
* Modifies workspace data--all modifications need validation
* Implied by membership in TDEI project group

Validator
* Modifies workspace data and approves changes from contributors
* Granted by Workspaces setting.

Viewer/Member/Everyone Else
* Read-only access to workspace data
* With express TDEI sign-up, the need for this access level diminishes greatly
* Granted by Workspaces setting.

