# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

## Execution statuses

Once a ticket leaves triage, the `**Status:**` line follows the cycle in `workflow.md`. These values are valid too:

| Status        | Meaning                                                         |
| ------------- | --------------------------------------------------------------- |
| `in-progress` | A Programador is implementing it on its own branch              |
| `in-review`   | The Pull Request is open and waiting for the Revisor and the CI |
| `done`        | The Pull Request was merged into `main`                         |
| `blocked`     | Waiting on a human decision, recorded under `## Comments`       |

Edit the right-hand column of the first table to match whatever vocabulary you actually use.
