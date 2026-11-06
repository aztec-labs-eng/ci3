# Independence from GitHub

Use this rule of thumb for each change: will this still work if we no longer use GitHub or GitHub Actions?

Put new behavior in ci3 scripts instead of the `ci3.yml` workflows of consuming repositories. We want to keep moving away from those workflows.

For example, a ci3 script sends run data directly to the service that consumes it. This replaces a new workflow step that publishes the data through GitHub annotations.

This rule guides new work. Leave existing GitHub integration in place unless the task is to change it.
