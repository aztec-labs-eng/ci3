# Rules for changes

Use this rule of thumb for each change: will this still work if we no longer use GitHub or GitHub Actions?

Put new behavior in ci3 scripts instead of the `ci3.yml` workflows of consuming repositories. We want to keep moving away from those workflows.
