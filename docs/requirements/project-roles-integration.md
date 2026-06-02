# User Role Management — Workspaces + Tasking Manager

## Current Behaviour (Workspaces)

- Users authenticate via TDEI and inherit **implicit contributor** access across all workspaces in their project groups — no manual provisioning needed.
- The user who creates a workspace is automatically assigned the **`lead`** role for that workspace and recorded in the system.
- Today, all collaboration within a workspace operates under this single workspace-level role model.

## New Requirement (Tasking Manager)

Tasking Manager introduces **project-level roles** (`lead`, `validator`, `contributor`) so that work inside a workspace can be delegated and reviewed by specific people, not just by anyone with workspace access.

To deliver this, two new capabilities are needed:

### 1. User Search
A workspace lead creating or managing a project must be able to search for the right users from within their project group — by name or username — and pick them. This requires **integration with the TDEI user-search API** to surface candidate users in the UI.

### 2. Role Assignment
Once selected, users are assigned a project-level role (`lead`, `validator`, or `contributor`) and recorded against the project. The lead can later **add**, **change**, or **remove** these assignments as the project evolves, with safeguards to ensure every project always has at least one `lead`.