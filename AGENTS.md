# Rules for changes

ci3 is a generic build tool. Use this rule of thumb for each change: will this still work with a different vendor or a different consuming repository? For example, will it still work if we no longer use GitHub or GitHub Actions?

Build general mechanisms in ci3, and let each consuming repository supply its own values through settings.

Avoid adding steps to the `ci3.yml` workflows of consuming repositories. We want to keep moving away from those workflows.

ci3 can depend on AWS and Slack.
