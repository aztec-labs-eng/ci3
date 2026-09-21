#!/usr/bin/env bash
source $(dirname "${BASH_SOURCE[0]}")/source_bootstrap

# ci3 relative to the repository being built: "ci3" as a submodule, "." standalone. Test
# commands run from the root, and the hash covers ci3 alone either way.
ci3_rel=$(realpath --relative-to="$root" "$ci3")
if [ "$ci3_rel" == "." ]; then
  hash=$(cache_content_hash ^)
  prefix=.
else
  hash=$(cache_content_hash "^$ci3_rel")
  prefix=./$ci3_rel
fi

function test_cmds {
  for f in tests/*; do
    echo "$hash $prefix/$f"
  done
  echo "$hash $prefix/semver test"
}

function test {
  echo_header "ci3 tests"
  test_cmds | filter_test_cmds | parallelize
}

case "$cmd" in
  "")
    test
    ;;
  *)
    default_cmd_handler "$@"
    ;;
esac
